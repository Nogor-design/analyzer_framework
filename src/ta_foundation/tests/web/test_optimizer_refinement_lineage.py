"""Regression tests for the risk-knob refinement flow.

Covers two bugs where a 'refined' session validated stage_1 defaults instead of
the swept ProfitStop/LossStop/MaxTrades winners:

1. NinjaTrader truncates long output-folder names, so a result row's batch_id
   diverges from the manifest template_id and parent_candidate_id was dropped
   (collapsing per-candidate selection to a single None group).
2. The final fixed-backtest stage re-pooled stage_1 coverage finalists via
   initial-bucket logic instead of validating the refine stage's winners 1:1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.web.optimizer_recipe_results import _enrich_results
from ta_foundation.web.optimizer_recipe_templates import _final_backtest_source_rows
from ta_foundation.web.optimizer_session import OptimizerSession


def test_enrich_recovers_parent_when_nt_truncates_output_name():
    # Manifest is keyed by the full template_id; NT truncated the output folder
    # name to a `<prefix>_<index>_<hash>` form, so the row batch_id no longer
    # matches the manifest key exactly — but the bucket_id token survives.
    manifest = {
        "refine_risk_x__parent_f_020__opt_maxnetprofit": {
            "bucket_id": "parent_f_020", "parent_candidate_id": "F_020",
        },
        "refine_risk_x__parent_f_020__opt_maxprofitfactor": {
            "bucket_id": "parent_f_020", "parent_candidate_id": "F_020",
        },
        "refine_risk_x__parent_f_087__opt_maxnetprofit": {
            "bucket_id": "parent_f_087", "parent_candidate_id": "F_087",
        },
    }
    df = pd.DataFrame([
        {"batch_id": "refine_risk_x__parent_f_020_0001_45b99c9d"},
        {"batch_id": "refine_risk_x__parent_f_087_0029_6a3a2f68"},
        # exact match still works
        {"batch_id": "refine_risk_x__parent_f_020__opt_maxnetprofit"},
    ])
    out = _enrich_results(df, recipe_id="rec", stage_id="refine_risk_x", manifest=manifest)
    assert list(out["parent_candidate_id"]) == ["F_020", "F_087", "F_020"]


def _seed_refinement_session(sd: Path) -> None:
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_x",
        "strategy_id": "PantheonMasterBotV01TesterV2",
        "base_matrix": [
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4]},
            {"param": "DurationTimeH", "role": "fixed", "value": 4},
        ],
        "stages": [
            {"stage_id": "stage_1", "stage_type": "optimizer",
             "optimize_inside_template": {"MaxStop": {"min": 50, "max": 350, "step": 50}}},
            {"stage_id": "final_backtest", "stage_type": "fixed_backtest", "from": "stage_1.selected_rows"},
            {"stage_id": "refine_risk_x", "stage_type": "optimizer",
             "from": "refine_src_x.selected_rows",
             "optimize_inside_template": {
                 "ProfitStop": {"min": 1, "max": 1001, "step": 500},
                 "LossStop": {"min": 1, "max": 1001, "step": 500},
                 "MaxTrades": {"min": 1, "max": 11, "step": 2},
             }},
            {"stage_id": "refine_final_x", "stage_type": "fixed_backtest", "from": "refine_risk_x.selected_rows"},
        ],
    }
    (sd / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")

    # stage_1 winners carry seed-default risk knobs; refine winners carry swept ones.
    stage1_sel = sd / "parsed_results" / "stage_1"
    stage1_sel.mkdir(parents=True, exist_ok=True)
    (stage1_sel / "selected.json").write_text(json.dumps([
        {"candidate_id": "stage_1__a", "param_MaxStop": 350,
         "param_ProfitStop": 10000, "param_LossStop": 10000, "param_MaxTrades": 500},
    ]), encoding="utf-8")

    refine_sel = sd / "parsed_results" / "refine_risk_x"
    refine_sel.mkdir(parents=True, exist_ok=True)
    (refine_sel / "selected.json").write_text(json.dumps([
        {"candidate_id": "refine_risk_x__parent_f_020__row1", "parent_candidate_id": "F_020",
         "param_MaxStop": 350, "param_ProfitStop": 501, "param_LossStop": 1001, "param_MaxTrades": 1},
        {"candidate_id": "refine_risk_x__parent_f_087__row1", "parent_candidate_id": "F_087",
         "param_MaxStop": 150, "param_ProfitStop": 1001, "param_LossStop": 1, "param_MaxTrades": 3},
    ]), encoding="utf-8")


def test_final_source_uses_refine_winners_not_stage1(tmp_path: Path):
    sd = tmp_path / "opt_rf"
    sd.mkdir()
    _seed_refinement_session(sd)
    session = OptimizerSession(sd)
    recipe = load_recipe(session)

    rows, report = _final_backtest_source_rows(
        session, recipe=recipe, parent_stage_id="refine_risk_x", finalists_per_bucket=2,
    )
    # One final row per refined winner, with the SWEPT risk values — not the
    # stage_1 defaults, and not re-bucketed against stage_1.
    assert len(rows) == 2
    cand_ids = {r["candidate_id"] for r in rows}
    assert cand_ids == {
        "refine_risk_x__parent_f_020__row1",
        "refine_risk_x__parent_f_087__row1",
    }
    profit_stops = sorted(r["param_ProfitStop"] for r in rows)
    assert profit_stops == [501, 1001]
    assert all(r["param_ProfitStop"] != 10000 for r in rows)
    assert len(report) == 2
