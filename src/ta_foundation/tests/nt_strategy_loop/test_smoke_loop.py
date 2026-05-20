from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.nt_strategy_loop.smoke_loop import SmokeGuardrails, run_smoke_loop


def test_smoke_loop_writes_archived_session(tmp_path: Path) -> None:
    result = run_smoke_loop(
        lab_root=tmp_path,
        strategy_name="AutonomousLoopSmokeTest",
        guardrails=SmokeGuardrails(max_drawdown=2500.0, min_trades=10, min_profit_factor=1.5),
    )

    session_dir = Path(result.session_dir)
    assert result.compile_state == "succeeded"
    assert result.optimizer_rows == 3
    assert result.passing_rows == 0
    assert result.decision == "archive"
    assert (session_dir / "attempts" / "attempt_001" / "AutonomousLoopSmokeTest.cs").is_file()
    assert (session_dir / "compile_clean" / "AutonomousLoopSmokeTest.cs").is_file()
    assert (session_dir / "optimizer" / "nt_output" / "AutonomousLoopSmokeTest_Optimization.csv").is_file()
    assert Path(result.summary_path).read_text(encoding="utf-8").startswith("# Strategy Loop Summary")


def test_smoke_loop_manifest_points_to_decision_artifacts(tmp_path: Path) -> None:
    result = run_smoke_loop(lab_root=tmp_path, strategy_name="AutonomousLoopSmokeManifest")

    manifest = json.loads((Path(result.session_dir) / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["decision"] == "archive"
    assert manifest["artifacts"]["summary"] == "decisions/STRATEGY_LOOP_SUMMARY.md"
    assert manifest["artifacts"]["next_action"] == "decisions/NEXT_ACTION.md"

