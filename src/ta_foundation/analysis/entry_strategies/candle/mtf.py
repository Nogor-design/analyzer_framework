from __future__ import annotations

"""
Multi-Timeframe Engine
=======================
Implements three MTF combination modes on top of per-TF signal DataFrames.

INDEPENDENT
  Each timeframe runs the full pattern+outcome pipeline separately.
  Results are stored independently; the ranking module compares them.
  This mode requires no code here — sweep.py handles it by looping TFs.

CONFLUENCE
  A signal fires only when N-of-M timeframes show a same-direction signal
  within the same time window.

  Algorithm:
    1. Align all per-TF signal DataFrames to a common UTC timestamp grid.
    2. For each 1m bar's timestamp, count how many TF signals fired within
       the preceding [current_tf_bar_seconds] window in the same direction.
    3. Emit a merged signal only when count >= min_agreement.

HIERARCHICAL
  The higher TF (e.g. 5m) sets a directional bias; the lower TF (e.g. 1m)
  provides the precise entry candle.

  Algorithm:
    1. For each 5m signal bar, mark all 1m bars within that 5m window as
       carrying the 5m bias (direction + strength_score).
    2. A 1m entry signal fires when:
         - The 1m bar is within a biased 5m window.
         - The 1m entry direction matches the 5m bias direction.
    3. The merged signal carries both 5m and 1m candle features.

Usage
-----
  from ta_foundation.analysis.entry_strategies.candle.mtf import (
      apply_confluence_filter,
      apply_hierarchical_filter,
  )
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_utc_naive_ns(dt_series: pd.Series) -> pd.Series:
    """
    Normalise any datetime Series to tz-naive UTC nanoseconds (int64 Series).
    Forces nanosecond resolution to stay consistent with pd.Timestamp.value
    on pandas 2.x where default resolution may be microseconds.
    """
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.astype("datetime64[ns]").astype("int64")


def _tf_seconds(tf_minutes: int) -> int:
    return tf_minutes * 60


# ---------------------------------------------------------------------------
# Confluence mode
# ---------------------------------------------------------------------------

def apply_confluence_filter(
    signals_by_tf: Dict[int, pd.DataFrame],
    min_agreement: int = 2,
    window_seconds: Optional[int] = None,
) -> pd.DataFrame:
    """
    Retain signals where at least *min_agreement* timeframes show a same-direction
    signal within *window_seconds* of each other.

    Parameters
    ----------
    signals_by_tf   : {tf_minutes: signals_df} — one entry per TF.
                      Each signals_df must have 'signal_dt' (or 'dt') and 'direction'.
    min_agreement   : minimum number of TFs that must agree              default 2
    window_seconds  : time window for agreement check.
                      Defaults to the smallest TF's bar duration in seconds.

    Returns
    -------
    Filtered signals DataFrame from the smallest TF with an added column:
      tf_agreement_count  (int) — how many TFs agreed on this signal
    Only rows with tf_agreement_count >= min_agreement are returned.
    """
    if not signals_by_tf:
        return pd.DataFrame()

    tfs = sorted(signals_by_tf.keys())
    smallest_tf = tfs[0]

    if window_seconds is None:
        window_seconds = _tf_seconds(smallest_tf)

    base_signals = signals_by_tf[smallest_tf].copy()
    if base_signals.empty:
        return pd.DataFrame()

    _sig_dt_col = "signal_dt" if "signal_dt" in base_signals.columns else "dt"
    base_dt_utc = _to_utc_naive_ns(base_signals[_sig_dt_col])

    # Build lookup of (utc_ns, direction) for all non-base TFs
    other_tf_events: List[pd.DataFrame] = []
    for tf in tfs[1:]:
        sig = signals_by_tf.get(tf)
        if sig is None or sig.empty:
            continue
        sig_dt_col = "signal_dt" if "signal_dt" in sig.columns else "dt"
        frame = pd.DataFrame({
            "utc_ns":    _to_utc_naive_ns(sig[sig_dt_col]).astype("int64").values,
            "direction": sig["direction"].values,
        })
        other_tf_events.append(frame)

    if not other_tf_events:
        # Only one TF available — agreement count is 1 by definition
        base_signals["tf_agreement_count"] = 1
        if min_agreement <= 1:
            return base_signals
        return pd.DataFrame()

    other_events = pd.concat(other_tf_events, ignore_index=True)
    other_utc   = other_events["utc_ns"].values
    other_dirs  = other_events["direction"].values
    window_ns   = window_seconds * 1_000_000_000  # seconds → nanoseconds

    agreement_counts: List[int] = []
    base_utc_ns = base_dt_utc.astype("int64").values

    for i, (utc_ns, base_dir) in enumerate(zip(base_utc_ns, base_signals["direction"].values)):
        base_dir_int = int(base_dir)
        # Count other-TF signals within window and same direction
        in_window = np.abs(other_utc - utc_ns) <= window_ns
        same_dir  = other_dirs == base_dir_int
        count = int((in_window & same_dir).sum())
        agreement_counts.append(1 + count)  # +1 for the base TF itself

    base_signals["tf_agreement_count"] = agreement_counts
    result = base_signals[base_signals["tf_agreement_count"] >= min_agreement].copy()
    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Hierarchical mode
# ---------------------------------------------------------------------------

def apply_hierarchical_filter(
    signals_context_tf: pd.DataFrame,
    signals_entry_tf: pd.DataFrame,
    context_tf_minutes: int = 5,
    entry_tf_minutes: int = 1,
    context_strength_min: float = 0.0,
) -> pd.DataFrame:
    """
    Use higher-TF (context) signals to bias lower-TF (entry) signals.

    A lower-TF signal survives only when:
      1. Its timestamp falls within the context bar's window.
      2. Its direction matches the context bar's direction.
      3. (Optional) The context bar's strength_score >= context_strength_min.

    The output carries both the lower-TF candle features and the context-TF
    features (prefixed ``htf_``).

    Parameters
    ----------
    signals_context_tf    : signals from the higher TF (e.g. 5m)
    signals_entry_tf      : signals from the lower TF (e.g. 1m)
    context_tf_minutes    : duration of the context TF bar in minutes   default 5
    entry_tf_minutes      : duration of the entry TF bar in minutes     default 1
    context_strength_min  : minimum context bar strength_score           default 0.0
                            Set > 0 to require a strong context candle.

    Returns
    -------
    Filtered entry signals with ``htf_*`` context columns added.
    """
    if signals_context_tf is None or signals_context_tf.empty:
        return pd.DataFrame()
    if signals_entry_tf is None or signals_entry_tf.empty:
        return pd.DataFrame()

    ctx_dt_col = "signal_dt" if "signal_dt" in signals_context_tf.columns else "dt"
    ent_dt_col = "signal_dt" if "signal_dt" in signals_entry_tf.columns else "dt"

    ctx = signals_context_tf.copy()
    ent = signals_entry_tf.copy()

    ctx_dt_utc = _to_utc_naive_ns(ctx[ctx_dt_col])
    ent_dt_utc = _to_utc_naive_ns(ent[ent_dt_col])

    ctx["_utc_ns"]     = ctx_dt_utc.astype("int64").values
    ctx["_utc_end_ns"] = ctx["_utc_ns"] + context_tf_minutes * 60 * 1_000_000_000
    ent["_utc_ns"]     = ent_dt_utc.astype("int64").values

    # Compute strength_score for context bars if not already present
    # strength_score = body_vs_atr * (1 - max(upper_wick_to_body, lower_wick_to_body))
    # Clipped to [0, 1]; bars without atr data get score 0.
    if "strength_score" not in ctx.columns:
        bva = ctx.get("body_vs_atr", pd.Series(np.nan, index=ctx.index))
        uwb = ctx.get("upper_wick_to_body", pd.Series(0.0, index=ctx.index)).fillna(0)
        lwb = ctx.get("lower_wick_to_body", pd.Series(0.0, index=ctx.index)).fillna(0)
        max_wick_ratio = pd.concat([uwb, lwb], axis=1).max(axis=1)
        ctx["strength_score"] = (
            bva.fillna(0) * (1.0 - max_wick_ratio.clip(0, 1))
        ).clip(0, None)

    # Filter context bars by strength
    ctx_filtered = ctx[ctx["strength_score"] >= context_strength_min].copy()
    if ctx_filtered.empty:
        return pd.DataFrame()

    ctx_starts   = ctx_filtered["_utc_ns"].values
    ctx_ends     = ctx_filtered["_utc_end_ns"].values
    ctx_dirs     = ctx_filtered["direction"].values
    ctx_rows     = ctx_filtered.reset_index(drop=True)

    # Build HTF prefix column map
    htf_cols = [c for c in ctx_filtered.columns if c not in
                ("direction", "_utc_ns", "_utc_end_ns", ctx_dt_col)]

    matched_rows: List[Dict] = []

    for _, ent_row in ent.iterrows():
        ent_ns  = int(ent_row["_utc_ns"])
        ent_dir = int(ent_row["direction"])

        # Find context bars that contain this entry bar's timestamp
        in_window  = (ent_ns >= ctx_starts) & (ent_ns < ctx_ends)
        same_dir   = ctx_dirs == ent_dir

        matches = np.where(in_window & same_dir)[0]
        if len(matches) == 0:
            continue

        # Use the most recent matching context bar
        ctx_idx = int(matches[-1])
        ctx_row = ctx_rows.iloc[ctx_idx]

        row_dict = dict(ent_row)
        row_dict.pop("_utc_ns", None)

        # Add HTF features with htf_ prefix
        for col in htf_cols:
            if col in ctx_row.index:
                row_dict[f"htf_{col}"] = ctx_row[col]
        row_dict["htf_direction"]      = int(ctx_row["direction"])
        row_dict["htf_strength_score"] = float(ctx_row["strength_score"])
        row_dict["mtf_mode"]           = f"hierarchical_{context_tf_minutes}m_{entry_tf_minutes}m"

        matched_rows.append(row_dict)

    if not matched_rows:
        return pd.DataFrame()

    result = pd.DataFrame(matched_rows).reset_index(drop=True)
    result.pop("_utc_ns", None) if "_utc_ns" in result.columns else None
    return result


# ---------------------------------------------------------------------------
# Convenience: label MTF mode in a signals DataFrame
# ---------------------------------------------------------------------------

def label_independent(signals_df: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
    """Add mtf_mode column for independent mode signals."""
    if signals_df is None or signals_df.empty:
        return signals_df
    out = signals_df.copy()
    out["mtf_mode"] = f"independent_{tf_minutes}m"
    return out
