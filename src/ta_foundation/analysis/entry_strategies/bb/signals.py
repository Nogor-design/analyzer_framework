from __future__ import annotations

"""
Bollinger Band Signal Detectors
==================================
Four BB-based entry setups:

BB_MEAN_REVERSION
  Price touches or pierces a band then closes back inside.
  Direction trades *against* the band touch — fade the extreme.
    Bull: price closed below lower band last bar, current bar closes above lower band.
    Bear: price closed above upper band last bar, current bar closes below upper band.

BB_CONTINUATION
  Price closes outside the band — momentum continuation trade *with* the breakout.
    Bull: close > upper band  → direction 1
    Bear: close < lower band  → direction -1
  Optional: require a prior squeeze (bb_squeeze was 1 in the last N bars).

BB_SQUEEZE_BREAKOUT
  Fire when price breaks out of a squeeze:
    - Last N bars had squeeze == 1 (low bandwidth)
    - Current bar's price moves more than breakout_atr_mult × ATR from mid
  Direction follows the breakout side.

BB_WALK_THE_BAND
  Price has been outside (or touching) the same band for consecutive_bars bars.
  Continuation entry in the direction of the walk.

All detectors return a signals DataFrame with standard columns:
  dt, direction, open, high, low, close
  + bb_pct_b, bb_z, bb_width, bb_squeeze (at signal bar)
"""

from typing import Any, Callable, Dict, List

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helper: resolve BB column prefix from params
# ---------------------------------------------------------------------------

def _bb_prefix(params: Dict[str, Any]) -> str:
    period  = int(params.get("period", 20))
    n_std   = float(params.get("n_std", 2.0))
    std_str = str(int(n_std)) if n_std == int(n_std) else str(n_std)
    return f"bb_{period}_{std_str}"


def _make_signal_row(bar: pd.Series, direction: int, pfx: str) -> Dict[str, Any]:
    # Try to find ATR column (atr_14 is the default from bb features)
    atr_val = np.nan
    for col in bar.index:
        if col.startswith("atr_"):
            v = bar[col]
            if not (isinstance(v, float) and np.isnan(v)):
                atr_val = float(v)
            break

    return {
        "dt":         bar["dt"],
        "direction":  direction,
        "open":       bar["open"],
        "high":       bar["high"],
        "low":        bar["low"],
        "close":      bar["close"],
        "atr":        atr_val,       # required by ATR-based outcome simulator
        "bb_pct_b":   bar.get(f"{pfx}_pct_b",  np.nan),
        "bb_z":       bar.get(f"{pfx}_z",       np.nan),
        "bb_width":   bar.get(f"{pfx}_width",   np.nan),
        "bb_squeeze": int(bar.get(f"{pfx}_squeeze", 0)),
        "bb_mid":     bar.get(f"{pfx}_mid",     np.nan),
        "bb_upper":   bar.get(f"{pfx}_upper",   np.nan),
        "bb_lower":   bar.get(f"{pfx}_lower",   np.nan),
    }


