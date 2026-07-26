from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.large_candle_excursion.adaptive_window import (
    _score_modes,
    emit_candle_center_events,
    run_adaptive_large_candle_windows,
    simulate_event_brackets,
)


def _bars(rows):
    return pd.DataFrame(
        rows,
        columns=["dt", "open", "high", "low", "close"],
    )


def _signal_config(**overrides):
    cfg = {
        "timeframe": 1,
        "lookback": 5,
        "basis": "range",
        "multiplier": 1.5,
        "tick_size": 0.25,
        "average_mode": "include_current",
        "signal_direction": "both",
        "bars_required": 4,
        "signals": {
            "fresh_large_candle": True,
            "center_zone_break": False,
            "region_percent": 30.0,
        },
        "time_filter": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


def test_current_inclusive_average_matches_candle_center_bot_semantics():
    dts = pd.date_range("2026-07-20 10:00", periods=5, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 101.25, 98.75, 100.25),
        (dts[1], 100.0, 101.25, 98.75, 99.75),
        (dts[2], 100.0, 101.25, 98.75, 100.25),
        (dts[3], 100.0, 101.25, 98.75, 99.75),
        (dts[4], 100.0, 101.875, 98.125, 99.50),
    ])

    include_current = emit_candle_center_events(
        bars, _signal_config(average_mode="include_current")
    )
    prior_only = emit_candle_center_events(
        bars, _signal_config(average_mode="prior_only")
    )

    # 15 ticks is exactly 1.5x the prior four-bar average (10 ticks), but
    # only 1.364x the bot's current-inclusive five-bar average (11 ticks).
    assert include_current == []
    assert len(prior_only) == 1
    assert prior_only[0]["signal_ratio"] == 1.5


def test_center_zone_wick_pierce_emits_separate_break_signal():
    dts = pd.date_range("2026-07-20 10:00", periods=3, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),   # 4 ticks
        (dts[1], 101.0, 101.50, 98.50, 99.00),   # 12 ticks, bearish zone
        (dts[2], 100.25, 100.50, 99.50, 99.75),  # wick reaches zone top
    ])
    cfg = _signal_config(
        lookback=2,
        bars_required=1,
        signal_direction="bear_only",
        signals={
            "fresh_large_candle": False,
            "center_zone_break": True,
            "region_percent": 30.0,
            "invalidate_on_close": False,
        },
    )

    events = emit_candle_center_events(bars, cfg)

    assert len(events) == 1
    assert events[0]["signal_dt"] == dts[2]
    assert events[0]["trigger_type"] == "zone_break"
    assert events[0]["zones_broken"] == 1
    assert events[0]["signal_side"] == "bear"


def test_doji_trigger_uses_strategy_short_branch():
    dts = pd.date_range("2026-07-20 10:00", periods=3, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),
        (dts[2], 100.0, 100.50, 99.50, 100.0),
    ])
    cfg = _signal_config(
        lookback=2,
        bars_required=1,
        signal_direction="bear_only",
        signals={
            "fresh_large_candle": False,
            "center_zone_break": True,
            "region_percent": 30.0,
            "invalidate_on_close": False,
        },
    )

    events = emit_candle_center_events(bars, cfg)

    assert len(events) == 1
    assert events[0]["signal_dt"] == dts[2]
    assert events[0]["signal_side"] == "bear"
    assert events[0]["signal_direction"] == -1


def test_outside_window_trigger_latches_until_first_in_window_bar():
    dts = pd.date_range("2026-07-20 09:58", periods=4, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),
        (dts[2], 100.0, 100.50, 99.50, 99.75),
        (dts[3], 99.75, 100.00, 99.00, 99.25),
    ])
    cfg = _signal_config(
        lookback=2,
        bars_required=1,
        signal_direction="bear_only",
        signals={
            "fresh_large_candle": True,
            "center_zone_break": False,
            "region_percent": 30.0,
            "latch_outside_window_triggers": True,
        },
        time_filter={"enabled": True, "start": "10:00", "end": "13:20"},
    )

    events = emit_candle_center_events(bars, cfg)

    assert len(events) == 1
    assert events[0]["signal_dt"] == dts[2]
    assert events[0]["entry_dt"] == dts[3]
    assert events[0]["trigger_source_dt"] == dts[1]
    assert events[0]["latched_outside_window"] is True


def test_signal_bar_at_end_of_window_is_allowed_before_next_bar_fill():
    dts = pd.date_range("2026-07-20 13:56", periods=5, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 101.25, 98.75, 100.25),
        (dts[1], 100.0, 101.25, 98.75, 99.75),
        (dts[2], 100.0, 101.25, 98.75, 100.25),
        (dts[3], 100.0, 101.25, 98.75, 99.75),
        (dts[4], 100.0, 102.50, 97.50, 99.50),
    ])
    cfg = _signal_config(
        average_mode="prior_only",
        time_filter={"enabled": True, "start": "10:00", "end": "14:00"},
    )

    events = emit_candle_center_events(bars, cfg)

    assert len(events) == 1
    assert events[0]["signal_dt"].strftime("%H:%M") == "14:00"
    assert events[0]["entry_dt"].strftime("%H:%M") == "14:01"


