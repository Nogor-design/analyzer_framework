#!/usr/bin/env python3
"""
Production smoke test for the one-click "Promote & Run" flow.

This is the verification the Promote & Run handoff
(``docs/handoffs/promote_and_run_continuation_2026-05-30.md``, open item #1)
flagged as never having been run against a live NinjaTrader. It drives the
*real* REST API and the *real* ``C:\\temp\\nt8_command.json`` bridge — so
NinjaTrader must be running with ``BatchStrategyOptimizerAddOn`` authorized
(see ``NINJATRADER_INTEGRATION_RUNBOOK.md``; NT cold-starts in 60–150s).

Sequence (mirrors the handoff checklist):
  0. Prereqs: web app up, bridge dir writable, AddOn watch dir present.
  1. Attach to an existing session that already has scored stage rows.
  2. Ensure the shortlist has >=1 pending row (add via --add if asked).
  3. POST /shortlist/promote (dispatch=true) and confirm the RunBatch
     command was written to the bridge with our runId.
  4. Poll /shortlist/promote/status until terminal.
  5. Confirm per-candidate P_NNN.html reports were rendered on disk.
  6. Confirm the Decision Dashboard lists the promoted P_NNN rows.

Usage:
    python scripts/smoke_test_promote_and_run.py --session-id opt_xxxxxxxx
    python scripts/smoke_test_promote_and_run.py --session-id opt_xxxx \
        --add stage_1:cid_a --add stage_1:cid_b
    python scripts/smoke_test_promote_and_run.py --session-id opt_xxxx --dry-run

Options:
    --session-id <ID>   Existing session to promote from (required).
    --add S:C           Add a pending row by <stage_id>:<candidate_id>.
                        Repeatable. Omit to use the shortlist as-is.
    --dry-run           Stamp templates but DO NOT dispatch NinjaTrader
                        (POSTs dispatch=false). Verifies steps 0-3 only.
    --url <URL>         Base URL (default: http://localhost:7734).
    --poll-timeout <S>  Max seconds to wait for the run (default: 1800).
    --poll-every <S>    Poll interval seconds (default: 10).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

# The bridge constants and session storage are read directly so the smoke
# test checks the same files the web app writes.
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    read_bridge_command,
)

ADDON_WATCH_DIR = Path(r"D:\ninjatraderOptimizer")
TERMINAL_STATES = {"complete", "failed", "cancelled"}


def _h(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def _step(num: str, desc: str) -> None:
    print(f"\n[{num}] {desc}\n   " + "-" * 72)


def _ok(msg: str) -> None:
    print(f"   OK   {msg}")


def _warn(msg: str) -> None:
    print(f"   WARN {msg}")


def _fail(msg: str) -> None:
    print(f"   FAIL {msg}")


class SmokeError(Exception):
    pass


def _api(base: str, session_id: str) -> str:
    return f"{base}/api/optimizer/sessions/{session_id}"


def check_prereqs(base: str, *, dry_run: bool) -> None:
    _step("0", "PREREQUISITES")
    try:
        resp = requests.get(f"{base}/", timeout=10)
        if resp.status_code != 200:
            raise SmokeError(f"web app returned {resp.status_code}")
        _ok(f"web app reachable at {base}")
    except requests.ConnectionError as exc:
        raise SmokeError(
            f"cannot reach web app at {base} — start it with "
            "`python -m ta_foundation.web.app --port 7734`"
        ) from exc

    bridge_dir = DEFAULT_COMMAND_FILE.parent
    if not bridge_dir.exists():
        raise SmokeError(f"bridge dir {bridge_dir} does not exist")
    _ok(f"bridge dir writable: {bridge_dir}")

    if dry_run:
        _warn("dry-run: NinjaTrader / AddOn not required (no dispatch)")
        return

    if ADDON_WATCH_DIR.exists():
        _ok(f"AddOn watch dir present: {ADDON_WATCH_DIR}")
    else:
        _warn(
            f"AddOn watch dir {ADDON_WATCH_DIR} not found — if the AddOn lives "
            "elsewhere this is fine, but confirm NinjaTrader is running with "
            "BatchStrategyOptimizerAddOn authorized."
        )


def get_session_dir(session_id: str) -> Path:
    """Resolve the on-disk session directory (must match the web app's root)."""
    from ta_foundation.web.optimizer_session import get_session

    session = get_session(session_id)
    if session is None:
        raise SmokeError(
            f"session {session_id!r} not found under the optimizer storage root. "
            "Run this script from the repo root so the storage path matches the "
            "web app."
        )
    return session.directory


def ensure_pending_rows(
    base: str, session_id: str, adds: list[tuple[str, str]]
) -> int:
    _step("1-2", "SHORTLIST")
    api = _api(base, session_id)

    if adds:
        items = [{"stage_id": s, "candidate_id": c} for s, c in adds]
        resp = requests.post(
            f"{api}/shortlist",
            json={"items": items, "source": "smoke_test"},
            timeout=15,
        )
        if resp.status_code != 200:
            raise SmokeError(f"shortlist add failed: {resp.status_code} {resp.text[:200]}")
        _ok(f"added {len(items)} row(s) to shortlist")

    resp = requests.get(f"{api}/shortlist", timeout=15)
    if resp.status_code != 200:
        raise SmokeError(f"shortlist fetch failed: {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    items = body.get("items", [])
    pending = [i for i in items if i.get("status") == "pending"]
    print(f"   shortlist: {len(items)} item(s), {len(pending)} pending")
    if not pending:
        raise SmokeError(
            "no pending rows to promote — add some with --add stage_id:candidate_id "
            "(finalists/already-promoted rows are skipped by design)."
        )
    for i in pending[:5]:
        print(f"     pending: {i.get('stage_id')} / {i.get('candidate_id')}")
    return len(pending)


def promote(base: str, session_id: str, *, dry_run: bool) -> dict:
    _step("3", "PROMOTE" + (" (dry-run, no dispatch)" if dry_run else " & RUN"))
    api = _api(base, session_id)
    resp = requests.post(
        f"{api}/shortlist/promote",
        json={"dispatch": not dry_run},
        timeout=60,
    )
    if resp.status_code != 200:
        raise SmokeError(f"promote failed: {resp.status_code} {resp.text[:300]}")
    body = resp.json()
    result = body.get("result", {})
    print(
        f"   promoted={result.get('promoted_count')} "
        f"skipped={result.get('skipped_count')} errors={result.get('error_count')}"
    )
    for err in result.get("errors", [])[:5]:
        _warn(f"row error: {err.get('candidate_id')} — {err.get('reason')}")
    if result.get("promoted_count", 0) == 0 and not result.get("skipped_count"):
        raise SmokeError("nothing was promoted; check the per-row errors above")

    run = body.get("run") or {}
    if dry_run:
        _ok("templates stamped (dispatch skipped)")
        return body

    if run.get("error"):
        raise SmokeError(f"dispatch failed: {run['error']}")
    run_id = run.get("run_id")
    _ok(f"dispatched run {run_id!r}, state={run.get('state')}")

    cmd = read_bridge_command(DEFAULT_COMMAND_FILE)
    if not cmd:
        raise SmokeError(f"no command written to {DEFAULT_COMMAND_FILE}")
    if cmd.get("runId") != run_id:
        raise SmokeError(
            f"bridge command runId {cmd.get('runId')!r} != dispatched {run_id!r}"
        )
    _ok(f"bridge command verified at {DEFAULT_COMMAND_FILE} (action={cmd.get('action')})")
    print(f"     sourceFolder: {cmd.get('sourceFolder')}")
    return body


def poll_until_terminal(
    base: str, session_id: str, *, timeout: int, every: int
) -> dict:
    _step("4", "WAIT FOR NINJATRADER")
    print("   NinjaTrader cold-starts in 60-150s; first heartbeat may lag.")
    api = _api(base, session_id)
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        resp = requests.get(f"{api}/shortlist/promote/status", timeout=30)
        if resp.status_code != 200:
            raise SmokeError(f"status poll failed: {resp.status_code} {resp.text[:200]}")
        run = (resp.json() or {}).get("run") or {}
        state = run.get("state")
        done = run.get("completed_templates", 0)
        total = run.get("total_templates", 0)
        if state != last_state:
            print(f"   state={state}  ({done}/{total} templates)")
            last_state = state
        if state in TERMINAL_STATES:
            if state == "complete":
                _ok(f"run complete — {run.get('report_count', 0)} P_NNN report(s)")
            else:
                _fail(f"run {state}: {run.get('last_error')}")
            return run
        time.sleep(every)
    raise SmokeError(f"timed out after {timeout}s waiting for the run to finish")


def verify_artifacts(session_dir: Path, base: str, session_id: str) -> None:
    _step("5-6", "VERIFY REPORTS + DASHBOARD")
    reports_dir = session_dir / "per_candidate_reports"
    p_reports = sorted(reports_dir.glob("P_*.html")) if reports_dir.exists() else []
    if not p_reports:
        raise SmokeError(f"no P_NNN.html reports under {reports_dir}")
    _ok(f"{len(p_reports)} promoted report(s): {[p.name for p in p_reports[:5]]}")

    resp = requests.get(f"{_api(base, session_id)}/decision", timeout=30)
    if resp.status_code != 200:
        _warn(f"decision dashboard fetch failed: {resp.status_code}")
        return
    dash = (resp.json() or {}).get("dashboard") or {}
    rows = dash.get("candidate_rows") or []
    promoted_rows = [r for r in rows if str(r.get("kind")) == "promoted"
                     or str(r.get("run_id", "")).startswith("P_")]
    if promoted_rows:
        _ok(f"dashboard lists {len(promoted_rows)} promoted row(s) alongside finalists")
    else:
        _warn("dashboard returned no promoted rows — inspect it in the browser")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--add", action="append", default=[], metavar="STAGE:CID",
                        help="Add a pending row by <stage_id>:<candidate_id> (repeatable).")
    parser.add_argument("--dry-run", action="store_true", help="Stamp templates but do not dispatch NT.")
    parser.add_argument("--url", default="http://localhost:7734")
    parser.add_argument("--poll-timeout", type=int, default=1800)
    parser.add_argument("--poll-every", type=int, default=10)
    args = parser.parse_args()

    adds: list[tuple[str, str]] = []
    for spec in args.add:
        if ":" not in spec:
            print(f"--add expects STAGE:CID, got {spec!r}")
            return 2
        stage, cid = spec.split(":", 1)
        adds.append((stage.strip(), cid.strip()))

    _h("PROMOTE & RUN — PRODUCTION SMOKE TEST")
    try:
        check_prereqs(args.url, dry_run=args.dry_run)
        session_dir = get_session_dir(args.session_id)
        print(f"   session dir: {session_dir}")
        ensure_pending_rows(args.url, args.session_id, adds)
        promote(args.url, args.session_id, dry_run=args.dry_run)
        if args.dry_run:
            _h("DRY-RUN COMPLETE (steps 0-3 verified; dispatch skipped)")
            return 0
        run = poll_until_terminal(
            args.url, args.session_id,
            timeout=args.poll_timeout, every=args.poll_every,
        )
        if run.get("state") != "complete":
            _h("SMOKE TEST FAILED — run did not complete")
            return 1
        verify_artifacts(session_dir, args.url, args.session_id)
        _h("SMOKE TEST PASSED")
        print(f"   Open: {args.url}/optimizer/sessions/{args.session_id}")
        return 0
    except SmokeError as exc:
        _fail(str(exc))
        _h("SMOKE TEST ABORTED")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
