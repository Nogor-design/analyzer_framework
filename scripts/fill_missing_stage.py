"""Self-healing fill for a deployment-matrix stage that partially completed
because of the NinjaTrader Strategy-Analyzer wedge ("Run command was not
executable after N attempts"). Heavy strategies (e.g. regime-off PantheonMaster)
bog NT down after ~90 cumulative runs, leaving a contiguous tail of RunError
templates with no output.

Loop, until every expected template has output (count-based completion) or no
progress:
  1. master = merge of every BatchRunSummary seen (Completed wins over RunError).
  2. missing = expected template names - Completed(master).
  3. copy missing .xml into a fresh rerun source folder.
  4. restart NT + log in (ensure-nt-ready --restart; exit code advisory).
  5. wait for the AddOn IPC watcher to be ready.
  6. dispatch RunBatch(source=missing, dest=SAME stage output dir).
  7. wait the status file to terminal; merge the new BatchRunSummary into master.
Finally rewrite BatchRunSummary.csv to the merged master so the recipe's
lineage (truncated->full name bridge) sees all templates, then exit. Pair with
resume_recipe_to_complete.py to advance refine->final.

Usage: python scripts/fill_missing_stage.py <session_id> [stage_id]
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.web.market_data_export import build_command_payload, _write_command_file
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    ensure_bridge_available,
)

SESSIONS = Path(".ta_artifacts/web_optimizer/sessions")
ADDON_LOG = Path(r"C:\temp\nt8_addon_batch.log")
NT_EXE = r"C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe"
PWD_FILE = r"C:\Users\Owner\Downloads\P.txt"
USERNAME = "eirwin"
MAX_PASSES = 6
NT_READY_TIMEOUT = 300
BATCH_TIMEOUT = 3 * 60 * 60
TERMINAL_NT = {"finished", "completed", "failed", "error", "timed_out", "timedout", "cancelled"}
SUMMARY_COLS = [
    "Template", "Status", "Strategy", "Instrument", "Backtest start", "Backtest end",
    "Total net profit", "Trades", "Profit factor", "Max drawdown",
    "Run start time", "Run end time", "Output folder", "Error",
]


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} [fill] {msg}", flush=True)


def read_summary(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[str, dict] = {}
    for r in rows:
        name = (r.get("Template") or "").strip()
        if name:
            out[name] = r
    return out


def merge(master: dict[str, dict], new: dict[str, dict]) -> None:
    for name, row in new.items():
        cur = master.get(name)
        new_done = (row.get("Status") or "").strip().lower() == "completed"
        cur_done = cur and (cur.get("Status") or "").strip().lower() == "completed"
        if cur is None or (new_done and not cur_done):
            master[name] = row


def completed_names(master: dict[str, dict]) -> set[str]:
    return {n for n, r in master.items() if (r.get("Status") or "").strip().lower() == "completed"}


def restart_nt_and_login() -> None:
    log("restarting NT + login (ensure-nt-ready --restart)")
    subprocess.run(
        [sys.executable, "-m", "ta_foundation.nt_strategy_loop.cli", "ensure-nt-ready",
         "--restart", "--nt-exe", NT_EXE, "--username", USERNAME, "--password-file", PWD_FILE],
        capture_output=True, text=True, timeout=420,
    )  # exit code is advisory (UIAutomation prompt-scan can timeout post-login)


def _nt_title() -> str:
    try:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-Process NinjaTrader -ErrorAction SilentlyContinue | Select-Object -First 1).MainWindowTitle"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return ""


def _ipc_ready_since(after_local: datetime) -> bool:
    """True if the AddOn log has an 'IPC watcher ready' line at/after the given
    LOCAL time. AddOn log timestamps are local, so compare against naive-local."""
    if not ADDON_LOG.exists():
        return False
    try:
        for line in ADDON_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]:
            if "IPC watcher ready" in line:
                try:
                    t = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                    if t >= after_local:
                        return True
                except ValueError:
                    pass
    except OSError:
        pass
    return False


def nt_currently_ready() -> bool:
    """NT is up, past the login wall, the AddOn IPC watcher has come up at some
    point, and no batch is currently mid-run. (No recency window: NT can sit idle
    for hours and still be ready; the IPC watcher does not expire.)"""
    title = _nt_title()
    if not (title and title != "Welcome"):
        return False
    if not (ADDON_LOG.exists() and "IPC watcher ready" in ADDON_LOG.read_text(errors="ignore")):
        return False
    try:
        s = json.loads(Path(DEFAULT_STATUS_FILE).read_text())
        if str(s.get("state") or "").lower() in {"running", "starting", "requested"}:
            return False
    except Exception:
        pass
    return True


def wait_nt_ready(after_local: datetime) -> bool:
    """Wait for a fresh 'IPC watcher ready' line (local time) after `after_local`."""
    deadline = time.time() + NT_READY_TIMEOUT
    while time.time() < deadline:
        title = _nt_title()
        if title and title != "Welcome" and _ipc_ready_since(after_local):
            log(f"NT ready (title={title!r}, IPC watcher up)")
            time.sleep(5)
            return True
        time.sleep(10)
    log("NT did not become ready in time")
    return False


def wait_batch(status_path: Path, run_id: str) -> str:
    deadline = time.time() + BATCH_TIMEOUT
    last = -1
    while time.time() < deadline:
        try:
            s = json.loads(status_path.read_text())
        except Exception:
            time.sleep(15); continue
        if str(s.get("runId") or "") != run_id:
            time.sleep(10); continue
        st = str(s.get("state") or "").lower()
        done = int(s.get("completed") or 0); tot = int(s.get("total") or 0)
        if done != last:
            log(f"  NT {run_id}: {st} {done}/{tot}")
            last = done
        if st in TERMINAL_NT or (tot and done >= tot and st not in {"running", "starting"}):
            return st
        time.sleep(20)
    return "timeout"


def main() -> int:
    session_id = sys.argv[1]
    stage_id = sys.argv[2] if len(sys.argv) > 2 else "stage_1"
    sdir = SESSIONS / session_id
    gen_dir = sdir / "generated_templates" / stage_id
    out_dir = sdir / "nt_output" / stage_id
    summary_path = out_dir / "BatchRunSummary.csv"
    instrument = json.loads((sdir / "session.json").read_text())["instrument"]

    expected = {p.stem for p in gen_dir.glob("*.xml")}
    log(f"{session_id}/{stage_id}: {len(expected)} expected templates, instrument={instrument}")

    master = read_summary(summary_path)
    log(f"seed master: {len(completed_names(master))} completed / {len(master)} rows")

    cmd_path = Path(DEFAULT_COMMAND_FILE); status_path = Path(DEFAULT_STATUS_FILE)

    for p in range(1, MAX_PASSES + 1):
        done = completed_names(master)
        missing = sorted(expected - done)
        log(f"pass {p}: {len(done)} completed, {len(missing)} missing")
        if not missing:
            break
        before = len(done)

        rerun = sdir / "rerun" / stage_id / f"pass_{p}"
        if rerun.exists():
            import shutil; shutil.rmtree(rerun)
        rerun.mkdir(parents=True, exist_ok=True)
        for name in missing:
            src = gen_dir / f"{name}.xml"
            if src.exists():
                (rerun / src.name).write_bytes(src.read_bytes())

        if p == 1 and nt_currently_ready():
            log("NT already fresh + ready; skipping restart for pass 1")
        else:
            marker = datetime.now()  # AddOn log is LOCAL time
            restart_nt_and_login()
            if not wait_nt_ready(marker):
                log("aborting: NT not ready"); return 3

        ensure_bridge_available(cmd_path, status_path, now=datetime.now(timezone.utc))
        try:
            if status_path.exists():
                status_path.unlink()
        except OSError:
            pass
        run_id = f"fill_{stage_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        payload = build_command_payload(
            run_id=run_id, source_folder=rerun, dest_folder=out_dir,
            instrument=instrument, timeout_seconds=600,
        )
        _write_command_file(cmd_path, payload)
        log(f"dispatched {run_id}: {len(missing)} templates")

        state = wait_batch(status_path, run_id)
        log(f"  batch terminal: {state}")
        merge(master, read_summary(summary_path))
        after = len(completed_names(master))
        log(f"  now {after} completed (+{after - before})")
        if after <= before:
            log("no progress this pass; stopping"); break

    # Rewrite merged master so lineage sees every template.
    with summary_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_COLS)
        w.writeheader()
        for name in sorted(master):
            row = {c: master[name].get(c, "") for c in SUMMARY_COLS}
            w.writerow(row)
    final_done = len(completed_names(master))
    log(f"FILL DONE: {final_done}/{len(expected)} completed; merged BatchRunSummary written")
    if final_done < len(expected):
        log("not fully filled; NOT advancing recipe (resume manually once filled)")
        return 2

    log("advancing recipe: stage_1 -> refine_risk -> final_backtest")
    rc = subprocess.call(
        [sys.executable, "scripts/resume_recipe_to_complete.py", session_id]
    )
    log(f"resume driver exited rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
