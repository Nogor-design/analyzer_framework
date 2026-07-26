from __future__ import annotations

"""Flask test-client coverage for the deployment-matrix launcher routes."""

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session


@pytest.fixture
def client(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    opt_session.set_storage_root(None)


def test_deployment_matrix_page_renders(client):
    res = client.get("/optimizer/deployment-matrix")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "Deployment Matrix" in body
    assert "Build &amp; preview (no NT)" in body


def test_preview_reports_252_cells(client):
    res = client.get("/api/optimizer/deployment-matrix/preview")
    assert res.status_code == 200
    data = res.get_json()
    assert data["counts"]["sessions"] == 7
    assert data["counts"]["tiers"] == 9
    assert data["counts"]["root_lanes"] == 7 * 2 * 9  # 126
    assert data["counts"]["final_cells"] == 252
    assert len(data["slow_ma_values"]) == 9


def test_run_validates_inputs(client):
    assert client.post("/api/optimizer/deployment-matrix/run", json={}).status_code == 400
    res = client.post("/api/optimizer/deployment-matrix/run", json={"strategy_id": "X"})
    assert res.status_code == 400  # missing seed


def test_run_is_safe_by_default_no_dispatch(client, tmp_path: Path):
    # A stub seed is enough for save_recipe + plan (the safe path does not dispatch).
    seed = tmp_path / "seed.xml"
    seed.write_text("<NinjaTrader><StrategyTemplate /></NinjaTrader>", encoding="utf-8")

    res = client.post(
        "/api/optimizer/deployment-matrix/run",
        json={
            "strategy_id": "PantheonMasterBotV01TesterV2",
            "seed_template_path": str(seed),
            "instrument": "NQ 06-26",
            # keep the inner sweeps tiny so the plan builds fast
            "max_stop_min": 50, "max_stop_max": 50, "max_stop_step": 50,
            "max_tp_ratio_min": 1.0, "max_tp_ratio_max": 1.0, "max_tp_ratio_step": 1.0,
            "max_trades_values": [1, 3],
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data["dispatched"] is False
    assert data["status"] is None
    plan = data["plan"]
    # 7 sessions x 2 reverse x 9 tiers = 126 root lanes
    assert plan["template_count"] == 126
    assert [s["stage_id"] for s in plan["stages"]] == ["stage_1", "refine_risk", "final_backtest"]
    # the session + recipe were persisted
    sid = data["session"]["session_id"]
    assert (opt_session.get_storage_root() / sid / "recipe.json").exists()
