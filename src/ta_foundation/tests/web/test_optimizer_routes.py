from __future__ import annotations

"""Flask test-client coverage for the /api/optimizer/* routes."""

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web import optimizer_strategy_catalog as catalog


PANTHEON_CS = """\
namespace NinjaTrader.NinjaScript.Strategies
{
    public class FakeStrategy : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                averageSlow = 200;
                MaxStop = 100;
            }
            else if (State == State.Configure) { }
        }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageSlow", GroupName = "Slow", Order = 1)]
        public int averageSlow { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "MaxStop", GroupName = "Risk", Order = 2)]
        public int MaxStop { get; set; }
    }
}
"""


@pytest.fixture
def fake_nt_install(tmp_path: Path, monkeypatch):
    source = tmp_path / "Strategies"
    templates = tmp_path / "templates" / "Strategy"
    source.mkdir(parents=True)
    templates.mkdir(parents=True)
    (source / "FakeStrategy.cs").write_text(PANTHEON_CS, encoding="utf-8")
    monkeypatch.setattr(catalog, "DEFAULT_STRATEGY_SOURCE_DIR", source)
    monkeypatch.setattr(catalog, "DEFAULT_STRATEGY_TEMPLATE_DIR", templates)
    return source, templates


@pytest.fixture
def client(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    opt_session.set_storage_root(None)


def test_get_strategies_lists_fake_strategy(client, fake_nt_install):
    res = client.get("/api/optimizer/strategies")
    assert res.status_code == 200
    body = res.get_json()
    ids = [s["strategy_id"] for s in body["strategies"]]
    assert "FakeStrategy" in ids


def test_get_strategy_detail_returns_parameters(client, fake_nt_install):
    res = client.get("/api/optimizer/strategies/FakeStrategy")
    assert res.status_code == 200
    body = res.get_json()
    names = [p["name"] for p in body["parameters"]]
    assert "averageSlow" in names and "MaxStop" in names


def test_get_unknown_strategy_404(client, fake_nt_install):
    res = client.get("/api/optimizer/strategies/Missing")
    assert res.status_code == 404


def test_resume_recipe_session_redirects_to_recipe_optimizer(client, tmp_path: Path):
    seed = tmp_path / "seed.xml"
    seed.write_text("<StrategyTemplate />", encoding="utf-8")
    session = opt_session.create_session(
        label="recipe",
        strategy_id="FakeStrategy",
        seed_template_path=str(seed),
        instrument="NQ 06-26",
    )
    (session.directory / "recipe.json").write_text("{}", encoding="utf-8")

    res = client.get(f"/optimizer/sessions/{session.id}/resume")

    assert res.status_code == 302
    assert res.headers["Location"] == "/optimizer/recipe"


def test_session_lifecycle_and_plan_preview(client, fake_nt_install):
    # Create
    res = client.post(
        "/api/optimizer/sessions",
        data=json.dumps({"label": "broad", "strategy_id": "FakeStrategy"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    sid = res.get_json()["session"]["session_id"]
    assert sid.startswith("opt_")

    # Patch with parameters + chunking
    res = client.patch(
        f"/api/optimizer/sessions/{sid}",
        data=json.dumps({
            "parameters": [
                {"name": "averageSlow", "type_name": "int", "mode": "optimize",
                 "minimum": 50, "maximum": 200, "increment": 50},
                {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
            ],
            "chunking": {"max_combinations_per_chunk": 100},
        }),
        content_type="application/json",
    )
    assert res.status_code == 200

    # Plan preview
    res = client.post(
        f"/api/optimizer/sessions/{sid}/plan/preview",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["combination_estimate"] == 4
    assert len(plan["chunks"]) == 1

    # GET round-trip returns the saved plan
    res = client.get(f"/api/optimizer/sessions/{sid}")
    assert res.status_code == 200
    body = res.get_json()
    assert body["plan"]["plan_hash"] == plan["plan_hash"]

    # List
    res = client.get("/api/optimizer/sessions")
    assert res.status_code == 200
    assert any(s["session_id"] == sid for s in res.get_json()["sessions"])

    # Delete
    res = client.delete(f"/api/optimizer/sessions/{sid}")
    assert res.status_code == 200
    assert res.get_json()["deleted"] is True


def test_optimizer_page_redirects_to_recipe(client, fake_nt_install):
    res = client.get("/optimizer", follow_redirects=False)
    assert res.status_code in (301, 302)
    assert res.headers["Location"].endswith("/optimizer/recipe")


SEED_XML_FOR_ROUTES = """<?xml version="1.0" encoding="utf-8"?>
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
      <averageSlow>100</averageSlow>
      <MaxStop>50</MaxStop>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
  <OptimizationParameters>
    <ArrayOfParameter>
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">100</Max>
        <Min xsi:type="xsd:int">100</Min>
        <Name>averageSlow</Name>
        <ValueSerializable>100</ValueSerializable>
      </Parameter>
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">50</Max>
        <Min xsi:type="xsd:int">50</Min>
        <Name>MaxStop</Name>
        <ValueSerializable>50</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</StrategyTemplate>
"""


def test_templates_generate_and_rename_round_trip(client, fake_nt_install, tmp_path, monkeypatch):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    # Create + configure a session
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy", "seed_template_path": str(seed_path),
    })
    sid = res.get_json()["session"]["session_id"]
    client.patch(f"/api/optimizer/sessions/{sid}", json={
        "parameters": [
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
            {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
        ],
        "chunking": {"max_combinations_per_chunk": 100},
    })
    client.post(f"/api/optimizer/sessions/{sid}/plan/preview", json={})

    # Generate templates
    res = client.post(f"/api/optimizer/sessions/{sid}/templates/generate", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert len(body["templates"]) == 1
    written_path = Path(body["templates"][0]["path"])
    assert written_path.exists()
    written_text = written_path.read_text(encoding="utf-8")
    assert "<MaxStop>100</MaxStop>" in written_text
    assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in written_text

    res = client.get(f"/api/optimizer/sessions/{sid}/preflight")
    assert res.status_code == 200
    preflight = res.get_json()["preflight"]
    assert "<BacktestType>" not in written_text
    assert "<Category>Optimize</Category>" in written_text
        
    assert preflight["ok"] is True
    assert preflight["resolved_instrument"] == "NQ 06-26"
    assert preflight["templates"][0]["instrument"] == "NQ 06-26"

    # Mock the template-namer subprocess so the route test stays hermetic
    from ta_foundation.web import optimizer_namer as namer_mod
    naming_dir = tmp_path / "fake_template_naming"
    naming_dir.mkdir()
    monkeypatch.setattr(namer_mod, "DEFAULT_TEMPLATE_NAMING_DIR", naming_dir)
    monkeypatch.setattr(namer_mod.shutil, "which", lambda _: None)

    def fake_run(cmd, cwd=None, capture_output=True, text=True, timeout=None):
        out_idx = cmd.index("--output-dir") + 1
        out_path = Path(cmd[out_idx])
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "RisingApolloBoltB-NQ.xml").write_text("<r/>", encoding="utf-8")

        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr(namer_mod.subprocess, "run", fake_run)

    res = client.post(f"/api/optimizer/sessions/{sid}/templates/rename", json={"market": "NQ"})
    assert res.status_code == 200
    result = res.get_json()["result"]
    assert result["returncode"] == 0
    assert any(p.endswith("RisingApolloBoltB-NQ.xml") for p in result["output_files"])


# ---------------------------------------------------------------------------
# Run bridge routes
# ---------------------------------------------------------------------------

def test_run_start_status_and_cancel(client, fake_nt_install, tmp_path, monkeypatch):
    # Reroute the IPC command file so the test never touches C:\temp.
    from ta_foundation.web import optimizer_runner as runner_mod

    fake_cmd = tmp_path / "fake_temp" / "nt8_command.json"
    monkeypatch.setattr(runner_mod, "DEFAULT_COMMAND_FILE", fake_cmd)

    # Create a session and drop a generated template manually (skip the full
    # plan-preview/generate path so the test stays focused on the run bridge).
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    gen_dir = session.directory / runner_mod.GENERATED_DIRNAME
    gen_dir.mkdir(parents=True, exist_ok=True)
    valid_xml = (
        "<StrategyTemplate>"
        "<BacktestType>Optimize</BacktestType>"
        "<Strategy>"
        "<FakeStrategy>"
        "<Category>Optimize</Category>"
        "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>"
        "</FakeStrategy>"
        "</Strategy>"
        "</StrategyTemplate>"
    )
    (gen_dir / "chunk_001.xml").write_text(valid_xml, encoding="utf-8")
    (gen_dir / "chunk_002.xml").write_text(valid_xml, encoding="utf-8")

    # Start
    res = client.post(f"/api/optimizer/sessions/{sid}/run")
    assert res.status_code == 200
    run = res.get_json()["run"]
    assert run["total_templates"] == 2
    assert run["state"] == "requested"
    assert fake_cmd.exists()
    payload = json.loads(fake_cmd.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["sourceFolder"].endswith("generated_templates")
    assert payload["destFolder"].endswith("nt_output")
    assert payload["instrument"] == "NQ 06-26"

    # Status: no output yet -> requested
    res = client.get(f"/api/optimizer/sessions/{sid}/run/status")
    assert res.status_code == 200
    status = res.get_json()["status"]
    assert status["state"] == "requested"
    assert status["total"] == 2
    assert status["completed"] == 0

    # Simulate AddOn finishing one template
    nt_out = session.directory / runner_mod.NT_OUTPUT_DIRNAME
    (nt_out / "chunk_001").mkdir(parents=True, exist_ok=True)
    (nt_out / "chunk_001" / "Summary.csv").write_text("p,a,l,s,\n", encoding="utf-8")
    (nt_out / "chunk_002").mkdir(parents=True, exist_ok=True)

    res = client.get(f"/api/optimizer/sessions/{sid}/run/status")
    status = res.get_json()["status"]
    assert status["state"] == "running"
    assert status["completed"] == 1
    assert status["current_template"] == "chunk_002"

    # Cancel
    res = client.post(f"/api/optimizer/sessions/{sid}/run/cancel")
    assert res.status_code == 200
    assert res.get_json()["run"]["state"] == "cancelled"
    assert not fake_cmd.exists()


def test_run_start_requires_generated_templates(client, fake_nt_install, tmp_path, monkeypatch):
    from ta_foundation.web import optimizer_runner as runner_mod
    monkeypatch.setattr(runner_mod, "DEFAULT_COMMAND_FILE", tmp_path / "nt8_command.json")

    res = client.post("/api/optimizer/sessions", json={"strategy_id": "FakeStrategy"})
    sid = res.get_json()["session"]["session_id"]

    res = client.post(f"/api/optimizer/sessions/{sid}/run")
    assert res.status_code == 400
    assert "generate" in res.get_json()["error"].lower()


def test_run_start_blocks_generic_generated_template_contract(client, fake_nt_install, tmp_path, monkeypatch):
    from ta_foundation.web import optimizer_runner as runner_mod
    monkeypatch.setattr(runner_mod, "DEFAULT_COMMAND_FILE", tmp_path / "nt8_command.json")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    gen_dir = session.directory / runner_mod.GENERATED_DIRNAME
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "chunk_001.xml").write_text(
        "<StrategyTemplate><Strategy><FakeStrategy>"
        "<InstrumentOrInstrumentList>NQ</InstrumentOrInstrumentList>"
        "</FakeStrategy></Strategy></StrategyTemplate>",
        encoding="utf-8",
    )

    res = client.post(f"/api/optimizer/sessions/{sid}/run")
    assert res.status_code == 400
    assert "preflight" in res.get_json()["error"].lower()


def test_run_status_returns_null_when_no_run(client, fake_nt_install):
    res = client.post("/api/optimizer/sessions", json={"strategy_id": "FakeStrategy"})
    sid = res.get_json()["session"]["session_id"]

    res = client.get(f"/api/optimizer/sessions/{sid}/run/status")
    assert res.status_code == 200
    assert res.get_json()["status"] is None


def test_run_cancel_404_when_no_run(client, fake_nt_install):
    res = client.post("/api/optimizer/sessions", json={"strategy_id": "FakeStrategy"})
    sid = res.get_json()["session"]["session_id"]

    res = client.post(f"/api/optimizer/sessions/{sid}/run/cancel")
    assert res.status_code == 404


def test_results_route_parses_optimization_exports(client, fake_nt_install):
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    out = session.directory / "nt_output" / "chunk_001"
    out.mkdir(parents=True, exist_ok=True)
    csv = (
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
        "NQ 06-26,2.5,100/50 (averageSlow MaxStop),1000,1500,-500,2.5,-250,12,60%,\n"
    )
    (out / "chunk_001_Optimization.csv").write_text(csv, encoding="utf-8")

    res = client.get(f"/api/optimizer/sessions/{sid}/results")
    assert res.status_code == 200
    results = res.get_json()["results"]
    assert results["row_count"] == 1
    assert results["batch_count"] == 1
    assert results["batches"][0]["successfully_parsed_rows"] == 1
    assert results["guardrail_rows"][0]["batch_id"] == "chunk_001"


def test_deployment_package_writes_decision_artifacts(client, fake_nt_install, tmp_path):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    client.patch(f"/api/optimizer/sessions/{sid}", json={
        "parameters": [
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 50, "increment": 1},
            {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
        ],
        "chunking": {"max_combinations_per_chunk": 100},
    })
    client.post(f"/api/optimizer/sessions/{sid}/plan/preview", json={})
    client.post(f"/api/optimizer/sessions/{sid}/templates/generate", json={})

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    out = session.directory / "nt_output" / "chunk_001"
    out.mkdir(parents=True, exist_ok=True)
    csv = (
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
        "NQ 06-26,2.5,100/50 (averageSlow MaxStop),1000,1500,-500,2.5,-250,12,60%,\n"
    )
    (out / "chunk_001_Optimization.csv").write_text(csv, encoding="utf-8")

    res = client.post(f"/api/optimizer/sessions/{sid}/deployment-package", json={"top_n": 5})
    assert res.status_code == 200
    package = res.get_json()["package"]
    assert package["decision_state"] == "needs_fixed_backtest_validation"
    summary = Path(package["summary_path"])
    manifest = Path(package["manifest_path"])
    assert summary.exists()
    assert manifest.exists()
    assert "fixed-template backtests" in summary.read_text(encoding="utf-8")
    assert (Path(package["package_dir"]) / "analysis" / "guardrail_candidates.csv").exists()


def test_recipe_stage_select_override(client, fake_nt_install, tmp_path, monkeypatch):
    from ta_foundation.web import optimizer_recipe_runner as recipe_runner_mod

    monkeypatch.setattr(recipe_runner_mod, "DEFAULT_COMMAND_FILE", tmp_path / "recipe_command.json")
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_override_routes",
        "recipe_name": "Override Route Recipe",
        "strategy_id": "FakeStrategy",
        "target_final_candidates": 1,
        "safety_caps": {"max_total_combinations": 100},
        "base_matrix": [
            {"param": "averageSlow", "role": "matrix_axis", "values": [100]},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "optimize_inside_template": {"MaxStop": {"min": 50, "max": 75, "step": 25}},
                "selection": {"group_by": ["averageSlow"], "keep_per_group": 1, "rank_by": "portfolio_score"},
            },
        ],
    }

    res = client.put(f"/api/optimizer/sessions/{sid}/recipe", json={"recipe": recipe})
    assert res.status_code == 200

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe")
    assert res.status_code == 200
    body = res.get_json()
    assert body["recipe"]["recipe_id"] == "rec_override_routes"
    assert body["plan"] is None

    res = client.post(f"/api/optimizer/sessions/{sid}/recipe/plan")
    assert res.status_code == 200

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe")
    assert res.status_code == 200
    assert res.get_json()["plan"]["template_count"] == 1

    res = client.post(f"/api/optimizer/sessions/{sid}/recipe/start")
    assert res.status_code == 200

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    template_id = manifest["templates"][0]["template_id"]
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{template_id}_Optimization.csv").write_text(
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
        "NQ 06-26,2.5,75 (MaxStop),1000,1500,-500,2.5,-250,12,60%,\n",
        encoding="utf-8",
    )
    (output_dir / "Summary.csv").write_text("Name,Value\nTemplate,Done\n", encoding="utf-8")

    # Perform POST to select override endpoint
    candidate_id = f"stage_1__{template_id}__row00001"
    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/stages/stage_1/select",
        json={"candidate_ids": [candidate_id]}
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "success"
    assert body["selected_count"] == 1
    assert body["rejected_count"] == 0

    # Verify files were written
    selected_path = session.directory / "parsed_results" / "stage_1" / "selected.json"
    assert selected_path.exists()
    selected_data = json.loads(selected_path.read_text(encoding="utf-8"))
    assert len(selected_data) == 1
    assert selected_data[0]["candidate_id"] == candidate_id
    assert selected_data[0]["selection_status"] == "selected"


def test_recipe_stage_results_route_returns_only_selected_rows_by_default(client, fake_nt_install, tmp_path, monkeypatch):
    from ta_foundation.web import optimizer_recipe_runner as recipe_runner_mod

    monkeypatch.setattr(recipe_runner_mod, "DEFAULT_COMMAND_FILE", tmp_path / "recipe_command.json")
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_route_selected_rows",
        "recipe_name": "Selected Rows Route",
        "strategy_id": "FakeStrategy",
        "target_final_candidates": 1,
        "safety_caps": {"max_total_combinations": 100},
        "base_matrix": [
            {"param": "averageSlow", "role": "matrix_axis", "values": [100]},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "optimize_inside_template": {"MaxStop": {"min": 50, "max": 75, "step": 25}},
                "selection": {
                    "group_by": ["averageSlow"],
                    "keep_per_group": 1,
                    "rank_by": "portfolio_score",
                    "min_trades": 10,
                    "min_profit_factor": 1.5,
                    "max_drawdown": 500,
                },
            },
        ],
    }

    assert client.put(f"/api/optimizer/sessions/{sid}/recipe", json={"recipe": recipe}).status_code == 200
    assert client.post(f"/api/optimizer/sessions/{sid}/recipe/plan").status_code == 200
    assert client.post(f"/api/optimizer/sessions/{sid}/recipe/start").status_code == 200

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    manifest = json.loads(
        (session.directory / "generated_templates" / "stage_1" / "recipe_template_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    template_id = manifest["templates"][0]["template_id"]
    output_dir = session.directory / "nt_output" / "stage_1" / template_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{template_id}_Optimization.csv").write_text(
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
        "NQ 06-26,2.5,75 (MaxStop),1000,1500,-500,2.5,-250,12,60%,\n"
        "NQ 06-26,1.2,50 (MaxStop),200,700,-500,1.2,-900,12,50%,\n",
        encoding="utf-8",
    )

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe/stages/stage_1/results")

    assert res.status_code == 200
    body = res.get_json()
    assert body["row_count"] == 2
    assert len(body["all_rows"]) == 2
    assert len(body["rows"]) == 1
    assert body["rows"][0]["profit_factor"] == 2.5
    assert body["rows"][0]["drawdown_abs"] == 250.0
    assert body["rejected_rows"][0]["rejection_reason"] in {
        "below_min_profit_factor;above_max_drawdown",
        "above_max_drawdown;below_min_profit_factor",
    }


def test_recipe_final_backtest_results_route_returns_parsed_rows_before_review(
    client, fake_nt_install, tmp_path, monkeypatch
):
    from ta_foundation.web import optimizer_recipe_runner as recipe_runner_mod

    monkeypatch.setattr(recipe_runner_mod, "DEFAULT_COMMAND_FILE", tmp_path / "recipe_command.json")
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_route_final_rows",
        "recipe_name": "Final Rows Route",
        "strategy_id": "FakeStrategy",
        "target_final_candidates": 1,
        "base_matrix": [
            {"param": "averageSlow", "role": "matrix_axis", "values": [100]},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "optimize_inside_template": {"MaxStop": {"min": 50, "max": 75, "step": 25}},
                "selection": {"group_by": ["averageSlow"], "keep_per_group": 1, "rank_by": "portfolio_score"},
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_1.selected_rows",
            },
        ],
    }

    assert client.put(f"/api/optimizer/sessions/{sid}/recipe", json={"recipe": recipe}).status_code == 200

    from ta_foundation.web.optimizer_session import get_session
    session = get_session(sid)
    output_dir = session.directory / "nt_output" / "final_backtest" / "final_backtest__F_001"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_backtest__F_001_Optimization.csv").write_text(
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
        "NQ 06-26,2.1,75 (MaxStop),1800,2300,-500,2.1,-300,25,60%,\n",
        encoding="utf-8",
    )

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe/stages/final_backtest/results")

    assert res.status_code == 200
    body = res.get_json()
    assert body["row_count"] == 1
    assert len(body["rows"]) == 1
    assert len(body["selected_rows"]) == 1
    assert body["rows"][0]["profit_factor"] == 2.1
    assert body["rows"][0]["drawdown_abs"] == 300.0


