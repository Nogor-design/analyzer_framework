from __future__ import annotations

"""
Candle Pattern Detectors
=========================
Each public ``detect_*`` function takes an *enriched* bars DataFrame
(output of ``candle.features.compute_candle_features``) plus a ``params``
dict, and returns a **signals DataFrame** — one row per detected signal —
with the signal bar's full feature set plus a ``direction`` column.

Direction semantics
-------------------
  direction =  1  → long entry suggested
  direction = -1  → short entry suggested

For "with_candle" patterns the direction is derived from is_bullish.
For "against_candle" (fade) patterns the direction is the opposite.
For fixed-direction patterns (pin_bar_bullish, engulfing_bullish, …)
the direction is always the pattern's inherent value.

The ``direction`` param in the params dict controls which subset to emit:
   1  → emit only long signals
  -1  → emit only short signals
   0  → emit both (default)

Pattern registry
----------------
``PATTERN_REGISTRY`` maps a string pattern_id to its detect function.
Use ``detect_pattern(pattern_id, enriched, params)`` as the single entry point.
"""

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _guard(enriched: pd.DataFrame, required: List[str]) -> bool:
    """Return True if all required columns are present."""
    return all(c in enriched.columns for c in required)


def _emit(
    enriched: pd.DataFrame,
    mask: pd.Series,
    direction: int,
    requested_dir: int,
) -> pd.DataFrame:
    """
    From *enriched*, select rows where *mask* is True, assign *direction*,
    and filter to *requested_dir* (0 = both).  Returns a fresh DataFrame.
    """
    if mask is None or not mask.any():
        return pd.DataFrame()
    if requested_dir != 0 and direction != requested_dir:
        return pd.DataFrame()
    out = enriched[mask].copy()
    out["direction"] = direction
    return out.reset_index(drop=True)


def _combine(*frames: pd.DataFrame) -> pd.DataFrame:
    valid = [f for f in frames if f is not None and not f.empty]
    if not valid:
        return pd.DataFrame()
    return pd.concat(valid, ignore_index=True)


def _comparison_col(params: dict, prefix: str) -> str:
    """
    Resolve which comparison column to use for size/body thresholding.

    Precedence:
      1. Explicit ``{prefix}_col`` key in params
      2. Combination of ``comparison_mode`` + ``lookback``
      3. Fallback: body_vs_roll_20 / size_vs_roll_20
    """
    explicit = params.get(f"{prefix}_col")
    if explicit:
        return str(explicit)
    mode = str(params.get("comparison_mode", "rolling_avg"))
    if mode == "atr_normalized":
        return f"{prefix}_vs_atr"
    if mode == "session_avg":
        return f"{prefix}_vs_session"
    # rolling_avg (default)
    n = int(params.get("lookback", 20))
    return f"{prefix}_vs_roll_{n}"


# ---------------------------------------------------------------------------
# 1. Large-body momentum  (direction follows the candle)
# ---------------------------------------------------------------------------

