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
    # Every parameter is pinned (min == max == default). The recipe planner
    # is the only thing that should introduce sweeps; auto-generated wide
    # ranges multiplied into hundreds of millions of combinations when NT
    # received the seed-derived template.
    for parameter in parameters:
        assert parameter.minimum == parameter.maximum == parameter.default


def test_cash_open_first_bar_follow_through_renders_fixed_reference_rule() -> None:
    spec = StrategySpec(
        strategy_name="YmFirstBarUnit",
        family="cash_open_first_bar_follow_through",
        intent="unit test",
        parameters={
            "CashOpenHour": 7,
            "CashOpenMinute": 30,
            "MinBodyTicks": 3,
            "TargetBodyMultiple": 2.0,
            "StopBodyMultiple": 1.0,
            "MaxBarsInTrade": 60,
        },
    )

    source = render_source(spec)

    assert "public class YmFirstBarUnit : Strategy" in source
    assert "OrderFillResolution = OrderFillResolution.High;" in source
    assert "(CashOpenHour * 60 + CashOpenMinute + 1) % (24 * 60)" in source
    assert "currentBarCloseMinute != signalBarCloseMinute" in source
    assert "double body = Close[0] - Open[0];" in source
    assert "bodyTicks * TargetBodyMultiple" in source
    assert 'EnterLong("FirstBarLong")' in source
    assert 'EnterShort("FirstBarShort")' in source


def test_cash_open_first_bar_parameters_are_extractable_and_pinned() -> None:
    from ta_foundation.nt_strategy_loop.seed_template import extract_strategy_parameters

    source = render_source(
        StrategySpec(
            strategy_name="YmFirstBarUnit",
            family="cash_open_first_bar_follow_through",
            intent="unit test",
        )
    )
    parameters = extract_strategy_parameters(source)
    by_name = {parameter.name: parameter for parameter in parameters}

    assert set(by_name) == {
        "CashOpenHour",
        "CashOpenMinute",
        "MinBodyTicks",
        "TargetBodyMultiple",
        "StopBodyMultiple",
        "MaxBarsInTrade",
    }
    for parameter in parameters:
        assert parameter.minimum == parameter.maximum == parameter.default
