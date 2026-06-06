from __future__ import annotations

from pathlib import Path
import json

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_recipe import (
    OptimizerRecipeDocument,
    OptimizerRecipeNotFoundError,
    load_recipe,
    save_recipe,
)
from ta_foundation.web.optimizer_recipe_plan import (
    build_and_save_recipe_plan,
    build_recipe_plan_preview,
    load_recipe_plan,
)
from ta_foundation.web.optimizer_recipe_templates import (
    RecipeTemplateWriteError,
    generate_recipe_stage_templates,
)
from ta_foundation.web.optimizer_final_templates import list_active_final_templates
from ta_foundation.web.optimizer_recipe_results import load_recipe_stage_results
from ta_foundation.web.optimizer_recipe_results import RecipeStageResults
from ta_foundation.web.optimizer_recipe_selection import select_recipe_stage_candidates
from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
from ta_foundation.web.optimizer_recipe_runner import (
    RecipeRunnerError,
    load_recipe_run,
    load_recipe_run_history,
    start_recipe_stage_run,
)
from ta_foundation.web.optimizer_recipe_state import load_recipe_events, load_recipe_state


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path)
    yield
    opt_session.set_storage_root(None)


def _recipe_payload(*, include_slow_axis: bool = False) -> dict:
    base_matrix = [
        {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4, 8, 12, 16, 20]},
        {"param": "DurationTimeH", "role": "fixed", "value": 4},
        {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
    ]
    if include_slow_axis:
        base_matrix.append(
            {"param": "averageSlow", "role": "matrix_axis", "values": [50, 100, 150, 200, 250, 300, 350, 400]}
        )
    optimize_inside = {
        "averageFast": {"min": 2, "max": 10, "step": 1},
        "MaxStop": {"min": 50, "max": 100, "step": 25},
    }
    if not include_slow_axis:
        optimize_inside["averageSlow"] = {"min": 50, "max": 400, "step": 50}
    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_test",
        "recipe_name": "Time Reverse Test",
        "strategy_id": "FakeStrategy",
        "target_final_candidates": 4,
        "safety_caps": {
            "max_total_combinations": 250000,
            "max_templates_per_stage": 250,
        },
        "base_matrix": base_matrix,
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Broad bucket search",
                "optimize_inside_template": optimize_inside,
                "selection": {
                    "group_by": ["StartTimeH", "Reverse"],
                    "keep_per_group": 2,
                    "rank_by": "portfolio_score",
                },
            },
            {
                "stage_id": "stage_2",
                "stage_type": "optimizer",
                "from": "stage_1.selected_rows",
                "pin": ["StartTimeH", "DurationTimeH", "Reverse"],
                "description": "Refine children",
                "refine_around_parent_result": {
                    "averageFast": {"source": "parent", "radius": 1, "step": 1, "min": 1, "max": 20},
                    "averageSlow": {"source": "parent", "radius": 50, "step": 25, "min": 50, "max": 400},
                    "MaxStop": {"source": "parent", "radius": 25, "step": 25, "min": 25, "max": 200},
                },
                "selection": {
                    "group_by": ["parent_candidate_id"],
                    "keep_per_group": 1,
                    "rank_by": "portfolio_score",
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_2.selected_rows",
            },
        ],
    }


def test_recipe_save_load_round_trips_without_session_schema_changes():
    session = opt_session.create_session(strategy_id="FakeStrategy")
    with pytest.raises(OptimizerRecipeNotFoundError):
        load_recipe(session)

    save_recipe(session, _recipe_payload())

    loaded = load_recipe(session)
    assert isinstance(loaded, OptimizerRecipeDocument)
    assert loaded.recipe_id == "rec_test"
    assert loaded.mode == "matrix_sequence"
    assert loaded.target_final_candidates == 4
    assert len(loaded.base_matrix) == 3
    assert loaded.base_matrix[0].values == (0, 4, 8, 12, 16, 20)
    assert loaded.stages[0].selection["rank_by"] == "portfolio_score"

    # Standard optimizer document stays schema_version=1 and has no recipe fields.
    session_doc = session.load_document().to_dict()
    assert session_doc["schema_version"] == 1
    assert "recipe" not in session_doc


def test_recipe_plan_expands_time_reverse_to_12_templates():
    recipe = OptimizerRecipeDocument.from_dict(_recipe_payload())

    plan = build_recipe_plan_preview(recipe)

    assert plan.template_count == 12
    assert len(plan.stages) == 3
    stage_1 = plan.stages[0]
    assert stage_1.stage_id == "stage_1"
    assert stage_1.template_count == 12
    assert len(stage_1.jobs) == 12
    assert plan.stages[1].deferred is True
    assert plan.stages[2].deferred is True

    first = stage_1.jobs[0]
    assert first.matrix_values == {"StartTimeH": 0, "Reverse": False}
    assert first.fixed_values == {"DurationTimeH": 4}
    assert first.template_id == "stage_1__starttimeh_00__reverse_false"
    assert first.bucket_id == "starttimeh_00__reverse_false"


def test_recipe_plan_expands_time_reverse_slow_to_96_templates():
    recipe = OptimizerRecipeDocument.from_dict(_recipe_payload(include_slow_axis=True))

    plan = build_recipe_plan_preview(recipe)

    stage_1 = plan.stages[0]
    assert stage_1.template_count == 96
    assert len(stage_1.jobs) == 96
    assert stage_1.jobs[0].template_id == "stage_1__starttimeh_00__reverse_false__averageslow_50"
    assert stage_1.jobs[-1].template_id == "stage_1__starttimeh_20__reverse_true__averageslow_400"


def test_recipe_plan_hash_is_stable_and_changes_when_matrix_changes():
    recipe_a = OptimizerRecipeDocument.from_dict(_recipe_payload())
    recipe_b = OptimizerRecipeDocument.from_dict(_recipe_payload())

    assert build_recipe_plan_preview(recipe_a).plan_hash == build_recipe_plan_preview(recipe_b).plan_hash

    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0, 6, 12, 18]
    recipe_c = OptimizerRecipeDocument.from_dict(payload)
    assert build_recipe_plan_preview(recipe_c).plan_hash != build_recipe_plan_preview(recipe_a).plan_hash


def test_build_and_save_recipe_plan_writes_recipe_plan_json():
    session = opt_session.create_session(strategy_id="FakeStrategy")
    save_recipe(session, _recipe_payload(include_slow_axis=True))

    plan = build_and_save_recipe_plan(session)
    saved = load_recipe_plan(session)

    assert saved is not None
    assert saved["plan_hash"] == plan.plan_hash
    assert saved["template_count"] == 96
    assert (session.directory / "recipe_plan.json").exists()


def test_recipe_plan_warns_when_safety_cap_is_exceeded():
    payload = _recipe_payload(include_slow_axis=True)
    payload["safety_caps"]["max_templates_per_stage"] = 12
    recipe = OptimizerRecipeDocument.from_dict(payload)

    plan = build_recipe_plan_preview(recipe)

    assert "stage_template_cap_exceeded:stage_1:96" in plan.warnings


SEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper>
      <ParameterWrapper>
        <Name>KeepBestResults</Name>
        <Value xsi:type="xsd:int">500</Value>
      </ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <Strategy>
    <FakeStrategy>
      <StartTimeH>0</StartTimeH>
      <DurationTimeH>1</DurationTimeH>
      <Reverse>false</Reverse>
      <averageSlow>100</averageSlow>
      <averageFast>2</averageFast>
      <MaxStop>50</MaxStop>
      <Category>NinjaScript</Category>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
  <OptimizationParameters>
    <ArrayOfParameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">0</Max><Min xsi:type="xsd:int">0</Min><Name>StartTimeH</Name><ValueSerializable>0</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">1</Max><Min xsi:type="xsd:int">1</Min><Name>DurationTimeH</Name><ValueSerializable>1</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:boolean">false</Max><Min xsi:type="xsd:boolean">false</Min><Name>Reverse</Name><ValueSerializable>false</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">100</Max><Min xsi:type="xsd:int">100</Min><Name>averageSlow</Name><ValueSerializable>100</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">2</Max><Min xsi:type="xsd:int">2</Min><Name>averageFast</Name><ValueSerializable>2</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">50</Max><Min xsi:type="xsd:int">50</Min><Name>MaxStop</Name><ValueSerializable>50</ValueSerializable></Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</StrategyTemplate>
