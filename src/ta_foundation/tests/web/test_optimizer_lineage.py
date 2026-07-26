"""Tests for the lineage walker that powers the read-only "How was this
candidate discovered" page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_lineage import (
    LineageError,
    build_lineage,
    list_finalist_ids,
)


@pytest.fixture
def storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session(strategy_id: str = "FakeStrategy") -> opt_session.OptimizerSession:
    return opt_session.create_session(
        strategy_id=strategy_id,
        seed_template_path="C:/fake/seed.xml",
        instrument="NQ 06-26",
    )


def _write_recipe(session: opt_session.OptimizerSession, stages: list[dict]) -> None:
    payload = {
        "recipe_version": 1,
        "recipe_id": "rec_test",
        "recipe_name": "lineage_test",
        "strategy_id": "FakeStrategy",
        "base_matrix": [],
        "stages": stages,
    }
    (session.directory / "recipe.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_manifest(session: opt_session.OptimizerSession, templates: list[dict]) -> None:
    manifest_dir = session.directory / "generated_templates" / "final_backtest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "stage_id": "final_backtest", "templates": templates}
    (manifest_dir / "recipe_template_manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_scored_rows(
    session: opt_session.OptimizerSession,
    stage_id: str,
    rows: list[dict],
    selected_ids: list[str] | None = None,
) -> None:
    stage_dir = session.directory / "parsed_results" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scored_rows.json").write_text(json.dumps(rows), encoding="utf-8")
    if selected_ids is not None:
        selected = [
            r for r in rows if str(r.get("candidate_id")) in set(selected_ids)
        ]
        (stage_dir / "selected.json").write_text(json.dumps(selected), encoding="utf-8")


def _write_evaluated(session: opt_session.OptimizerSession, rows: list[dict]) -> None:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "evaluated_candidates.json").write_text(
        json.dumps({"rows": rows}), encoding="utf-8"
    )


def _write_recommendations(
    session: opt_session.OptimizerSession,
    recommended: list[dict],
    rejected: list[dict] | None = None,
) -> None:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    payload = {"recommendations": recommended, "rejected": rejected or []}
    (review_dir / "recommendations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_build_lineage_multi_stage_walks_back_to_stage_1(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Broad sweep"},
        {"stage_id": "stage_2", "stage_type": "optimizer", "description": "Refine winners"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])

    # Stage 1: parent of the eventual stage_2 row.
    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__P1_T021__row01024",
            "template_id": "P1_T021",
            "bucket_id": "b_S08_R0_M200",
            "optimizer_row_id": 1024,
            "parent_candidate_id": None,
            "param_StartTimeH": 8,
            "param_Reverse": False,
            "param_averageSlow": 200,
            "param_averageFast": 5,
            "param_MaxStop": 125,
            "param_MaxTPRatio": 2.5,
            "profit_factor": 2.31,
            "total_net_profit": 18420,
            "max_drawdown": -1840,
            "trades": 412,
        },
        {
            "candidate_id": "stage_1__P1_T021__row09999",
            "template_id": "P1_T021",
            "bucket_id": "b_S08_R0_M200",
            "optimizer_row_id": 9999,
            "parent_candidate_id": None,
            "param_averageFast": 8,
            "profit_factor": 1.20,
            "total_net_profit": 800,
            "max_drawdown": -1100,
            "trades": 50,
        },
    ], selected_ids=["stage_1__P1_T021__row01024"])

    # Stage 2: row #42 was the eventual winner whose parent is the stage_1 row above.
    _write_scored_rows(session, "stage_2", rows=[
        {
            "candidate_id": "stage_2__P2_T003__row00042",
            "template_id": "P2_T003",
            "bucket_id": "b_S08_R0_M200",
            "optimizer_row_id": 42,
            "parent_candidate_id": "stage_1__P1_T021__row01024",
            "param_averageFast": 5,
            "param_MaxStop": 120,
            "param_MaxTPRatio": 2.55,
            "profit_factor": 2.48,
            "total_net_profit": 19240,
            "max_drawdown": -1690,
            "trades": 418,
        },
        {
            "candidate_id": "stage_2__P2_T003__row00018",
            "template_id": "P2_T003",
            "bucket_id": "b_S08_R0_M200",
            "optimizer_row_id": 18,
            "parent_candidate_id": "stage_1__P1_T021__row01024",
            "param_averageFast": 4,
            "param_MaxStop": 110,
            "param_MaxTPRatio": 2.35,
            "profit_factor": 2.18,
            "total_net_profit": 16820,
            "max_drawdown": -1740,
            "trades": 401,
        },
    ], selected_ids=["stage_2__P2_T003__row00042"])

    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_2__P2_T003__row00042",
            "parent_stage_id": "stage_2",
            "final_selection_source_stage": "stage_2",
            "initial_bucket_key": "starttimeh_8__reverse_false",
            "initial_bucket_values": {"StartTimeH": 8, "Reverse": False},
        },
        {
            "template_id": "final_backtest__F_002",
            "parent_candidate_id": "stage_2__P2_T003__row00018",
            "parent_stage_id": "stage_2",
            "final_selection_source_stage": "stage_2",
            "initial_bucket_key": "starttimeh_8__reverse_false",
            "initial_bucket_values": {"StartTimeH": 8, "Reverse": False},
        },
    ])

    _write_evaluated(session, rows=[
        {
            "run_id": "F_001",
            "profit_factor": 2.36,
            "total_net_profit": 18420,
            "max_drawdown": -1520,
            "trades": 388,
            "label": "Pantheon NQ – Morning Trend",
        },
        {
            "run_id": "F_002",
            "profit_factor": 1.92,
            "total_net_profit": 14000,
            "max_drawdown": -1900,
            "trades": 350,
        },
    ])
    _write_recommendations(session, recommended=[
        {"run_id": "F_001", "rank": 1, "reason": "Passed all checks."},
    ])

    report = build_lineage(session, "F_001")

    assert report.candidate_id == "F_001"
    assert report.candidate_label == "Pantheon NQ – Morning Trend"
    assert report.siblings == ["F_001", "F_002"]
    assert report.notes == []

    # Three nodes: stage_1 root → stage_2 winner → finalist
    assert [n.stage_id for n in report.nodes] == ["stage_1", "stage_2", "final_backtest"]
    assert [n.depth for n in report.nodes] == [0, 1, 2]
    assert [n.role for n in report.nodes] == ["stage_root", "stage_winner", "finalist"]

    stage1 = report.nodes[0]
    assert stage1.template_id == "P1_T021"
    assert stage1.bucket_id == "b_S08_R0_M200"
    assert stage1.status == "selected"
    assert stage1.kpis.profit_factor == pytest.approx(2.31)
    assert stage1.params["averageSlow"] == 200

    stage2 = report.nodes[1]
    assert stage2.candidate_id == "stage_2__P2_T003__row00042"
    assert stage2.status == "selected"
    assert stage2.kpis.profit_factor == pytest.approx(2.48)
    assert stage2.params["MaxStop"] == 120

    finalist = report.nodes[2]
    assert finalist.candidate_id == "F_001"
    assert finalist.status == "recommended"
    assert finalist.status_reason == "Passed all checks."
    assert finalist.kpis.profit_factor == pytest.approx(2.36)


def test_build_lineage_stage_skipping_finalist_only_one_hop(storage):
    """A finalist sourced straight from stage_1 (no intermediate refinement)
    must produce a 2-node lineage: stage_1 root + finalist."""
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Single sweep"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])

    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__P1_T009__row07724",
            "template_id": "P1_T009",
            "bucket_id": "b_S00_R1_M200",
            "optimizer_row_id": 7724,
            "parent_candidate_id": None,
            "param_averageSlow": 200,
            "profit_factor": 2.05,
            "total_net_profit": 17110,
            "max_drawdown": -2200,
            "trades": 401,
        },
    ], selected_ids=["stage_1__P1_T009__row07724"])

    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_007",
            "parent_candidate_id": "stage_1__P1_T009__row07724",
            "parent_stage_id": "final_backtest",
            "final_selection_source_stage": "stage_1",
        },
    ])

    _write_evaluated(session, rows=[
        {"run_id": "F_007", "profit_factor": 1.98, "total_net_profit": 16000, "trades": 380},
    ])

    report = build_lineage(session, "F_007")
    assert [n.stage_id for n in report.nodes] == ["stage_1", "final_backtest"]
    assert report.nodes[0].role == "stage_root"
    assert report.nodes[1].role == "finalist"


def test_build_lineage_missing_scored_rows_returns_warning_note(storage):
    """If a mid-chain stage's scored_rows.json is missing the walker should
    stop, note where it broke, and still return what it has."""
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "First"},
        {"stage_id": "stage_2", "stage_type": "optimizer", "description": "Second"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])

    # NOTE: stage_2 scored_rows.json is intentionally NOT written.
    _write_scored_rows(session, "stage_1", rows=[
        {"candidate_id": "stage_1__T01__row001", "parent_candidate_id": None, "profit_factor": 1.5},
    ])
    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_010",
            "parent_candidate_id": "stage_2__T07__row099",
            "final_selection_source_stage": "stage_2",
        },
    ])
    _write_evaluated(session, rows=[
        {"run_id": "F_010", "profit_factor": 1.6, "trades": 100},
    ])

    report = build_lineage(session, "F_010")
    # Only the finalist node — walker broke immediately when it couldn't find
    # stage_2's scored_rows.json.
    assert [n.stage_id for n in report.nodes] == ["final_backtest"]
    assert any("stage_2" in note for note in report.notes)


def test_build_lineage_unknown_candidate_raises(storage):
    session = _make_session()
    _write_manifest(session, templates=[
        {"template_id": "final_backtest__F_001"},
    ])
    with pytest.raises(LineageError) as excinfo:
        build_lineage(session, "F_999")
    assert "F_999" in str(excinfo.value)


def test_build_lineage_no_manifest_raises(storage):
    session = _make_session()
    with pytest.raises(LineageError) as excinfo:
        build_lineage(session, "F_001")
    assert "manifest" in str(excinfo.value).lower()


def test_param_diff_normalizes_nt_display_names_against_strategy_values(storage):
    """Production scored_rows.json keys params under NinjaTrader DISPLAY
    names (``param_Start_Time_(HH)``) while the finalist's manifest entry
    carries a ``strategy_values`` dict using CODE names (``StartTimeH``).
    The walker must normalize display→code so matrix axes don't light up
    as spurious ``added`` badges at the finalist (the F_012 symptom).
    """
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Broad sweep"},
        {"stage_id": "stage_2", "stage_type": "optimizer", "description": "Refine"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])

    # Real scored_rows.json shape: NinjaTrader display names under param_.
    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__T1__row001",
            "template_id": "T1",
            "parent_candidate_id": None,
            "param_Start_Time_(HH)": 4,
            "param_Reverse": False,
            "param_Use_Time_Filter": True,
            "param_averageFast": 5,
            "param_MaxStop": 125,
            "profit_factor": 2.31,
            "total_net_profit": 18420,
            "trades": 412,
        },
    ], selected_ids=["stage_1__T1__row001"])

    _write_scored_rows(session, "stage_2", rows=[
        {
            "candidate_id": "stage_2__T2__row042",
            "template_id": "T2",
            "parent_candidate_id": "stage_1__T1__row001",
            "param_Start_Time_(HH)": 4,
            "param_Reverse": False,
            "param_Use_Time_Filter": True,
            "param_averageFast": 5,
            "param_MaxStop": 120,
            "profit_factor": 2.48,
            "total_net_profit": 19240,
            "trades": 418,
        },
    ], selected_ids=["stage_2__T2__row042"])

    # Finalist manifest entry carries the authoritative strategy_values
    # dict in code-level names — the recipe writer's actual output.
    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_012",
            "parent_candidate_id": "stage_2__T2__row042",
            "parent_stage_id": "stage_2",
            "final_selection_source_stage": "stage_2",
            "strategy_values": {
                "StartTimeH": 4,
                "Reverse": False,
                "UseTimeFilter": True,
                "averageFast": 5,
                # MaxStop drifted further at the final-backtest stamp.
                "MaxStop": 130,
            },
        },
    ])
    _write_evaluated(session, rows=[
        {
            "run_id": "F_012",
            "profit_factor": 1.85,
            "total_net_profit": 13500,
            "trades": 380,
        },
    ])

    report = build_lineage(session, "F_012")
    assert [n.stage_id for n in report.nodes] == ["stage_1", "stage_2", "final_backtest"]

    # Stage 1 root: NT display names translated to canonical.
    stage1_params = report.nodes[0].params
    assert stage1_params["StartTimeH"] == 4
    assert stage1_params["Reverse"] is False
    assert stage1_params["UseTimeFilter"] is True
    assert "Start_Time_(HH)" not in stage1_params

    # Stage 2 vs Stage 1: matrix axes identical -> unchanged (not "added").
    s2_diff = report.nodes[1].param_diff
    assert s2_diff["StartTimeH"]["kind"] == "unchanged"
    assert s2_diff["Reverse"]["kind"] == "unchanged"
    assert s2_diff["MaxStop"]["kind"] == "changed"
    assert s2_diff["MaxStop"]["previous"] == 125

    # Finalist vs Stage 2: matrix axes still unchanged (the bug was that
    # they showed as ``added`` here because of the namespace mismatch).
    final_diff = report.nodes[2].param_diff
    assert final_diff["StartTimeH"]["kind"] == "unchanged", (
        f"StartTimeH must be unchanged on the finalist, got {final_diff['StartTimeH']!r}"
    )
    assert final_diff["Reverse"]["kind"] == "unchanged"
    # The real drift the operator wants to see: MaxStop 120 -> 130.
    assert final_diff["MaxStop"]["kind"] == "changed"
    assert final_diff["MaxStop"]["previous"] == 120


def test_finalist_filters_decorative_strategy_values_not_seen_at_prior_stages(storage):
    """``strategy_values`` is the strategy's FULL canonical param dict —
    including decorative non-sweep things like BotName, FileName, IOpacity
    that the optimizer never touched. The walker must drop those from the
    finalist's params so the diff doesn't show spurious ``new`` badges
    for state that was always there."""
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Sweep"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__T__row001",
            "template_id": "T",
            "parent_candidate_id": None,
            "param_averageFast": 5,
            "param_MaxStop": 125,
            "param_Reverse": False,
            "profit_factor": 2.0,
            "total_net_profit": 5000,
            "trades": 100,
        },
    ], selected_ids=["stage_1__T__row001"])
    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_1__T__row001",
            "final_selection_source_stage": "stage_1",
            # strategy_values is the strategy's full canonical state. Only
            # averageFast / MaxStop / Reverse were ever swept; the rest is
            # decorative noise that must not appear on the finalist node.
            "strategy_values": {
                "averageFast": 5,
                "MaxStop": 130,
                "Reverse": False,
                "BotName": "PantheonMasterBotV01TesterV2",
                "FileName": "panthionIcon4.png",
                "FileName2": "Oden2.png",
                "IOpacity": 0.3,
            },
        },
    ])
    _write_evaluated(session, rows=[
        {"run_id": "F_001", "profit_factor": 1.8, "total_net_profit": 4000, "trades": 90},
    ])

    report = build_lineage(session, "F_001")
    finalist = report.nodes[-1]
    assert "BotName" not in finalist.params, (
        "BotName never appeared at a prior stage — must be filtered out."
    )
    assert "FileName" not in finalist.params
    assert "FileName2" not in finalist.params
    assert "IOpacity" not in finalist.params
    # Real swept params must survive the filter.
    assert finalist.params["averageFast"] == 5
    assert finalist.params["MaxStop"] == 130
    # And the diff still flags MaxStop as changed (125 -> 130).
    assert finalist.param_diff["MaxStop"]["kind"] == "changed"
    assert finalist.param_diff["MaxStop"]["previous"] == 125


def test_finalist_keeps_manifest_declared_params_even_if_absent_at_prior_stage(storage):
    """A param declared on the finalist manifest entry (initial_bucket_values
    or fixed_values) should survive the filter even if no scored_rows row
    happens to carry it — the operator clearly cared about it."""
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Sweep"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__T__row001",
            "parent_candidate_id": None,
            "param_averageFast": 5,
            "profit_factor": 2.0,
        },
    ], selected_ids=["stage_1__T__row001"])
    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_1__T__row001",
            "final_selection_source_stage": "stage_1",
            "initial_bucket_values": {"StartTimeH": 8},
            "fixed_values": {"UseTimeFilter": True},
            "strategy_values": {
                "averageFast": 5,
                "StartTimeH": 8,
                "UseTimeFilter": True,
                "FileName": "decorative.png",  # noise
            },
        },
    ])
    _write_evaluated(session, rows=[{"run_id": "F_001", "profit_factor": 2.0}])

    finalist = build_lineage(session, "F_001").nodes[-1]
    assert "StartTimeH" in finalist.params, (
        "Manifest-declared bucket axes must survive even if no prior row had them."
    )
    assert "UseTimeFilter" in finalist.params
    assert "FileName" not in finalist.params, (
        "Decorative strategy_values keys not declared on the manifest entry "
        "must still get filtered."
    )


def test_finalist_falls_back_to_bucket_values_when_no_strategy_values(storage):
    """Older sessions don't have ``strategy_values`` on the manifest entry.
    The walker must still produce a usable params dict from
    ``initial_bucket_values`` + ``fixed_values`` + the eval row."""
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Sweep"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__T__row001",
            "parent_candidate_id": None,
            "param_averageFast": 5,
            "profit_factor": 1.8,
        },
    ], selected_ids=["stage_1__T__row001"])
    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_1__T__row001",
            "final_selection_source_stage": "stage_1",
            # No strategy_values — fallback path.
            "initial_bucket_values": {"StartTimeH": 8},
            "fixed_values": {"UseTimeFilter": True},
        },
    ])
    _write_evaluated(session, rows=[
        {"run_id": "F_001", "param_averageFast": 5, "profit_factor": 1.9},
    ])

    report = build_lineage(session, "F_001")
    finalist_params = report.nodes[-1].params
    assert finalist_params["StartTimeH"] == 8
    assert finalist_params["UseTimeFilter"] is True
    assert finalist_params["averageFast"] == 5


def test_param_diff_flags_changed_and_added_params_vs_parent(storage):
    """The root node sees every param as unchanged (no parent to diff
    against). Downstream nodes flag values that shifted vs the previous
    stage and call out params that only appear from this stage onward.
    """
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer", "description": "Broad sweep"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])

    _write_scored_rows(session, "stage_1", rows=[
        {
            "candidate_id": "stage_1__T__row001",
            "template_id": "T",
            "parent_candidate_id": None,
            "param_StartTimeH": 8,
            "param_Reverse": False,
            "param_averageFast": 5,
            "param_MaxStop": 125,
            "profit_factor": 2.31,
            "total_net_profit": 18420,
            "trades": 412,
        },
    ], selected_ids=["stage_1__T__row001"])

    _write_manifest(session, templates=[
        {
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_1__T__row001",
            "final_selection_source_stage": "stage_1",
            # Param-only-on-finalist: ProfitStop wasn't in the stage_1 row.
            "fixed_values": {"ProfitStop": 8000},
            "initial_bucket_values": {"StartTimeH": 8, "Reverse": False},
        },
    ])
    _write_evaluated(session, rows=[
        {
            "run_id": "F_001",
            # The finalist's actual params: MaxStop drifted, averageFast unchanged.
            "param_averageFast": 5,
            "param_MaxStop": 130,
            "profit_factor": 1.92,
            "total_net_profit": 14000,
            "trades": 380,
        },
    ])

    report = build_lineage(session, "F_001")
    assert [n.stage_id for n in report.nodes] == ["stage_1", "final_backtest"]

    root_diff = report.nodes[0].param_diff
    assert all(entry["kind"] == "unchanged" for entry in root_diff.values()), (
        "Root node has no parent — every param must be marked unchanged."
    )

    finalist_diff = report.nodes[1].param_diff
    # MaxStop went 125 -> 130 between stage_1 and the finalist.
    assert finalist_diff["MaxStop"]["kind"] == "changed"
    assert finalist_diff["MaxStop"]["previous"] == 125
    assert finalist_diff["MaxStop"]["value"] == 130
    # averageFast stayed at 5 in both rows.
    assert finalist_diff["averageFast"]["kind"] == "unchanged"
    # ProfitStop only appeared on the finalist via manifest.fixed_values.
    assert finalist_diff["ProfitStop"]["kind"] == "added"
    assert finalist_diff["ProfitStop"]["previous"] is None
    # The matrix-pinned params StartTimeH/Reverse are identical so they
    # must NOT light up as changed.
    assert finalist_diff["StartTimeH"]["kind"] == "unchanged"
    assert finalist_diff["Reverse"]["kind"] == "unchanged"


def test_list_finalist_ids_returns_sorted_short_ids(storage):
    session = _make_session()
    _write_manifest(session, templates=[
        {"template_id": "final_backtest__F_003"},
        {"template_id": "final_backtest__F_001"},
        {"template_id": "final_backtest__F_002"},
        {"template_id": "ignored_non_finalist"},
    ])
    assert list_finalist_ids(session) == ["F_001", "F_002", "F_003"]
