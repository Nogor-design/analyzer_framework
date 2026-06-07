from __future__ import annotations

from pathlib import Path

import pandas as pd

from ta_foundation.web.optimizer_deployment_matrix import (
    build_deployment_matrix_recipe,
    build_name,
    classify_session,
    classify_single_multi,
    enumerate_cells,
    load_naming_rules,
    pantheonmaster_recipe_overrides,
    session_timeboxes,
    tier_slow_values,
)
from ta_foundation.web.optimizer_recipe import OptimizerRecipeDocument
from ta_foundation.web.optimizer_recipe_plan import build_recipe_plan_preview
from ta_foundation.web.optimizer_recipe_selection import _add_scores, _select_rows


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "naming_rules.json"


def _rules() -> dict:
    return load_naming_rules(FIXTURE_PATH)


def _one_stage_recipe(base_matrix: list[dict]) -> OptimizerRecipeDocument:
    return OptimizerRecipeDocument.from_dict(
        {
            "recipe_version": 1,
            "mode": "matrix_sequence",
            "recipe_id": "rec_matrix_test",
            "recipe_name": "Matrix Test",
            "strategy_id": "FakeStrategy",
            "base_matrix": base_matrix,
            "stages": [
                {
                    "stage_id": "stage_1",
                    "stage_type": "optimizer",
                    "optimize_inside_template": {
                        "MaxStop": {"min": 50, "max": 100, "step": 50},
                    },
                }
            ],
        }
    )


def _single_multi_selection_frame() -> pd.DataFrame:
    return _add_scores(
        pd.DataFrame(
            [
                {
                    "candidate_id": "a_single_maxtrades",
                    "parent_candidate_id": "parent_a",
                    "param_Start_Time_(HH)": 0,
                    "param_Reverse": False,
                    "param_MaxTrades": 1,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.8,
                    "total_net_profit": 1000,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 100,
                },
                {
                    "candidate_id": "a_single_forced",
                    "parent_candidate_id": "parent_a",
                    "param_Start_Time_(HH)": 0,
                    "param_Reverse": False,
                    "param_MaxTrades": 5,
                    "param_ProfitStop": 1,
                    "param_LossStop": 1,
                    "profit_factor": 2.0,
                    "total_net_profit": 1200,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 120,
                },
                {
                    "candidate_id": "a_multi_best",
                    "parent_candidate_id": "parent_a",
                    "param_Start_Time_(HH)": 0,
                    "param_Reverse": False,
                    "param_MaxTrades": 5,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.9,
                    "total_net_profit": 1100,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 110,
                },
                {
                    "candidate_id": "a_multi_low",
                    "parent_candidate_id": "parent_a",
                    "param_Start_Time_(HH)": 0,
                    "param_Reverse": False,
                    "param_MaxTrades": 3,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.6,
                    "total_net_profit": 900,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 90,
                },
                {
                    "candidate_id": "b_single_best",
                    "parent_candidate_id": "parent_b",
                    "param_Start_Time_(HH)": 4,
                    "param_Reverse": True,
                    "param_MaxTrades": 1,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.7,
                    "total_net_profit": 800,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 80,
                },
                {
                    "candidate_id": "b_multi_low",
                    "parent_candidate_id": "parent_b",
                    "param_Start_Time_(HH)": 4,
                    "param_Reverse": True,
                    "param_MaxTrades": 10,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.5,
                    "total_net_profit": 700,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 70,
                },
                {
                    "candidate_id": "b_multi_best",
                    "parent_candidate_id": "parent_b",
                    "param_Start_Time_(HH)": 4,
                    "param_Reverse": True,
                    "param_MaxTrades": 3,
                    "param_ProfitStop": 500,
                    "param_LossStop": 500,
                    "profit_factor": 1.85,
                    "total_net_profit": 950,
                    "drawdown_abs": 100,
                    "total_trades": 20,
                    "portfolio_score": 95,
                },
            ]
        )
    )


