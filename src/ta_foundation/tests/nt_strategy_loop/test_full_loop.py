from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.nt_strategy_loop.analyzer import Guardrails
from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.compile_observer import CompileError, CompileObservation
from ta_foundation.nt_strategy_loop.full_loop import run_full_loop
from ta_foundation.nt_strategy_loop.policy import RepairPolicy
from ta_foundation.nt_strategy_loop.repair import RepairContext
from ta_foundation.web.optimizer_runner import RunRecord, RunStatus
from ta_foundation.web.optimizer_session import OptimizerSession


def _spec() -> StrategySpec:
    return StrategySpec(
        strategy_name="FullLoopUnit",
        family="sma_cross_smoke",
        intent="full loop test",
        parameters={"FastPeriod": 9, "SlowPeriod": 21},
    )


def _clean(source: Path) -> CompileObservation:
    return CompileObservation(
        run_id="r",
        state="succeeded",
        strategy_name="FullLoopUnit",
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


def _broken(source: Path) -> CompileObservation:
    err = CompileError(
        file="FullLoopUnit.cs",
        line=10,
        column=5,
        code="CS0103",
        message="The name 'SMA' does not exist in the current context",
        raw="",
        source="FullLoopUnit.cs",
    )
    return CompileObservation(
        run_id="r",
        state="failed",
        strategy_name="FullLoopUnit",
        source_file=str(source),
        compiled=False,
        error_count=1,
        errors_csv=None,
        errors_text=None,
        last_error=err.formatted(),
        heartbeat_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        output_root=None,
        errors=(err,),
        status_path="C:/tmp/nt8_status.json",
    )


class _FakeRunner:
    def __init__(self, rows: list[list[str]] | None = None) -> None:
        self._rows = rows

    def start(self, session: OptimizerSession) -> RunRecord:
        nt_output = session.directory / "nt_output" / "chunk_001"
        nt_output.mkdir(parents=True, exist_ok=True)
        _write_csv(nt_output / "RunBatch_Optimization.csv", self._rows)
        return RunRecord(
            run_id="r1",
            state="finished",
            started_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            source_folder=str(session.directory / "generated"),
            dest_folder=str(session.directory / "nt_output"),
            command_file="",
            status_file="",
            total_templates=1,
        )

    def get_status(self, session: OptimizerSession) -> RunStatus | None:
        return RunStatus(
            run_id="r1",
            state="finished",
            total=1,
            completed=1,
            current_template=None,
            finished_templates=["chunk_001"],
        )


def _write_csv(path: Path, rows: list[list[str]] | None) -> None:
    rows = rows or [
        ["NQ 06-26", "2.10",
         "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
         "1500", "3000", "-1500", "2.10", "-800", "30", "55%", ""],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.writer(h)
        w.writerow([
            "Instrument", "Performance", "Parameters",
            "Total net profit", "Gross profit", "Gross loss",
            "Profit factor", "Max. drawdown", "Total # of trades",
            "Percent profitable", "",
        ])
        w.writerows(rows)


def test_full_loop_returns_candidate_when_clean_compile_and_passing_rows(tmp_path: Path) -> None:
    result = run_full_loop(
        _spec(),
        lab_root=tmp_path / "lab",
        guardrails=Guardrails(min_profit_factor=1.5, min_trades=10, max_drawdown=2500),
        instrument="NQ 06-26",
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=3),
        observation_provider=lambda source, attempt: _clean(source),
        runner=_FakeRunner(),
        optimizer_storage_root=tmp_path / "optimizer_store",
        optimizer_poll_seconds=0.01,
        optimizer_timeout_seconds=5,
    )

    assert result.decision == "candidate"
    assert result.optimizer is not None
    assert result.optimizer["decision"] == "candidate"

    session_dir = Path(result.session_dir)
    summary = (session_dir / "decisions" / "STRATEGY_LOOP_SUMMARY.md").read_text(encoding="utf-8")
    assert "Decision: `candidate`" in summary
    next_action = (session_dir / "decisions" / "NEXT_ACTION.md").read_text(encoding="utf-8")
    assert "candidate review queue" in next_action

    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "candidate"
    assert "optimizer_session_id" in manifest["artifacts"]


def test_full_loop_archives_when_optimizer_no_passing_rows(tmp_path: Path) -> None:
    result = run_full_loop(
        _spec(),
        lab_root=tmp_path / "lab",
        guardrails=Guardrails(min_profit_factor=1.5, min_trades=10, max_drawdown=2500),
        instrument="NQ 06-26",
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=3),
        observation_provider=lambda source, attempt: _clean(source),
        runner=_FakeRunner(rows=[
            ["NQ 06-26", "1.10",
             "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
             "100", "500", "-400", "1.10", "-1200", "7", "45%", ""],
        ]),
        optimizer_storage_root=tmp_path / "optimizer_store",
        optimizer_poll_seconds=0.01,
        optimizer_timeout_seconds=5,
    )

    assert result.decision == "archive"
    assert result.optimizer is not None


def test_full_loop_repairs_then_optimizes(tmp_path: Path) -> None:
    def provider(source: Path, attempt: int) -> CompileObservation:
        return _broken(source) if attempt == 1 else _clean(source)

    def callback(ctx: RepairContext) -> str:
        return ctx.current_source + "\n// callback\n"

    result = run_full_loop(
        _spec(),
        lab_root=tmp_path / "lab",
        guardrails=Guardrails(min_profit_factor=1.5, min_trades=10, max_drawdown=2500),
        instrument="NQ 06-26",
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=3),
        observation_provider=provider,
        repair_callback=callback,
        runner=_FakeRunner(),
        optimizer_storage_root=tmp_path / "optimizer_store",
        optimizer_poll_seconds=0.01,
        optimizer_timeout_seconds=5,
    )

    assert result.decision == "candidate"
    assert len(result.repair["attempts"]) == 2


def test_full_loop_halts_without_invoking_optimizer_when_repair_fails(tmp_path: Path) -> None:
    result = run_full_loop(
        _spec(),
        lab_root=tmp_path / "lab",
        guardrails=Guardrails(),
        instrument="NQ 06-26",
        compile_mode="fixture",
        policy=RepairPolicy(max_repair_attempts=2),
        observation_provider=lambda source, attempt: _broken(source),
        # No repair callback → after heuristic fails on a known error twice,
        # the loop should halt without trying the optimizer.
        runner=_FakeRunner(),
        optimizer_storage_root=tmp_path / "optimizer_store",
        optimizer_poll_seconds=0.01,
        optimizer_timeout_seconds=5,
    )

    assert result.decision == "halted"
    assert result.optimizer is None
    session_dir = Path(result.session_dir)
    summary = (session_dir / "decisions" / "STRATEGY_LOOP_SUMMARY.md").read_text(encoding="utf-8")
    assert "Not run (repair did not reach compile-clean)" in summary
