"""Drive a recipe session through all stages headless, polling NinjaTrader
between stages. Intended for an already-configured session whose recipe +
plan are saved. Starts stage 1 and advances across stage boundaries until the
recipe reaches a terminal state (complete / failed / stopped).

Usage:
    python scripts/drive_recipe_to_complete.py <session_id>
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.web.optimizer_session import get_session
from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
from ta_foundation.web.optimizer_recipe_state import load_recipe_state
from ta_foundation.web.optimizer_recipe_runner import load_recipe_run

STATUS_FILE = Path(r"C:\temp\nt8_status.json")
WAIT_STATES = {"running_stage", "running_final_backtest"}
TERMINAL = {"complete", "failed", "stopped"}
TERMINAL_NT = {"finished", "completed", "failed", "error", "timed_out", "timedout", "cancelled"}
NT_WAIT_TIMEOUT = 3 * 60 * 60   # 3h per stage (matches recipe timeout)
TOTAL_TIMEOUT = 5 * 60 * 60     # 5h hard cap for the whole run


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def wait_for_nt(run_id: str) -> str:
    """Poll the shared status file until the given run reaches a terminal NT
    state. Returns the terminal state string."""
    deadline = time.time() + NT_WAIT_TIMEOUT
    last = -1
    while time.time() < deadline:
        try:
            s = json.loads(STATUS_FILE.read_text())
        except Exception:
            time.sleep(20)
            continue
        if str(s.get("runId") or "") != run_id:
            time.sleep(15)
            continue
        st = str(s.get("state") or "").lower()
        done = int(s.get("completed") or 0)
        tot = int(s.get("total") or 0)
        if done != last:
            log(f"  NT {run_id}: {st} {done}/{tot} cur={s.get('currentTemplate')}")
            last = done
        if st in TERMINAL_NT or (tot and done >= tot and st not in {"running", "starting"}):
            return st
        time.sleep(30)
    return "timeout"


def main() -> int:
    session_id = sys.argv[1]
    session = get_session(session_id)
    if session is None:
        log(f"No such session: {session_id}")
        return 1
    orch = RecipeRunOrchestrator(session)

    log(f"START recipe for {session_id}")
    orch.start()  # generates + dispatches stage_1
    log(f"  state -> {load_recipe_state(session).state}")

    start_time = time.time()
    while time.time() - start_time < TOTAL_TIMEOUT:
        state = load_recipe_state(session).state
        if state in TERMINAL:
            log(f"TERMINAL state: {state}")
            st = load_recipe_state(session)
            if st.last_error:
                log(f"  last_error: {st.last_error}")
            return 0 if state == "complete" else 2

        if state in WAIT_STATES:
            run = load_recipe_run(session)
            run_id = run.run_id if run else ""
            log(f"state={state}; waiting for NT run {run_id}")
            nt_state = wait_for_nt(run_id)
            log(f"  NT terminal: {nt_state}")
            if nt_state == "timeout":
                log("NT wait timed out; aborting.")
                return 3
            orch.advance_once()
            log(f"  advanced -> {load_recipe_state(session).state}")
        else:
            # transient / parked state: advance immediately
            log(f"state={state}; advancing")
            orch.advance_once()
            new_state = load_recipe_state(session).state
            log(f"  advanced -> {new_state}")
            if new_state == state and new_state not in WAIT_STATES:
                # no progress guard
                time.sleep(10)

    log("TOTAL timeout reached; aborting.")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
