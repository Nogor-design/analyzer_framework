from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_results import load_optimizer_results


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def test_load_results_surfaces_batch_summary_timeout():
    session = opt_session.create_session(strategy_id="FakeStrategy")
    output = session.directory / "nt_output"
    chunk = output / "chunk_001"
    chunk.mkdir(parents=True)
    (output / "BatchRunSummary.csv").write_text(
        "Template,Status,Strategy,Instrument,Backtest start,Backtest end,"
        "Total net profit,Trades,Profit factor,Max drawdown,Run start time,Run end time,Output folder,Error\n"
        "chunk_001,TimedOut,,,2025-03-08,2025-03-14,,,,,2026-05-20 23:10:46,"
        "2026-05-20 23:20:48,C:\\out,\n",
        encoding="utf-8",
    )
    (chunk / "chunk_001_Optimization.csv").write_text(
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n",
        encoding="utf-8",
    )

    results = load_optimizer_results(session)

    assert results.row_count == 0
    assert results.batch_run_statuses[0]["template"] == "chunk_001"
    assert results.batch_run_statuses[0]["status"] == "TimedOut"
    assert any("TimedOut" in note for note in results.notes)
