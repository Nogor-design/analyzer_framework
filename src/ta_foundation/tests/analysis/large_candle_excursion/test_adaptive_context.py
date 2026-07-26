from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from ta_foundation.analysis.large_candle_excursion.adaptive_context import (
    attach_context_to_events,
    build_intraday_context,
    classify_trend_state,
    structurally_aligned_mode,
)


DENVER = "America/Denver"


def _trend_bars(
    start: str = "2026-07-20 07:30",
    periods: int = 90,
) -> pd.DataFrame:
    dt = pd.date_range(start, periods=periods, freq="1min", tz=DENVER)
    close = 100.0 + np.arange(periods, dtype=float)
    return pd.DataFrame(
        {
            "dt": dt,
            "open": close - 0.25,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.ones(periods),
        }
    )


def test_build_intraday_context_pins_vwap_slope_return_and_bucket():
    context = build_intraday_context(
        _trend_bars(),
        {"vwap_price": "close"},
    )

    row = context.iloc[60]
    assert row["dt"] == pd.Timestamp("2026-07-20 08:30", tz=DENVER)
    assert row["session_vwap"] == pytest.approx(130.0)
    assert row["close_vs_vwap"] == pytest.approx(30.0)
    assert row["vwap_slope_15"] == pytest.approx(7.5)
    assert row["return_60m"] == pytest.approx(0.6)
    assert row["time_bucket"] == "08:30-09:00"
    assert row["trend_votes"] == (1, 1, 1)
    assert row["trend_state"] == "up"
    assert bool(row["context_history_complete"]) is True


def test_insufficient_history_is_mixed_even_with_positive_available_votes():
    context = build_intraday_context(
        _trend_bars(periods=60),
        {"vwap_price": "close"},
    )

    row = context.iloc[-1]
    assert row["close_vs_vwap"] > 0
    assert row["vwap_slope_15"] > 0
    assert pd.isna(row["return_60m"])
    assert row["trend_state"] == "mixed"
    assert bool(row["context_history_complete"]) is False


@pytest.mark.parametrize(
    ("raw_values", "expected"),
    [
        ((1.0, 2.0, -0.01), "up"),
        ((-1.0, -2.0, 0.01), "down"),
        ((1.0, -2.0, 0.0), "mixed"),
        ((1.0, np.nan, 1.0), "mixed"),
    ],
)
def test_classify_trend_state_uses_fixed_majority_and_complete_history(
    raw_values,
    expected,
):
    row = dict(
        zip(
            ("close_vs_vwap", "vwap_slope_15", "return_60m"),
            raw_values,
        )
    )
    assert classify_trend_state(row) == expected


@pytest.mark.parametrize(
    ("trend", "side", "expected"),
    [
        ("up", "bull", "continuation"),
        ("up", "bear", "reversion"),
        ("down", "bear", "continuation"),
        ("down", "bull", "reversion"),
        ("mixed", "bull", None),
        ("mixed", "bear", None),
    ],
)
def test_structurally_aligned_mode_pins_direction_policy(
    trend,
    side,
    expected,
):
    assert structurally_aligned_mode(trend, side) == expected


def test_structurally_aligned_mode_rejects_unknown_values():
    with pytest.raises(ValueError, match="trend_state"):
        structurally_aligned_mode("sideways", "bull")
    with pytest.raises(ValueError, match="signal_side"):
        structurally_aligned_mode("up", "flat")


def test_session_vwap_resets_at_configured_anchor():
    dt = pd.date_range(
        "2026-07-20 07:28",
        periods=4,
        freq="1min",
        tz=DENVER,
    )
    close = np.array([10.0, 20.0, 100.0, 110.0])
    bars = pd.DataFrame(
        {
            "dt": dt,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
    )

    context = build_intraday_context(
        bars,
        {"session_anchor": "07:30", "vwap_price": "close"},
    )

    assert context["session_id"].tolist() == [
        "2026-07-19",
        "2026-07-19",
        "2026-07-20",
        "2026-07-20",
    ]
    assert context["session_vwap"].tolist() == [10.0, 15.0, 100.0, 105.0]


def test_appending_future_bars_does_not_change_earlier_context():
    original = _trend_bars(periods=80)
    extended = _trend_bars(periods=120)

    before = build_intraday_context(original, {"vwap_price": "close"})
    after = build_intraday_context(extended, {"vwap_price": "close"}).iloc[:80]

    assert_frame_equal(before, after.reset_index(drop=True))


def test_mutating_future_ohlcv_does_not_change_earlier_context():
    bars = _trend_bars(periods=120)
    decision_index = 75
    before = build_intraday_context(bars, {"vwap_price": "typical"}).iloc[
        : decision_index + 1
    ]

    mutated = bars.copy()
    future = mutated.index > decision_index
    mutated.loc[future, ["high", "low", "close"]] += 10_000.0
    mutated.loc[future, "volume"] *= 1_000.0
    after = build_intraday_context(mutated, {"vwap_price": "typical"}).iloc[
        : decision_index + 1
    ]

    assert_frame_equal(before, after)


def test_attach_context_uses_signal_close_not_entry_or_future_bar():
    bars = _trend_bars(periods=90)
    context = build_intraday_context(bars, {"vwap_price": "close"})
    signal_dt = bars.iloc[60]["dt"]
    event = {
        "signal_dt": signal_dt,
        "entry_dt": signal_dt + pd.Timedelta(minutes=1),
        "signal_side": "bull",
    }

    attached = attach_context_to_events([event], context)

    assert attached[0]["context_dt"] == signal_dt
    assert attached[0]["session_vwap"] == pytest.approx(130.0)
    assert attached[0]["trend_state"] == "up"


def test_attach_context_asof_never_uses_a_later_context_row():
    context = build_intraday_context(_trend_bars(periods=90))
    signal_dt = context.iloc[60]["dt"] + pd.Timedelta(seconds=30)

    attached = attach_context_to_events(
        [{"signal_dt": signal_dt, "signal_side": "bear"}],
        context,
    )

    assert attached[0]["context_dt"] == context.iloc[60]["dt"]
    assert attached[0]["context_dt"] < signal_dt


def test_context_keeps_timezone_awareness_across_dst_boundary():
    dt = pd.date_range(
        "2026-03-08 07:28",
        periods=4,
        freq="1min",
        tz=DENVER,
    )
    close = 100.0 + np.arange(len(dt)) / 100.0
    bars = pd.DataFrame(
        {
            "dt": dt,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": 1.0,
        }
    )

    context = build_intraday_context(
        bars,
        {"session_anchor": "07:30"},
    )

    assert str(context["dt"].dt.tz) == DENVER
    before_anchor = context.loc[
        context["dt"] == pd.Timestamp("2026-03-08 07:29", tz=DENVER)
    ].iloc[0]
    at_anchor = context.loc[
        context["dt"] == pd.Timestamp("2026-03-08 07:30", tz=DENVER)
    ].iloc[0]
    assert before_anchor["session_id"] == "2026-03-07"
    assert at_anchor["session_id"] == "2026-03-08"
    assert at_anchor["session_vwap"] == pytest.approx(close[2])


def test_context_rejects_naive_timestamps():
    bars = _trend_bars(periods=2)
    bars["dt"] = bars["dt"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        build_intraday_context(bars)
