"""Re-backtest a finished session's final templates over an INDEPENDENT holdout
window, then re-run account-survival + the selector on the fresh results.

Why: the survival read (47% APEX-pass) and the selector verdict (equal_weight wins)
were both measured on the SAME window the candidate universe was chosen on. A true
out-of-sample check re-runs the SAME 124 templates (no re-optimizing = no
re-overfitting) on a window they never saw, and asks whether the survival rate and
the selector edge hold.

Mechanism: copy each ``named_backtest_templates/recipe/*.xml`` with only ``<From>``/
``<To>`` swapped to the holdout window, dispatch ONE RunBatch through the proven
optimizer bridge (same path as run_parity_backtest / market_data_export), collect
each template's fresh Trades.csv, and report. Use ``--limit N`` to probe output
naming on a few templates before committing all 124.

Usage:
  python scripts/holdout_rebacktest.py [SESSION] --from-date 2026-03-12 \
      --to-date 2026-04-30 [--instrument "NQ 06-26"] [--limit 3] [--no-dispatch]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.analysis.risk.account_state import load_firm_profile
from ta_foundation.analysis.risk.survival import per_template_survival
from ta_foundation.analysis.risk.trade_loader import parse_trades_csv
from ta_foundation.web.market_data_export import (
    _poll_to_terminal,
    _write_command_file,
    build_command_payload,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    ensure_bridge_available,
)

_SROOT = Path(".ta_artifacts/web_optimizer/sessions")
_FROM_RE = re.compile(r"<From>[^<]*</From>")
_TO_RE = re.compile(r"<To>[^<]*</To>")


def _restamp(text: str, from_date: str, to_date: str) -> str:
    text = _FROM_RE.sub(f"<From>{from_date}T00:00:00</From>", text, count=1)
    text = _TO_RE.sub(f"<To>{to_date}T00:00:00</To>", text, count=1)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="opt_a09359e6b60b")
    ap.add_argument("--from-date", required=True)
    ap.add_argument("--to-date", required=True)
    ap.add_argument("--instrument", default="NQ 06-26")
    ap.add_argument("--limit", type=int, default=0, help="re-run only the first N templates (probe)")
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--firm", default="APEX")
    ap.add_argument("--size", default="50000")
    ap.add_argument("--timeout", type=int, default=3 * 60 * 60)
    args = ap.parse_args()

    sdir = Path(args.session) if Path(args.session).exists() else _SROOT / args.session
    tdir = sdir / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    templates = sorted(tdir.glob("*.xml"))
    if not templates:
        print(f"no templates under {tdir}", file=sys.stderr)
        return 2
    if args.limit:
        templates = templates[: args.limit]
    # run_id = leading F_<NNN> of the filename (matches the original output-dir naming).
    rid_of = lambda p: re.match(r"(F_\d+)", p.stem).group(1) if re.match(r"(F_\d+)", p.stem) else p.stem

    moment = datetime.now(timezone.utc)
    base = Path(tempfile.mkdtemp(prefix="holdout_bt_"))
    src = base / "generated_templates"
    dest = base / "nt_output"
    src.mkdir(parents=True)
    dest.mkdir(parents=True)
    for p in templates:
        (src / p.name).write_text(_restamp(p.read_text(encoding="utf-8"),
                                            args.from_date, args.to_date), encoding="utf-8")
    print(f"session    : {sdir.name}")
    print(f"templates  : {len(templates)}  (window {args.from_date}..{args.to_date})")
    print(f"work dir   : {base}")

    if args.no_dispatch:
        print(f"[dry-run] re-windowed templates in {src}")
        return 0

    run_id = "holdout_" + moment.strftime("%Y%m%d_%H%M%S")
    cmd_path, status_path = DEFAULT_COMMAND_FILE, DEFAULT_STATUS_FILE
    ensure_bridge_available(cmd_path, status_path, now=moment)
    try:
        if Path(status_path).exists():
            Path(status_path).unlink()
    except OSError:
        pass
    _write_command_file(cmd_path, build_command_payload(
        run_id=run_id, source_folder=src, dest_folder=dest,
        instrument=args.instrument, timeout_seconds=args.timeout,
    ))
    print(f"dispatched RunBatch {run_id}; polling...")
    state = _poll_to_terminal(status_path, run_id=run_id, timeout_seconds=args.timeout,
                              poll_interval_seconds=20, logger=print)
    print(f"run state  : {state}")

    # Collect fresh Trades.csv (defensive: glob, map by parent dir name).
    found = {p.parent.name: p for p in dest.rglob("Trades.csv")}
    print(f"output dirs: {sorted(found)[:6]}{' ...' if len(found) > 6 else ''}  ({len(found)} total)")

    template_trades = {}
    path_of = {}
    slice_of = {}
    # slice keys from the original evaluated_candidates.csv
    cand_csv = sdir / "deployment_package" / "final_backtest_handoff" / "final_backtest_review" / "evaluated_candidates.csv"
    if cand_csv.exists():
        for r in csv.DictReader(cand_csv.open(encoding="utf-8-sig")):
            slice_of[(r.get("run_id") or "").strip()] = (r.get("session_bucket") or "all").strip() or "all"
    for p in templates:
        rid = rid_of(p)
        # A fresh RunBatch names each output dir by the FULL template stem
        # (F_001_StartTimeH_08); fall back to the stripped run_id just in case.
        tp = found.get(p.stem) or found.get(rid)
        if tp is None:
            continue
        trades = parse_trades_csv(tp, template_id=rid, slice_key=slice_of.get(rid, "all"))
        if trades:
            template_trades[rid] = trades
            path_of[rid] = tp

    print(f"\nmapped     : {len(template_trades)}/{len(templates)} templates produced trades")
    if not template_trades:
        print("NO trades mapped — check output-dir naming above vs run_id (F_NNN).")
        return 1

    # WINDOW GUARD — the bare RunBatch bridge applies a template's strategy PARAMS but
    # NOT its <From>/<To> date range: NT uses its sticky/default Strategy Analyzer window
    # (confirmed 2026-06-13: a 04-06..04-10 request produced a 05-04 trade). So a result
    # here is only a valid OOS test if the realized trades actually fall in the window.
    from datetime import date as _date
    fy, fm, fd = map(int, args.from_date.split("-"))
    ty, tm, td = map(int, args.to_date.split("-"))
    lo, hi = _date(fy, fm, fd), _date(ty, tm, td)
    all_days = [t.entry_dt.date() for ts in template_trades.values() for t in ts]
    in_win = sum(1 for d in all_days if lo <= d <= hi)
    frac = in_win / len(all_days) if all_days else 0.0
    print(f"window guard: {frac*100:.0f}% of trades fall in {args.from_date}..{args.to_date} "
          f"(realized range {min(all_days)}..{max(all_days)})")
    if frac < 0.80:
        print("!! WINDOW NOT HONORED — NT ignored the template date range (sticky SA window). "
              "This is NOT a valid OOS test; survival numbers below are over NT's default "
              "range, not the requested window. Fix: inject the analyzer date range the way "
              "the deployment-matrix orchestrator does before trusting a holdout result.")

    profile = load_firm_profile(args.firm)
    sweep = per_template_survival(
        template_trades, profile=profile, account_type="evaluation",
        account_size=args.size, starting_balance=float(args.size),
        contracts=1, dollar_scale=1.0,
    )
    print(f"\n=== HOLDOUT survival ({args.firm} {args.size}, NQ 1-contract) ===")
    print(f"survived {sweep.n_survived}/{sweep.n_templates}   "
          f"passed {sweep.n_passed}/{sweep.n_templates}   "
          f"(in-window baseline was 84 survive / 58 pass of 124)")

    # Persist fresh trades for downstream selector replay.
    out = sdir / "deployment_package" / f"holdout_{args.from_date}_{args.to_date}_trades"
    out.mkdir(parents=True, exist_ok=True)
    for rid, tp in path_of.items():
        (out / f"{rid}.csv").write_text(tp.read_text(encoding="utf-8-sig"), encoding="utf-8")
    print(f"fresh trades saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
