from __future__ import annotations

import numpy as np
import pandas as pd

from ta_foundation.analysis.regime_recommender import build_multitf_features, classify_regime
from ta_foundation.marketdata.store import MarketDataStore


RNG = np.random.default_rng(7)


def _make_minute_bars(n: int = 7 * 24 * 60) -> pd.DataFrame:
    dt = pd.date_range("2026-03-15 00:00", periods=n, freq="1min", tz="America/Denver")
    base = 20000 + np.linspace(0, 120, n)
    noise = RNG.normal(0, 2.5, n).cumsum()
    close = base + noise
    high = close + RNG.uniform(0.5, 2.0, n)
    low = close - RNG.uniform(0.5, 2.0, n)
    open_ = close + RNG.normal(0, 0.5, n)
    vol = RNG.integers(100, 600, n)
    return pd.DataFrame({
        "dt": dt,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })


def test_build_multitf_features_returns_expected_keys():
    market = MarketDataStore()
    bars = _make_minute_bars()
    market.put_minute_bars("NQ", "03-26", bars)

    asof = pd.Timestamp("2026-03-21 23:59:00", tz="America/Denver")
    out = build_multitf_features(market, "NQ", "03-26", asof=asof)

    assert out["instrument"] == "NQ"
    assert out["contract"] == "03-26"
    assert "feature_values" in out
    fv = out["feature_values"]
    assert "tf15m_trend_slope" in fv
    assert "tf60m_trend_strength" in fv
    assert "tf240m_atr" in fv
    assert "cross_tf_agreement" in fv


def test_classify_regime_returns_confidence_and_influences():
    market = MarketDataStore()
    bars = _make_minute_bars()
    market.put_minute_bars("NQ", "03-26", bars)

    asof = pd.Timestamp("2026-03-21 23:59:00", tz="America/Denver")
    features = build_multitf_features(market, "NQ", "03-26", asof=asof)
    regime = classify_regime(features)

    assert "regime_id" in regime
    assert "primary" in regime
    assert "confidence" in regime
    assert 0.0 <= regime["confidence"] <= 1.0
    assert isinstance(regime.get("feature_influences"), list)
    assert len(regime["feature_influences"]) >= 1


def test_build_multitf_features_warns_for_missing_market_data():
    market = MarketDataStore()
    asof = pd.Timestamp("2026-03-21 23:59:00", tz="America/Denver")

    out = build_multitf_features(market, "NQ", "03-26", asof=asof)
    assert out["warnings"]
    regime = classify_regime(out)
    assert regime["diagnostics"]["warnings_count"] >= 1


# --- 4h trend-slope regression -------------------------------------------
#
# Two coupled defects produced a degenerate trend axis:
#   1. the 4h lookback was too short for its own EMA(50), so tf240m_trend_slope
#      fell back to exactly 0.0 and the classifier answered `range` every time;
#   2. the classifier's flat band defaulted to 0.0, making `range` the
#      measure-zero set {slope == 0.0}. Fixing (1) alone flipped the output to
#      `range` never occurring.
# These pin both halves so neither can silently regress.


def _feature_vector(slope: float, atr: float = 100.0) -> dict:
    return {
        "feature_values": {
            "tf240m_trend_slope": slope,
            "tf240m_atr": atr,
            "tf60m_trend_strength": 0.1,
            "tf15m_compression_ratio": 1.0,
            "cross_tf_agreement": 1.0,
        },
        "warnings": [],
    }


def test_four_hour_window_supplies_enough_bars_for_ema50():
    """tf240m needs >= ~53 4h bars for EMA(50) + a 3-bar slope to be defined."""
    market = MarketDataStore()
    market.put_minute_bars("NQ", "03-26", _make_minute_bars(n=60 * 24 * 60))

    asof = pd.Timestamp("2026-04-20 23:59:00", tz="America/Denver")
    out = build_multitf_features(market, "NQ", "03-26", asof=asof)

    assert out["diagnostics"]["tf240m_rows"] >= 53, (
        "4h lookback is too short for EMA(50); tf240m_trend_slope will silently "
        "fall back to 0.0 and pin the classifier to `range`"
    )


def test_four_hour_trend_slope_is_live_on_a_trending_series():
    """The synthetic series drifts upward, so the 4h slope must not be 0.0."""
    market = MarketDataStore()
    market.put_minute_bars("NQ", "03-26", _make_minute_bars(n=60 * 24 * 60))

    asof = pd.Timestamp("2026-04-20 23:59:00", tz="America/Denver")
    fv = build_multitf_features(market, "NQ", "03-26", asof=asof)["feature_values"]

    assert fv["tf240m_trend_slope"] != 0.0


def test_flat_band_scales_with_atr():
    """A slope inside 10% of ATR is range; outside it is a trend."""
    assert classify_regime(_feature_vector(slope=5.0, atr=100.0))["primary"] == "range"
    assert classify_regime(_feature_vector(slope=-5.0, atr=100.0))["primary"] == "range"
    assert classify_regime(_feature_vector(slope=15.0, atr=100.0))["primary"] == "trend_up"
    assert classify_regime(_feature_vector(slope=-15.0, atr=100.0))["primary"] == "trend_down"


def test_flat_band_is_relative_not_absolute():
    """The same slope is range on a volatile instrument and a trend on a calm one."""
    assert classify_regime(_feature_vector(slope=8.0, atr=200.0))["primary"] == "range"
    assert classify_regime(_feature_vector(slope=8.0, atr=20.0))["primary"] == "trend_up"


def test_flat_band_configurable_and_backward_compatible():
    features = _feature_vector(slope=5.0, atr=100.0)
    # Opting out restores the old (degenerate) behaviour exactly.
    assert classify_regime(features, {"trend_flat_atr_frac": 0.0})["primary"] == "trend_up"
    # A wider band pulls a formerly-trending slope back into range.
    assert classify_regime(_feature_vector(15.0, 100.0), {"trend_flat_atr_frac": 0.25})["primary"] == "range"
    # An explicit absolute floor is never narrowed by the ATR band.
    assert classify_regime(features, {"trend_flat_threshold": 50.0})["primary"] == "range"


def test_flat_band_falls_back_to_absolute_when_atr_missing():
    """No ATR (or zero) must not divide by zero or silently trend everything."""
    out = classify_regime(_feature_vector(slope=5.0, atr=0.0))
    assert out["primary"] == "trend_up"          # band collapses to the 0.0 absolute floor
    assert out["diagnostics"]["slope_atr_ratio"] is None
    assert out["diagnostics"]["flat_band"] == 0.0


def test_range_verdict_is_distinguishable_from_a_dead_slope():
    """The ambiguity that hid the original bug: `range` must be inspectable."""
    out = classify_regime(_feature_vector(slope=5.0, atr=100.0))
    assert out["primary"] == "range"
    assert out["diagnostics"]["flat_band"] == 10.0
    assert out["diagnostics"]["slope_atr_ratio"] == 0.05