def test_enumerate_cells_returns_unique_252_cell_grid() -> None:
    cells = enumerate_cells(_rules())

    assert len(cells) == 252
    assert len({tuple(cell.items()) for cell in cells}) == 252
    assert len({cell["session"] for cell in cells}) == 7
    assert len({cell["single_multi"] for cell in cells}) == 2
    assert len({cell["tier_index"] for cell in cells}) == 9
    assert len({cell["side"] for cell in cells}) == 2


def test_session_timeboxes_match_naming_rule_windows() -> None:
    by_session = {item["session"]: item for item in session_timeboxes(_rules())}

    assert len(by_session) == 7
    assert by_session["NY Open"]["start_h"] == 7
    assert by_session["NY Open"]["start_m"] == 30
    assert by_session["NY Open"]["dur_h"] == 0
    assert by_session["NY Open"]["dur_m"] == 30
    assert by_session["Pre-Market"]["start_h"] == 7
    assert by_session["Pre-Market"]["start_m"] == 0
    assert by_session["Pre-Market"]["dur_h"] == 0
    assert by_session["Pre-Market"]["dur_m"] == 30
    assert by_session["Asia"]["start_h"] == 16
    assert by_session["Asia"]["start_m"] == 0
    assert by_session["Asia"]["dur_h"] == 8
    assert by_session["Asia"]["dur_m"] == 0
    assert by_session["London Early"]["start_h"] == 0
    assert by_session["London Early"]["start_m"] == 0
    assert by_session["London Early"]["dur_h"] == 4
    assert by_session["London Early"]["dur_m"] == 0


def test_tier_slow_values_are_in_each_tier_range() -> None:
    rules = _rules()
    values = tier_slow_values(rules)

    assert len(values) == 9
    for value, tier in zip(values, rules["ma_tiers"]):
        assert tier["minimum"] <= value <= tier["maximum"]


def test_scalar_matrix_axes_keep_existing_plan_buckets() -> None:
    recipe = _one_stage_recipe(
        [
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4]},
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
        ]
    )

    first_plan = build_recipe_plan_preview(recipe)
    second_plan = build_recipe_plan_preview(recipe)

    first_buckets = {job.bucket_id for job in first_plan.stages[0].jobs}
    second_buckets = {job.bucket_id for job in second_plan.stages[0].jobs}
    assert first_plan.template_count == second_plan.template_count == 4
    assert first_buckets == second_buckets
    assert first_buckets == {
        "starttimeh_00__reverse_false",
        "starttimeh_00__reverse_true",
        "starttimeh_04__reverse_false",
        "starttimeh_04__reverse_true",
    }


def test_matrix_bundle_axis_expands_and_merges_fragments() -> None:
    recipe = _one_stage_recipe(
        [
            {
                "param": "Session",
                "role": "matrix_bundle_axis",
                "values": [
                    {
                        "StartTimeH": 0,
                        "StartTimeM": 0,
                        "DurationTimeH": 4,
                        "DurationTimeM": 0,
                    },
                    {
                        "StartTimeH": 7,
                        "StartTimeM": 30,
                        "DurationTimeH": 0,
                        "DurationTimeM": 30,
                    },
                ],
            },
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
        ]
    )

    plan = build_recipe_plan_preview(recipe)
    jobs = plan.stages[0].jobs

    assert plan.template_count == 4
    assert len(jobs) == 4
    assert jobs[0].matrix_values == {
        "StartTimeH": 0,
        "StartTimeM": 0,
        "DurationTimeH": 4,
        "DurationTimeM": 0,
        "Reverse": False,
    }
    assert jobs[-1].matrix_values == {
        "StartTimeH": 7,
        "StartTimeM": 30,
        "DurationTimeH": 0,
        "DurationTimeM": 30,
        "Reverse": True,
    }


