from __future__ import annotations

"""
MA Signal Detectors
====================
Detects two classic MA-based entry setups:

MA_CROSS
  A price × MA crossover. Fires on the bar where price crosses above (bullish)
  or below (bearish) a moving average.

  Bull cross: close[t-1] <= ma[t-1]  AND  close[t] > ma[t]   (last bar crossed)
  Bear cross: close[t-1] >= ma[t-1]  AND  close[t] < ma[t]   (last bar crossed)

  Lag-safe: the detection uses {col}_above change from bar[t-1] → bar[t].
  Since {col}_above is already shifted, this is forward-look free.

MA_PULLBACK
  A trend-following pullback entry. Price was trending on one side of the MA,
  pulled back to touch or pierce it, and the current bar closes back on the
  trend side.

  Bull pullback (direction=1):
    - N bars ago: close was above MA (trend_lookback window)
    - Previous bar touched MA zone (|dist| <= touch_atr_mult)
    - Current bar closes above MA (back on trend side)
    - MA slope is positive (uptrend confirmation) — optional

  Bear pullback (direction=-1): mirror.

Both detectors require the enriched DataFrame from compute_ma_features().

Returns a signals DataFrame with columns:
  dt, direction, open, high, low, close,
  signal_ma_col  (which MA was used),
  signal_ma_val  (MA value at signal bar),
  signal_atr     (ATR at signal bar),
  signal_dist    (dist at signal bar),
  signal_slope   (slope at signal bar)
"""

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Shared output builder
# ---------------------------------------------------------------------------

def _make_signal_row(bar: pd.Series, direction: int, ma_col: str) -> Dict[str, Any]:
    return {
        "dt":           bar["dt"],
        "direction":    direction,
        "open":         bar["open"],
        "high":         bar["high"],
        "low":          bar["low"],
        "close":        bar["close"],
        "signal_ma_col": ma_col,
        "signal_ma_val": bar.get(ma_col, np.nan),
        "signal_atr":    bar.get(f"atr_{_atr_period_from(ma_col)}", np.nan),
        "signal_dist":   bar.get(f"{ma_col}_dist", np.nan),
        "signal_slope":  bar.get(f"{ma_col}_slope", np.nan),
    }


def _atr_period_from(ma_col: str) -> str:
    """Best-effort: return the atr column suffix (e.g. '14')."""
    # We always check for atr_14 (default); detectors receive enriched which has it.
    return "14"


