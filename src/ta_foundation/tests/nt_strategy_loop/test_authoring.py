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
