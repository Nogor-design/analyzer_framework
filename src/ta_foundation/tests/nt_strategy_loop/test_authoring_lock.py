"""The overnight_range_fade renderer's lock exit (lca.exits "lock"), added 2026-10-03."""
import pytest

from ta_foundation.nt_strategy_loop.authoring import AuthoringError, StrategySpec, render_source


def test_overnight_range_fade_lock_exit_renders_and_is_validated() -> None:
    base = {"StopAtrMult": 2.0, "TargetAtrMult": 4.0, "TrailBars": 0, "TrailArmAtrMult": 2.0}
    spec = StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
        parameters={**base, "LockAtrMult": 1.0},
    )
    source = render_source(spec)
    # Default off: the parameter exists and nothing in the existing templates changes.
    assert "LockAtrMult = 1.0;" in source
    assert "private void AdvanceLock()" in source
    assert "else if (LockAtrMult > 0.0)" in source
    # lca.exits "lock": armed by the shared trigger, level rounded DOWN, never loosening.
    assert "bestTicks >= TrailArmAtrMult * tradeAtrTicks" in source
    assert "Math.Floor(LockAtrMult * tradeAtrTicks + LatticeTolerance)" in source
    assert "lockPrice > trailPrice : lockPrice < trailPrice" in source
    # The docstring bullet stays in the Python docstring, not in the C# header.
    assert "``LockAtrMult``" not in source
    off = render_source(StrategySpec(strategy_name="OnFadeUnit", family="overnight_range_fade",
                                     intent="unit test", parameters=base))
    assert "LockAtrMult = 0.0;" in off
    for bad, reason in (
        ({**base, "LockAtrMult": 1.0, "TrailBars": 10}, "different exit policies"),
        ({**base, "LockAtrMult": 2.0}, "below TrailArmAtrMult"),
        ({**base, "LockAtrMult": 1.0, "TrailArmAtrMult": 0.0}, "positive TrailArmAtrMult"),
        ({**base, "LockAtrMult": -0.5}, "cannot be negative"),
    ):
        with pytest.raises(AuthoringError, match=reason):
            render_source(StrategySpec(strategy_name="OnFadeUnit", family="overnight_range_fade",
                                       intent="unit test", parameters=bad))


def test_overnight_fade_orders_route_to_the_one_minute_series_under_standard_fills():
    """NinjaTrader allows High fill resolution only for single-series strategies.

    The strategy adds a one-minute series, so the one-minute fill model is
    realized by submitting every order on BarsInProgress 1 under Standard
    resolution (observed refusal 2026-10-04: "'High' Order Fill Resolution is
    only available for single-series strategies").
    """
    source = render_source(StrategySpec(
        strategy_name="OnFadeUnit", family="overnight_range_fade", intent="unit test",
        parameters={"StopAtrMult": 2.0, "TargetAtrMult": 4.0, "TrailBars": 0,
                    "TrailArmAtrMult": 2.0, "LockAtrMult": 1.0}))
    assert "OrderFillResolution = OrderFillResolution.Standard;" in source
    assert "OrderFillResolution.High" not in source
    assert "OrderFillResolutionType" not in source and "OrderFillResolutionValue" not in source
    assert 'EnterShort(1, Contracts, "OnFadeShort")' in source
    assert 'EnterLong(1, Contracts, "OnFadeLong")' in source
    assert "ExitLong(1, " in source and "ExitShort(1, " in source
    assert "AddDataSeries(BarsPeriodType.Minute, 1);" in source