def _collect_signals(rows: List[Dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# MA Cross detector
# ---------------------------------------------------------------------------

def detect_ma_cross(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Detect price × MA crossovers.

    Parameters (all optional with defaults)
    ----------------------------------------
    period    : int   — MA period                        default 20
    ma_type   : str   — 'ema' or 'sma'                  default 'ema'
    direction : int   — 1 (bull cross only), -1 (bear),  default 0 (both)
                        0 means detect both
    min_slope : float — minimum abs(slope) to confirm trend momentum
                        default 0.0 (no filter)
    min_atr_dist_before : float — require cross from at least this far away
                                   (ensures a real trend, not chop crossing)
                                   default 0.0 (no filter)
    """
    period    = int(params.get("period",    20))
    ma_type   = str(params.get("ma_type",   "ema")).lower()
    direction = int(params.get("direction", 0))
    min_slope = float(params.get("min_slope", 0.0))
    min_dist_before = float(params.get("min_atr_dist_before", 0.0))

    ma_col = f"ma_{period}_{ma_type}"
    ab_col = f"{ma_col}_above"
    sl_col = f"{ma_col}_slope"

    if ma_col not in enriched.columns or ab_col not in enriched.columns:
        return pd.DataFrame()

    above      = enriched[ab_col]
    prev_above = above.shift(1)
    slope      = enriched.get(sl_col, pd.Series(0.0, index=enriched.index))
    dist       = enriched.get(f"{ma_col}_dist", pd.Series(np.nan, index=enriched.index))

    rows: List[Dict] = []

    for i in range(1, len(enriched)):
        bar      = enriched.iloc[i]
        cur_ab   = int(above.iloc[i])
        prev_ab  = int(prev_above.iloc[i])
        sl_val   = float(slope.iloc[i]) if not pd.isna(slope.iloc[i]) else 0.0
        dist_prev = abs(float(dist.iloc[i - 1])) if not pd.isna(dist.iloc[i - 1]) else 0.0

        # Bull cross: was at or below, now above
        if cur_ab == 1 and prev_ab != 1:
            if direction in (0, 1):
                if abs(sl_val) >= min_slope and dist_prev >= min_dist_before:
                    rows.append(_make_signal_row(bar, 1, ma_col))

        # Bear cross: was at or above, now below
        elif cur_ab == -1 and prev_ab != -1:
            if direction in (0, -1):
                if abs(sl_val) >= min_slope and dist_prev >= min_dist_before:
                    rows.append(_make_signal_row(bar, -1, ma_col))

    return _collect_signals(rows)


# ---------------------------------------------------------------------------
# MA Pullback detector
# ---------------------------------------------------------------------------

def detect_ma_pullback(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Detect pullback-to-MA entries.

    Parameters
    ----------
    period              : int   — MA period                      default 20
    ma_type             : str   — 'ema' or 'sma'                 default 'ema'
    direction           : int   — 1, -1, or 0 (both)            default 0
    trend_lookback      : int   — bars to look back for trend    default 10
    trend_bars_min      : int   — minimum bars on trend side     default 6
    touch_atr_mult      : float — |dist| <= this = "touching MA" default 0.5
    require_slope       : bool  — slope must confirm direction   default True
    close_back_required : bool  — bar must close back on trend side default True
    """
    period         = int(params.get("period",           20))
    ma_type        = str(params.get("ma_type",          "ema")).lower()
    direction      = int(params.get("direction",        0))
    trend_lookback = int(params.get("trend_lookback",   10))
    trend_bars_min = int(params.get("trend_bars_min",   6))
    touch_mult     = float(params.get("touch_atr_mult", 0.5))
    req_slope      = bool(params.get("require_slope",   True))
    close_back     = bool(params.get("close_back_required", True))

    ma_col = f"ma_{period}_{ma_type}"
    ab_col = f"{ma_col}_above"
    sl_col = f"{ma_col}_slope"
    di_col = f"{ma_col}_dist"

    if ma_col not in enriched.columns or ab_col not in enriched.columns:
        return pd.DataFrame()

    above = enriched[ab_col].values
    slope = enriched[sl_col].values if sl_col in enriched.columns else np.zeros(len(enriched))
    dist  = enriched[di_col].values if di_col in enriched.columns else np.full(len(enriched), np.nan)

    rows: List[Dict] = []
    directions_to_check = [1, -1] if direction == 0 else [direction]

    for i in range(trend_lookback + 1, len(enriched)):
        bar = enriched.iloc[i]

        for d in directions_to_check:
            # Trend window: look back trend_lookback bars
            window_above = above[i - trend_lookback: i]
            n_on_trend   = int((window_above == d).sum())
            if n_on_trend < trend_bars_min:
                continue

            # Previous bar must be touching MA (in the touch zone)
            prev_dist = dist[i - 1]
            if pd.isna(prev_dist) or abs(prev_dist) > touch_mult:
                continue

            # Current bar must close back on trend side (or be in touch zone)
            cur_above = int(above[i])
            if close_back and cur_above != d:
                continue

            # Slope confirmation
            sl_val = float(slope[i]) if not pd.isna(slope[i]) else 0.0
            if req_slope and np.sign(sl_val) != d:
                continue

            rows.append(_make_signal_row(bar, d, ma_col))

    return _collect_signals(rows)


# ---------------------------------------------------------------------------
# Signal registry
# ---------------------------------------------------------------------------

MA_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "ma_cross":    detect_ma_cross,
    "ma_pullback": detect_ma_pullback,
}

MA_SIGNAL_LABELS: Dict[str, str] = {
    "ma_cross":    "MA Cross",
    "ma_pullback": "MA Pullback to MA",
}


def detect_ma_signal(
    signal_id: str,
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """Dispatch to the named MA signal detector."""
    fn = MA_SIGNAL_REGISTRY.get(signal_id)
    if fn is None:
        raise ValueError(f"Unknown MA signal: {signal_id!r}. Available: {list(MA_SIGNAL_REGISTRY)}")
    return fn(enriched, params)