def test_same_minute_target_and_stop_uses_conservative_stop_first_policy():
    dts = pd.date_range("2026-07-20 10:00", periods=2, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.0, 100.0, 100.0),
        (dts[1], 100.0, 103.0, 97.0, 100.0),
    ])
    events = [{
        "signal_dt": dts[0],
        "entry_dt": dts[1],
        "signal_direction": 1,
        "signal_side": "bull",
    }]
    cfg = {
        "tick_size": 1.0,
        "outcome": {
            "target_ticks": 2,
            "stop_ticks": 2,
            "max_hold_minutes": 10,
            "same_bar_policy": "stop_first",
            "round_trip_cost_ticks": 0.5,
            "max_concurrent_per_direction": 3,
        },
        "time_filter": {"enabled": False},
    }

    rows = simulate_event_brackets(events, bars, cfg)
    continuation = rows[0]["continuation"]

    assert continuation["exit_reason"] == "ambiguous_stop_first"
    assert continuation["gross_pnl_ticks"] == -2.0
    assert continuation["net_pnl_ticks"] == -2.5


def test_time_filter_exit_fills_at_second_bar_after_inclusive_end():
    dts = pd.date_range("2026-07-20 13:20", periods=3, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.5, 99.5, 100.0),
        (dts[1], 100.0, 100.5, 99.5, 100.25),
        (dts[2], 101.0, 101.5, 100.5, 101.25),
    ])
    events = [{
        "signal_dt": dts[0],
        "entry_dt": dts[1],
        "signal_direction": -1,
        "signal_side": "bear",
    }]
    cfg = {
        "timeframe": 1,
        "tick_size": 0.25,
        "outcome": {
            "target_ticks": 75,
            "stop_ticks": 150,
            "max_hold_minutes": 300,
            "same_bar_policy": "stop_first",
            "round_trip_cost_ticks": 0,
            "max_concurrent_per_direction": 3,
        },
        "time_filter": {"enabled": True, "start": "10:00", "end": "13:20"},
    }

    rows = simulate_event_brackets(events, bars, cfg)
    continuation = rows[0]["continuation"]

    assert continuation["exit_reason"] == "time_filter_exit"
    assert continuation["exit_dt"] == dts[2]
    assert continuation["exit_price"] == 101.0


def test_walk_forward_score_ignores_outcomes_not_known_at_decision_time():
    old_entry = pd.Timestamp("2026-07-20 10:05")
    future_entry = pd.Timestamp("2026-07-21 10:10")
    rows = [
        {
            "entry_dt": old_entry,
            "continuation": {
                "available": True,
                "exit_known_dt": old_entry + pd.Timedelta(minutes=5),
                "net_pnl_ticks": 10.0,
            },
            "reversion": {
                "available": True,
                "exit_known_dt": old_entry + pd.Timedelta(minutes=5),
                "net_pnl_ticks": -10.0,
            },
        },
        {
            "entry_dt": future_entry,
            "continuation": {
                "available": True,
                "exit_known_dt": future_entry + pd.Timedelta(days=1),
                "net_pnl_ticks": 1000.0,
            },
            "reversion": {
                "available": True,
                "exit_known_dt": future_entry + pd.Timedelta(days=1),
                "net_pnl_ticks": -1000.0,
            },
        },
    ]
    cfg = {
        "training_days": 10,
        "time_bin_minutes": 30,
        "neighbor_bins": 0,
        "min_local_signals": 1,
        "half_life_days": 5,
        "prior_strength": 0,
        "confidence_z": 0,
        "min_expected_net_ticks": 0,
        "min_lower_bound_ticks": 0,
        "mode_margin_ticks": 0,
    }

    score = _score_modes(
        rows,
        pd.Timestamp("2026-07-21 10:15"),
        10 * 60,
        cfg,
    )

    assert score["local_n"] == 1
    assert score["continuation"]["weighted_mean_ticks"] == 10.0
    assert score["reversion"]["weighted_mean_ticks"] == -10.0


# ---------------------------------------------------------------------------
# Zone-lifecycle triggers ported from the LCR region engine.
# Shared fixture: bar 1 is a 12-tick bearish candle whose 30% center band is
# [99.50, 100.50]. A bearish zone breaks when a high reaches the top.
# ---------------------------------------------------------------------------

def _zone_config(**signal_overrides):
    signals = {
        "fresh_large_candle": False,
        "center_zone_break": False,
        "region_percent": 30.0,
        "invalidate_on_close": False,
    }
    signals.update(signal_overrides)
    return _signal_config(
        lookback=2,
        bars_required=1,
        signal_direction="both",
        signals=signals,
    )


