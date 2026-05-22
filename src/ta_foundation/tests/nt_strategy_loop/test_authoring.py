from __future__ import annotations

import pytest

from ta_foundation.nt_strategy_loop.authoring import (
    AuthoringError,
    StrategySpec,
    render_source,
    render_source_request,
)


def test_render_source_emits_strategy_class_and_property_attributes() -> None:
    spec = StrategySpec(
        strategy_name="LoopUnit",
        family="sma_cross_smoke",
        intent="unit test",
        parameters={"FastPeriod": 5, "SlowPeriod": 13, "Reverse": True},
    )

    source = render_source(spec)

    assert "public class LoopUnit : Strategy" in source
    assert 'Name = "LoopUnit"' in source
    assert "FastPeriod = 5" in source
    assert "SlowPeriod = 13" in source
    assert "Reverse = true" in source
    assert "[NinjaScriptProperty]" in source


def test_render_source_request_includes_family_and_intent() -> None:
    spec = StrategySpec(strategy_name="LoopUnit", family="sma_cross", intent="testing intent")
    body = render_source_request(spec)
    assert "LoopUnit" in body
    assert "sma_cross" in body
    assert "testing intent" in body


def test_render_source_rejects_unknown_family() -> None:
    spec = StrategySpec(strategy_name="X", family="not_registered", intent="")
    with pytest.raises(AuthoringError):
        render_source(spec)


def test_orb_failure_reclaim_renders_strategy_with_candidate_defaults() -> None:
    spec = StrategySpec(
        strategy_name="OrbUnit",
        family="orb_failure_reclaim",
        intent="unit test",
        parameters={
            "OrbMinutes": 5,
            "MinSweepTicks": 4.0,
            "MaxReclaimBars": 1,
            "TargetTicks": 150,
            "StopTicks": 20,
            "TradeDirection": 0,
        },
    )

    source = render_source(spec)

    assert "public class OrbUnit : Strategy" in source
    assert 'Name = "OrbUnit"' in source
    assert "OrbMinutes = 5;" in source
    assert "MinSweepTicks = 4.0;" in source
    assert "MaxReclaimBars = 1;" in source
    assert "TargetTicks = 150;" in source
    assert "StopTicks = 20;" in source
    assert "EnterShortLimit(0, true, 1, midPrice" in source
    assert "EnterLongLimit(0, true, 1, midPrice" in source
    assert "SetProfitTarget(CalculationMode.Ticks, TargetTicks)" in source
    assert "SetStopLoss(CalculationMode.Ticks, StopTicks)" in source


def test_orb_failure_reclaim_parameters_are_extractable_for_seed_template() -> None:
    from ta_foundation.nt_strategy_loop.seed_template import extract_strategy_parameters

    spec = StrategySpec(
        strategy_name="OrbUnit",
        family="orb_failure_reclaim",
        intent="unit test",
        parameters={"TargetTicks": 150, "StopTicks": 20},
    )
    parameters = extract_strategy_parameters(render_source(spec))

    by_name = {parameter.name: parameter for parameter in parameters}
    # Every exposed NinjaScriptProperty must be discoverable by the seed-template
    # writer so the optimizer template carries them.
    assert {"OrbMinutes", "TargetTicks", "StopTicks", "TradeDirection"} <= by_name.keys()
    # Only TargetTicks / StopTicks carry [Range] -> they are the swept params;
    # everything else stays fixed (min == max).
    assert by_name["TargetTicks"].minimum != by_name["TargetTicks"].maximum
    assert by_name["StopTicks"].minimum != by_name["StopTicks"].maximum
    assert by_name["OrbMinutes"].minimum == by_name["OrbMinutes"].maximum
    assert by_name["TradeDirection"].minimum == by_name["TradeDirection"].maximum