def _collect(rows: List[Dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# BB Mean Reversion
# ---------------------------------------------------------------------------

def detect_bb_mean_reversion(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Fade extreme: price touches/pierces band then closes back inside.

    Parameters
    ----------
    period           : BB period (must match a computed column)  default 20
    n_std            : BB std multiplier                         default 2.0
    direction        : 1, -1, or 0 (both)                       default 0
    pct_b_touch_max  : %B threshold for lower touch (bull)       default 0.0
                       bar[t-1] pct_b <= pct_b_touch_max to qualify
    pct_b_return_min : %B threshold for return (bull)            default 0.1
                       bar[t] pct_b > pct_b_return_min to confirm
    min_z_extreme    : minimum abs(z-score) at touch bar         default 1.5
    """
    period          = int(params.get("period",           20))
    n_std           = float(params.get("n_std",          2.0))
    direction_cfg   = int(params.get("direction",        0))
    touch_max       = float(params.get("pct_b_touch_max",   0.0))
    return_min      = float(params.get("pct_b_return_min",  0.1))
    min_z           = float(params.get("min_z_extreme",     1.5))

    pfx = _bb_prefix(params)
    pct_b_col = f"{pfx}_pct_b"
    z_col     = f"{pfx}_z"

    if pct_b_col not in enriched.columns:
        return pd.DataFrame()

    pct_b     = enriched[pct_b_col].values
    z_vals    = enriched[z_col].values if z_col in enriched.columns else np.zeros(len(enriched))
    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(1, len(enriched)):
        bar      = enriched.iloc[i]
        pb_cur   = pct_b[i]
        pb_prev  = pct_b[i - 1]
        z_prev   = z_vals[i - 1]

        if any(np.isnan(v) for v in (pb_cur, pb_prev)):
            continue

        for d in directions:
            if d == 1:
                # Bull reversion: prev bar touched lower band, now closing back up
                if pb_prev <= touch_max and abs(z_prev) >= min_z and pb_cur > return_min:
                    rows.append(_make_signal_row(bar, 1, pfx))
            else:
                # Bear reversion: prev bar touched upper band, now closing back down
                if pb_prev >= (1.0 - touch_max) and abs(z_prev) >= min_z and pb_cur < (1.0 - return_min):
                    rows.append(_make_signal_row(bar, -1, pfx))

    return _collect(rows)


# ---------------------------------------------------------------------------
# BB Continuation (breakout with or without squeeze prerequisite)
# ---------------------------------------------------------------------------

def detect_bb_continuation(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Momentum continuation: close outside the band.

    Parameters
    ----------
    period               : BB period               default 20
    n_std                : BB std multiplier        default 2.0
    direction            : 1, -1, or 0 (both)      default 0
    require_prior_squeeze: if True, a squeeze must  default False
                           have occurred in last
                           squeeze_lookback bars
    squeeze_lookback     : bars to look back        default 5
    min_pct_b_bull       : minimum %B for bull      default 1.0 (outside upper)
    max_pct_b_bear       : maximum %B for bear      default 0.0 (outside lower)
    """
    direction_cfg   = int(params.get("direction",               0))
    req_squeeze     = bool(params.get("require_prior_squeeze",  False))
    squeeze_lb      = int(params.get("squeeze_lookback",        5))
    min_pb_bull     = float(params.get("min_pct_b_bull",        1.0))
    max_pb_bear     = float(params.get("max_pct_b_bear",        0.0))

    pfx       = _bb_prefix(params)
    pct_b_col = f"{pfx}_pct_b"
    sq_col    = f"{pfx}_squeeze"

    if pct_b_col not in enriched.columns:
        return pd.DataFrame()

    pct_b    = enriched[pct_b_col].values
    squeeze  = enriched[sq_col].values if sq_col in enriched.columns else np.zeros(len(enriched))
    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(squeeze_lb, len(enriched)):
        bar   = enriched.iloc[i]
        pb    = pct_b[i]

        if np.isnan(pb):
            continue

        # Prior squeeze check
        if req_squeeze:
            had_squeeze = squeeze[max(0, i - squeeze_lb): i].any()
            if not had_squeeze:
                continue

        for d in directions:
            if d == 1 and pb >= min_pb_bull:
                rows.append(_make_signal_row(bar, 1, pfx))
            elif d == -1 and pb <= max_pb_bear:
                rows.append(_make_signal_row(bar, -1, pfx))

    return _collect(rows)


# ---------------------------------------------------------------------------
# BB Squeeze Breakout
# ---------------------------------------------------------------------------

def detect_bb_squeeze_breakout(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Fire when price breaks out of a low-bandwidth squeeze.

    Parameters
    ----------
    period             : BB period                    default 20
    n_std              : BB std multiplier            default 2.0
    direction          : 1, -1, or 0 (both)          default 0
    min_squeeze_bars   : consecutive squeeze bars     default 3
    breakout_z_min     : min abs(z-score) for fire    default 1.8
    require_close_out  : close must be outside band   default False
    """
    direction_cfg    = int(params.get("direction",          0))
    min_sq_bars      = int(params.get("min_squeeze_bars",   3))
    breakout_z       = float(params.get("breakout_z_min",   1.8))
    req_close_out    = bool(params.get("require_close_out", False))

    pfx     = _bb_prefix(params)
    sq_col  = f"{pfx}_squeeze"
    z_col   = f"{pfx}_z"
    pb_col  = f"{pfx}_pct_b"

    if sq_col not in enriched.columns:
        return pd.DataFrame()

    squeeze  = enriched[sq_col].values
    z_vals   = enriched[z_col].values if z_col in enriched.columns else np.zeros(len(enriched))
    pct_b    = enriched[pb_col].values if pb_col in enriched.columns else np.full(len(enriched), np.nan)
    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(min_sq_bars, len(enriched)):
        bar = enriched.iloc[i]

        # Count consecutive squeeze bars ending at i-1
        sq_count = 0
        for j in range(i - 1, max(i - 1 - min_sq_bars * 3, 0) - 1, -1):
            if squeeze[j]:
                sq_count += 1
            else:
                break

        if sq_count < min_sq_bars:
            continue

        z_cur = float(z_vals[i]) if not np.isnan(z_vals[i]) else 0.0
        pb    = float(pct_b[i])  if not np.isnan(pct_b[i])  else 0.5

        if abs(z_cur) < breakout_z:
            continue

        for d in directions:
            if d == 1 and z_cur > 0:
                if not req_close_out or pb >= 1.0:
                    rows.append(_make_signal_row(bar, 1, pfx))
            elif d == -1 and z_cur < 0:
                if not req_close_out or pb <= 0.0:
                    rows.append(_make_signal_row(bar, -1, pfx))

    return _collect(rows)


# ---------------------------------------------------------------------------
# BB Walk the Band
# ---------------------------------------------------------------------------

def detect_bb_walk(
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Price walks the upper or lower band for N consecutive bars.
    Continuation entry in the direction of the walk.

    Parameters
    ----------
    period           : BB period               default 20
    n_std            : BB std multiplier       default 2.0
    direction        : 1, -1, or 0 (both)     default 0
    consecutive_bars : bars touching/beyond    default 3
    pct_b_walk_min   : %B threshold for upper  default 0.8  (bull walk)
                       mirrors for bear:  1 - pct_b_walk_min
    """
    direction_cfg   = int(params.get("direction",          0))
    consec          = int(params.get("consecutive_bars",   3))
    pb_walk_min     = float(params.get("pct_b_walk_min",   0.8))

    pfx     = _bb_prefix(params)
    pb_col  = f"{pfx}_pct_b"

    if pb_col not in enriched.columns:
        return pd.DataFrame()

    pct_b    = enriched[pb_col].values
    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(consec, len(enriched)):
        bar = enriched.iloc[i]

        for d in directions:
            if d == 1:
                # Bull walk: last `consec` bars all had pct_b >= pb_walk_min
                window = pct_b[i - consec: i]
                if all(not np.isnan(v) and v >= pb_walk_min for v in window):
                    rows.append(_make_signal_row(bar, 1, pfx))
            else:
                # Bear walk: last `consec` bars all had pct_b <= (1 - pb_walk_min)
                threshold = 1.0 - pb_walk_min
                window = pct_b[i - consec: i]
                if all(not np.isnan(v) and v <= threshold for v in window):
                    rows.append(_make_signal_row(bar, -1, pfx))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BB_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "bb_mean_reversion":   detect_bb_mean_reversion,
    "bb_continuation":     detect_bb_continuation,
    "bb_squeeze_breakout": detect_bb_squeeze_breakout,
    "bb_walk":             detect_bb_walk,
}

BB_SIGNAL_LABELS: Dict[str, str] = {
    "bb_mean_reversion":   "BB Mean Reversion (fade extreme)",
    "bb_continuation":     "BB Continuation (breakout)",
    "bb_squeeze_breakout": "BB Squeeze Breakout",
    "bb_walk":             "BB Walk the Band",
}


def detect_bb_signal(
    signal_id: str,
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    fn = BB_SIGNAL_REGISTRY.get(signal_id)
    if fn is None:
        raise ValueError(f"Unknown BB signal: {signal_id!r}. Available: {list(BB_SIGNAL_REGISTRY)}")
    return fn(enriched, params)
