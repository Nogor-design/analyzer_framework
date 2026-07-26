from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ta_foundation.nt_strategy_loop.analyzer import Guardrails
from ta_foundation.nt_strategy_loop.authoring import StrategySpec, render_source
from ta_foundation.nt_strategy_loop.optimizer_bridge import (
    LiveOptimizerRunner,
    OptimizerBridgeError,
    OptimizerRunner,
    run_optimizer_for_strategy,
)
from ta_foundation.nt_strategy_loop.session import create_session
from ta_foundation.web.optimizer_runner import RunRecord, RunStatus
from ta_foundation.web.optimizer_session import OptimizerSession


class _FakeRunner:
    """Pretend NinjaTrader: when start() is called, drop a Summary CSV into the
    session's nt_output and report finished on the first status poll."""

    def __init__(self, csv_rows: list[list[str]] | None = None) -> None:
        self._csv_rows = csv_rows
        self._started_for: OptimizerSession | None = None

    def start(self, session: OptimizerSession) -> RunRecord:
        self._started_for = session
        nt_output = session.directory / "nt_output"
        nt_output.mkdir(parents=True, exist_ok=True)
        # Mimic real NT layout: one subfolder per chunk, each containing
        # Summary.csv and an *_Optimization.csv. The bridge prefers the
        # *_Optimization.csv files.
        chunk_dir = nt_output / "chunk_001"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        _write_optimization_csv(chunk_dir / "RunBatch_Optimization.csv", self._csv_rows)
        return RunRecord(
            run_id="r1",
            state="finished",
            started_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            source_folder=str(session.directory / "generated"),
            dest_folder=str(nt_output),
            command_file="C:/temp/nt8_command.json",
            status_file="C:/temp/nt8_status.json",
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


class _NeverFinishesRunner:
    """Status forever stays 'running' to exercise the timeout path."""

    def start(self, session: OptimizerSession) -> RunRecord:
        return RunRecord(
            run_id="r1",
            state="requested",
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
            state="running",
            total=1,
            completed=0,
            current_template="chunk_001.xml",
            finished_templates=[],
        )


def _write_optimization_csv(path: Path, rows: list[list[str]] | None) -> None:
    fieldnames = [
        "Instrument",
        "Performance",
        "Parameters",
        "Total net profit",
        "Gross profit",
        "Gross loss",
        "Profit factor",
        "Max. drawdown",
        "Total # of trades",
        "Percent profitable",
        "",
    ]
    rows = rows or [
        ["NQ 06-26", "2.10",
         "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
         "1500", "3000", "-1500", "2.10", "-800", "30", "55%", ""],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def _seed_compile_clean_source(tmp_path: Path) -> tuple[Path, str]:
    spec = StrategySpec(
        strategy_name="BridgeUnit",
        family="sma_cross_smoke",
        intent="bridge test",
        parameters={"FastPeriod": 9, "SlowPeriod": 21},
    )
    cs_path = tmp_path / "compile_clean" / "BridgeUnit.cs"
    cs_path.parent.mkdir(parents=True, exist_ok=True)
    cs_path.write_text(render_source(spec), encoding="utf-8")
    return cs_path, spec.strategy_name


def test_run_optimizer_for_strategy_with_finished_runner_returns_candidate(tmp_path: Path) -> None:
    loop_session = create_session(
        lab_root=tmp_path / "lab",
        strategy_name="BridgeUnit",
        compile_mode="fixture",
    )
    loop_session.ensure_dirs()
    source, _ = _seed_compile_clean_source(tmp_path)
    runner: OptimizerRunner = _FakeRunner()

    result = run_optimizer_for_strategy(
        loop_session=loop_session,
        compile_clean_source=source,
        guardrails=Guardrails(min_profit_factor=1.5, min_trades=10, max_drawdown=2500),
        instrument="NQ 06-26",
        optimizer_storage_root=tmp_path / "optimizer_store",
        runner=runner,
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    assert result.run_state == "finished"
    assert result.optimizer_csv is not None
    assert Path(result.optimizer_csv).is_file()
    assert result.analyzer_result["row_count"] == 1
    assert result.analyzer_result["passing_rows"] == 1
    assert result.decision == "candidate"

    analysis_path = loop_session.optimizer_dir / "optimizer_analysis.json"
    assert analysis_path.is_file()
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["passing_rows"] == 1


def test_run_optimizer_for_strategy_archives_when_no_rows_pass(tmp_path: Path) -> None:
    loop_session = create_session(
        lab_root=tmp_path / "lab",
        strategy_name="BridgeUnit",
        compile_mode="fixture",
    )
    loop_session.ensure_dirs()
    source, _ = _seed_compile_clean_source(tmp_path)
    runner = _FakeRunner(
        csv_rows=[
            ["NQ 06-26", "1.10",
             "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
             "100", "500", "-400", "1.10", "-1200", "7", "45%", ""],
        ]
    )

    result = run_optimizer_for_strategy(
        loop_session=loop_session,
        compile_clean_source=source,
        guardrails=Guardrails(),
        instrument="NQ 06-26",
        optimizer_storage_root=tmp_path / "optimizer_store",
        runner=runner,
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    assert result.decision == "archive"
    assert result.analyzer_result["passing_rows"] == 0


def test_run_optimizer_for_strategy_marks_timeout_as_incomplete(tmp_path: Path) -> None:
    loop_session = create_session(
        lab_root=tmp_path / "lab",
        strategy_name="BridgeUnit",
        compile_mode="fixture",
    )
    loop_session.ensure_dirs()
    source, _ = _seed_compile_clean_source(tmp_path)
    runner = _NeverFinishesRunner()

    result = run_optimizer_for_strategy(
        loop_session=loop_session,
        compile_clean_source=source,
        guardrails=Guardrails(),
        instrument="NQ 06-26",
        optimizer_storage_root=tmp_path / "optimizer_store",
        runner=runner,
        poll_seconds=0.01,
        timeout_seconds=1,
    )

    assert result.run_state == "timeout"
    assert result.decision == "incomplete"
    assert any("did not reach a terminal state" in w for w in result.warnings)


def test_run_optimizer_for_strategy_raises_if_source_missing(tmp_path: Path) -> None:
    loop_session = create_session(
        lab_root=tmp_path / "lab",
        strategy_name="BridgeUnit",
        compile_mode="fixture",
    )
    loop_session.ensure_dirs()

    with pytest.raises(OptimizerBridgeError):
        run_optimizer_for_strategy(
            loop_session=loop_session,
            compile_clean_source=tmp_path / "missing.cs",
            guardrails=Guardrails(),
            optimizer_storage_root=tmp_path / "optimizer_store",
            runner=_FakeRunner(),
            poll_seconds=0.01,
            timeout_seconds=1,
        )


def test_live_optimizer_runner_delegates_to_module_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_start(session):  # noqa: ANN001
        calls["start"] = session
        return "record"

    def fake_status(session):  # noqa: ANN001
        calls["status"] = session
        return "status"

    monkeypatch.setattr("ta_foundation.nt_strategy_loop.optimizer_bridge.optimizer_runner.start_run", fake_start)
    monkeypatch.setattr("ta_foundation.nt_strategy_loop.optimizer_bridge.optimizer_runner.get_status", fake_status)

    runner = LiveOptimizerRunner()
    assert runner.start("session-obj") == "record"  # type: ignore[arg-type]
    assert runner.get_status("session-obj") == "status"  # type: ignore[arg-type]
    assert calls == {"start": "session-obj", "status": "session-obj"}
