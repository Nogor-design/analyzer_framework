from __future__ import annotations

"""
Bar-level market regime labeling
=================================
Computes ADX-based trend/range classification and ATR-percentile volatility
regime from raw OHLCV bars.  Results are stored on MarketDataStore (shared),
never on AnalysisPackage — regime labels must not be derived from strategy P&L.

Columns added to output DataFrame:
  adx              : ADX(period) value
  plus_di          : +DI line
  minus_di         : -DI line
  atr              : ATR(period)
  atr_pct_rank     : rolling percentile rank of ATR (0-1) over atr_lookback bars
  trend_direction  : 'up' | 'down' | 'flat'
  trend_strength   : 'trending' | 'ranging'
  vol_regime       : 'high_vol' | 'normal_vol' | 'low_vol'
  regime           : composite label (e.g. 'trending_up', 'ranging_tight', 'high_vol_expansion')

Composite regime labels:
  trending_up          : trending + up
  trending_down        : trending + down
  ranging_tight        : ranging + low_vol
  ranging_wide         : ranging + normal_vol
  high_vol_expansion   : high_vol (regardless of trend)
  low_vol_compression  : low_vol + ranging
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.features.regime import atr_wilder


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: alpha = 1/period."""
    return series.ewm(alpha=1.0 / int(period), adjust=False, min_periods=int(period)).mean()


