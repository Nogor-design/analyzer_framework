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
