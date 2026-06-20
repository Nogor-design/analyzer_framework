from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.analysis.features.regime import atr_wilder, ema, ema_slope, session_vwap
from ta_foundation.marketdata.store import MarketDataStore
from .models import RegimeFeatureVector


DENVER_TZ = "America/Denver"


def _validate_bars(df: Optional[pd.DataFrame]) -> bool:
    if df is None or df.empty:
        return False
    required = {"dt", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return False
    dt = pd.to_datetime(df["dt"], errors="coerce")
    return bool(dt.notna().all() and dt.dt.tz is not None)


def _last_value(series: pd.Series, default: float = 0.0) -> float:
    if series is None or series.empty:
        return float(default)
    x = pd.to_numeric(series, errors="coerce").dropna()
    if x.empty:
        return float(default)
    return float(x.iloc[-1])


def _build_timeframe_features(df: pd.DataFrame, prefix: str) -> Dict[str, float]:
    out: Dict[str, float] = {}

    close = pd.to_numeric(df["close"], errors="coerce")
    ema_fast = ema(close, period=20)
    ema_slow = ema(close, period=50)
    atr = atr_wilder(df, period=14)

    trend_spread = ema_fast - ema_slow
    trend_slope = ema_slope(ema_slow, lookback=3)
    atr_norm = trend_slope.abs() / atr.replace(0, pd.NA)

    out[f"{prefix}_trend_spread"] = _last_value(trend_spread)
    out[f"{prefix}_trend_slope"] = _last_value(trend_slope)
    out[f"{prefix}_atr"] = _last_value(atr)
    out[f"{prefix}_trend_strength"] = _last_value(atr_norm, default=0.0)

    # compression proxy: short ATR / longer ATR SMA
    atr_sma = atr.rolling(20, min_periods=5).mean()
    compression_ratio = atr / atr_sma.replace(0, pd.NA)
    out[f"{prefix}_compression_ratio"] = _last_value(compression_ratio, default=1.0)

    if prefix == "tf15m":
        vwap = session_vwap(df)
        vwap_dist = (close - vwap) / atr.replace(0, pd.NA)
        out[f"{prefix}_vwap_dist_atr"] = _last_value(vwap_dist)

    return out


def build_multitf_features(
    market: MarketDataStore,
    instrument: str,
    contract: str,
    asof: Optional[pd.Timestamp] = None,
) -> Dict[str, Any]:
    if asof is None:
        asof = pd.Timestamp.now(tz=DENVER_TZ)
    else:
        asof = pd.Timestamp(asof)
        if asof.tzinfo is None:
            asof = asof.tz_localize(DENVER_TZ)
        else:
            asof = asof.tz_convert(DENVER_TZ)

    feat = RegimeFeatureVector(
        timestamp=asof.isoformat(),
        instrument=instrument,
        contract=contract,
    )

    # Each frame feeds ema(period=50) (min_periods=50) -> ema_slope(lookback=3),
    # so a usable trend_slope needs >= ~53 bars of THAT timeframe, else the EMA is
    # all-NaN and _last_value falls back to 0.0. The old 5-day window on 4h yields
    # only ~30 bars (~6 4h-bars/trading-day), so tf240m_trend_slope was always 0 and
    # the classifier was stuck on `range`. Widen each window with margin:
    #   15m: ~53 bars ~= 13h trading -> 4 calendar days covers gaps/holidays
    #   1h : ~53 bars ~= 3 trading days -> 15 calendar days
    #   4h : ~53 bars ~= 9-10 trading days -> 45 calendar days
    tf15 = market.get_bars(instrument, contract, timeframe="15m", start=asof - pd.Timedelta(days=4), end=asof)
    tf60 = market.get_bars(instrument, contract, timeframe="1h", start=asof - pd.Timedelta(days=15), end=asof)
    tf240 = market.get_bars(instrument, contract, timeframe="4h", start=asof - pd.Timedelta(days=45), end=asof)

    frames = {"tf15m": tf15, "tf60m": tf60, "tf240m": tf240}

    for name, df in frames.items():
        if not _validate_bars(df):
            feat.warnings.append(f"missing_or_invalid_bars:{name}")
            continue
        feat.diagnostics[f"{name}_rows"] = int(len(df))
        feat.feature_values.update(_build_timeframe_features(df, prefix=name))

    # cross timeframe agreement
    slope15 = feat.feature_values.get("tf15m_trend_slope", 0.0)
    slope60 = feat.feature_values.get("tf60m_trend_slope", 0.0)
    slope240 = feat.feature_values.get("tf240m_trend_slope", 0.0)

    signs = [
        1 if slope15 > 0 else (-1 if slope15 < 0 else 0),
        1 if slope60 > 0 else (-1 if slope60 < 0 else 0),
        1 if slope240 > 0 else (-1 if slope240 < 0 else 0),
    ]
    non_zero = [s for s in signs if s != 0]
    if non_zero:
        agreement = abs(sum(non_zero)) / float(len(non_zero))
    else:
        agreement = 0.0

    feat.feature_values["cross_tf_agreement"] = float(agreement)

    return feat.as_dict()
