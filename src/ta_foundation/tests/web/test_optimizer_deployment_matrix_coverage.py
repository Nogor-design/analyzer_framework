from __future__ import annotations

"""Coverage-view: build a 252-cell manifest from a session's final-backtest
outputs, classify + name via the canonical rules, and serve page + JSON."""

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_deployment_matrix_session import (
    build_session_deployment_manifest,
    session_final_rows,
)
from ta_foundation.web.optimizer_deployment_matrix_manifest import write_manifest


def _final_template_xml(*, start_h, start_m, avg_fast, avg_slow, reverse, max_trades,
                        profit_stop, loss_stop, max_stop, max_tp_ratio, long_, short_):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate><Strategy><PantheonMasterBotV01TesterV2>
<StartTimeH>{start_h}</StartTimeH><StartTimeM>{start_m}</StartTimeM>
<DurationTimeH>4</DurationTimeH><DurationTimeM>0</DurationTimeM>
<averageFast>{avg_fast}</averageFast><averageSlow>{avg_slow}</averageSlow>
<MaxTrades>{max_trades}</MaxTrades><ProfitStop>{profit_stop}</ProfitStop>
<MaxStop>{max_stop}</MaxStop><LossStop>{loss_stop}</LossStop><MaxTPRatio>{max_tp_ratio}</MaxTPRatio>
<Long>{str(long_).lower()}</Long><Short>{str(short_).lower()}</Short><Reverse>{str(reverse).lower()}</Reverse>
<BotName>Old</BotName></PantheonMasterBotV01TesterV2></Strategy></StrategyTemplate>"""


def _make_session_with_finals(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    seed = tmp_path / "seed.xml"
    seed.write_text("<StrategyTemplate />", encoding="utf-8")
    session = opt_session.create_session(
        label="dm", strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path=str(seed), instrument="NQ 06-26", market_suffix="NQ",
    )
    base = session.directory / "deployment_package" / "final_backtest_handoff"
    tdir = base / "renamed_backtest_templates"
    rdir = base / "final_backtest_review"
    tdir.mkdir(parents=True)
    rdir.mkdir(parents=True)

    # F_001: London Early, MaxTrades=2 but $500 brackets == stops -> effectively SINGLE.
    f1 = tdir / "F1.xml"
    f1.write_text(_final_template_xml(start_h=0, start_m=0, avg_fast=5, avg_slow=40, reverse=False,
                  max_trades=2, profit_stop=500, loss_stop=500, max_stop=100, max_tp_ratio=1.0,
                  long_=True, short_=True), encoding="utf-8")
    # F_002: London Early, roomy stops -> genuinely MULTI.
    f2 = tdir / "F2.xml"
    f2.write_text(_final_template_xml(start_h=0, start_m=0, avg_fast=5, avg_slow=40, reverse=False,
                  max_trades=5, profit_stop=9000, loss_stop=9000, max_stop=100, max_tp_ratio=1.0,
                  long_=True, short_=True), encoding="utf-8")

    (tdir / "renamed_template_index.json").write_text(json.dumps({
        "market": "NQ",
        "templates": {
            "F_001": {"run_id": "F_001", "semantic_name": "x.xml", "renamed_path": str(f1)},
            "F_002": {"run_id": "F_002", "semantic_name": "y.xml", "renamed_path": str(f2)},
        },
    }), encoding="utf-8")
    (rdir / "evaluated_candidates.json").write_text(json.dumps({
        "rows": [
            {"run_id": "F_001", "profit_factor": 1.9, "total_net_profit": 1000, "trades": 12},
            {"run_id": "F_002", "profit_factor": 2.3, "total_net_profit": 2000, "trades": 30},
        ]
    }), encoding="utf-8")
    return session


@pytest.fixture
def client(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    opt_session.set_storage_root(None)


def test_session_manifest_classifies_effective_single_and_multi(tmp_path: Path):
    session = _make_session_with_finals(tmp_path)
    rows = session_final_rows(session)
    assert len(rows) == 2
    assert {r["run_id"] for r in rows} == {"F_001", "F_002"}
    manifest = build_session_deployment_manifest(session, with_features=True)
    assert manifest["total"] == 252
    covered = {(c["single_multi"]): c for c in manifest["cells"] if c["status"] == "covered"}
    # F_001 (guardrail-capped) -> single cell; F_002 (roomy) -> multi cell.
    assert "single" in covered and "multi" in covered
    assert covered["single"]["session"] == "London Early" and covered["single"]["tier_index"] == 1
    # The name's single/multi agrees with the cell axis.
    assert covered["single"]["name"].startswith("Rise") and "Hermes" in covered["single"]["name"]
    assert covered["multi"]["name"].startswith("Rising")
    assert covered["single"]["run_id"] == "F_001"
    assert "features" in covered["single"]
    assert covered["single"]["features"]["prop_max_daily_loss"] == 999.0
    assert covered["single"]["features"]["effective_trades"] == 1
    opt_session.set_storage_root(None)


def test_session_manifest_features_survive_missing_trades_and_csv_write(tmp_path: Path):
    session = _make_session_with_finals(tmp_path)
    manifest = build_session_deployment_manifest(session, with_features=True)
    covered = [c for c in manifest["cells"] if c["status"] == "covered"]
    assert covered
    assert all("features" in c for c in covered)
    assert all(c["features"]["best_policy"] is None for c in covered)

    paths = write_manifest(manifest, tmp_path / "manifest_out")
    text = paths["csv"].read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert "features.prop_max_daily_loss" in header
    assert "features.effective_trades" in header
    with paths["json"].open(encoding="utf-8") as f:
        assert json.load(f) == manifest
    opt_session.set_storage_root(None)


def test_coverage_page_and_manifest_api(tmp_path: Path):
    session = _make_session_with_finals(tmp_path)
    sid = session.id
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        page = c.get(f"/optimizer/sessions/{sid}/deployment-matrix/coverage")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "252-cell coverage" in body
        assert "God Coverage" in body and "covered 2" in body

        api = c.get(f"/api/optimizer/sessions/{sid}/deployment-matrix/manifest?write=1")
        assert api.status_code == 200
        data = api.get_json()
        assert data["total"] == 252
        assert data["covered"] == 2
        assert "written" in data
        assert (session.directory / "deployment_matrix" / "deployment_matrix_manifest.json").exists()
    opt_session.set_storage_root(None)