def test_selection_can_group_by_derived_single_multi_key() -> None:
    selected = _select_rows(
        _single_multi_selection_frame(),
        {
            "group_by": ["parent_candidate_id", "single_multi"],
            "keep_per_group": 1,
            "rank_by": "portfolio_score",
        },
    )

    by_group = {
        (row["parent_candidate_id"], row["single_multi"]): row["candidate_id"]
        for row in selected.to_dict(orient="records")
    }

    assert len(selected) == 4
    assert by_group == {
        ("parent_a", "single"): "a_single_forced",
        ("parent_a", "multi"): "a_multi_best",
        ("parent_b", "single"): "b_single_best",
        ("parent_b", "multi"): "b_multi_best",
    }


def test_selection_without_single_multi_keeps_legacy_grouping_behavior() -> None:
    selected = _select_rows(
        _single_multi_selection_frame(),
        {
            "group_by": ["StartTimeH", "Reverse"],
            "keep_per_group": 1,
            "rank_by": "portfolio_score",
        },
    )

    assert "single_multi" not in selected.columns
    assert selected["candidate_id"].tolist() == ["a_single_forced", "b_multi_best"]


def test_build_deployment_matrix_recipe_plan_shape() -> None:
    recipe_payload = build_deployment_matrix_recipe(
        strategy_id="FakeStrategy",
        recipe_name="Deployment Matrix",
        rules=_rules(),
    )

    document = OptimizerRecipeDocument.from_dict(recipe_payload)
    plan = build_recipe_plan_preview(document)
    stage_1 = plan.stages[0]
    ny_open_job = next(
        job
        for job in stage_1.jobs
        if job.matrix_values["StartTimeH"] == 7 and job.matrix_values["StartTimeM"] == 30
    )
    max_trades = recipe_payload["stages"][1]["optimize_inside_template"]["MaxTrades"]
    max_trades_values = list(range(max_trades["min"], max_trades["max"] + 1, max_trades["step"]))
    refine_selection = recipe_payload["stages"][1]["selection"]

    assert [stage["stage_id"] for stage in recipe_payload["stages"]] == [
        "stage_1",
        "refine_risk",
        "final_backtest",
    ]
    assert stage_1.template_count == 126
    assert len(stage_1.jobs) == 126
    assert ny_open_job.matrix_values["DurationTimeH"] == 0
    assert ny_open_job.matrix_values["DurationTimeM"] == 30
    assert 1 in max_trades_values
    assert refine_selection["group_by"] == ["parent_candidate_id", "single_multi"]
    assert refine_selection["keep_per_group"] == 1
    assert recipe_payload["stages"][2]["finalists_per_bucket"] == 1

    # The trend filter must be pinned OFF in the base matrix, or the whole grid
    # silently runs trend-on (seed default) and produces a near-empty,
    # settings-contract-violating pool.
    base_by_param = {entry["param"]: entry for entry in recipe_payload["base_matrix"]}
    assert base_by_param["UseTrend"]["role"] == "fixed"
    assert base_by_param["UseTrend"]["value"] is False
    assert base_by_param["UseTrendReverse"]["value"] is False


def test_build_deployment_matrix_recipe_default_uses_ma_cross_params() -> None:
    # Default (MA-cross) path must be unchanged: averageSlow swept, averageFast fixed.
    recipe = build_deployment_matrix_recipe(
        strategy_id="PantheonMasterBotV01TesterV2", recipe_name="ma", rules=_rules()
    )
    base = {e["param"]: e["role"] for e in recipe["base_matrix"]}
    assert base.get("averageSlow") == "matrix_axis"
    assert base.get("averageFast") == "fixed"
    assert "SlowPeriod" not in base and "RegimeMode" not in base
    # Default: no trade floor on refine selection (top-PF behaviour unchanged).
    assert "hard_filters" not in recipe["stages"][1]["selection"]