def compute_adx(bars: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Compute ADX, +DI, -DI on bars.
    Returns bars copy with columns: adx, plus_di, minus_di
    Requires: high, low, close
    """
    out = bars.copy()

    h = pd.to_numeric(out.get("high"), errors="coerce")
    l = pd.to_numeric(out.get("low"), errors="coerce")
    c = pd.to_numeric(out.get("close"), errors="coerce")
    prev_c = c.shift(1)

    # True Range components
    tr = pd.concat(
        [(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()],
        axis=1,
    ).max(axis=1)

    # Directional Movement
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=out.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=out.index,
    )

    # Wilder smooth TR, +DM, -DM
    smooth_tr = _wilder_smooth(tr, period)
    smooth_plus_dm = _wilder_smooth(plus_dm, period)
    smooth_minus_dm = _wilder_smooth(minus_dm, period)

    # DI lines
    plus_di = 100.0 * smooth_plus_dm / smooth_tr.replace(0, np.nan)
    minus_di = 100.0 * smooth_minus_dm / smooth_tr.replace(0, np.nan)

    # DX and ADX
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = _wilder_smooth(dx.fillna(0), period)

    out["adx"] = adx
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    return out


def compute_bar_regime(
    bars: pd.DataFrame,
    *,
    adx_period: int = 14,
    atr_period: int = 14,
    atr_lookback: int = 100,
    adx_trend_threshold: float = 25.0,
    atr_high_pct: float = 0.75,
    atr_low_pct: float = 0.25,
) -> pd.DataFrame:
    """
    Add regime label columns to a bars DataFrame.
    All inputs must be pre-sorted by dt ascending.
    Returns a copy with regime columns added.
    """
    # Step 1: compute ADX, +DI, -DI
    out = compute_adx(bars, period=adx_period)

    # Step 2: compute ATR via existing helper
    out["atr"] = atr_wilder(out, period=atr_period)

    # Step 3: rolling percentile rank of ATR
    atr_series = pd.to_numeric(out["atr"], errors="coerce")
    out["atr_pct_rank"] = atr_series.rolling(window=int(atr_lookback), min_periods=1).rank(pct=True)

    # Step 4: trend_direction
    plus_di = pd.to_numeric(out["plus_di"], errors="coerce")
    minus_di = pd.to_numeric(out["minus_di"], errors="coerce")

    trend_direction = pd.Series(index=out.index, dtype="object")
    trend_direction.loc[plus_di > minus_di] = "up"
    trend_direction.loc[minus_di > plus_di] = "down"
    trend_direction.loc[trend_direction.isna() & plus_di.notna() & minus_di.notna()] = "flat"
    out["trend_direction"] = trend_direction.fillna("flat")

    # Step 5: trend_strength
    adx_series = pd.to_numeric(out["adx"], errors="coerce")
    out["trend_strength"] = np.where(
        adx_series >= float(adx_trend_threshold),
        "trending",
        "ranging",
    )

    # Step 6: vol_regime
    atr_pct_rank = pd.to_numeric(out["atr_pct_rank"], errors="coerce")
    vol_regime = pd.Series(index=out.index, dtype="object")
    vol_regime.loc[atr_pct_rank >= float(atr_high_pct)] = "high_vol"
    vol_regime.loc[atr_pct_rank <= float(atr_low_pct)] = "low_vol"
    vol_regime.loc[vol_regime.isna() & atr_pct_rank.notna()] = "normal_vol"
    out["vol_regime"] = vol_regime.fillna("normal_vol")

    # Step 7: composite regime
    def _composite(row: Any) -> str:
        vol = row["vol_regime"]
        strength = row["trend_strength"]
        direction = row["trend_direction"]

        if vol == "high_vol":
            return "high_vol_expansion"
        elif strength == "trending" and direction == "up":
            return "trending_up"
        elif strength == "trending" and direction == "down":
            return "trending_down"
        elif vol == "low_vol":
            return "low_vol_compression"
        elif strength == "ranging" and vol == "normal_vol":
            return "ranging_wide"
        elif strength == "ranging":
            return "ranging_wide"
        else:
            return "ranging_wide"

    out["regime"] = out.apply(_composite, axis=1)

    return out


def summarize_daily_regime(bars_with_regime: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate per-bar regime labels to a per-day summary.
    Returns DataFrame with columns: day_id, dominant_regime, pct_trending,
    pct_ranging, pct_high_vol, avg_adx, avg_atr
    """
    if bars_with_regime is None or bars_with_regime.empty:
        return pd.DataFrame()

    df = bars_with_regime.copy()

    # Ensure day_id column
    if "day_id" not in df.columns:
        if "dt" in df.columns:
            df["day_id"] = pd.to_datetime(df["dt"], errors="coerce").dt.date.astype(str)
        else:
            return pd.DataFrame()

    required = {"regime", "trend_strength", "vol_regime"}
    missing = required - set(df.columns)
    if missing:
        return pd.DataFrame()

    rows = []
    for day_id, group in df.groupby("day_id", sort=True):
        n = len(group)
        if n == 0:
            continue

        # Dominant regime: most frequent label
        regime_counts = group["regime"].value_counts()
        dominant_regime = regime_counts.index[0] if len(regime_counts) > 0 else None

        # Trending / ranging percentages
        n_trending = int((group["trend_strength"] == "trending").sum())
        n_ranging = int((group["trend_strength"] == "ranging").sum())
        n_high_vol = int((group["vol_regime"] == "high_vol").sum())

        pct_trending = round(100.0 * n_trending / n, 2)
        pct_ranging = round(100.0 * n_ranging / n, 2)
        pct_high_vol = round(100.0 * n_high_vol / n, 2)

        avg_adx: Optional[float] = None
        if "adx" in group.columns:
            valid_adx = pd.to_numeric(group["adx"], errors="coerce").dropna()
            avg_adx = round(float(valid_adx.mean()), 4) if len(valid_adx) > 0 else None

        avg_atr: Optional[float] = None
        if "atr" in group.columns:
            valid_atr = pd.to_numeric(group["atr"], errors="coerce").dropna()
            avg_atr = round(float(valid_atr.mean()), 4) if len(valid_atr) > 0 else None

        rows.append({
            "day_id": str(day_id),
            "dominant_regime": dominant_regime,
            "pct_trending": pct_trending,
            "pct_ranging": pct_ranging,
            "pct_high_vol": pct_high_vol,
            "avg_adx": avg_adx,
            "avg_atr": avg_atr,
        })

    return pd.DataFrame(rows)
