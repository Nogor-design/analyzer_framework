"""One-off driver: re-arm and re-run a crashed recipe fixed_backtest stage.

Drives RecipeRunOrchestrator directly (no web server needed) to:
  1. stop()   -> cancel the stale "running" run record + remove stale command file
  2. resume() -> regenerate the stage templates and submit a fresh RunBatch to NT

Usage:
    python scripts/resume_final_stage.py <session_dir>
"""
import sys
from pathlib import Path

from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
from ta_foundation.web.optimizer_recipe_state import load_recipe_state


def main() -> int:
    session_dir = Path(sys.argv[1]).resolve()
    if not session_dir.exists():
        print(f"No such session dir: {session_dir}")
        return 1

    session = OptimizerSession(session_dir)
    orch = RecipeRunOrchestrator(session)

    before = load_recipe_state(session)
    print(f"Before:  state={before.state!r} stage={before.current_stage_id!r}")

    print("stop() ...")
    orch.stop()

    print("resume() -> regenerate + dispatch to NinjaTrader ...")
    status = orch.resume()

    after = load_recipe_state(session)
    print(f"After:   state={after.state!r} stage={after.current_stage_id!r}")
    run = status.get("run") or {}
    print(f"Run:     run_id={run.get('run_id')!r} state={run.get('state')!r} "
          f"total_templates={run.get('total_templates')}")
    print(f"Command: {run.get('command_file')}")
    print(f"Dest:    {run.get('dest_folder')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
