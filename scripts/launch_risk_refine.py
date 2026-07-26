"""Launch the in-session risk-knob refinement (ProfitStop / LossStop /
MaxTrades) on the top-N finished candidates of a session, then drive it through
NinjaTrader to completion. Additive: appends refine_risk_* / refine_final_*
stages; originals are left untouched.

Usage:
    python scripts/launch_risk_refine.py <session_id> [top_n]
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ta_foundation.web.optimizer_session import get_session
from ta_foundation.web.optimizer_refinement import (
    RefinementRanges,
    list_refinable_candidates,
    prepare_refinement,
)
from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
from ta_foundation.web.optimizer_recipe_state import (
    RecipeRunState,
    load_recipe_state,
    save_recipe_state,
)
from ta_foundation.web.optimizer_recipe_runner import load_recipe_run

STATUS_FILE = Path(r"C:\temp\nt8_status.json")
WAIT_STATES = {"running_stage", "running_final_backtest"}
TERMINAL = {"complete", "failed", "stopped"}
TERMINAL_NT = {"finished", "completed", "failed", "error", "timed_out", "timedout", "cancelled"}
NT_WAIT_TIMEOUT = 3 * 60 * 60
TOTAL_TIMEOUT = 5 * 60 * 60


def log(m):
    print(f"{datetime.now().strftime('%H:%M:%S')} {m}", flush=True)


def wait_for_nt(run_id):
    deadline = time.time() + NT_WAIT_TIMEOUT
    last = -1
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
            log(f"  NT {run_id}: {st} {done}/{tot} cur={s.get('currentTemplate')}"); last = done
        if st in TERMINAL_NT or (tot and done >= tot and st not in {"running", "starting"}):
            return st
        time.sleep(30)
    return "timeout"


def main():
    session_id = sys.argv[1]
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    session = get_session(session_id)
    if session is None:
        log(f"No such session: {session_id}"); return 1

    cands = list_refinable_candidates(session)  # sorted by net desc
    picked = [c["run_id"] for c in cands[:top_n]]
    log(f"Refining top {len(picked)} of {len(cands)} candidates: {picked}")

    prep = prepare_refinement(session, picked, ranges=RefinementRanges.from_payload(None), keep_per_candidate=2)
    log(f"prepared: refine={prep.refine_stage_id} final={prep.final_stage_id} "
        f"candidates={prep.candidate_count} combos/candidate={prep.combos_per_candidate} "
        f"total_combos={prep.total_combinations} pinned={prep.pinned_params}")

    recipe = load_recipe(session)
    state = load_recipe_state(session) or RecipeRunState(recipe_id=recipe.recipe_id, state="generating_child_stage")
    state.state = "generating_child_stage"
    state.current_stage_id = prep.refine_stage_id
    state.last_error = None
    state.pause_requested = False
    state.stop_requested = False
    save_recipe_state(session, state)

    orch = RecipeRunOrchestrator(session)
    start_time = time.time()
    while time.time() - start_time < TOTAL_TIMEOUT:
        st = load_recipe_state(session).state
        if st in TERMINAL:
            log(f"TERMINAL: {st}")
            e = load_recipe_state(session).last_error
            if e:
                log(f"  last_error: {e}")
            return 0 if st == "complete" else 2
        if st in WAIT_STATES:
            run = load_recipe_run(session)
            rid = run.run_id if run else ""
            log(f"state={st}; waiting for NT {rid}")
            nts = wait_for_nt(rid)
            log(f"  NT terminal: {nts}")
            if nts == "timeout":
                log("NT timeout; aborting."); return 3
            orch.advance_once()
            log(f"  advanced -> {load_recipe_state(session).state}")
        else:
            log(f"state={st}; advancing")
            orch.advance_once()
            new = load_recipe_state(session).state
            log(f"  advanced -> {new}")
            if new == st and new not in WAIT_STATES:
                time.sleep(10)
    log("TOTAL timeout; aborting."); return 4


if __name__ == "__main__":
    raise SystemExit(main())
