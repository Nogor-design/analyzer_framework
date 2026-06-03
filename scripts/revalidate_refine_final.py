"""Dispatch an already-generated refine_final stage to NinjaTrader and drive it
to completion (reusing existing refine_risk results). One-off used to revalidate
the corrected refinement templates without re-sweeping refine_risk.

Usage:
    python scripts/revalidate_refine_final.py <session_id> <final_stage_id>
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ta_foundation.web.optimizer_session import get_session
from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
from ta_foundation.web.optimizer_recipe_runner import start_recipe_stage_run, load_recipe_run
from ta_foundation.web.optimizer_recipe_state import (
    RecipeRunState, load_recipe_state, save_recipe_state,
)

STATUS_FILE = Path(r"C:\temp\nt8_status.json")
TERMINAL_NT = {"finished", "completed", "failed", "error", "timed_out", "timedout", "cancelled"}


def log(m):
    print(f"{datetime.now().strftime('%H:%M:%S')} {m}", flush=True)


def wait_for_nt(run_id):
    last = -1
    deadline = time.time() + 3 * 60 * 60
    while time.time() < deadline:
        try:
            s = json.loads(STATUS_FILE.read_text())
        except Exception:
            time.sleep(20); continue
        if str(s.get("runId") or "") != run_id:
            time.sleep(15); continue
        st = str(s.get("state") or "").lower()
        done, tot = int(s.get("completed") or 0), int(s.get("total") or 0)
        if done != last:
            log(f"  NT {run_id}: {st} {done}/{tot}"); last = done
        if st in TERMINAL_NT or (tot and done >= tot and st not in {"running", "starting"}):
            return st
        time.sleep(30)
    return "timeout"


def main():
    session_id, stage_id = sys.argv[1], sys.argv[2]
    session = get_session(session_id)
    recipe = load_recipe(session)
    orch = RecipeRunOrchestrator(session)

    log(f"Dispatching {stage_id} ({session_id}) to NT")
    run = start_recipe_stage_run(session, stage_id=stage_id)
    state = load_recipe_state(session) or RecipeRunState(recipe_id=recipe.recipe_id, state="running_final_backtest")
    state.state = "running_final_backtest"
    state.current_stage_id = stage_id
    state.pause_requested = False
    state.stop_requested = False
    state.last_error = None
    save_recipe_state(session, state)
    log(f"  run_id={run.run_id} total={run.total_templates}")

    nts = wait_for_nt(run.run_id)
    log(f"NT terminal: {nts}")
    if nts not in {"finished", "completed"}:
        log("NT did not finish cleanly; not finalizing."); return 2

    # advance_once finalizes: detect completion -> review -> artifacts
    orch.advance_once()
    log(f"final state -> {load_recipe_state(session).state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