def detect_large_body(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Large-body candle with small wicks — strong directional conviction.

    Params
    ------
    body_multiplier   : body >= body_multiplier * avg_body          default 1.5
    wick_to_body_max  : both upper_wick_to_body and lower_wick_to_body
                        must be <= this value                        default 0.5
    min_size_ticks    : total_range in ticks >= this                 default 4
    max_size_ticks    : total_range in ticks <= this                 default 200
    comparison_mode   : rolling_avg | session_avg | atr_normalized   default rolling_avg
    lookback          : lookback N for rolling_avg mode              default 20
    direction         : 1 | -1 | 0=both                             default 0

    With direction=1:  emit only signals from bullish bars → long entries.
    With direction=-1: emit only signals from bearish bars → short entries.
    With direction=0:  emit both → "trend-with-candle" discovery.
    """
    req = ["is_bullish", "body_to_range", "upper_wick_to_body", "lower_wick_to_body",
           "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    body_col   = _comparison_col(params, "body")
    body_mult  = float(params.get("body_multiplier", 1.5))
    wick_max   = float(params.get("wick_to_body_max", 0.5))
    min_ticks  = float(params.get("min_size_ticks", 4))
    max_ticks  = float(params.get("max_size_ticks", 200))
    req_dir    = int(params.get("direction", 0))

    if body_col not in enriched.columns:
        return pd.DataFrame()

    large_body = enriched[body_col].fillna(0) >= body_mult
    small_wicks = (
        (enriched["upper_wick_to_body"].fillna(0) <= wick_max) &
        (enriched["lower_wick_to_body"].fillna(0) <= wick_max)
    )
    in_bounds = (
        (enriched["size_ticks"] >= min_ticks) &
        (enriched["size_ticks"] <= max_ticks)
    )

    base = large_body & small_wicks & in_bounds

    long_df  = _emit(enriched, base & enriched["is_bullish"],  direction=1,  requested_dir=req_dir)
    short_df = _emit(enriched, base & ~enriched["is_bullish"], direction=-1, requested_dir=req_dir)
    return _combine(long_df, short_df)


# ---------------------------------------------------------------------------
# 2. Pin bar — bullish (hammer)
# ---------------------------------------------------------------------------

def detect_pin_bar_bullish(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Bullish pin bar (hammer): large lower wick, small upper wick, small body vs range.
    Always emits long signals (direction=1).

    Params
    ------
    wick_to_body_min         : lower_wick_to_body >= this           default 1.5
    upper_wick_to_body_max   : upper_wick_to_body <= this           default 0.5
    body_to_range_max        : body_to_range <= this                default 0.35
    min_size_ticks           : size_ticks >=                        default 4
    max_size_ticks           : size_ticks <=                        default 200
    direction                : ignored (always 1); use for filter only
    """
    req = ["lower_wick_to_body", "upper_wick_to_body", "body_to_range", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    wick_min    = float(params.get("wick_to_body_min", 1.5))
    upper_max   = float(params.get("upper_wick_to_body_max", 0.5))
    btr_max     = float(params.get("body_to_range_max", 0.35))
    min_ticks   = float(params.get("min_size_ticks", 4))
    max_ticks   = float(params.get("max_size_ticks", 200))
    req_dir     = int(params.get("direction", 0))

    mask = (
        (enriched["lower_wick_to_body"].fillna(0) >= wick_min) &
        (enriched["upper_wick_to_body"].fillna(0) <= upper_max) &
        (enriched["body_to_range"].fillna(1) <= btr_max) &
        (enriched["size_ticks"] >= min_ticks) &
        (enriched["size_ticks"] <= max_ticks)
    )
    return _emit(enriched, mask, direction=1, requested_dir=req_dir if req_dir != -1 else 0)


# ---------------------------------------------------------------------------
# 3. Pin bar — bearish (shooting star)
# ---------------------------------------------------------------------------

def detect_pin_bar_bearish(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Bearish pin bar (shooting star): large upper wick, small lower wick, small body vs range.
    Always emits short signals (direction=-1).

    Params
    ------
    wick_to_body_min         : upper_wick_to_body >= this           default 1.5
    lower_wick_to_body_max   : lower_wick_to_body <= this           default 0.5
    body_to_range_max        : body_to_range <= this                default 0.35
    min_size_ticks           : size_ticks >=                        default 4
    max_size_ticks           : size_ticks <=                        default 200
    """
    req = ["upper_wick_to_body", "lower_wick_to_body", "body_to_range", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    wick_min  = float(params.get("wick_to_body_min", 1.5))
    lower_max = float(params.get("lower_wick_to_body_max", 0.5))
    btr_max   = float(params.get("body_to_range_max", 0.35))
    min_ticks = float(params.get("min_size_ticks", 4))
    max_ticks = float(params.get("max_size_ticks", 200))
    req_dir   = int(params.get("direction", 0))

    mask = (
        (enriched["upper_wick_to_body"].fillna(0) >= wick_min) &
        (enriched["lower_wick_to_body"].fillna(0) <= lower_max) &
        (enriched["body_to_range"].fillna(1) <= btr_max) &
        (enriched["size_ticks"] >= min_ticks) &
        (enriched["size_ticks"] <= max_ticks)
    )
    return _emit(enriched, mask, direction=-1, requested_dir=req_dir if req_dir != 1 else 0)


# ---------------------------------------------------------------------------
# 4. Inside bar  (breakout direction follows close vs prior bar midpoint)
# ---------------------------------------------------------------------------

def detect_inside_bar(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Inside bar: current bar's range is entirely within the prior bar's range.
    Signal fires on the INSIDE bar itself; entry is taken on the breakout of
    the inside bar's range in the next bar (handled in signals.py).

    Direction assignment:
      is_bullish (close >= prior midpoint) → long  (upside breakout expected)
      bearish                               → short (downside breakout expected)
    Can be overridden with direction param.

    Params
    ------
    min_size_ticks  : default 2
    direction       : 0=both (default), 1=long only, -1=short only
    """
    req = ["high", "low", "is_bullish", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    min_ticks = float(params.get("min_size_ticks", 2))
    req_dir   = int(params.get("direction", 0))

    prev_high = enriched["high"].shift(1)
    prev_low  = enriched["low"].shift(1)

    inside = (
        (enriched["high"] < prev_high) &
        (enriched["low"] > prev_low) &
        (enriched["size_ticks"] >= min_ticks)
    )

    long_df  = _emit(enriched, inside & enriched["is_bullish"],  direction=1,  requested_dir=req_dir)
    short_df = _emit(enriched, inside & ~enriched["is_bullish"], direction=-1, requested_dir=req_dir)
    return _combine(long_df, short_df)


# ---------------------------------------------------------------------------
# 5. Outside bar  (range exceeds prior bar; direction follows close)
# ---------------------------------------------------------------------------

def detect_outside_bar(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Outside bar: current bar's range fully exceeds the prior bar's range.
    Direction follows the candle close (bullish → long, bearish → short).

    Params
    ------
    min_size_ticks  : default 4
    direction       : 0=both (default), 1, -1
    """
    req = ["high", "low", "is_bullish", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    min_ticks = float(params.get("min_size_ticks", 4))
    req_dir   = int(params.get("direction", 0))

    prev_high = enriched["high"].shift(1)
    prev_low  = enriched["low"].shift(1)

    outside = (
        (enriched["high"] > prev_high) &
        (enriched["low"] < prev_low) &
        (enriched["size_ticks"] >= min_ticks)
    )

    long_df  = _emit(enriched, outside & enriched["is_bullish"],  direction=1,  requested_dir=req_dir)
    short_df = _emit(enriched, outside & ~enriched["is_bullish"], direction=-1, requested_dir=req_dir)
    return _combine(long_df, short_df)


# ---------------------------------------------------------------------------
# 6. Engulfing — bullish
# ---------------------------------------------------------------------------

def detect_engulfing_bullish(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Bullish engulfing: current bar is bullish AND its body fully wraps the
    prior bar's body (open <= prior_close AND close >= prior_open).
    Always emits long signals.

    Params
    ------
    engulf_ratio    : current_body >= prior_body * engulf_ratio     default 1.0
    min_size_ticks  : default 4
    direction       : ignored (always 1)
    """
    req = ["open", "close", "is_bullish", "body", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    engulf_ratio = float(params.get("engulf_ratio", 1.0))
    min_ticks    = float(params.get("min_size_ticks", 4))
    req_dir      = int(params.get("direction", 0))

    o  = enriched["open"].astype(float)
    c  = enriched["close"].astype(float)
    po = o.shift(1)
    pc = c.shift(1)
    pb = enriched["body"].shift(1).fillna(0)

    mask = (
        enriched["is_bullish"] &
        (o <= pc) &
        (c >= po) &
        (enriched["body"] >= pb * engulf_ratio) &
        (enriched["size_ticks"] >= min_ticks)
    )
    return _emit(enriched, mask, direction=1, requested_dir=req_dir if req_dir != -1 else 0)


# ---------------------------------------------------------------------------
# 7. Engulfing — bearish
# ---------------------------------------------------------------------------

def detect_engulfing_bearish(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Bearish engulfing: current bar is bearish AND its body fully wraps the
    prior bar's body.  Always emits short signals.

    Params
    ------
    engulf_ratio    : default 1.0
    min_size_ticks  : default 4
    direction       : ignored (always -1)
    """
    req = ["open", "close", "is_bullish", "body", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    engulf_ratio = float(params.get("engulf_ratio", 1.0))
    min_ticks    = float(params.get("min_size_ticks", 4))
    req_dir      = int(params.get("direction", 0))

    o  = enriched["open"].astype(float)
    c  = enriched["close"].astype(float)
    po = o.shift(1)
    pc = c.shift(1)
    pb = enriched["body"].shift(1).fillna(0)

    mask = (
        ~enriched["is_bullish"] &
        (o >= pc) &
        (c <= po) &
        (enriched["body"] >= pb * engulf_ratio) &
        (enriched["size_ticks"] >= min_ticks)
    )
    return _emit(enriched, mask, direction=-1, requested_dir=req_dir if req_dir != 1 else 0)


# ---------------------------------------------------------------------------
# 8. Doji  (indecision candle — tiny body vs range)
# ---------------------------------------------------------------------------

def detect_doji(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Doji: body is very small relative to the total range.
    Both directions are emitted (data decides which side has edge).

    Params
    ------
    body_to_range_max : body_to_range <= this                       default 0.15
    min_size_ticks    : total_range in ticks >=                     default 4
    direction         : 0=both (default), 1, -1
    """
    req = ["body_to_range", "is_bullish", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    btr_max   = float(params.get("body_to_range_max", 0.15))
    min_ticks = float(params.get("min_size_ticks", 4))
    req_dir   = int(params.get("direction", 0))

    mask = (
        (enriched["body_to_range"].fillna(1) <= btr_max) &
        (enriched["size_ticks"] >= min_ticks)
    )

    # For a doji, we emit both long and short for discovery; the analysis will
    # show whether fading or continuing after indecision has edge.
    long_df  = _emit(enriched, mask, direction=1,  requested_dir=req_dir)
    short_df = _emit(enriched, mask, direction=-1, requested_dir=req_dir)
    return _combine(long_df, short_df)


# ---------------------------------------------------------------------------
# 9. Clean breakout bar  (large ATR-relative range, mostly body, new extreme)
# ---------------------------------------------------------------------------

def detect_clean_breakout_bar(enriched: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Clean breakout bar: large candle (vs ATR), body dominates the range,
    and the bar closes at or near a rolling high/low extreme.
    Direction follows the candle close.

    Params
    ------
    atr_mult          : size_vs_atr >= this                          default 1.5
    body_to_range_min : body_to_range >= this                        default 0.60
    extreme_lookback  : bars to check for new high/low extreme       default 20
    min_size_ticks    : default 4
    direction         : 0=both, 1, -1
    """
    req = ["size_vs_atr", "body_to_range", "is_bullish", "high", "low", "size_ticks"]
    if not _guard(enriched, req):
        return pd.DataFrame()

    atr_mult    = float(params.get("atr_mult", 1.5))
    btr_min     = float(params.get("body_to_range_min", 0.60))
    ext_lookback = int(params.get("extreme_lookback", 20))
    min_ticks   = float(params.get("min_size_ticks", 4))
    req_dir     = int(params.get("direction", 0))

    # Large candle vs ATR
    large = enriched["size_vs_atr"].fillna(0) >= atr_mult

    # Body dominates range
    clean = enriched["body_to_range"].fillna(0) >= btr_min

    # At a new rolling extreme (shift(1) so we compare against PRIOR n bars, no look-ahead)
    roll_high = enriched["high"].shift(1).rolling(ext_lookback, min_periods=1).max()
    roll_low  = enriched["low"].shift(1).rolling(ext_lookback, min_periods=1).min()

    at_new_high = enriched["high"] >= roll_high
    at_new_low  = enriched["low"]  <= roll_low

    in_bounds = enriched["size_ticks"] >= min_ticks

    long_mask  = large & clean & in_bounds & enriched["is_bullish"] & at_new_high
    short_mask = large & clean & in_bounds & ~enriched["is_bullish"] & at_new_low

    long_df  = _emit(enriched, long_mask,  direction=1,  requested_dir=req_dir)
    short_df = _emit(enriched, short_mask, direction=-1, requested_dir=req_dir)
    return _combine(long_df, short_df)


# ---------------------------------------------------------------------------
# Pattern registry + dispatch
# ---------------------------------------------------------------------------

PATTERN_REGISTRY: Dict[str, Callable[[pd.DataFrame, Dict[str, Any]], pd.DataFrame]] = {
    "large_body":           detect_large_body,
    "pin_bar_bullish":      detect_pin_bar_bullish,
    "pin_bar_bearish":      detect_pin_bar_bearish,
    "inside_bar":           detect_inside_bar,
    "outside_bar":          detect_outside_bar,
    "engulfing_bullish":    detect_engulfing_bullish,
    "engulfing_bearish":    detect_engulfing_bearish,
    "doji":                 detect_doji,
    "clean_breakout_bar":   detect_clean_breakout_bar,
}

# Human-readable names for reporting
PATTERN_LABELS: Dict[str, str] = {
    "large_body":           "Large Body",
    "pin_bar_bullish":      "Pin Bar (Bullish)",
    "pin_bar_bearish":      "Pin Bar (Bearish)",
    "inside_bar":           "Inside Bar",
    "outside_bar":          "Outside Bar",
    "engulfing_bullish":    "Engulfing (Bullish)",
    "engulfing_bearish":    "Engulfing (Bearish)",
    "doji":                 "Doji",
    "clean_breakout_bar":   "Clean Breakout Bar",
}


def detect_pattern(
    pattern_id: str,
    enriched: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Dispatch entry point.

    Parameters
    ----------
    pattern_id : key from PATTERN_REGISTRY
    enriched   : output of compute_candle_features()
    params     : pattern-specific params dict (see individual detect_* docstrings)

    Returns
    -------
    signals DataFrame — one row per detected signal.
    Columns include all enriched bar columns plus ``direction`` (1 or -1).
    Empty DataFrame if pattern_id not found or no signals detected.
    """
    fn = PATTERN_REGISTRY.get(pattern_id)
    if fn is None:
        return pd.DataFrame()
    try:
        return fn(enriched, params)
    except Exception:
        return pd.DataFrame()