def test_recipe_final_backtest_results_route_keeps_workflow_available_without_results(
    client, fake_nt_install, tmp_path
):
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe/stages/final_backtest/results")

    assert res.status_code == 200
    body = res.get_json()
    assert body["stage_id"] == "final_backtest"
    assert body["row_count"] == 0
    assert body["selected_rows"] == []
    assert body["artifact_links"]["decision_dashboard"].endswith(
        f"/optimizer/sessions/{sid}/decision"
    )
    assert body["artifact_links"]["template_list"].endswith(
        f"/optimizer/sessions/{sid}/templates/final"
    )
    assert "not available yet" in body["notes"][0]



def test_send_to_final_rearms_completed_recipe(client, fake_nt_install, tmp_path: Path, monkeypatch):
    """Regression: clicking ``Send to Final`` on the Candidate Results page
    after a recipe has already finished must reset the orchestrator from
    ``complete`` back to ``ready_for_final_backtest`` so the very next
    advance call regenerates the final templates and dispatches them to
    NinjaTrader. Without this, the Run dashboard leaves every action button
    disabled and the operator's new selection is silently dropped on disk.
    """
    import json as _json
    from ta_foundation.web.optimizer_recipe_state import (
        RecipeRunState,
        load_recipe_state,
        save_recipe_state,
    )
    from ta_foundation.web.optimizer_recipe_results import PARSED_RESULTS_DIRNAME
    from ta_foundation.web.optimizer_session import get_session

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    session = get_session(sid)

    # Fabricate a finished stage_1 result with one candidate the test can
    # send to final, and put the recipe state in the post-completion shape
    # the user sees after a clean end-to-end run.
    stage_dir = session.directory / PARSED_RESULTS_DIRNAME / "stage_1"
    stage_dir.mkdir(parents=True)
    candidate = {
        "candidate_id": "stage_1_row1",
        "stage_id": "stage_1",
        "param_FastMa": 5,
        "profit_factor": 1.5,
    }
    (stage_dir / "scored_rows.json").write_text(
        _json.dumps([candidate]), encoding="utf-8",
    )
    save_recipe_state(session, RecipeRunState(
        recipe_id="rec_test",
        state="complete",
        current_stage_id="final_backtest",
    ))

    # Monkey-patch load_recipe_stage_results so the select handler sees the
    # fabricated candidate. The real implementation reaches into parquet/csv
    # ingest paths that aren't relevant to the rearm assertion.
    from ta_foundation.web import optimizer_recipe_results as recipe_results_mod

    class _FakeResults:
        def __init__(self, rows): self.rows = rows
    monkeypatch.setattr(
        recipe_results_mod,
        "load_recipe_stage_results",
        lambda session, *, stage_id: _FakeResults([candidate]),
    )

    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/stages/stage_1/select",
        json={"candidate_ids": ["stage_1_row1"], "intent": "final"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["rearmed_final_backtest"] is True
    assert body["selected_count"] == 1

    state = load_recipe_state(session)
    assert state.state == "ready_for_final_backtest"
    assert state.current_stage_id == "final_backtest"
    assert state.pause_requested is False
    assert state.stop_requested is False

    # And the new final selection is on disk where the orchestrator looks.
    final_selected = session.directory / PARSED_RESULTS_DIRNAME / "stage_1" / "final_selected.json"
    assert final_selected.exists()
    payload = _json.loads(final_selected.read_text(encoding="utf-8"))
    assert payload[0]["candidate_id"] == "stage_1_row1"
    assert payload[0]["selection_status"] == "selected"


def test_rearm_stage_jumps_state_machine_without_resetting_prior_stages(
    client, fake_nt_install, tmp_path: Path,
):
    """Regression: after a recipe completes the operator may add a refinement
    stage and want to launch it without re-running the earlier ones. The
    rearm endpoint moves the orchestrator state directly to the new stage
    so the next Advance call generates the new child-stage templates from
    the saved selection instead of restarting from stage_1.
    """
    import json as _json
    from ta_foundation.web.optimizer_recipe_state import (
        RecipeRunState,
        load_recipe_state,
        save_recipe_state,
    )
    from ta_foundation.web.optimizer_recipe_plan import RECIPE_PLAN_FILENAME
    from ta_foundation.web.optimizer_session import get_session

    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML_FOR_ROUTES, encoding="utf-8")

    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    session = get_session(sid)

    # Save a minimal valid recipe via the public API so the orchestrator can
    # load it during the rearm call.
    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_refine",
        "recipe_name": "refine",
        "strategy_id": "FakeStrategy",
        "base_matrix": [],
        "stages": [
            {"stage_id": "stage_1", "stage_type": "optimizer"},
            {"stage_id": "stage_2", "stage_type": "optimizer", "from": "stage_1.selected_rows"},
            {"stage_id": "final_backtest", "stage_type": "fixed_backtest", "from": "stage_2.selected_rows"},
        ],
    }
    res = client.put(
        f"/api/optimizer/sessions/{sid}/recipe",
        json={"recipe": recipe},
    )
    assert res.status_code == 200

    # Write a plan file directly so we don't depend on the heavy plan
    # builder. The endpoint only inspects stage_type and from.
    (session.directory / RECIPE_PLAN_FILENAME).write_text(
        _json.dumps({"stages": [
            {"stage_id": "stage_1", "stage_type": "optimizer"},
            {"stage_id": "stage_2", "stage_type": "optimizer", "from": "stage_1.selected_rows"},
            {"stage_id": "final_backtest", "stage_type": "fixed_backtest", "from": "stage_2.selected_rows"},
        ]}),
        encoding="utf-8",
    )

    save_recipe_state(session, RecipeRunState(
        recipe_id="rec_refine",
        state="complete",
        current_stage_id="final_backtest",
    ))

    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/rearm-stage",
        json={"stage_id": "stage_2"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["new_state"] == "generating_child_stage"
    assert body["previous_state"] == "complete"
    assert body["stage_id"] == "stage_2"

    state = load_recipe_state(session)
    assert state.state == "generating_child_stage"
    assert state.current_stage_id == "stage_2"
    assert state.pause_requested is False
    assert state.stop_requested is False

    # final_backtest also rearms (different state value).
    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/rearm-stage",
        json={"stage_id": "final_backtest"},
    )
    assert res.status_code == 200
    assert res.get_json()["new_state"] == "ready_for_final_backtest"
    state = load_recipe_state(session)
    assert state.state == "ready_for_final_backtest"
    assert state.current_stage_id == "final_backtest"

    # Root optimizer stage cannot be rearmed in isolation — caller must
    # use Start Recipe (which restarts the whole pipeline).
    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/rearm-stage",
        json={"stage_id": "stage_1"},
    )
    assert res.status_code == 400
    assert "root optimizer stage" in res.get_json()["error"]

    # Missing / unknown stages return the appropriate errors.
    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/rearm-stage",
        json={"stage_id": "stage_999"},
    )
    assert res.status_code == 404
    res = client.post(
        f"/api/optimizer/sessions/{sid}/recipe/rearm-stage",
        json={},
    )
    assert res.status_code == 400
