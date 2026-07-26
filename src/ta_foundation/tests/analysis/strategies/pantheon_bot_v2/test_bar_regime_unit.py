"""
Self-contained unit tests for bar_regime.compute_bar_regime building blocks.
No NT fixture required — these run on every CI invocation and catch
formula regressions independent of any captured backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ta_foundation.analysis.strategies.pantheon_bot_v2.bar_regime import (
    BarRegimeConfig,
    _assign_session_id,
    _atr_wilder_nt,
    _ema_nt,
    _rolling_percentile_rank_nt,
    _session_vwap,
    compute_bar_regime,
)


def _denver(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts, tz="America/Denver")


def _flat_bars(n: int, start_close: float = 100.0, step: float = 0.0) -> pd.DataFrame:
    base = _denver("2026-02-26 09:00:00")
    rows = []
    c = start_close
    for i in range(n):
        rows.append(
            {
                "dt": base + pd.Timedelta(minutes=5 * (i + 1)),
                "open": c,
                "high": c + 1.0,
                "low": c - 1.0,
                "close": c,
                "volume": 100,
            }
        )
        c += step
    return pd.DataFrame(rows)


def test_ema_matches_nt_recursion_seed():
    closes = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
    period = 3
    alpha = 2.0 / (period + 1)
    expected = [100.0]
    for x in closes.iloc[1:]:
        expected.append(alpha * x + (1 - alpha) * expected[-1])

    py = _ema_nt(closes, period).tolist()
    np.testing.assert_allclose(py, expected, rtol=0, atol=1e-12)


def test_atr_wilder_seeds_at_first_bar_range():
    df = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 102.5],
            "low":  [ 99.0, 100.5, 101.5, 101.0],
            "close":[100.0, 101.0, 102.0, 101.5],
        }
    )
    atr = _atr_wilder_nt(df, period=2)
    # TR[0] = H[0] - L[0] = 2.0 by NT seed.
    assert atr.iloc[0] == pytest.approx(2.0)
    assert all(atr.notna())


def test_percentile_rank_uses_half_equal_formula():
    s = pd.Series([1.0, 1.0, 2.0])
    pct = _rolling_percentile_rank_nt(s, window=10)
    # i=0: window=[1.0]; less=0 equal=1 → 0.5
    # i=1: window=[1.0,1.0]; less=0 equal=2 → 1.0/2 = 0.5
    # i=2: window=[1.0,1.0,2.0]; less=2 equal=1 → (2 + 0.5)/3 = 0.833...
    assert pct.iloc[0] == pytest.approx(0.5)
    assert pct.iloc[1] == pytest.approx(0.5)
    assert pct.iloc[2] == pytest.approx(2.5 / 3.0)


def test_percentile_rank_window_cap():
    # Once the window is full, older values fall out.
    s = pd.Series([5.0, 5.0, 5.0, 1.0])
    pct = _rolling_percentile_rank_nt(s, window=3)
    # i=3: window=[5,5,1]; current=1; less=0 equal=1 → 1/(2*3)=0.166...
    assert pct.iloc[3] == pytest.approx(0.5 / 3.0)


def test_session_id_resets_at_configured_hour():
    bars = pd.DataFrame(
        {
            "dt": [
                _denver("2026-02-26 14:55:00"),
                _denver("2026-02-26 15:00:00"),
                _denver("2026-02-26 15:05:00"),
                _denver("2026-02-27 14:55:00"),
                _denver("2026-02-27 15:00:00"),
            ]
        }
    )
    ids = _assign_session_id(bars, reset_hour=15, reset_minute=0).tolist()
    assert ids[0] == "2026-02-25"
    assert ids[1] == "2026-02-26"
    assert ids[2] == "2026-02-26"
    assert ids[3] == "2026-02-26"
    assert ids[4] == "2026-02-27"


def test_session_vwap_with_anchor_ignores_pre_anchor_bars():
    """When vwap_anchor_dt is set, bars before the anchor contribute zero
    to the first session's cumulative sums — mirroring NT's behavior of
    starting VWAP accumulation at the first bar of the loaded backtest
    window rather than at a calendar session boundary."""
    bars = pd.DataFrame(
        {
            "dt": [
                _denver("2026-02-26 15:00:00"),
                _denver("2026-02-26 17:00:00"),
                _denver("2026-02-26 18:05:00"),  # anchor here
                _denver("2026-02-26 18:10:00"),
            ],
            "open":   [100.0, 200.0, 300.0, 304.0],
            "high":   [100.0, 200.0, 300.0, 304.0],
            "low":    [100.0, 200.0, 300.0, 304.0],
            "close":  [100.0, 200.0, 300.0, 304.0],
            "volume": [10, 10, 10, 10],
        }
    )
    anchor = _denver("2026-02-26 18:05:00")
    vwap = _session_vwap(
        bars, reset_hour=15, reset_minute=0, use_close=True, anchor_dt=anchor,
    ).tolist()
    # Pre-anchor bars get NaN.
    assert pd.isna(vwap[0])
    assert pd.isna(vwap[1])
    # Anchor bar VWAP equals its own close, not blended with the prior
    # session bars NT never saw.
    assert vwap[2] == pytest.approx(300.0)
    assert vwap[3] == pytest.approx(302.0)


def test_session_vwap_resets_per_session():
    bars = pd.DataFrame(
        {
            "dt": [
                _denver("2026-02-26 15:00:00"),
                _denver("2026-02-26 15:05:00"),
                _denver("2026-02-27 15:00:00"),
            ],
            "open":   [100.0, 102.0, 200.0],
            "high":   [100.0, 102.0, 200.0],
            "low":    [100.0, 102.0, 200.0],
            "close":  [100.0, 102.0, 200.0],
            "volume": [10, 10, 10],
        }
    )
    vwap = _session_vwap(bars, reset_hour=15, reset_minute=0, use_close=True).tolist()
    assert vwap[0] == pytest.approx(100.0)
    assert vwap[1] == pytest.approx(101.0)   # cumulative avg within session
    assert vwap[2] == pytest.approx(200.0)   # new session starts fresh


def test_compute_bar_regime_flat_series_emits_zero_slope():
    # Flat closes → EMA flat → slope ≈ 0 regardless of slope mode.
    primary = _flat_bars(80, start_close=100.0, step=0.0)
    htf = _flat_bars(20, start_close=100.0, step=0.0)
    htf = htf.assign(dt=pd.to_datetime(htf["dt"]) + pd.Timedelta(minutes=10))  # 15m label
    cfg = BarRegimeConfig(
        trend_ema_period=5, trend_slope_lookback_bars=3, trend_atr_period=5,
        volatility_lookback_window=20, volatility_atr_period=5, vwap_atr_period=5,
    )
    out = compute_bar_regime(primary, htf, cfg)
    assert (out["slope"].dropna().abs() < 1e-9).all()


def test_compute_bar_regime_dist_atr_zero_when_price_equals_vwap():
    primary = _flat_bars(40, start_close=100.0, step=0.0)
    htf = _flat_bars(10, start_close=100.0, step=0.0)
    cfg = BarRegimeConfig(
        volatility_lookback_window=20, volatility_atr_period=5, vwap_atr_period=5,
        trend_ema_period=5, trend_atr_period=5,
    )
    out = compute_bar_regime(primary, htf, cfg)
    # Once VWAP equals close (constant prices), dist_atr should be ~0.
    assert out["dist_atr"].abs().tail(20).max() < 1e-9