"""


def test_generate_recipe_stage_templates_writes_stage_folder_and_manifest(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    session.update(chunking={"keep_best_results": 750})
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)

    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    assert len(written) == 12
    output_dir = session.directory / "generated_templates" / "stage_1"
    assert output_dir.exists()
    assert len(list(output_dir.glob("*.xml"))) == 12
    manifest = json.loads((output_dir / "recipe_template_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage_id"] == "stage_1"
    assert manifest["template_count"] == 12
    assert manifest["templates"][0]["bucket_id"] == "starttimeh_00__reverse_false"


def test_generate_recipe_stage_templates_patches_matrix_fixed_and_sweeps(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)

    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    first_xml = Path(written[0].path).read_text(encoding="utf-8")
    assert "<Category>Optimize</Category>" in first_xml
    assert "<Category>NinjaScript</Category>" not in first_xml
    assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in first_xml
    assert "<StartTimeH>0</StartTimeH>" in first_xml
    assert "<DurationTimeH>4</DurationTimeH>" in first_xml
    assert "<Reverse>false</Reverse>" in first_xml
    assert "<Min xsi:type=\"xsd:int\">50</Min>" in first_xml
    assert "<Max xsi:type=\"xsd:int\">400</Max>" in first_xml
    assert "<Increment>50</Increment><Max xsi:type=\"xsd:int\">400</Max><Min xsi:type=\"xsd:int\">50</Min><Name>averageSlow</Name>" in first_xml
    assert "<Name>KeepBestResults</Name>" in first_xml

    true_xml = Path(written[1].path).read_text(encoding="utf-8")
    assert "<Reverse>true</Reverse>" in true_xml


def test_generate_recipe_stage_templates_optimizes_bool_as_two_state_sweep(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"] = [
        {"param": "StartTimeH", "role": "matrix_axis", "values": [0]},
        {"param": "DurationTimeH", "role": "fixed", "value": 4},
    ]
    payload["stages"][0]["optimize_inside_template"] = {
        "Reverse": {"min": False, "max": True, "step": 1},
    }
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    assert len(written) == 1
    xml = Path(written[0].path).read_text(encoding="utf-8")
    assert '<Increment>1</Increment><Max xsi:type="xsd:boolean">true</Max><Min xsi:type="xsd:boolean">false</Min><Name>Reverse</Name>' in xml
    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["templates"][0]["optimized"][0]["step_count"] == 2


def test_generate_recipe_stage_templates_multiplies_by_nt_fitness_targets(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    payload["active_targets"] = ["MaxProfitFactor", "MinDrawDown"]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    assert len(written) == 2
    assert {Path(item.path).stem.rsplit("__opt_", 1)[-1] for item in written} == {
        "maxprofitfactor",
        "mindrawdown",
    }
    xml_by_stem = {Path(item.path).stem: Path(item.path).read_text(encoding="utf-8") for item in written}
    assert any(
        "<OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>"
        in text
        for text in xml_by_stem.values()
    )
    assert any(
        "<OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MinDrawDown</OptimizationFitness>"
        in text
        for text in xml_by_stem.values()
    )
    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert {row["optimization_target"] for row in manifest["templates"]} == {"MaxProfitFactor", "MinDrawDown"}


def test_generate_recipe_stage_templates_accepts_legacy_fitness_aliases(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    payload["active_targets"] = ["profit_factor", "drawdown_abs"]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    assert len(written) == 2
    texts = [Path(item.path).read_text(encoding="utf-8") for item in written]
    assert any("OptimizationFitnesses.MaxProfitFactor" in text for text in texts)
    assert any("OptimizationFitnesses.MinDrawDown" in text for text in texts)


def test_generate_recipe_stage_templates_refuses_deferred_stage(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ 06-26",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)

    with pytest.raises(RecipeTemplateWriteError):
        generate_recipe_stage_templates(session, stage_id="stage_2")


def test_generate_recipe_child_stage_templates_from_selected_parent_rows(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    payload["stages"][0]["selection"]["keep_per_group"] = 1
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)
    written = generate_recipe_stage_templates(session, stage_id="stage_1")
    template_id = written[0].template_id
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    _write_optimization_csv(
        output_dir / f"{template_id}_Optimization.csv",
        [("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80)],
    )
    load_recipe_stage_results(session, stage_id="stage_1")
    selected = select_recipe_stage_candidates(session, stage_id="stage_1")

    child_written = generate_recipe_stage_templates(session, stage_id="stage_2")

    assert selected.selected_count == 1
    assert len(child_written) == 1
    child = child_written[0]
    assert child.parent_candidate_id == selected.selected_rows[0]["candidate_id"]
    assert child.combination_count == 45
    child_xml = Path(child.path).read_text(encoding="utf-8")
    assert "<StartTimeH>0</StartTimeH>" in child_xml
    assert "<DurationTimeH>4</DurationTimeH>" in child_xml
    assert "<Reverse>false</Reverse>" in child_xml
    assert "<Increment>1</Increment><Max xsi:type=\"xsd:int\">6</Max><Min xsi:type=\"xsd:int\">4</Min><Name>averageFast</Name>" in child_xml
    assert "<Increment>25</Increment><Max xsi:type=\"xsd:int\">250</Max><Min xsi:type=\"xsd:int\">150</Min><Name>averageSlow</Name>" in child_xml
    assert "<Increment>25</Increment><Max xsi:type=\"xsd:int\">100</Max><Min xsi:type=\"xsd:int\">50</Min><Name>MaxStop</Name>" in child_xml
    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_2" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["templates"][0]["parent_candidate_id"] == selected.selected_rows[0]["candidate_id"]


def test_generate_recipe_child_stage_templates_pins_nt_export_column_aliases(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["stages"][1]["pin"] = ["StartTimeH", "DurationTimeH", "Reverse"]
    save_recipe(session, payload)
    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__alias_row",
                "param_Start_Time_(HH)": 16,
                "param_Duration_Time_(HH)": 2,
                "param_Reverse": True,
                "param_averageFast": 4,
                "param_averageSlow": 100,
                "param_MaxStop": 75,
            }
        ]),
        encoding="utf-8",
    )

    child_written = generate_recipe_stage_templates(session, stage_id="stage_2")

    child_xml = Path(child_written[0].path).read_text(encoding="utf-8")
    assert "<StartTimeH>16</StartTimeH>" in child_xml
    assert "<DurationTimeH>2</DurationTimeH>" in child_xml
    assert "<Reverse>true</Reverse>" in child_xml


def test_generate_recipe_final_backtest_uses_only_recipe_params_and_preserves_fixed_values(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"] = [
        {"param": "StartTimeH", "role": "matrix_axis", "values": [16]},
        {"param": "DurationTimeH", "role": "fixed", "value": 4},
    ]
    payload["stages"] = [
        {
            "stage_id": "stage_1",
            "stage_type": "optimizer",
            "optimize_inside_template": {
                "averageFast": {"min": 2, "max": 10, "step": 1},
                "averageSlow": {"min": 50, "max": 400, "step": 50},
            },
            "selection": {"keep_per_group": 1},
        },
        {
            "stage_id": "final_backtest",
            "stage_type": "fixed_backtest",
            "from": "stage_1.selected_rows",
        },
    ]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)
    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "param_Start_Time_(HH)": 16,
                "param_DurationTimeH": "bad-date-fragment",
                "param_averageFast": 6,
                "param_averageSlow": 225,
                "param_StartTime": 99,
                "total_net_profit": 1000,
            }
        ]),
        encoding="utf-8",
    )

    written = generate_recipe_stage_templates(session, stage_id="final_backtest")

    assert len(written) == 1
    xml = Path(written[0].path).read_text(encoding="utf-8")
    assert "<DurationTimeH>4</DurationTimeH>" in xml
    assert "<StartTimeH>16</StartTimeH>" in xml
    assert "bad-date-fragment" not in xml
    assert "<averageFast>6</averageFast>" in xml
    assert "<averageSlow>225</averageSlow>" in xml
    assert "<StartTime>99</StartTime>" not in xml


def test_generate_recipe_final_backtest_propagates_bundle_axis_component_params(tmp_path: Path):
    """A ``matrix_bundle_axis`` (e.g. ``Session``) sweeps several real strategy
    params under one synthetic axis name. Its component params (StartTimeH,
    DurationTimeH, ...) must survive into the final fixed-backtest template;
    otherwise every final collapses to the seed-default session (the observed
    "all StartTimeH=0 / all London Early" bug)."""
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"] = [
        {
            "param": "Session",
            "role": "matrix_bundle_axis",
            "values": [
                {"StartTimeH": 0, "DurationTimeH": 4},
                {"StartTimeH": 16, "DurationTimeH": 3},
            ],
        },
    ]
    payload["stages"] = [
        {
            "stage_id": "stage_1",
            "stage_type": "optimizer",
            "optimize_inside_template": {
                "averageSlow": {"min": 50, "max": 400, "step": 50},
            },
            "selection": {"keep_per_group": 1},
        },
        {
            "stage_id": "final_backtest",
            "stage_type": "fixed_backtest",
            "from": "stage_1.selected_rows",
        },
    ]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)
    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "StartTimeH": 16,
                "DurationTimeH": 3,
                "param_averageSlow": 225,
                "total_net_profit": 1000,
            }
        ]),
        encoding="utf-8",
    )

    written = generate_recipe_stage_templates(session, stage_id="final_backtest")

    assert len(written) == 1
    xml = Path(written[0].path).read_text(encoding="utf-8")
    # Bundle component params propagate from the selected row, not the seed default
    # (seed default is StartTimeH=0 / DurationTimeH=1).
    assert "<StartTimeH>16</StartTimeH>" in xml
    assert "<DurationTimeH>3</DurationTimeH>" in xml
    # The synthetic bundle axis name must never leak as a strategy element.
    assert "<Session>" not in xml


def test_generate_recipe_final_backtest_clears_stale_renamed_exports(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"] = [
        {"param": "StartTimeH", "role": "matrix_axis", "values": [16]},
    ]
    payload["stages"] = [
        {
            "stage_id": "stage_1",
            "stage_type": "optimizer",
            "selection": {"keep_per_group": 1},
        },
        {
            "stage_id": "final_backtest",
            "stage_type": "fixed_backtest",
            "from": "stage_1.selected_rows",
        },
    ]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)
    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "param_StartTimeH": 16,
            }
        ]),
        encoding="utf-8",
    )
    stale_renamed = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "renamed_backtest_templates"
    )
    stale_renamed.mkdir(parents=True, exist_ok=True)
    (stale_renamed / "F_001__old-name.xml").write_text("<old />", encoding="utf-8")

    generate_recipe_stage_templates(session, stage_id="final_backtest")

    active = list_active_final_templates(session)
    assert active
    assert all("named_backtest_templates" in str(path) for path in active)
    assert not stale_renamed.exists()


def test_generate_recipe_final_backtest_selects_by_initial_bucket_across_refinements(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0, 4]
    payload["base_matrix"][2]["values"] = [False]
    payload["stages"] = [
        {
            "stage_id": "stage_1",
            "stage_type": "optimizer",
            "optimize_inside_template": {"averageSlow": {"min": 50, "max": 100, "step": 50}},
            "selection": {"keep_per_group": 1},
        },
        {
            "stage_id": "stage_2",
            "stage_type": "optimizer",
            "from": "stage_1.selected_rows",
            "pin": ["StartTimeH", "Reverse"],
            "refine_around_parent_result": {"averageSlow": {"source": "parent", "radius": 25, "step": 25}},
            "selection": {"keep_per_group": 1},
        },
        {
            "stage_id": "final_backtest",
            "stage_type": "fixed_backtest",
            "from": "stage_2.selected_rows",
            "finalists_per_bucket": 1,
        },
    ]
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    stage1_dir = session.directory / "parsed_results" / "stage_1"
    stage2_dir = session.directory / "parsed_results" / "stage_2"
    stage1_dir.mkdir(parents=True, exist_ok=True)
    stage2_dir.mkdir(parents=True, exist_ok=True)
    (stage1_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage1_bucket0",
                "bucket_id": "b0",
                "param_Start_Time_(HH)": 0,
                "param_Reverse": False,
                "param_averageSlow": 100,
                "param_MaxStop": 75,
                "profit_factor": 4.0,
                "total_net_profit": 4000,
                "drawdown_abs": 500,
                "total_trades": 40,
                "portfolio_score": 9000,
            },
            {
                "candidate_id": "stage1_bucket4",
                "bucket_id": "b4",
                "param_Start_Time_(HH)": 4,
                "param_Reverse": False,
                "param_averageSlow": 50,
                "param_MaxStop": 100,
                "profit_factor": 3.0,
                "total_net_profit": 3000,
                "drawdown_abs": 700,
                "total_trades": 30,
                "portfolio_score": 7000,
            },
        ]),
        encoding="utf-8",
    )
    (stage2_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage2_refined_bucket0",
                "parent_candidate_id": "stage1_bucket0",
                "bucket_id": "parent_stage1_bucket0",
                "param_averageSlow": 75,
                "param_MaxStop": 50,
                "profit_factor": 2.0,
                "total_net_profit": 2000,
                "drawdown_abs": 600,
                "total_trades": 35,
                "portfolio_score": 5000,
            },
        ]),
        encoding="utf-8",
    )

    written = generate_recipe_stage_templates(session, stage_id="final_backtest")

    assert len(written) == 2
    manifest = json.loads(
        (session.directory / "generated_templates" / "final_backtest" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["target_buckets"] == 2
    assert [item["parent_candidate_id"] for item in manifest["templates"]] == [
        "stage1_bucket0",
        "stage1_bucket4",
    ]
    assert {row["selected_count"] for row in manifest["bucket_report"]} == {1}
    first_xml = Path(manifest["templates"][0]["path"]).read_text(encoding="utf-8")
    assert "<StartTimeH>0</StartTimeH>" in first_xml
    assert "<averageSlow>100</averageSlow>" in first_xml


def test_generate_recipe_final_backtest_builds_two_finalists_for_each_stage1_bucket(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["target_final_candidates"] = 2
    payload["stages"].insert(
        -1,
        {
            "stage_id": "stage_3",
            "stage_type": "optimizer",
            "from": "stage_2.selected_rows",
            "pin": ["StartTimeH", "DurationTimeH", "Reverse"],
            "refine_around_parent_result": {
                "averageSlow": {"source": "parent", "radius": 25, "step": 25, "min": 50, "max": 400},
                "MaxStop": {"source": "parent", "radius": 25, "step": 25, "min": 25, "max": 200},
            },
            "selection": {
                "group_by": ["parent_candidate_id"],
                "keep_per_group": 1,
                "rank_by": "portfolio_score",
            },
        },
    )
    payload["stages"][-1]["from"] = "stage_3.selected_rows"
    payload["stages"][-1]["finalists_per_bucket"] = 2
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    stage1_rows = []
    stage2_rows = []
    stage3_rows = []
    for start in [0, 4, 8, 12, 16, 20]:
        for reverse in [False, True]:
            bucket = f"start{start}_reverse{str(reverse).lower()}"
            primary_id = f"stage1_{bucket}_primary"
            alternate_id = f"stage1_{bucket}_alternate"
            stage2_id = f"stage2_{bucket}_refined"
            stage1_rows.extend([
                {
                    "candidate_id": primary_id,
                    "bucket_id": bucket,
                    "param_Start_Time_(HH)": start,
                    "param_Duration_Time_(HH)": 4,
                    "param_Reverse": reverse,
                    "param_averageFast": 5,
                    "param_averageSlow": 200,
                    "param_MaxStop": 75,
                    "profit_factor": 4.0,
                    "total_net_profit": 4000 + start,
                    "drawdown_abs": 500,
                    "total_trades": 40,
                    "portfolio_score": 9000 + start,
                },
                {
                    "candidate_id": alternate_id,
                    "bucket_id": bucket,
                    "param_Start_Time_(HH)": start,
                    "param_Duration_Time_(HH)": 4,
                    "param_Reverse": reverse,
                    "param_averageFast": 6,
                    "param_averageSlow": 250,
                    "param_MaxStop": 100,
                    "profit_factor": 2.0,
                    "total_net_profit": 2000 + start,
                    "drawdown_abs": 800,
                    "total_trades": 32,
                    "portfolio_score": 5000 + start,
                },
            ])
            stage2_rows.append({
                "candidate_id": stage2_id,
                "parent_candidate_id": primary_id,
                "bucket_id": f"parent_{primary_id}",
                "param_averageFast": 7,
                "param_averageSlow": 225,
                "param_MaxStop": 50,
                "profit_factor": 3.0,
                "total_net_profit": 3000 + start,
                "drawdown_abs": 600,
                "total_trades": 36,
                "portfolio_score": 7000 + start,
            })
            stage3_rows.append({
                "candidate_id": f"stage3_{bucket}_refined",
                "parent_candidate_id": stage2_id,
                "bucket_id": f"parent_{stage2_id}",
                "param_averageFast": 8,
                "param_averageSlow": 175,
                "param_MaxStop": 50,
                "profit_factor": 3.5,
                "total_net_profit": 3500 + start,
                "drawdown_abs": 550,
                "total_trades": 38,
                "portfolio_score": 8000 + start,
            })

    for stage_id, rows in {"stage_1": stage1_rows, "stage_2": stage2_rows, "stage_3": stage3_rows}.items():
        stage_dir = session.directory / "parsed_results" / stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "selected.json").write_text(json.dumps(rows), encoding="utf-8")

    written = generate_recipe_stage_templates(session, stage_id="final_backtest")

    assert len(written) == 24
    manifest = json.loads(
        (session.directory / "generated_templates" / "final_backtest" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["target_buckets"] == 12
    assert manifest["finalists_per_bucket"] == 2
    assert manifest["template_count"] == 24
    assert all(row["selected_count"] == 2 for row in manifest["bucket_report"])
    parents = {item["parent_candidate_id"] for item in manifest["templates"]}
    assert "stage1_start0_reversefalse_primary" in parents
    assert "stage3_start0_reversefalse_refined" in parents
    assert "stage2_start0_reversefalse_refined" not in parents
    assert "stage1_start0_reversefalse_alternate" not in parents
    first_xml = Path(manifest["templates"][0]["path"]).read_text(encoding="utf-8")
    assert "<DurationTimeH>4</DurationTimeH>" in first_xml


def _write_optimization_csv(path: Path, rows: list[tuple[str, float, float, float, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,"
    ]
    for params, performance, net, drawdown, trades in rows:
        lines.append(
            f"NQ 06-26,{performance},{params},{net},{net + 500},-500,{performance},-{drawdown},{trades},60%,"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_recipe_stage_results_attaches_template_bucket_and_matrix_metadata(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)
    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    template_id = written[0].template_id
    output_csv = session.directory / "nt_output" / "stage_1" / template_id / f"{template_id}_Optimization.csv"
    _write_optimization_csv(
        output_csv,
        [
            ("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80),
            ("6/250/100 (averageFast averageSlow MaxStop)", 1.4, 3000, 1000, 70),
        ],
    )

    results = load_recipe_stage_results(session, stage_id="stage_1")

    assert results.row_count == 2
    assert results.batch_count == 1
    row = results.rows[0]
    assert row["recipe_id"] == "rec_test"
    assert row["stage_id"] == "stage_1"
    assert row["template_id"] == template_id
    assert row["bucket_id"] == written[0].bucket_id
    assert row["StartTimeH"] == 0
    assert row["Reverse"] is False
    assert row["DurationTimeH"] == 4
    assert row["candidate_id"].startswith(f"stage_1__{template_id}__row")
    assert Path(results.parsed_rows_csv).exists()


def test_select_recipe_stage_candidates_keeps_top_row_per_bucket(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["stages"][0]["selection"] = {
        "group_by": ["StartTimeH", "Reverse"],
        "keep_per_group": 1,
        "hard_filters": {
            "min_trades": 50,
            "min_profit_factor": 1.3,
            "max_drawdown": 2500,
            "min_net_profit": 0,
        },
        "rank_by": "portfolio_score",
        "tie_breakers": ["lower_drawdown", "higher_trade_count", "higher_net_profit"],
    }
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)
    written = generate_recipe_stage_templates(session, stage_id="stage_1")

    first = written[0]
    second = written[1]
    _write_optimization_csv(
        session.directory / "nt_output" / "stage_1" / first.template_id / f"{first.template_id}_Optimization.csv",
        [
            ("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80),
            ("6/250/100 (averageFast averageSlow MaxStop)", 1.2, 6000, 900, 80),
        ],
    )
    _write_optimization_csv(
        session.directory / "nt_output" / "stage_1" / second.template_id / f"{second.template_id}_Optimization.csv",
        [
            ("5/200/75 (averageFast averageSlow MaxStop)", 1.6, 4000, 1100, 75),
            ("6/250/100 (averageFast averageSlow MaxStop)", 2.0, 8000, 3000, 100),
        ],
    )

    summary = select_recipe_stage_candidates(session, stage_id="stage_1")

    assert summary.row_count == 4
    assert summary.passing_count == 2
    assert summary.selected_count == 2
    assert summary.rejected_count == 2
    assert {row["bucket_id"] for row in summary.selected_rows} == {first.bucket_id, second.bucket_id}
    assert all(row["selection_status"] == "selected" for row in summary.selected_rows)
    rejected_reasons = {row["rejection_reason"] for row in summary.rejected_rows}
    assert "below_min_profit_factor" in rejected_reasons
    assert "above_max_drawdown" in rejected_reasons
    assert Path(summary.selected_csv).exists()
    assert (session.directory / "recipe_selection.csv").exists()


def test_select_recipe_stage_candidates_resolves_nt_export_column_aliases(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["stages"][0]["selection"] = {
        "group_by": ["StartTimeH"],
        "keep_per_group": 1,
        "rank_by": "portfolio_score",
    }
    save_recipe(session, payload)
    rows = [
        {
            "candidate_id": "candidate_1",
            "bucket_id": "bucket_1",
            "param_Start_Time_(HH)": 0,
            "profit_factor": 1.5,
            "total_net_profit": 1000,
            "drawdown_abs": 100,
            "total_trades": 40,
        },
        {
            "candidate_id": "candidate_2",
            "bucket_id": "bucket_2",
            "param_Start_Time_(HH)": 0,
            "profit_factor": 2.0,
            "total_net_profit": 2000,
            "drawdown_abs": 100,
            "total_trades": 40,
        },
        {
            "candidate_id": "candidate_3",
            "bucket_id": "bucket_3",
            "param_Start_Time_(HH)": 4,
            "profit_factor": 1.2,
            "total_net_profit": 500,
            "drawdown_abs": 100,
            "total_trades": 40,
        },
    ]
    results = RecipeStageResults(
        recipe_id=payload["recipe_id"],
        stage_id="stage_1",
        output_dir=str(session.directory / "nt_output" / "stage_1"),
        row_count=len(rows),
        batch_count=1,
        parse_warnings=0,
        rows=rows,
    )

    summary = select_recipe_stage_candidates(session, stage_id="stage_1", results=results)

    assert summary.selected_count == 2
    assert {row["candidate_id"] for row in summary.selected_rows} == {"candidate_2", "candidate_3"}


def test_select_recipe_stage_candidates_uses_explicit_stage_target_cap(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["target_final_candidates"] = 2
    payload["stages"][0]["selection"] = {
        "group_by": ["StartTimeH"],
        "keep_per_group": 2,
        "target_total_candidates": 2,
        "rank_by": "portfolio_score",
    }
    save_recipe(session, payload)
    rows = [
        {
            "candidate_id": f"candidate_{idx}",
            "bucket_id": f"bucket_{idx}",
            "StartTimeH": idx,
            "profit_factor": 1 + idx,
            "total_net_profit": 1000 * idx,
            "drawdown_abs": 100,
            "total_trades": 40,
        }
        for idx in range(1, 5)
    ]
    results = RecipeStageResults(
        recipe_id=payload["recipe_id"],
        stage_id="stage_1",
        output_dir=str(session.directory / "nt_output" / "stage_1"),
        row_count=len(rows),
        batch_count=1,
        parse_warnings=0,
        rows=rows,
    )

    summary = select_recipe_stage_candidates(session, stage_id="stage_1", results=results)

    assert summary.selected_count == 2
    assert [row["candidate_id"] for row in summary.selected_rows] == ["candidate_4", "candidate_3"]


def test_select_recipe_stage_candidates_coverage_matrix_keeps_diverse_lane_winners(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["stages"][0]["selection"] = {
        "mode": "coverage_matrix_sequence",
        "group_by": ["StartTimeH", "Reverse", "averageSlow"],
        "keep_per_group": 2,
        "fitness_metrics": ["profit_factor", "total_net_profit"],
        "coverage_grid": {
            "StartTimeH": [0, 4, 8],
            "Reverse": [False],
            "averageSlow": [100],
        },
    }
    save_recipe(session, payload)
    rows = [
        {
            "candidate_id": "lane0_pf",
            "bucket_id": "bucket_0",
            "StartTimeH": 0,
            "Reverse": False,
            "averageSlow": 100,
            "DurationTimeH": 4,
            "averageFast": 8,
            "MaxStop": 100,
            "MaxTPRatio": 1.5,
            "ProfitStop": 300,
            "LossStop": 200,
            "MaxTrades": 3,
            "Long": True,
            "Short": False,
            "profit_factor": 3.0,
            "total_net_profit": 1000,
            "drawdown_abs": 200,
            "total_trades": 12,
        },
        {
            "candidate_id": "lane0_net_duplicate_shape",
            "bucket_id": "bucket_0",
            "StartTimeH": 0,
            "Reverse": False,
            "averageSlow": 100,
            "DurationTimeH": 4,
            "averageFast": 8,
            "MaxStop": 100,
            "MaxTPRatio": 1.5,
            "ProfitStop": 300,
            "LossStop": 200,
            "MaxTrades": 3,
            "Long": True,
            "Short": False,
            "profit_factor": 2.0,
            "total_net_profit": 5000,
            "drawdown_abs": 250,
            "total_trades": 12,
        },
        {
            "candidate_id": "lane0_short_alternative",
            "bucket_id": "bucket_0",
            "StartTimeH": 0,
            "Reverse": False,
            "averageSlow": 100,
            "DurationTimeH": 4,
            "averageFast": 8,
            "MaxStop": 100,
            "MaxTPRatio": 1.5,
            "ProfitStop": 300,
            "LossStop": 200,
            "MaxTrades": 3,
            "Long": False,
            "Short": True,
            "profit_factor": 1.8,
            "total_net_profit": 4000,
            "drawdown_abs": 250,
            "total_trades": 12,
        },
        {
            "candidate_id": "lane4_only",
            "bucket_id": "bucket_4",
            "StartTimeH": 4,
            "Reverse": False,
            "averageSlow": 100,
            "DurationTimeH": 4,
            "averageFast": 9,
            "MaxStop": 100,
            "MaxTPRatio": 1.5,
            "ProfitStop": 300,
            "LossStop": 200,
            "MaxTrades": 3,
            "Long": True,
            "Short": False,
            "profit_factor": 2.2,
            "total_net_profit": 3000,
            "drawdown_abs": 250,
            "total_trades": 12,
        },
    ]
    results = RecipeStageResults(
        recipe_id=payload["recipe_id"],
        stage_id="stage_1",
        output_dir=str(session.directory / "nt_output" / "stage_1"),
        row_count=len(rows),
        batch_count=1,
        parse_warnings=0,
        rows=rows,
    )

    summary = select_recipe_stage_candidates(session, stage_id="stage_1", results=results)

    assert summary.selected_count == 3
    assert {row["candidate_id"] for row in summary.selected_rows} == {
        "lane0_pf",
        "lane0_short_alternative",
        "lane4_only",
    }
    assert "lane0_net_duplicate_shape" not in {row["candidate_id"] for row in summary.selected_rows}
    assert summary.coverage_csv
    coverage_rows = json.loads(Path(summary.coverage_json).read_text(encoding="utf-8"))
    by_start = {int(row["StartTimeH"]): row for row in coverage_rows}
    assert by_start[0]["lane_status"] == "full"
    assert by_start[4]["lane_status"] == "thin"
    assert by_start[4]["lane_gap_reason"] == "thin_only_one_passed"
    assert by_start[8]["lane_status"] == "missing"
    assert by_start[8]["lane_gap_reason"] == "missing_no_results"


def test_start_recipe_stage_run_writes_stage_specific_runbatch_command(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    session.update(chunking={"max_runtime_minutes_per_chunk": 3})
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)
    generate_recipe_stage_templates(session, stage_id="stage_1")

    command_file = tmp_path / "nt8_command.json"
    record = start_recipe_stage_run(session, stage_id="stage_1", command_file=command_file)

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["runId"] == record.run_id
    assert payload["sourceFolder"].endswith("generated_templates\\stage_1")
    assert payload["destFolder"].endswith("nt_output\\stage_1")
    assert payload["instrument"] == "NQ 06-26"
    assert payload["timeoutSeconds"] == 180
    assert record.total_templates == 12
    assert load_recipe_run(session).stage_id == "stage_1"


def test_start_recipe_stage_run_clears_stale_stage_artifacts(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)
    generate_recipe_stage_templates(session, stage_id="stage_1")

    stale_output = session.directory / "nt_output" / "stage_1" / "old_template" / "old_Optimization.csv"
    stale_output.parent.mkdir(parents=True, exist_ok=True)
    stale_output.write_text("stale", encoding="utf-8")
    stale_parsed = session.directory / "parsed_results" / "stage_1" / "selected.json"
    stale_parsed.parent.mkdir(parents=True, exist_ok=True)
    stale_parsed.write_text("[]", encoding="utf-8")
    root_selection = session.directory / "recipe_selection.json"
    root_selection.write_text("[]", encoding="utf-8")

    start_recipe_stage_run(session, stage_id="stage_1", command_file=tmp_path / "cmd.json")

    assert (session.directory / "nt_output" / "stage_1").exists()
    assert not stale_output.exists()
    assert not stale_parsed.exists()
    assert not root_selection.exists()


def test_start_recipe_stage_run_requires_stage_templates(tmp_path: Path):
    session = opt_session.create_session(strategy_id="FakeStrategy")

    with pytest.raises(RecipeRunnerError):
        start_recipe_stage_run(session, stage_id="stage_1", command_file=tmp_path / "cmd.json")


def test_start_recipe_stage_run_refuses_while_promoted_run_active(tmp_path: Path):
    # The promotion feature is optional/in-flight; skip if it isn't present.
    promo = pytest.importorskip("ta_foundation.web.optimizer_promotion_run")

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)
    generate_recipe_stage_templates(session, stage_id="stage_1")

    promo.save_promoted_run(session, promo.PromotedRunRecord(
        run_id="promoted_20260530_000000",
        state="running",
        started_at="2026-05-30T00:00:00",
    ))

    command_file = tmp_path / "cmd.json"
    with pytest.raises(RecipeRunnerError, match="promoted run is active"):
        start_recipe_stage_run(session, stage_id="stage_1", command_file=command_file)
    assert not command_file.exists()


def test_recipe_orchestrator_start_generates_stage_and_requests_run(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    command_file = tmp_path / "nt8_command.json"

    status = RecipeRunOrchestrator(session).start(command_file=command_file)

    state = status["state"]
    run = status["run"]
    assert state["state"] == "running_stage"
    assert state["current_stage_id"] == "stage_1"
    assert run["stage_id"] == "stage_1"
    assert run["total_templates"] == 12
    assert len(status["run_history"]) == 1
    assert status["timeline"][0]["stage_id"] == "stage_1"
    assert command_file.exists()
    assert (session.directory / "recipe_state.json").exists()
    assert (session.directory / "recipe_events.jsonl").exists()
    assert (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").exists()
    event_types = [event["event_type"] for event in status["events"]]
    assert "recipe_started" in event_types
    assert "stage_run_requested" in event_types


def test_recipe_status_reconciles_matching_nt_heartbeat_and_ignores_stale_run_id(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    command_file = tmp_path / "nt8_command.json"
    status_file = tmp_path / "nt8_status.json"

    orchestrator = RecipeRunOrchestrator(session)
    started = orchestrator.start(command_file=command_file, status_file=status_file)
    run_id = started["run"]["run_id"]

    status_file.write_text(
        json.dumps({
            "runId": "some_other_recipe_run",
            "state": "running",
            "completed": 99,
            "total": 99,
            "currentTemplate": "wrong",
        }),
        encoding="utf-8",
    )
    stale = orchestrator.status()
    assert stale["progress"]["source"] == "folder"
    assert stale["progress"]["completed_templates"] == 0

    status_file.write_text(
        json.dumps({
            "runId": run_id,
            "state": "running",
            "completed": 5,
            "total": 12,
            "currentTemplate": "stage_1__000006",
        }),
        encoding="utf-8",
    )
    live = orchestrator.status()

    assert live["progress"]["source"] == "heartbeat"
    assert live["progress"]["completed_templates"] == 5
    assert live["progress"]["total_templates"] == 12
    assert live["progress"]["current_template"] == "stage_1__000006"
    assert live["progress"]["run_state"] == "running"
    assert live["run"]["state"] == "running"
    assert live["run_history"][0]["state"] == "running"

    status_file.write_text(
        json.dumps({
            "runId": run_id,
            "state": "finished",
            "completed": 12,
            "total": 12,
            "currentTemplate": "",
        }),
        encoding="utf-8",
    )
    finished = orchestrator.status()

    assert finished["progress"]["complete"] is True
    assert finished["progress"]["run_state"] == "completed"
    assert finished["run"]["state"] == "completed"
    assert finished["run"]["finished_at"]
    assert load_recipe_run_history(session)[0].state == "completed"


def test_recipe_status_failed_nt_heartbeat_marks_recipe_failed(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    status_file = tmp_path / "nt8_status.json"

    orchestrator = RecipeRunOrchestrator(session)
    started = orchestrator.start(command_file=tmp_path / "nt8_command.json", status_file=status_file)
    run_id = started["run"]["run_id"]
    status_file.write_text(
        json.dumps({
            "runId": run_id,
            "state": "timedOut",
            "completed": 3,
            "total": 12,
            "lastError": "Timed out waiting for Strategy Analyzer.",
        }),
        encoding="utf-8",
    )

    failed = orchestrator.status()

    assert failed["state"]["state"] == "failed"
    assert failed["state"]["last_error"] == "Timed out waiting for Strategy Analyzer."
    assert failed["progress"]["run_state"] == "timed_out"
    assert failed["run"]["state"] == "timed_out"
    assert failed["run"]["finished_at"]
    assert "stage_run_failed" in [event["event_type"] for event in failed["events"]]


def test_recipe_status_marks_completed_stage_ready_for_results(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    save_recipe(session, payload)
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    template = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )["templates"][0]
    template_id = template["template_id"]
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    _write_optimization_csv(
        output_dir / f"{template_id}_Optimization.csv",
        [("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80)],
    )
    (output_dir / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    status = orchestrator.status()

    assert status["state"]["state"] == "waiting_for_results"
    assert status["progress"]["complete"] is True
    assert status["run"]["state"] == "completed"
    assert status["run"]["finished_at"]
    assert "stage_output_ready" in [event["event_type"] for event in load_recipe_events(session)]


def test_recipe_orchestrator_advance_once_ingests_selects_and_submits_child_stage(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    orchestrator = RecipeRunOrchestrator(session)
    start_status = orchestrator.start(command_file=tmp_path / "cmd.json")
    child_command_file = tmp_path / "child_cmd.json"

    for index, template in enumerate(json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )["templates"]):
        template_id = template["template_id"]
        output_dir = session.directory / "nt_output" / "stage_1" / template_id
        _write_optimization_csv(
            output_dir / f"{template_id}_Optimization.csv",
            [(f"{5 + index}/200/75 (averageFast averageSlow MaxStop)", 1.5 + index / 100, 3000 + index, 900, 80)],
        )
        (output_dir / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    status = orchestrator.advance_once(command_file=child_command_file)

    assert start_status["state"]["state"] == "running_stage"
    assert status["state"]["state"] == "running_stage"
    assert status["state"]["current_stage_id"] == "stage_2"
    assert status["run"]["stage_id"] == "stage_2"
    history = load_recipe_run_history(session)
    assert [run.stage_id for run in history] == ["stage_1", "stage_2"]
    assert history[0].state == "completed"
    assert len(status["timeline"]) == 2
    assert child_command_file.exists()
    command_payload = json.loads(child_command_file.read_text(encoding="utf-8"))
    assert command_payload["action"] == "RunBatch"
    assert command_payload["sourceFolder"].endswith("generated_templates\\stage_2")
    assert command_payload["destFolder"].endswith("nt_output\\stage_2")
    assert (session.directory / "parsed_results" / "stage_1" / "scored_rows.csv").exists()
    assert (session.directory / "parsed_results" / "stage_1" / "scored_rows.json").exists()
    assert (session.directory / "parsed_results" / "stage_1" / "selected.csv").exists()
    assert (session.directory / "parsed_results" / "stage_1" / "selected.json").exists()
    assert (session.directory / "parsed_results" / "stage_1" / "rejected.csv").exists()
    assert (session.directory / "parsed_results" / "stage_1" / "rejected.json").exists()
    assert (session.directory / "recipe_selection.csv").exists()
    assert (session.directory / "recipe_selection.json").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "waiting_for_results" in event_types
    assert "stage_output_complete" in event_types
    assert "ingesting_results" in event_types
    assert "selecting_candidates" in event_types
    assert "child_stage_pending" in event_types
    assert "child_stage_templates_generated" in event_types
    assert event_types.count("stage_run_requested") >= 2


def test_recipe_orchestrator_advance_once_waits_when_stage_output_is_incomplete(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    template_id = manifest["templates"][0]["template_id"]
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    _write_optimization_csv(
        output_dir / f"{template_id}_Optimization.csv",
        [("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80)],
    )
    (output_dir / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    status = orchestrator.advance_once()

    assert status["state"]["state"] == "waiting_for_results"
    assert status["state"]["current_stage_id"] == "stage_1"
    assert not (session.directory / "parsed_results" / "stage_1" / "selected.csv").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "waiting_for_results" in event_types
    assert "stage_output_complete" not in event_types


def test_stage_output_completion_ignores_partial_batch_run_summary(tmp_path: Path):
    """A chunked NT run can emit a BatchRunSummary.csv covering only some of the
    stage's templates. That partial summary must NOT be treated as the whole
    stage being complete, otherwise the recipe advances early and tries to
    dispatch the next stage onto a still-busy command bridge (the opt_3f40...
    refine wedge / Advance 500)."""
    from ta_foundation.web.optimizer_recipe_orchestrator import _stage_output_completion

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    build_and_save_recipe_plan(session)
    generate_recipe_stage_templates(session, stage_id="stage_1")

    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    expected = len(manifest["templates"])
    assert expected > 1

    output_dir = session.directory / "nt_output" / "stage_1"
    output_dir.mkdir(parents=True, exist_ok=True)
    header = (
        "Template,Status,Strategy,Instrument,Backtest start,Backtest end,"
        "Total net profit,Trades,Profit factor,Max drawdown,Run start time,"
        "Run end time,Output folder,Error\n"
    )

    def _write_batch_summary(rows: int) -> None:
        lines = [header]
        for i in range(rows):
            lines.append(f"tpl_{i},Completed,,,,,,,,,,,,\n")
        (output_dir / "BatchRunSummary.csv").write_text("".join(lines), encoding="utf-8")

    # Partial summary (all rows "Completed", but fewer than the stage expects).
    _write_batch_summary(expected - 1)
    partial = _stage_output_completion(session, stage_id="stage_1")
    assert partial["complete"] is False
    assert partial["reason"] == "incomplete"

    # Full-coverage summary is still honored.
    _write_batch_summary(expected)
    full = _stage_output_completion(session, stage_id="stage_1")
    assert full["complete"] is True
    assert full["reason"] == "batch_run_summary"


def test_recipe_orchestrator_recovers_ready_to_run_stage_on_next_advance(tmp_path: Path):
    """If an inline dispatch parks at ready_to_run_stage because the submit
    failed (e.g. the command bridge was busy), a later advance must retry the
    submit and move the stage to running rather than no-op forever."""
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    # Simulate the wedge: templates exist for stage_1, but state is parked at
    # ready_to_run_stage with no submitted run.
    state = load_recipe_state(session)
    state.state = "ready_to_run_stage"
    state.current_stage_id = "stage_1"
    from ta_foundation.web.optimizer_recipe_state import save_recipe_state

    save_recipe_state(session, state)

    status = orchestrator.advance_once(command_file=tmp_path / "retry_cmd.json")

    assert status["state"]["state"] == "running_stage"
    assert status["state"]["current_stage_id"] == "stage_1"
    assert (tmp_path / "retry_cmd.json").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "stage_run_requested" in event_types


def test_recipe_orchestrator_stops_when_no_candidates_pass_guardrails(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    payload["stages"][0]["selection"]["min_trades"] = 20
    save_recipe(session, payload)
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    template = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )["templates"][0]
    template_id = template["template_id"]
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    _write_optimization_csv(
        output_dir / f"{template_id}_Optimization.csv",
        [("5/200/75 (averageFast averageSlow MaxStop)", 1.0, 0, 0, 0)],
    )
    (output_dir / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    status = orchestrator.advance_once()

    assert status["state"]["state"] == "failed"
    assert "No candidates passed guardrails" in status["state"]["last_error"]
    assert (session.directory / "parsed_results" / "stage_1" / "scored_rows.json").exists()
    rejected = json.loads((session.directory / "parsed_results" / "stage_1" / "rejected.json").read_text(encoding="utf-8"))
    assert rejected[0]["rejection_reason"] == "below_min_trades"
    assert not (session.directory / "generated_templates" / "final_backtest").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "no_candidates_passed_guardrails" in event_types


def test_recipe_orchestrator_advances_child_stage_to_final_backtest_pending(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    payload["base_matrix"][0]["values"] = [0]
    payload["base_matrix"][2]["values"] = [False]
    payload["stages"][0]["selection"]["keep_per_group"] = 1
    save_recipe(session, payload)
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "stage1_cmd.json")

    stage1_template = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )["templates"][0]
    stage1_output = session.directory / "nt_output" / "stage_1" / stage1_template["template_id"]
    _write_optimization_csv(
        stage1_output / f"{stage1_template['template_id']}_Optimization.csv",
        [("5/200/75 (averageFast averageSlow MaxStop)", 1.8, 5000, 1200, 80)],
    )
    (stage1_output / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    stage2_status = orchestrator.advance_once(command_file=tmp_path / "stage2_cmd.json")
    assert stage2_status["state"]["state"] == "running_stage"
    assert stage2_status["state"]["current_stage_id"] == "stage_2"

    stage2_template = json.loads(
        (session.directory / "generated_templates" / "stage_2" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )["templates"][0]
    stage2_output = session.directory / "nt_output" / "stage_2" / stage2_template["template_id"]
    _write_optimization_csv(
        stage2_output / f"{stage2_template['template_id']}_Optimization.csv",
        [("6/225/100 (averageFast averageSlow MaxStop)", 2.2, 7000, 1000, 90)],
    )
    (stage2_output / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    final_status = orchestrator.advance_once()

    assert final_status["state"]["state"] == "ready_for_final_backtest"
    assert final_status["state"]["current_stage_id"] == "final_backtest"
    assert final_status["run"]["stage_id"] == "stage_2"
    assert final_status["run"]["state"] == "completed"
    assert final_status["run"]["finished_at"]
    assert (session.directory / "parsed_results" / "stage_2" / "selected.json").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "final_backtest_pending" in event_types

    running_final = orchestrator.advance_once(command_file=tmp_path / "final_cmd.json")

    assert running_final["state"]["state"] == "running_final_backtest"
    assert running_final["run"]["stage_id"] == "final_backtest"
    assert (tmp_path / "final_cmd.json").exists()
    final_manifest = session.directory / "generated_templates" / "final_backtest" / "recipe_template_manifest.json"
    assert final_manifest.exists()
    final_xmls = sorted((session.directory / "generated_templates" / "final_backtest").glob("*.xml"))
    assert len(final_xmls) == 2
    final_xml = final_xmls[0].read_text(encoding="utf-8")
    assert "<Category>Backtest</Category>" in final_xml
    assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in final_xml
    assert "<averageFast>6</averageFast>" in final_xml
    assert "<averageSlow>225</averageSlow>" in final_xml
    assert "<MaxStop>100</MaxStop>" in final_xml
    assert list((session.directory / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe").glob("*.xml"))

    final_template_ids = [
        item["template_id"]
        for item in json.loads(final_manifest.read_text(encoding="utf-8"))["templates"]
    ]
    for final_template_id in final_template_ids:
        final_output = session.directory / "nt_output" / "final_backtest" / final_template_id
        final_output.mkdir(parents=True, exist_ok=True)
        (final_output / "Summary.csv").write_text(
            """Performance,All trades,Long trades,Short trades