def test_build_deployment_matrix_recipe_pantheonmaster_overrides() -> None:
    # PantheonMaster: MA params are FastPeriod/SlowPeriod and regime+exit are
    # pinned (fixed) — never swept (would explode the 59-param Stage 1).
    recipe = build_deployment_matrix_recipe(
        strategy_id="PantheonMaster",
        recipe_name="pm",
        rules=_rules(),
        **pantheonmaster_recipe_overrides(),
    )
    base = {e["param"]: e for e in recipe["base_matrix"]}
    assert base["SlowPeriod"]["role"] == "matrix_axis"
    assert base["FastPeriod"]["role"] == "fixed"
    # Relaxed regime pin: entry filters OFF (full coverage, MA-cross-parity entry),
    # regime inert (Any), exit policy ON (the single variable under test).
    assert base["EnableDiscoveryFilters"] == {
        "param": "EnableDiscoveryFilters", "role": "fixed", "value": False
    }
    assert base["RegimeMode"] == {"param": "RegimeMode", "role": "fixed", "value": "Any"}
    assert base["DiscoveryExitPolicy"]["value"] == "AtrTrail"
    assert base["UseDiscoveryExitPolicy"]["value"] is True
    # Grid axis + refine pins follow the renamed MA param, not the MA-cross name.
    assert recipe["stages"][0]["selection"]["group_by"] == [
        "StartTimeH", "StartTimeM", "Reverse", "SlowPeriod",
    ]
    refine_pins = recipe["stages"][1]["pin"]
    assert "SlowPeriod" in refine_pins and "averageSlow" not in refine_pins
    assert {"EnableDiscoveryFilters", "RegimeMode", "DiscoveryExitPolicy"}.issubset(set(refine_pins))


def test_refine_selection_min_trades_adds_trade_floor() -> None:
    recipe = build_deployment_matrix_recipe(
        strategy_id="FakeStrategy",
        recipe_name="dm",
        rules=_rules(),
        refine_selection_min_trades=15,
    )
    refine_selection = recipe["stages"][1]["selection"]
    assert refine_selection["hard_filters"]["min_trades"] == 15
    # Still keeps best PF per (parent, single/multi) among the survivors.
    assert refine_selection["group_by"] == ["parent_candidate_id", "single_multi"]
    assert refine_selection["fitness_metrics"] == ["profit_factor", "total_net_profit"]


def test_classify_single_multi() -> None:
    assert classify_single_multi(1, 500, 500) == "single"
    assert classify_single_multi(5, 500, 500) == "multi"
    assert classify_single_multi(5, 1, 1) == "single"


def test_classify_session() -> None:
    rules = _rules()

    assert classify_session(60, rules) == "London Early"
    assert classify_session(455, rules) == "NY Open"


def test_build_name_oracles() -> None:
    rules = _rules()

    assert (
        build_name(
            start_minute=60,
            average_fast=5,
            average_slow=200,
            reverse=False,
            max_trades=1,
            profit_stop=500,
            loss_stop=500,
            max_loss=200,
            rr=2.0,
            long_enabled=True,
            short_enabled=False,
            rules=rules,
        )
        == "RiseApolloBalanceL"
    )
    assert (
        build_name(
            start_minute=60,
            average_fast=5,
            average_slow=300,
            reverse=True,
            max_trades=5,
            profit_stop=500,
            loss_stop=500,
            max_loss=1500,
            rr=2.0,
            long_enabled=True,
            short_enabled=True,
            rules=rules,
        )
        == "RisingCerberusInfernoB"
    )
    assert (
        build_name(
            start_minute=430,
            average_fast=5,
            average_slow=350,
            reverse=True,
            max_trades=1,
            profit_stop=500,
            loss_stop=500,
            max_loss=1500,
            rr=1.0,
            long_enabled=False,
            short_enabled=True,
            rules=rules,
            version=2,
            market="ES",
        )
        == "CoilSphinxFireSV2-ES"
    )


# --- Phase 2c: NT template-generation integration guard -----------------------
#
# The 252-cell capability depends on the bundle time params
# (StartTimeM/DurationTimeM) flowing into the generated NT optimize XML for the
# root jobs, pinned (Min == Max == ValueSerializable). This was the one genuine
# integration risk in the design. Verified manually 2026-06-04; locked here.
#
# This test needs the real strategy registered (its .cs lives outside the repo
# under the NinjaTrader install), so it SKIPS when the strategy/seed is absent
# rather than failing CI. On the trading machine it runs and guards the invariant
# before any live dispatch.
_DM_STRATEGY = "PantheonMasterBotV01TesterV2"


