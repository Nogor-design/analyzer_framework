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


def test_overnight_range_fade_renders_strategy_with_study_defaults() -> None:
    spec = StrategySpec(
        strategy_name="OnFadeUnit",
        family="overnight_range_fade",
        intent="unit test",
        parameters={
            "SessionOpenHour": 16,
            "RthOpenHour": 7,
            "RthOpenMinute": 30,
            "EntryWindowMinutes": 60,
            "StopAtrMult": 2.0,
            "TargetAtrMult": 4.0,
            "TrailBars": 0,
        },
    )

    source = render_source(spec)

    assert "public class OnFadeUnit : Strategy" in source
    assert 'Name = "OnFadeUnit"' in source
    assert "SessionOpenHour = 16;" in source
    assert "EntryWindowMinutes = 60;" in source
    assert "StopAtrMult = 2.0;" in source
    assert "TargetAtrMult = 4.0;" in source
    # Source population and horizon: 600 one-minute overnight bars, no extra
    # range-width filter, and the 240-row path truncation.
    assert "MinOvernightBars = 600;" in source
    assert "MinRangeTicks = 0;" in source
    assert "MaxHoldMinutes = 240;" in source
    # The bracket is sized per trade from the PRIOR bar's source Wilder ATR,
    # so it must not be a fixed SetStopLoss in State.Configure.
    assert "double priorAtr = hasAtr ? atrWilder : double.NaN;" in source
    assert "SetStopLoss(\"\", CalculationMode.Ticks, stopTicks, false)" in source
    assert "SetProfitTarget(\"\", CalculationMode.Ticks, targetTicks)" in source
    # Fade: a break of the overnight high is sold.
    # Orders go to the one-minute series: NinjaTrader allows High fill
    # resolution only on single-series strategies, so the one-minute fill
    # model is realized by submitting on BarsInProgress 1 under Standard.
    assert 'EnterShort(1, Contracts, "OnFadeShort")' in source
    assert 'EnterLong(1, Contracts, "OnFadeLong")' in source
    assert "OrderFillResolution = OrderFillResolution.Standard;" in source
    assert "OrderFillResolutionType" not in source
    assert "BarsPeriodType.Tick" not in source
    assert "State.Realtime" in source


def test_overnight_range_fade_resets_on_session_not_calendar_day() -> None:
    """The overnight window wraps midnight.

    A calendar-day reset -- which the ORB family correctly uses for a window
    that sits wholly inside one date -- would split the 16:01-07:30 range in
    half and latch a range built from six hours instead of fifteen. Session
    identity comes from the one-minute clock, not from whichever trading-hours
    template happens to drive ``Bars.IsFirstBarOfSession``.
    """
    spec = StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade",
        intent="unit test", parameters={},
    )
    source = render_source(spec)

    assert "AddDataSeries(BarsPeriodType.Minute, 1);" in source
    assert "Bars.IsFirstBarOfSession" not in source
    assert "Time[0].Date != currentDay" not in source
    assert "DateTime coverageStart = closeStamp.AddMinutes(-1);" in source
    # Wrap-safe minute indexing rather than a raw minute-of-day comparison.
    assert "MinutesSinceSessionOpen" in source
    assert "delta += 1440" in source


def test_overnight_range_fade_has_no_warmup_return_before_session_reset() -> None:
    """Audit defect 6: a warm-up return ahead of the reset could skip a session."""
    source = render_source(StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
    ))

    assert "BarsRequiredToTrade = 0;" in source
    assert "CurrentBar < BarsRequiredToTrade" not in source
    minute_bar = source[source.index("private void OnMinuteBar()"):]
    assert minute_bar.index("ResetSession(session)") < minute_bar.index("return")


def test_overnight_range_fade_consumes_first_break_before_position_test() -> None:
    """Audit defect 2: an occupied first break must be consumed, not re-armed."""
    source = render_source(StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
    ))
    body = source[source.index("private void EvaluateBreak("):source.index("// ---- source arithmetic")]

    consumed = body.index("firedUp = true;\n            else\n                firedDown = true;")
    assert consumed < body.index("MarketPosition.Flat")
    assert consumed < body.index('"suppressed_occupied"')
    assert consumed < body.index('"side_filtered"')


def test_overnight_range_fade_moves_legs_outward_onto_the_tick_lattice() -> None:
    """Audit defect 7: Math.Round could place an order tighter than the research."""
    source = render_source(StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
    ))

    assert "Math.Round" not in source
    assert "private const double LatticeTolerance = 1e-10;" in source
    assert "(int)Math.Ceiling(exactTicks - LatticeTolerance)" in source


def test_overnight_range_fade_trail_counts_minutes_and_arms_after_trigger() -> None:
    source = render_source(StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
        parameters={"TrailBars": 10, "TrailArmAtrMult": 2.0},
    ))

    assert "TrailBars = 10;" in source
    assert "TrailArmAtrMult = 2.0;" in source
    assert "bestTicks >= TrailArmAtrMult * tradeAtrTicks" in source
    assert "Lows[1][i]" in source and "Highs[1][i]" in source
    # The trail starts from the working stop, so it can never loosen it.
    assert "trailPrice = fillPrice - tradeDirection * pendingStopTicks * TickSize;" in source


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"RthOpenHour": 15}, "RthOpen < FlatBy < SessionOpen"),
        ({"FlatByHour": 17}, "RthOpen < FlatBy < SessionOpen"),
        ({"EntryWindowMinutes": 500}, "EntryWindowMinutes"),
        ({"EntryWindowMinutes": 0}, "EntryWindowMinutes"),
        ({"MinOvernightBars": 1000}, "MinOvernightBars"),
        ({"TrailBars": 0, "TargetAtrMult": 0.0}, "TargetAtrMult"),
        ({"StopAtrMult": 0.0}, "StopAtrMult"),
        ({"MaxHoldMinutes": 0}, "MaxHoldMinutes"),
        ({"BreakSide": 3}, "BreakSide"),
        ({"SessionOpenMinute": 75}, "not a minute"),
        ({"ExpectedTradingHoursName": 'CME "ETH"'}, "ExpectedTradingHoursName"),
    ],
)
def test_overnight_range_fade_rejects_inconsistent_specs(overrides, fragment) -> None:
    spec = StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade",
        intent="unit test", parameters=overrides,
    )
    with pytest.raises(AuthoringError, match=fragment):
        render_source(spec)


def test_overnight_range_fade_parameters_are_extractable_for_seed_template() -> None:
    from ta_foundation.nt_strategy_loop.seed_template import extract_strategy_parameters

    spec = StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade",
        intent="unit test", parameters={"StopAtrMult": 2.0, "TargetAtrMult": 4.0},
    )
    parameters = extract_strategy_parameters(render_source(spec))

    by_name = {parameter.name: parameter for parameter in parameters}
    assert {
        "SessionOpenHour", "RthOpenHour", "EntryWindowMinutes",
        "StopAtrMult", "TargetAtrMult", "TrailBars", "Reverse",
    } <= by_name.keys()
    # The ATR multiples must reach the optimizer as doubles; an int would
    # quantise the very sweep the study says matters.
    assert by_name["StopAtrMult"].type_name == "System.Double"
    assert by_name["TargetAtrMult"].type_name == "System.Double"
    assert by_name["Reverse"].type_name == "System.Boolean"
