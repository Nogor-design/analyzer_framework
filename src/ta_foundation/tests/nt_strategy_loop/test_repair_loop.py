from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.compile_observer import CompileError, CompileObservation
from ta_foundation.nt_strategy_loop.policy import RepairPolicy
from ta_foundation.nt_strategy_loop.repair import RepairContext
from ta_foundation.nt_strategy_loop.repair_loop import run_repair_loop


def _clean_observation(source: Path) -> CompileObservation:
    return CompileObservation(
        run_id="r",
        state="succeeded",
        strategy_name="LoopUnit",
        source_file=str(source),
        compiled=True,
        error_count=0,
        errors_csv=None,
        errors_text=None,
        last_error=None,
        heartbeat_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        output_root=None,
        errors=(),
        status_path="C:/tmp/nt8_status.json",
    )


def _broken_observation(source: Path) -> CompileObservation:
    error = CompileError(
        file="LoopUnit.cs",
        line=10,
        column=5,
        code="CS0103",
        message="The name 'SMA' does not exist in the current context",
        raw="",
        source="LoopUnit.cs",
    )
    return CompileObservation(
        run_id="r",
        state="failed",
        strategy_name="LoopUnit",
        source_file=str(source),
        compiled=False,
        error_count=1,
        errors_csv=None,
        errors_text=None,
        last_error=error.formatted(),
        heartbeat_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        output_root=None,
        errors=(error,),
        status_path="C:/tmp/nt8_status.json",
    )


def test_run_repair_loop_completes_on_clean_first_observation(tmp_path: Path) -> None:
    spec = StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent="")

    def provider(source: Path, attempt: int) -> CompileObservation:
        return _clean_observation(source)

    result = run_repair_loop(
        spec,
        lab_root=tmp_path,
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=3),
        observation_provider=provider,
    )

    assert result.decision == "compile_clean"
    assert result.stop_reason["code"] == "compile_clean"
    assert len(result.attempts) == 1
    session_dir = Path(result.session_dir)
    assert (session_dir / "compile_clean" / "LoopUnit.cs").is_file()
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "compile_clean"


def test_run_repair_loop_repairs_then_compiles_clean(tmp_path: Path) -> None:
    spec = StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent="")
    observations: list[CompileObservation] = []

    def provider(source: Path, attempt: int) -> CompileObservation:
        # First attempt fails with a CS0103 the heuristic can fix; second compiles clean.
        if attempt == 1:
            obs = _broken_observation(source)
        else:
            obs = _clean_observation(source)
        observations.append(obs)
        return obs

    def callback(ctx: RepairContext) -> str:
        # Backstop: ensure the loop still progresses even if the heuristic is
        # too narrow. Real heuristic should fire first.
        return ctx.current_source + "\n// callback-edit\n"

    result = run_repair_loop(
        spec,
        lab_root=tmp_path,
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=4),
        observation_provider=provider,
        repair_callback=callback,
    )

    assert result.decision == "compile_clean"
    assert len(result.attempts) == 2
    session_dir = Path(result.session_dir)
    assert (session_dir / "attempts" / "attempt_001" / "compile_status.json").is_file()
    assert (session_dir / "attempts" / "attempt_001" / "repair_prompt.md").is_file()
    assert (session_dir / "attempts" / "attempt_001" / "repair_summary.md").is_file()
    assert (session_dir / "attempts" / "attempt_002" / "LoopUnit.cs").is_file()
    assert (session_dir / "compile_clean" / "LoopUnit.cs").is_file()


def test_run_repair_loop_halts_on_repeated_signature(tmp_path: Path) -> None:
    spec = StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent="")

    def provider(source: Path, attempt: int) -> CompileObservation:
        return _broken_observation(source)

    def callback(ctx: RepairContext) -> str:
        # Force a content change so the heuristic's idempotency doesn't end the loop
        # on its own — we want to verify the *signature* check halts us.
        return ctx.current_source + f"\n// touch {ctx.attempt}\n"

    result = run_repair_loop(
        spec,
        lab_root=tmp_path,
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=5),
        observation_provider=provider,
        repair_callback=callback,
    )

    assert result.decision == "halted"
    assert result.stop_reason["code"] in {"repeated_signature", "max_attempts"}


def test_run_repair_loop_halts_when_no_repair_possible(tmp_path: Path) -> None:
    spec = StrategySpec(strategy_name="LoopUnit", family="sma_cross_smoke", intent="")

    def provider(source: Path, attempt: int) -> CompileObservation:
        novel = CompileError(
            file="LoopUnit.cs",
            line=1,
            column=1,
            code="CS9999",
            message="novel error nobody can fix",
            raw="",
            source="LoopUnit.cs",
        )
        return CompileObservation(
            run_id="r",
            state="failed",
            strategy_name="LoopUnit",
            source_file=str(source),
            compiled=False,
            error_count=1,
            errors_csv=None,
            errors_text=None,
            last_error=novel.formatted(),
            heartbeat_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            output_root=None,
            errors=(novel,),
            status_path="C:/tmp/nt8_status.json",
        )

    result = run_repair_loop(
        spec,
        lab_root=tmp_path,
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=3),
        observation_provider=provider,
    )

    assert result.decision == "halted"
    assert result.stop_reason["code"] == "repair_declined"
