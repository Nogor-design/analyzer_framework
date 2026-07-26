"""
Shared feature primitives used by horizon agents.

Phase 1/2 shipped two agents (`StatisticalProbabilityAgent`,
`AnalogueProbabilityAgent`) that grew up side-by-side and duplicated:

  - the per-bar session / regime / ATR computation,
  - the `_lite_outcome` walker that turns a future window into
    (direction, return, MFE, MAE, threshold-first-hit).

The duplication was load-bearing — both agents define a "neutral"
direction band as 0.30 × prior_atr, both compute regime as
close-vs-SMA banded by 0.5 × ATR, and the analogue agent's tests
explicitly compare its outputs to the statistical agent's. That
agreement is fragile when the implementations live in separate files.

This module is the single source of truth for those primitives. Both
agents now call into it; downstream agents that want to add new
feature columns can layer their own logic on top of `compute_session_regime_atr`.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

from .session_classifier import SessionConfig, label_sessions_for_index

# Net displacement beyond ATR fraction needed to call a horizon "directional".
# Shared with the daily outcome_measurer for consistency.
NEUTRAL_ATR_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# ATR / regime / session
# ---------------------------------------------------------------------------

def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    """
    Wilder-style true range, smoothed by a simple rolling mean. Falls
    back to a partial mean during warm-up so the array is always the
    same length as the inputs.
    """
    if len(closes) == 0:
        return np.empty(0, dtype=float)
    prev_close = np.concatenate([[closes[0]], closes[:-1]])
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - prev_close),
        np.abs(lows - prev_close),
    ])
    return pd.Series(tr).rolling(int(period), min_periods=1).mean().to_numpy()


def compute_regime_labels(
    closes: np.ndarray,
    sma_period: int,
    atr: np.ndarray,
    atr_band_frac: float,
) -> np.ndarray:
    """
    Regime = close vs SMA, banded by `atr_band_frac × atr`.

      close > SMA + band → "trend_up"
      close < SMA - band → "trend_down"
      otherwise          → "range"
    """
    n = len(closes)
    if n == 0:
        return np.empty(0, dtype=object)
    sma = pd.Series(closes).rolling(int(sma_period), min_periods=1).mean().to_numpy()
    band = float(atr_band_frac) * atr
    regime = np.full(n, "range", dtype=object)
    regime[closes > sma + band] = "trend_up"
    regime[closes < sma - band] = "trend_down"
    return regime


def compute_session_regime_atr(
    bars: pd.DataFrame,
    *,
    atr_period: int,
    sma_period: int,
    atr_band_frac: float,
    session_config: Optional[SessionConfig] = None,
) -> pd.DataFrame:
    """
    Per-bar features used both for asof-bucket lookup and for historical
    bucketing inside the horizon agents.

    Returns a DataFrame aligned to `bars` with columns:
        session, regime, atr
    """
    if "dt" not in bars.columns:
        raise ValueError("bars must include a 'dt' column")

    closes = bars["close"].astype(float).to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()

    atr = compute_atr(highs, lows, closes, atr_period)
    regime = compute_regime_labels(closes, sma_period, atr, atr_band_frac)

    dt_index = pd.DatetimeIndex(bars["dt"])
    sessions = label_sessions_for_index(dt_index, session_config).to_numpy()

    return pd.DataFrame({
        "session": sessions,
        "regime": regime,
        "atr": atr,
    })


# ---------------------------------------------------------------------------
# Lite outcome — used by both statistical and analogue agents to bucket history
# ---------------------------------------------------------------------------

def compute_lite_outcome(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    asof_idx: int,
    horizon: int,
    prior_atr: float,
    upside_atr: float,
    downside_atr: float,
    *,
    neutral_atr_threshold: float = NEUTRAL_ATR_THRESHOLD,
) -> Optional[Tuple[str, float, float, float, str]]:
    """
    Compute a compact outcome snapshot for one historical asof.

    Returns `(direction, return_pts, mfe_pts, mae_pts, threshold_label)`
    or `None` when the asof has no future bars to measure (i.e.,
    `asof_idx + horizon >= len(closes)`).

    `threshold_label` ∈ {"upside_first", "downside_first",
    "both_same_bar", "neither"} resolves the direction of the first
    threshold hit when both upside and downside thresholds are active.
    Setting either threshold ATR multiplier to 0 disables that side.
    """
    end_idx = asof_idx + int(horizon)
    if end_idx >= len(closes):
        return None

    asof_close = float(closes[asof_idx])
    fut_high = float(highs[asof_idx + 1 : end_idx + 1].max())
    fut_low = float(lows[asof_idx + 1 : end_idx + 1].min())
    fut_close = float(closes[end_idx])

    net = fut_close - asof_close
    threshold = float(neutral_atr_threshold) * prior_atr

    if net > threshold:
        direction = "bullish"
    elif net < -threshold:
        direction = "bearish"
    else:
        direction = "neutral"

    mfe = max(0.0, fut_high - asof_close)
    mae = max(0.0, asof_close - fut_low)

    up_pts = upside_atr * prior_atr if upside_atr > 0.0 else 0.0
    down_pts = downside_atr * prior_atr if downside_atr > 0.0 else 0.0
    up_level = asof_close + up_pts if up_pts > 0.0 else None
    down_level = asof_close - down_pts if down_pts > 0.0 else None

    label = "neither"
    for j in range(asof_idx + 1, end_idx + 1):
        h = float(highs[j])
        l = float(lows[j])
        bar_up = up_level is not None and h >= up_level
        bar_down = down_level is not None and l <= down_level
        if bar_up and bar_down:
            label = "both_same_bar"
            break
        if bar_up:
            label = "upside_first"
            break
        if bar_down:
            label = "downside_first"
            break

    return direction, float(net), float(mfe), float(mae), label
