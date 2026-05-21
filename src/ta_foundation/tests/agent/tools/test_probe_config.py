"""Tests for the per-family probe-config builder (Phase 0 defect #9)."""

from __future__ import annotations

import pytest

from ta_foundation.agent.tools.probe_config import (
    ConfigBuildError,
    build_probe_config,
    has_config_builder,
)


def _orb_params(**overrides) -> dict:
    base = {
        "fill_mode": "body_midpoint",
        "orb_minutes": 5,
        "sweep_min_ticks": 4,
        "reclaim_within_bars": 1,
        "stop_ticks": 20,
        "target_ticks": 150,
    }
    base.update(overrides)
    return base


def _build(**overrides) -> dict:
    kw = {
        "hypothesis_id": "h_test_001",
        "family": "orb_failure_reclaim",
        "instrument": "NQ",
        "timeframe": "1m",
        "session_window": "ny_open_0730_1000_denver",
        "direction": "both",
        "params": _orb_params(),
    }
    kw.update(overrides)
    return build_probe_config(**kw)


# ---- has_config_builder ----------------------------------------------------


def test_has_config_builder() -> None:
    assert has_config_builder("orb_failure_reclaim") is True
    assert has_config_builder("vwap_reject_fade") is False


# ---- happy path ------------------------------------------------------------


def test_orb_build_is_runnable_and_substitutes_params() -> None:
    cfg = _build(params=_orb_params(orb_minutes=15, sweep_min_ticks=8,
                                    reclaim_within_bars=3, stop_ticks=24,
                                    target_ticks=90))
    # A runnable config has a discovery block + the orb_discovery sweep config.
    assert cfg["discovery"]["stage"]
    assert cfg["discovery"]["instrument"] == "NQ"
    orb = cfg["orb_discovery"]["orb"]
    assert orb["orb_minutes"] == [15]
    assert orb["min_sweep_ticks"] == [8.0]
    assert orb["max_reclaim_bars"] == [3]
    ticks = cfg["orb_discovery"]["outcome"]["ticks"]
    assert ticks["take_profit"] == [90]
    assert ticks["stop"] == [24]
    # report block names the hypothesis.
    assert cfg["report"]["output_filename"] == "h_test_001.html"
    # hardening stays enabled so the probe yields dev/oos metrics.
    assert cfg["orb_discovery"]["hardening"]["enabled"] is True


def test_orb_build_signal_type_is_failure_reclaim() -> None:
    cfg = _build()
    assert cfg["orb_discovery"]["orb"]["signal_type"] == ["failure_reclaim"]


# ---- fill_mode mapping -----------------------------------------------------


def test_fill_mode_body_midpoint_enables_body_midpoint_timing() -> None:
    et = _build(params=_orb_params(fill_mode="body_midpoint"))["orb_discovery"]["entry_timing"]
    assert et["body_midpoint"]["enabled"] is True
    assert et["next_open"]["enabled"] is False


def test_fill_mode_reclaim_close_enables_next_open_timing() -> None:
    et = _build(params=_orb_params(fill_mode="reclaim_close"))["orb_discovery"]["entry_timing"]
    assert et["next_open"]["enabled"] is True
    assert et["body_midpoint"]["enabled"] is False


def test_fill_mode_range_midpoint_is_rejected() -> None:
    with pytest.raises(ConfigBuildError, match="range_midpoint"):
        _build(params=_orb_params(fill_mode="range_midpoint"))


# ---- direction -------------------------------------------------------------


@pytest.mark.parametrize("direction, code", [("both", 0), ("long", 1), ("short", -1)])
def test_direction_codes(direction: str, code: int) -> None:
    cfg = _build(direction=direction)
    assert cfg["orb_discovery"]["orb"]["direction"] == [code]


def test_unknown_direction_is_rejected() -> None:
    with pytest.raises(ConfigBuildError, match="direction"):
        _build(direction="sideways")


# ---- session window parsing -----------------------------------------------


def test_session_window_is_parsed_into_hours() -> None:
    orb = _build(session_window="ny_open_0730_1000_denver")["orb_discovery"]["orb"]
    assert (orb["session_open_hour"], orb["session_open_minute"],
            orb["session_close_hour"]) == (7, 30, 10)


def test_session_window_other_window_is_parsed() -> None:
    orb = _build(session_window="london_0200_0515_denver")["orb_discovery"]["orb"]
    assert (orb["session_open_hour"], orb["session_open_minute"],
            orb["session_close_hour"]) == (2, 0, 5)


def test_session_window_none_falls_back_to_ny_open_default() -> None:
    orb = _build(session_window=None)["orb_discovery"]["orb"]
    assert (orb["session_open_hour"], orb["session_open_minute"],
            orb["session_close_hour"]) == (7, 30, 10)


# ---- rejection paths -------------------------------------------------------


def test_unknown_family_is_rejected() -> None:
    with pytest.raises(ConfigBuildError, match="no discovery-config template"):
        _build(family="vwap_reject_fade")


def test_non_nq_instrument_is_rejected() -> None:
    with pytest.raises(ConfigBuildError, match="NQ"):
        _build(instrument="ES")