Total net profit,$1000.00,,
Profit factor,2.50,,
Max. drawdown,($250.00),,
Total # of trades,12,,
""",
            encoding="utf-8",
        )

    review_ready = orchestrator.advance_once()

    assert review_ready["state"]["state"] == "complete"
    assert review_ready["run"]["stage_id"] == "final_backtest"
    assert review_ready["run"]["state"] == "completed"
    assert review_ready["run"]["finished_at"]
    mirrored = session.directory / "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results" / "F_001"
    assert (mirrored / "Summary.csv").exists()
    review_dir = session.directory / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    assert (review_dir / "result_intake.csv").exists()
    assert (review_dir / "evaluated_candidates.json").exists()
    assert (review_dir / "recommendations.json").exists()
    assert (review_dir / "review_summary.json").exists()
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "final_backtest_review_complete" in event_types


def test_recipe_orchestrator_advance_once_respects_pause_and_stop_requests(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    save_recipe(session, _recipe_payload())
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    paused = orchestrator.pause()
    assert paused["state"]["pause_requested"] is True
    pause_advance = orchestrator.advance_once()
    assert pause_advance["state"]["state"] == "paused"
    assert not (session.directory / "parsed_results").exists()

    resumed = orchestrator.resume()
    assert resumed["state"]["state"] == "planned"
    stopped = orchestrator.stop()
    assert stopped["state"]["stop_requested"] is True
    assert stopped["run"]["state"] == "cancelled"
    assert stopped["run"]["finished_at"]
    assert not (tmp_path / "cmd.json").exists()
    stop_advance = orchestrator.advance_once()
    assert stop_advance["state"]["state"] == "stopped"
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert event_types.count("advance_blocked") >= 2


def test_recipe_orchestrator_pause_resume_stop_persist_state(tmp_path: Path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ 06-26",
    )
    save_recipe(session, _recipe_payload())
    orchestrator = RecipeRunOrchestrator(session)
    orchestrator.start(command_file=tmp_path / "cmd.json")

    paused = orchestrator.pause()
    assert paused["state"]["pause_requested"] is True
    assert paused["state"]["state"] == "running_stage"

    resumed = orchestrator.resume()
    assert resumed["state"]["pause_requested"] is False
    assert resumed["state"]["stop_requested"] is False

    stopped = orchestrator.stop()
    assert stopped["state"]["state"] == "stopped"
    assert stopped["state"]["stop_requested"] is True
    assert stopped["run"]["state"] == "cancelled"
    assert not (tmp_path / "cmd.json").exists()

    persisted = load_recipe_state(session)
    assert persisted.state == "stopped"
    event_types = [event["event_type"] for event in load_recipe_events(session)]
    assert "pause_requested" in event_types
    assert "resume_requested" in event_types
    assert "stop_requested" in event_types


def test_generate_recipe_child_stage_templates_uses_strategy_default_fallback_on_missing_parent(tmp_path: Path):
    from unittest.mock import patch
    from ta_foundation.web.optimizer_strategy_catalog import StrategyParameter, StrategyDetail

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "param_StartTimeH": 0,
                "param_Reverse": False,
                "param_averageFast": 5,
                "param_MaxStop": 75,
            }
        ]),
        encoding="utf-8",
    )

    mock_detail = StrategyDetail(
        strategy_id="FakeStrategy",
        cs_path="dummy.cs",
        parameters=[
            StrategyParameter(name="averageSlow", type_name="int", default=150, has_default=True)
        ],
        seed_templates=[],
        warnings=[]
    )

    with patch("ta_foundation.web.optimizer_recipe_templates.get_strategy_detail", return_value=mock_detail):
        child_written = generate_recipe_stage_templates(session, stage_id="stage_2")

    assert len(child_written) == 1
    child_xml = Path(child_written[0].path).read_text(encoding="utf-8")
    assert '<Increment>25</Increment><Max xsi:type="xsd:int">200</Max><Min xsi:type="xsd:int">100</Min><Name>averageSlow</Name>' in child_xml


def test_generate_recipe_child_stage_templates_uses_midpoint_fallback_on_missing_parent_and_missing_default(tmp_path: Path):
    from unittest.mock import patch

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "param_StartTimeH": 0,
                "param_Reverse": False,
                "param_averageFast": 5,
                "param_MaxStop": 75,
            }
        ]),
        encoding="utf-8",
    )

    with patch("ta_foundation.web.optimizer_recipe_templates.get_strategy_detail", return_value=None):
        child_written = generate_recipe_stage_templates(session, stage_id="stage_2")

    assert len(child_written) == 1
    child_xml = Path(child_written[0].path).read_text(encoding="utf-8")
    assert '<Increment>25</Increment><Max xsi:type="xsd:int">275</Max><Min xsi:type="xsd:int">175</Min><Name>averageSlow</Name>' in child_xml


def test_generate_recipe_child_stage_templates_fails_cleanly_on_empty_bounds(tmp_path: Path):
    from unittest.mock import patch

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ",
    )
    payload = _recipe_payload()
    save_recipe(session, payload)
    build_and_save_recipe_plan(session)

    selected_dir = session.directory / "parsed_results" / "stage_1"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "selected.json").write_text(
        json.dumps([
            {
                "candidate_id": "stage_1__base__row00001",
                "bucket_id": "base",
                "param_StartTimeH": 0,
                "param_Reverse": False,
                "param_averageFast": 5,
                "param_MaxStop": 75,
            }
        ]),
        encoding="utf-8",
    )

    from ta_foundation.web.optimizer_recipe_plan import RecipeSweep
    mock_sweep = [
        RecipeSweep(name="averageSlow", minimum=None, maximum=None, increment=25, step_count=1)
    ]

    with patch("ta_foundation.web.optimizer_recipe_templates._child_stage_sweeps", return_value=mock_sweep):
        with pytest.raises(RecipeTemplateWriteError) as exc_info:
            generate_recipe_stage_templates(session, stage_id="stage_2")
            
    assert "empty or invalid sweep bounds" in str(exc_info.value)