def _generate_root_job_xml(strategy_id: str, tmp_path):
    import re

    from ta_foundation.web.optimizer_recipe_templates import _job_from_dict, _patch_recipe_job
    from ta_foundation.web.optimizer_strategy_catalog import regenerate_recipe_seed

    summary = regenerate_recipe_seed(
        strategy_id,
        instrument="NQ 06-25",
        from_date="2025-01-01",
        to_date="2025-03-01",
        template_dir=tmp_path,
    )
    seed_text = Path(getattr(summary, "template_path", None) or summary.path).read_text(
        encoding="utf-8"
    )
    doc = OptimizerRecipeDocument.from_dict(
        build_deployment_matrix_recipe(strategy_id=strategy_id, recipe_name="dm", rules=_rules())
    )
    preview = build_recipe_plan_preview(doc)
    jobs = preview.stages[0].jobs
    ny = next(
        (
            j
            for j in jobs
            if j.matrix_values.get("StartTimeH") == 7
            and j.matrix_values.get("StartTimeM") == 30
            and j.matrix_values.get("DurationTimeH") == 0
            and j.matrix_values.get("DurationTimeM") == 30
        ),
        None,
    )
    assert ny is not None, "NY Open lane (7:30 / 30m) not found among root jobs"
    text = _patch_recipe_job(
        seed_text,
        _job_from_dict(ny.to_dict()),
        instrument="NQ 06-25",
        keep_best_results=1000,
        optimizer_type=None,
        optimization_fitness=None,
        strategy_id=strategy_id,
    )
    return text, ny, re, len(jobs)


def _pinned(text, re, name):
    m = re.search(
        r"<Parameter>(?:(?!</Parameter>).)*<Name>"
        + re.escape(name)
        + r"</Name>(?:(?!</Parameter>).)*</Parameter>",
        text,
        re.DOTALL,
    )
    if not m:
        return None
    blk = m.group(0)

    def tag(t):
        mm = re.search(r"<" + t + r"[^>]*>(.*?)</" + t + r">", blk, re.DOTALL)
        return mm.group(1).strip() if mm else None

    strat = re.search(r"<" + re.escape(name) + r"(?:\s[^>]*)?>(.*?)</" + re.escape(name) + r">", text, re.DOTALL)
    return {
        "min": tag("Min"),
        "max": tag("Max"),
        "value": tag("ValueSerializable"),
        "strategy_tag": strat.group(1).strip() if strat else None,
    }


def test_deployment_matrix_root_template_pins_bundle_time_params(tmp_path):
    import pytest

    from ta_foundation.web.optimizer_strategy_catalog import get_strategy_detail

    if get_strategy_detail(_DM_STRATEGY) is None:
        pytest.skip(f"{_DM_STRATEGY} not registered on this machine; integration guard skipped")

    text, ny, re, n_jobs = _generate_root_job_xml(_DM_STRATEGY, tmp_path)
    assert n_jobs == 126  # 7 sessions x 2 reverse x 9 tiers

    # All four bundle time params must be pinned (Min == Max == Value == strategy tag).
    expected = {"StartTimeH": "7", "StartTimeM": "30", "DurationTimeH": "0", "DurationTimeM": "30"}
    for name, val in expected.items():
        p = _pinned(text, re, name)
        assert p is not None, f"{name} has no <Parameter> block — would not pin in the optimizer"
        assert p["min"] == p["max"] == p["value"] == p["strategy_tag"] == val, f"{name} not pinned: {p}"

    # The actual swept knob must still sweep (proves we pin lanes, not knobs).
    ms = _pinned(text, re, "MaxStop")
    assert ms is not None and ms["min"] != ms["max"], f"MaxStop should sweep in stage_1, got {ms}"
