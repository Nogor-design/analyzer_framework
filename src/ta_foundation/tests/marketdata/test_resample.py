from __future__ import annotations

import pandas as pd
import pytest

from ta_foundation.marketdata.resample import (
    _is_subminute,
    _tf_to_pandas_rule,
    ohlcv_resample_from_bars,
    ohlcv_resample_from_ticks,
)

TZ = "America/Denver"


def _make_1m_bars(n: int = 10) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-02 09:30:00", tz=TZ)
    dts = [start + pd.Timedelta(minutes=i) for i in range(n)]
    return pd.DataFrame(
        {
            "dt": dts,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [500.0] * n,
        }
    )


def _make_ticks(n: int = 60) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-02 09:30:00", tz=TZ)
    dts = [start + pd.Timedelta(seconds=i) for i in range(n)]
    return pd.DataFrame(
        {
            "dt": dts,
            "last": [100.0 + i * 0.01 for i in range(n)],
            "volume": [10.0] * n,
        }
    )


# ---------------------------------------------------------------------------
# _tf_to_pandas_rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tf,expected",
    [
        ("1m", "1min"),
        ("5m", "5min"),
        ("15m", "15min"),
        ("2m", "2min"),
        ("3m", "3min"),
        ("4m", "4min"),
        ("1h", "1h"),
        ("4h", "4h"),
        ("1d", "1D"),
        ("5s", "5s"),
        ("10s", "10s"),
        ("30s", "30s"),
    ],
)
def test_tf_to_pandas_rule(tf, expected):
    assert _tf_to_pandas_rule(tf) == expected


def test_tf_to_pandas_rule_invalid():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        _tf_to_pandas_rule("7x")


# ---------------------------------------------------------------------------
# _is_subminute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tf", ["5s", "10s", "20s", "30s"])
def test_is_subminute_true(tf):
    assert _is_subminute(tf) is True


@pytest.mark.parametrize("tf", ["1m", "5m", "1h", "1d"])
def test_is_subminute_false(tf):
    assert _is_subminute(tf) is False


# ---------------------------------------------------------------------------
# ohlcv_resample_from_bars — multi-minute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tf", ["2m", "3m", "4m"])
def test_resample_bars_multi_minute(tf):
    bars = _make_1m_bars(12)
    out = ohlcv_resample_from_bars(bars, tf)
    assert not out.empty
    assert list(out.columns) == ["dt", "open", "high", "low", "close", "volume"]
    # high must be >= open for every row
    assert (out["high"] >= out["open"]).all()


def test_resample_bars_subminute_raises():
    bars = _make_1m_bars(10)
    with pytest.raises(ValueError, match="sub-minute"):
        ohlcv_resample_from_bars(bars, "5s")


# ---------------------------------------------------------------------------
# ohlcv_resample_from_ticks — sub-minute
# ---------------------------------------------------------------------------


def test_resample_ticks_5s():
    ticks = _make_ticks(60)
    out = ohlcv_resample_from_ticks(ticks, "5s")
    assert not out.empty
    assert list(out.columns) == ["dt", "open", "high", "low", "close", "volume"]
    # 60 ticks at 1s intervals → 12 complete 5s bars
    assert len(out) == 12


def test_resample_ticks_invalid_tf():
    ticks = _make_ticks(10)
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        ohlcv_resample_from_ticks(ticks, "7x")
