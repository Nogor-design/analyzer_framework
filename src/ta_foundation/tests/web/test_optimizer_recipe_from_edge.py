"""
Tests for discovery → recipe routing (build_confirmation_recipe / seed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.analysis.strategy_discovery.edge_spec import EdgeSpec
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_recipe import OptimizerRecipeDocument, load_recipe, save_recipe
from ta_foundation.web.optimizer_recipe_from_edge import (
    CONFIRMATION_STRATEGY_ID,
    build_confirmation_recipe,
    build_confirmation_seed_xml,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path)
    yield
    opt_session.set_storage_root(None)


def _edge(direction=1, stop=24, target=36, regime=None):
    return EdgeSpec(
        structure="engulfing_bullish",
        entry_signal="EngulfingBullish",
        timeframe_minutes=5,
        timing_mode="next_open",
        direction=direction,
        stop_ticks=stop,
        target_ticks=target,
        regime_mode=regime,
        observed_pf=1.62,
        observed_win_rate=0.58,
        observed_n=140,
        rule_str="structure == engulfing_bullish",
    )


def test_recipe_targets_strategy_discovery_filter():
    rec = build_confirmation_recipe(_edge())
    assert isinstance(rec, OptimizerRecipeDocument)
    assert rec.strategy_id == CONFIRMATION_STRATEGY_ID
    assert [s.stage_id for s in rec.stages] == ["confirm", "final_backtest"]
    assert rec.active_targets == ("MaxProfitFactor",)


def test_direction_pins_allow_flags():
    rec_long = build_confirmation_recipe(_edge(direction=1))
    flags = {m.param: m.value for m in rec_long.base_matrix if m.role == "fixed"}
    assert flags["AllowLong"] is True and flags["AllowShort"] is False

    rec_short = build_confirmation_recipe(_edge(direction=-1))
    flags = {m.param: m.value for m in rec_short.base_matrix if m.role == "fixed"}
    assert flags["AllowLong"] is False and flags["AllowShort"] is True

    rec_both = build_confirmation_recipe(_edge(direction=0))
    flags = {m.param: m.value for m in rec_both.base_matrix if m.role == "fixed"}
    assert flags["AllowLong"] is True and flags["AllowShort"] is True


def test_stop_target_sweep_centered_on_discovery():
    rec = build_confirmation_recipe(_edge(stop=24, target=36), stop_radius_ticks=8, step_ticks=4)
    opt = rec.stages[0].optimize_inside_template
    assert opt["StopTicks"] == {"min": 16, "max": 32, "step": 4}
    assert opt["TargetTicks"] == {"min": 28, "max": 44, "step": 4}


def test_stop_floor_never_below_four():
    rec = build_confirmation_recipe(_edge(stop=6, target=8), stop_radius_ticks=8)
    opt = rec.stages[0].optimize_inside_template
    assert opt["StopTicks"]["min"] == 4
    assert opt["TargetTicks"]["min"] == 4


def test_regime_pinned_when_present():
    rec = build_confirmation_recipe(_edge(regime="TrendingUp"))
    fixed = {m.param: m.value for m in rec.base_matrix if m.role == "fixed"}
    assert fixed.get("RegimeMode") == "TrendingUp"


def test_recipe_round_trips_through_session():
    session = opt_session.create_session(strategy_id=CONFIRMATION_STRATEGY_ID)
    rec = build_confirmation_recipe(_edge())
    save_recipe(session, rec)
    loaded = load_recipe(session)
    assert loaded.recipe_id == rec.recipe_id
    assert loaded.strategy_id == CONFIRMATION_STRATEGY_ID
    assert loaded.stages[0].optimize_inside_template["StopTicks"]["min"] == 16


def test_seed_xml_carries_entry_and_timeframe():
    xml = build_confirmation_seed_xml(_edge())
    assert "<EntrySignal>EngulfingBullish</EntrySignal>" in xml
    assert "<BaseBarsPeriodValue>5</BaseBarsPeriodValue>" in xml
    assert "<TimingMode>NextOpen</TimingMode>" in xml
