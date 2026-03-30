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
