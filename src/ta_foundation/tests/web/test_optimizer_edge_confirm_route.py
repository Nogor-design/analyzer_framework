from __future__ import annotations

"""
Coverage for the discovery → confirmation-run trigger:
prepare_confirmation_session and the /api/optimizer/edge-confirm/run route.

Uses a fake NinjaTrader install (the real StrategyDiscoveryFilter.cs copied in)
so seed regeneration + plan building run end-to-end WITHOUT real NinjaTrader.
start=False throughout, so nothing is ever dispatched to NT.
"""

import shutil
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web import optimizer_strategy_catalog as catalog
from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.web.optimizer_recipe_from_edge import (
    CONFIRMATION_STRATEGY_ID,
    prepare_confirmation_session,
)
from ta_foundation.analysis.strategy_discovery.edge_spec import EdgeSpec


_REAL_CS = (
    Path(__file__).resolve().parents[2]
    / "strategies" / "StrategyDiscoveryFilter" / "StrategyDiscoveryFilter.cs"
)


@pytest.fixture
def fake_nt_install(tmp_path: Path, monkeypatch):
    source = tmp_path / "Strategies"
    templates = tmp_path / "templates" / "Strategy"
    source.mkdir(parents=True)
    templates.mkdir(parents=True)
    shutil.copy(_REAL_CS, source / "StrategyDiscoveryFilter.cs")
    monkeypatch.setattr(catalog, "DEFAULT_STRATEGY_SOURCE_DIR", source)
    monkeypatch.setattr(catalog, "DEFAULT_STRATEGY_TEMPLATE_DIR", templates)
    return source, templates


@pytest.fixture
def storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    yield
    opt_session.set_storage_root(None)


@pytest.fixture
def client(storage):
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _edge():
    return EdgeSpec(
        structure="engulfing_bullish",
        entry_signal="EngulfingBullish",
        timeframe_minutes=5,
        timing_mode="next_open",
        direction=1,
        stop_ticks=24,
        target_ticks=36,
        observed_pf=1.62,
        observed_win_rate=0.58,
        observed_n=140,
        rule_str="structure == engulfing_bullish",
    )


def test_prepare_session_dry_run_builds_everything(fake_nt_install, storage):
    prepared = prepare_confirmation_session(_edge(), instrument="NQ 06-26", start=False)
    assert prepared["started"] is False
    assert prepared["status"] is None

    seed_text = Path(prepared["seed_path"]).read_text(encoding="utf-8")
    # Timeframe patched into the seed data series.
    assert "<BaseBarsPeriodValue>5</BaseBarsPeriodValue>" in seed_text

    # Recipe persisted and pins the discovered entry.
    recipe = load_recipe(prepared["session"])
    fixed = {m.param: m.value for m in recipe.base_matrix if m.role == "fixed"}
    assert fixed["EntrySignal"] == "EngulfingBullish"
    assert fixed["AllowLong"] is True and fixed["AllowShort"] is False
    # Stop/target are swept, not pinned.
    assert "StopTicks" not in fixed and "TargetTicks" not in fixed
    opt = recipe.stages[0].optimize_inside_template
    assert opt["StopTicks"] == {"min": 16, "max": 32, "step": 4}


def test_route_dry_run_returns_plan_without_dispatch(client, fake_nt_install):
    res = client.post("/api/optimizer/edge-confirm/run", json={"edge": _edge().to_dict()})
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["started"] is False
    assert body["status"] is None
    assert body["recipe"]["strategy_id"] == CONFIRMATION_STRATEGY_ID
    assert "NOT started" in body["note"]
    assert body["urls"]["resume"].endswith("/resume")


def test_route_from_discovery_payload(client, fake_nt_install):
    sd = {
        "signal_entry_discovery": {
            "top_signal_rules": [
                {
                    "rule_str": "structure == large_body",
                    "conditions": [{"column": "structure", "value": "large_body"}],
                    "n_signals": 90,
                }
            ]
        },
        "signal_exit_sweep": {"overall_best": {"stop": 20, "target": 30, "avg_profit_factor": 1.4}},
    }
    res = client.post(
        "/api/optimizer/edge-confirm/run",
        json={"discovery": sd, "timeframe_minutes": 1, "instrument": "NQ 06-26"},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["edge"]["entry_signal"] == "LargeBody"
    assert body["started"] is False


def test_route_rejects_payload_without_edge(client, fake_nt_install):
    res = client.post("/api/optimizer/edge-confirm/run", json={"discovery": {"signal_entry_discovery": {"top_signal_rules": []}}})
    assert res.status_code == 400
    assert "confirmable edge" in res.get_json()["error"]
