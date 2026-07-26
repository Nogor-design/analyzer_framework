from __future__ import annotations

"""Tests for the Phase 6 row-level refinement backend."""

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_refine import (
    OptimizerRefineError,
    refine_from_rows,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _seed_session(label: str = "source"):
    session = opt_session.create_session(
        label=label,
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="C:/seed.xml",
        instrument="NQ 06-26",
    )
    session.update(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 500, "increment": 50},
            {"name": "MaxStop", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 350, "increment": 50},
            {"name": "MaxTPRatio", "type_name": "double", "mode": "optimize",
             "minimum": 0.5, "maximum": 2.0, "increment": 0.5},
            {"name": "Reverse", "type_name": "bool", "mode": "fixed",
             "fixed_value": False},
            {"name": "UseTrend", "type_name": "bool", "mode": "fixed",
             "fixed_value": False},
            {"name": "BotName", "type_name": "string", "mode": "fixed",
             "fixed_value": "PantheonMasterBotV01TesterV2"},
        ],
    )
    return session


def _write_evaluated(session, rows):
    review = session.directory / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "evaluated_candidates.json").write_text(
        json.dumps({"schema_version": 1, "config": {}, "candidate_count": len(rows), "rows": rows}),
        encoding="utf-8",
    )


def test_refine_tightens_numeric_params_when_rows_differ():
    source = _seed_session()
    _write_evaluated(source, [
        {"run_id": "F_001", "status": "pass", "average_slow": 200, "max_stop": 100, "max_tp_ratio": 1.5,
         "use_trend": "False", "use_trend_reverse": "False"},
        {"run_id": "F_002", "status": "pass", "average_slow": 300, "max_stop": 150, "max_tp_ratio": 2.0,
         "use_trend": "False", "use_trend_reverse": "False"},
    ])

    new_session, summary = refine_from_rows(source, ["F_001", "F_002"])

    new_doc = new_session.load_document()
    by_name = {p.name: p for p in new_doc.parameters}

    # averageSlow swept 200..300 with widening (default widen=20)
    assert by_name["averageSlow"].mode == "optimize"
    assert by_name["averageSlow"].minimum == 200 - 20
    assert by_name["averageSlow"].maximum == 300 + 20
    assert by_name["averageSlow"].increment == 10

    # MaxStop swept 100..150 with widening
    assert by_name["MaxStop"].mode == "optimize"
    assert by_name["MaxStop"].minimum == 100 - 30
    assert by_name["MaxStop"].maximum == 150 + 30

    # MaxTPRatio swept (double)
    assert by_name["MaxTPRatio"].mode == "optimize"
    assert by_name["MaxTPRatio"].minimum == pytest.approx(1.5 - 0.3)
    assert by_name["MaxTPRatio"].maximum == pytest.approx(2.0 + 0.3)

    # Bool params: all rows agreed False, become fixed False
    assert by_name["UseTrend"].mode == "fixed"
    assert by_name["UseTrend"].fixed_value is False

    # Unmapped param (BotName) carried through unchanged
    assert by_name["BotName"].mode == "fixed"
    assert by_name["BotName"].fixed_value == "PantheonMasterBotV01TesterV2"

    # Summary surfaces what changed
    assert summary.source_session_id == source.id
    assert summary.new_session_id == new_session.id
    assert set(summary.selected_run_ids) == {"F_001", "F_002"}
    decisions = {c["name"]: c["decision"] for c in summary.parameter_changes}
    assert "swept (tightened)" in decisions["averageSlow"]
    assert decisions["UseTrend"].startswith("fixed")


def test_refine_pins_unanimous_numeric_when_no_widening():
    source = _seed_session()
    source.update(parameters=[
        {"name": "StartTimeH", "type_name": "int", "mode": "optimize",
         "minimum": 0, "maximum": 23, "increment": 1},
    ])
    _write_evaluated(source, [
        {"run_id": "F_001", "status": "pass", "start_hour": 8},
        {"run_id": "F_002", "status": "pass", "start_hour": 8},
    ])
    new_session, _ = refine_from_rows(source, ["F_001", "F_002"])
    by_name = {p.name: p for p in new_session.load_document().parameters}
    # StartTimeH has widen=0 in hints; unanimous -> fixed.
    assert by_name["StartTimeH"].mode == "fixed"
    assert by_name["StartTimeH"].fixed_value == 8


def test_refine_uses_majority_for_bool_disagreement():
    source = _seed_session()
    _write_evaluated(source, [
        {"run_id": "F_001", "use_trend": "True"},
        {"run_id": "F_002", "use_trend": "False"},
        {"run_id": "F_003", "use_trend": "False"},
    ])
    new_session, summary = refine_from_rows(source, ["F_001", "F_002", "F_003"])
    by_name = {p.name: p for p in new_session.load_document().parameters}
    assert by_name["UseTrend"].mode == "fixed"
    assert by_name["UseTrend"].fixed_value is False  # 2 vs 1


def test_refine_raises_when_run_ids_missing_from_review():
    source = _seed_session()
    _write_evaluated(source, [{"run_id": "F_001"}])
    with pytest.raises(OptimizerRefineError):
        refine_from_rows(source, ["F_999"])


def test_refine_raises_when_no_evaluated_candidates_file():
    source = _seed_session()
    with pytest.raises(OptimizerRefineError):
        refine_from_rows(source, ["F_001"])


def test_refine_clamps_minimum_to_param_floor():
    """ProfitStop, LossStop etc. have small sentinel values like 1. Widening
    by 200 would push the lower bound negative; the param's min_floor must
    clamp it."""
    source = _seed_session()
    source.update(parameters=[
        {"name": "ProfitStop", "type_name": "int", "mode": "optimize",
         "minimum": 1, "maximum": 10000, "increment": 50},
    ])
    _write_evaluated(source, [
        {"run_id": "F_001", "profit_stop": 1},
        {"run_id": "F_002", "profit_stop": 1},
    ])
    new_session, summary = refine_from_rows(source, ["F_001", "F_002"])
    by_name = {p.name: p for p in new_session.load_document().parameters}
    # observed was [1], widen=200, floor=1. Min must clamp to 1, not -199.
    assert by_name["ProfitStop"].minimum >= 1
    assert by_name["ProfitStop"].minimum == 1


def test_refine_returns_new_session_separate_from_source():
    source = _seed_session()
    _write_evaluated(source, [
        {"run_id": "F_001", "average_slow": 200, "max_stop": 100, "max_tp_ratio": 1.5,
         "use_trend": "False", "use_trend_reverse": "False"},
    ])
    new_session, _ = refine_from_rows(source, ["F_001"], label="my refinement")
    assert new_session.id != source.id
    assert new_session.load_document().label == "my refinement"
    # New session must be writable and queryable just like any other session.
    assert opt_session.get_session(new_session.id) is not None