def test_zone_touch_fires_when_band_holds_and_trades_with_the_zone():
    dts = pd.date_range("2026-07-20 10:00", periods=3, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),   # 4 ticks
        (dts[1], 101.0, 101.50, 98.50, 99.00),   # 12 ticks, bearish zone
        # Enters the band (high 100.25) but never reaches the 100.50 top, and
        # closes back below the 99.50 bottom: the zone was tested and HELD.
        (dts[2], 99.00, 100.25, 98.75, 99.25),
    ])

    events = emit_candle_center_events(bars, _zone_config(zone_touch=True))

    assert len(events) == 1
    assert events[0]["trigger_type"] == "zone_touch"
    assert events[0]["signal_dt"] == dts[2]
    # A zone that held is traded WITH: a bearish zone is resistance -> short.
    assert events[0]["signal_direction"] == -1
    assert events[0]["zone_touch_count"] == 1


def test_zone_touch_requires_close_back_outside_not_merely_overlap():
    dts = pd.date_range("2026-07-20 10:00", periods=3, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),
        # Overlaps the band and closes INSIDE it — price is sitting in the
        # zone, which is not a rejection, so no touch is emitted.
        (dts[2], 99.00, 100.25, 98.75, 100.00),
    ])

    assert emit_candle_center_events(bars, _zone_config(zone_touch=True)) == []


def test_zone_retrace_fires_on_re_entry_and_trades_the_break_direction():
    dts = pd.date_range("2026-07-20 10:00", periods=4, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),   # bearish zone [99.50, 100.50]
        (dts[2], 100.25, 100.50, 99.50, 100.25),  # high hits top -> zone breaks up
        (dts[3], 100.0, 100.25, 99.75, 100.0),    # price returns into the band
    ])

    events = emit_candle_center_events(bars, _zone_config(zone_retrace=True))

    assert len(events) == 1
    assert events[0]["trigger_type"] == "zone_retrace"
    assert events[0]["signal_dt"] == dts[3]
    # A zone that FAILED is traded in the break direction: broken resistance
    # becomes support -> long.
    assert events[0]["signal_direction"] == 1
    # No other zone survives, so there is no structural target distance.
    assert events[0]["next_zone_dist_ticks"] is None


def test_zone_retrace_expires_after_retrace_window_bars():
    dts = pd.date_range("2026-07-20 10:00", periods=6, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),
        (dts[2], 100.25, 100.50, 99.50, 100.25),  # break
        (dts[3], 101.0, 101.25, 100.75, 101.0),   # away from the zone
        (dts[4], 101.0, 101.25, 100.75, 101.0),
        (dts[5], 100.0, 100.25, 99.75, 100.0),    # re-entry, but too late
    ])

    cfg = _zone_config(zone_retrace=True)
    cfg["signals"]["retrace_window_bars"] = 1

    assert emit_candle_center_events(bars, cfg) == []


def test_zone_triggers_are_off_by_default_so_bot_parity_is_unchanged():
    dts = pd.date_range("2026-07-20 10:00", periods=4, freq="1min")
    bars = _bars([
        (dts[0], 100.0, 100.50, 99.50, 99.75),
        (dts[1], 101.0, 101.50, 98.50, 99.00),
        (dts[2], 99.00, 100.25, 98.75, 99.25),    # would be a touch
        (dts[3], 100.25, 100.50, 99.50, 100.25),  # would break, then retrace
    ])

    events = emit_candle_center_events(bars, _zone_config())

    assert [e["trigger_type"] for e in events] == []


def test_parameter_lanes_remain_separate_even_when_the_same_candle_qualifies():
    dts = pd.date_range("2026-07-20 10:00", periods=40, freq="1min")
    rows = []
    for i, dt in enumerate(dts):
        half_range = 1.5 if i % 5 == 4 else 0.5
        rows.append((dt, 100.25, 100.0 + half_range, 100.0 - half_range, 99.75))
    bars = _bars(rows)
    result = run_adaptive_large_candle_windows(
        bars,
        {
            "timeframes": [1],
            "lookbacks": [5],
            "bases": ["range"],
            "multipliers": [1.2, 1.5],
            "tick_size": 0.25,
            "average_mode": "include_current",
            "signal_direction": "bear_only",
            "bars_required": 1,
            "signals": {
                "fresh_large_candle": True,
                "center_zone_break": False,
                "region_percent": 30,
            },
            "time_filter": {"enabled": False},
            "outcome": {
                "target_ticks": 2,
                "stop_ticks": 4,
                "max_hold_minutes": 5,
                "round_trip_cost_ticks": 0,
                "max_concurrent_per_direction": 3,
            },
            "adaptive": {"min_local_signals": 1, "time_bin_minutes": 30},
        },
    )

    assert result["n_streams"] == 2
    assert {s["lane_id"] for s in result["streams"]} == {
        "tf1m|lb5|range|x1.2",
        "tf1m|lb5|range|x1.5",
    }
    assert result["n_events"] == sum(s["n_events"] for s in result["streams"])
