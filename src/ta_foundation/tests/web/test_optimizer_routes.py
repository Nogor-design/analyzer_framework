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


def test_optimizer_page_renders_and_sets_cookie(client, fake_nt_install):
    res = client.get("/optimizer")
    assert res.status_code == 200
    assert b"Optimizer" in res.data
    cookies = res.headers.getlist("Set-Cookie")
    assert any("ta_optimizer_session_id=" in c for c in cookies)


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
    (gen_dir / "chunk_001.xml").write_text("<x/>", encoding="utf-8")
    (gen_dir / "chunk_002.xml").write_text("<x/>", encoding="utf-8")

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
