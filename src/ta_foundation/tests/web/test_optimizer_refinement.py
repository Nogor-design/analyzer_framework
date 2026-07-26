from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_refinement import (
    RefinementError,
    RefinementRanges,
    list_refinable_candidates,
    prepare_refinement,
)


def _seed_session(session_dir: Path) -> None:
    # Minimal recipe + final manifest so the refinement can read winners.
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_weekly_coverage",
        "strategy_id": "PantheonMasterBotV01TesterV2",
        "base_matrix": [
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4]},
            {"param": "DurationTimeH", "role": "fixed", "value": 4},
        ],
        "stages": [
            {"stage_id": "stage_1", "stage_type": "optimizer", "selection": {"mode": "coverage_matrix_sequence"}},
            {"stage_id": "final_backtest", "stage_type": "fixed_backtest", "from": "stage_1.selected_rows"},
        ],
    }
    (session_dir / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")

    manifest = {
        "templates": [
            {"template_id": "final_backtest__F_001",
             "strategy_values": {"StartTimeH": 0, "averageSlow": 100, "MaxStop": 350, "MaxTPRatio": 2.0}},
            {"template_id": "final_backtest__F_002",
             "strategy_values": {"StartTimeH": 4, "averageSlow": 200, "MaxStop": 150, "MaxTPRatio": 1.0}},
        ]
    }
    mpath = session_dir / "generated_templates" / "final_backtest" / "recipe_template_manifest.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest), encoding="utf-8")


def test_ranges_default_combo_count():
    r = RefinementRanges.from_payload(None)
    # 3 (profit) * 3 (loss) * 6 (max trades) = 54
    assert r.combos_per_candidate() == 54


def test_list_refinable_candidates(tmp_path: Path):
    sd = tmp_path / "opt_r"
    sd.mkdir()
    _seed_session(sd)
    cands = list_refinable_candidates(OptimizerSession(sd))
    assert {c["run_id"] for c in cands} == {"F_001", "F_002"}
    assert cands[0]["strategy_values"]["MaxStop"] in (350, 150)


def test_prepare_refinement_appends_stages_and_pins(tmp_path: Path):
    sd = tmp_path / "opt_r2"
    sd.mkdir()
    _seed_session(sd)
    session = OptimizerSession(sd)

    prep = prepare_refinement(session, ["F_001"])
    assert prep.candidate_count == 1
    assert prep.combos_per_candidate == 54
    assert "StartTimeH" in prep.pinned_params and "MaxStop" in prep.pinned_params

    # Parent selection written for the synthetic source stage.
    src = sd / "parsed_results" / prep.source_stage_id / "selected.json"
    rows = json.loads(src.read_text(encoding="utf-8"))
    assert rows[0]["candidate_id"] == "F_001"
    assert rows[0]["param_MaxStop"] == 350

    # Recipe gained a refine optimizer stage (risk sweep) + a final backtest stage.
    recipe = json.loads((sd / "recipe.json").read_text(encoding="utf-8"))
    stage_ids = [s["stage_id"] for s in recipe["stages"]]
    assert prep.refine_stage_id in stage_ids and prep.final_stage_id in stage_ids
    refine = next(s for s in recipe["stages"] if s["stage_id"] == prep.refine_stage_id)
    assert set(refine["optimize_inside_template"]) == {"ProfitStop", "LossStop", "MaxTrades"}
    assert refine["optimize_inside_template"]["ProfitStop"] == {"min": 1.0, "max": 1001.0, "step": 500.0}
    # ProfitStop/LossStop/MaxTrades must NOT be pinned (they are swept).
    assert "ProfitStop" not in refine["pin"]


def test_prepare_refinement_rejects_empty(tmp_path: Path):
    sd = tmp_path / "opt_r3"
    sd.mkdir()
    _seed_session(sd)
    with pytest.raises(RefinementError):
        prepare_refinement(OptimizerSession(sd), [])


def test_no_manifest_raises(tmp_path: Path):
    sd = tmp_path / "opt_r4"
    sd.mkdir()
    (sd / "recipe.json").write_text(json.dumps({"recipe_id": "x", "stages": []}), encoding="utf-8")
    with pytest.raises(RefinementError):
        list_refinable_candidates(OptimizerSession(sd))
