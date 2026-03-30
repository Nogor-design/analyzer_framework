from __future__ import annotations

"""
Feature Matrix Builder
=======================
Builds a temporally-gated feature DataFrame that joins trade entries with
market context. Serves as input to entry_discovery.py.

Key design rules:
  - Every feature uses data available BEFORE or AT the entry bar (lag-safe).
  - Pattern Engine audit results are bridged here as first-class features.
  - Regime labels from bars_with_regime are joined by entry_time.
  - Features are computed on-demand per trade, not pre-materialized for all bars.

Output columns added to trades:
  regime          : market regime at entry (from bars_with_regime)
  adx             : ADX at entry bar
  atr             : ATR at entry bar
  entry_hour      : hour of entry_time (local)
  session_label   : session bucket (pre_open, us_open, us_morning, etc.)
  direction       : 1 (Long) or -1 (Short)
  pattern_score   : trade_pattern_audit confirming_score (if available)
  pattern_verdict : trade_pattern_audit verdict (if available)
  [pattern_key]   : one bool column per pattern that fired (from pattern_detail JSON)
"""

from typing import Any, Dict, List, Optional
import json
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _session_label(hour: int) -> str:
    """Map an entry hour (America/Denver local) to a PantheonMaster session bucket."""
    if 0 <= hour <= 5:   return "London"    # 00:00-05:59
    if hour == 6:        return "NyPre"     # 06:00-07:29 (approximated at hour boundary)
    if hour in (7, 8):   return "NyOpen"    # 07:30-09:00
    if 9 <= hour <= 12:  return "NyMid"     # 09:01-13:00
    if hour == 13:       return "PowerHr"   # 13:01-14:00
    if 18 <= hour <= 23: return "Asia"      # 18:00-23:59
    return "other"  # 14-17: not in any named session


def _tz_to_naive_utc(s: pd.Series) -> pd.Series:
    """
    Strip timezone for merge_asof: convert to tz-naive UTC.
    If already tz-naive, returns as-is.
    """
    s = pd.to_datetime(s)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


def _parse_direction(market_pos_series: pd.Series) -> pd.Series:
    """
    Parse market_pos column to numeric direction: 1 for Long, -1 for Short.
    Unrecognised values become NaN.
    """
    mp = market_pos_series.astype(str).str.strip().str.lower()
    direction = pd.Series(np.nan, index=market_pos_series.index, dtype=float)
    direction[mp.isin(("long", "1"))] = 1.0
    direction[mp.isin(("short", "-1"))] = -1.0
    return direction


def _parse_pattern_detail(detail_value: Any) -> Dict[str, bool]:
    """
    Parse a pattern_detail JSON string/dict into a flat dict of {pattern_key: bool}.
    Returns empty dict on any failure.
    """
    if detail_value is None:
        return {}
    try:
        if isinstance(detail_value, str):
            parsed = json.loads(detail_value)
        elif isinstance(detail_value, dict):
            parsed = detail_value
        else:
            return {}
        if not isinstance(parsed, dict):
            return {}
        # Flatten to {key: bool}
        result: Dict[str, bool] = {}
        for k, v in parsed.items():
            col_name = f"pat_{k}" if not str(k).startswith("pat_") else str(k)
            result[col_name] = bool(v)
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_feature_matrix(
    trades: pd.DataFrame,
    *,
    bars_with_regime: Optional[pd.DataFrame] = None,
    audit_df: Optional[pd.DataFrame] = None,
    signal_feature_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Build a feature matrix by augmenting trades with market context columns.

    Parameters
    ----------
    trades           : pkg.trades DataFrame (not modified in-place)
    bars_with_regime : optional bars DataFrame with regime/adx/atr columns;
                       joined to trades by entry_time via backward merge_asof
    audit_df         : optional trade_pattern_audit DataFrame with trade_id,
                       confirming_score, verdict, n_patterns_fired, pattern_detail
    signal_feature_df : optional enriched signal corpus from entry_pattern_bridge;
                        when provided, trades are matched to the nearest signal and
                        corpus stats (corpus_win_rate, corpus_avg_ticks, signal_family,
                        signal_structure, signal_match_quality) are added to each row.

    Returns
    -------
    Copy of trades with additional feature columns.
    """
    result = trades.copy()

    # ------------------------------------------------------------------
    # Step 2: entry_hour
    # ------------------------------------------------------------------
    if "entry_time" in result.columns:
        try:
            entry_dt = pd.to_datetime(result["entry_time"])
            # Use local (America/Denver) hour if tz-aware
            if entry_dt.dt.tz is not None:
                local_dt = entry_dt.dt.tz_convert("America/Denver")
            else:
                local_dt = entry_dt
            result["entry_hour"] = local_dt.dt.hour
        except Exception:
            result["entry_hour"] = np.nan

    # ------------------------------------------------------------------
    # Step 3: session_label
    # ------------------------------------------------------------------
    if "entry_hour" in result.columns:
        try:
            result["session_label"] = result["entry_hour"].apply(
                lambda h: _session_label(int(h)) if pd.notna(h) else "other"
            )
        except Exception:
            result["session_label"] = "other"

    # ------------------------------------------------------------------
    # Step 4: direction
    # ------------------------------------------------------------------
    if "market_pos" in result.columns:
        try:
            result["direction"] = _parse_direction(result["market_pos"])
        except Exception:
            result["direction"] = np.nan

    # ------------------------------------------------------------------
    # Step 5: join regime columns from bars_with_regime
    # ------------------------------------------------------------------
    if (
        bars_with_regime is not None
        and isinstance(bars_with_regime, pd.DataFrame)
        and len(bars_with_regime) > 0
        and "dt" in bars_with_regime.columns
        and "entry_time" in result.columns
    ):
        try:
            # Determine which columns to carry over from bars (excluding dt itself)
            regime_cols_to_add = [
                c for c in bars_with_regime.columns
                if c != "dt" and c not in result.columns
            ]
            # Always include well-known columns if present
            desired = {"regime", "adx", "atr", "trend_direction", "vol_regime"}
            regime_merge_cols = [
                c for c in bars_with_regime.columns
                if c in desired or c in regime_cols_to_add
            ]

            if regime_merge_cols:
                trades_dt = _tz_to_naive_utc(pd.to_datetime(result["entry_time"]))
                bars_dt = _tz_to_naive_utc(pd.to_datetime(bars_with_regime["dt"]))

                bars_for_merge = bars_with_regime[regime_merge_cols].copy()
                bars_for_merge["_dt_utc"] = bars_dt.values
                bars_for_merge = bars_for_merge.sort_values("_dt_utc").reset_index(drop=True)

                trades_for_merge = pd.DataFrame({"_entry_utc": trades_dt.values}, index=result.index)
                trades_for_merge = trades_for_merge.sort_values("_entry_utc")

                merged = pd.merge_asof(
                    trades_for_merge,
                    bars_for_merge,
                    left_on="_entry_utc",
                    right_on="_dt_utc",
                    direction="backward",
                )
                # Re-align index to result
                merged.index = trades_for_merge.index
                merged_realigned = merged.reindex(result.index)

                for col in regime_merge_cols:
                    if col in merged_realigned.columns:
                        result[col] = merged_realigned[col].values
        except Exception:
            pass  # Regime join is best-effort — don't break the pipeline

    # ------------------------------------------------------------------
    # Step 6: bridge Pattern Engine audit results
    # ------------------------------------------------------------------
    if (
        audit_df is not None
        and isinstance(audit_df, pd.DataFrame)
        and len(audit_df) > 0
        and "trade_id" in audit_df.columns
        and "confirming_score" in audit_df.columns
        and "trade_id" in result.columns
    ):
        try:
            # Select audit columns to merge
            audit_cols = ["trade_id"]
            if "confirming_score" in audit_df.columns:
                audit_cols.append("confirming_score")
            if "verdict" in audit_df.columns:
                audit_cols.append("verdict")
            if "n_patterns_fired" in audit_df.columns:
                audit_cols.append("n_patterns_fired")
            if "pattern_detail" in audit_df.columns:
                audit_cols.append("pattern_detail")

            audit_subset = audit_df[audit_cols].copy()

            # Rename to avoid column name collisions
            rename_map: Dict[str, str] = {}
            if "confirming_score" in audit_subset.columns:
                rename_map["confirming_score"] = "pattern_score"
            if "verdict" in audit_subset.columns:
                rename_map["verdict"] = "pattern_verdict"
            audit_subset = audit_subset.rename(columns=rename_map)

            result = result.merge(
                audit_subset.drop(columns=["pattern_detail"], errors="ignore"),
                on="trade_id",
                how="left",
            )

            # Parse pattern_detail JSON into one bool column per pattern
            if "pattern_detail" in audit_subset.columns:
                # Re-join pattern_detail separately to avoid the drop above
                detail_df = audit_df[["trade_id", "pattern_detail"]].copy()
                result = result.merge(detail_df, on="trade_id", how="left")

                # Collect all pattern keys across all rows
                all_pattern_keys: set = set()
                for val in result["pattern_detail"].dropna():
                    keys = _parse_pattern_detail(val)
                    all_pattern_keys.update(keys.keys())

                # Initialize all pattern columns as False
                for key in sorted(all_pattern_keys):
                    if key not in result.columns:
                        result[key] = False

                # Populate per-row
                for idx, val in result["pattern_detail"].items():
                    if pd.isna(val) if not isinstance(val, dict) else False:
                        continue
                    parsed = _parse_pattern_detail(val)
                    for key, fired in parsed.items():
                        if key in result.columns:
                            result.at[idx, key] = fired

                result = result.drop(columns=["pattern_detail"], errors="ignore")

        except Exception:
            pass  # Audit bridge is best-effort

    # ------------------------------------------------------------------
    # Step 7: bridge signal corpus stats from entry_pattern_bridge
    # ------------------------------------------------------------------
    if (
        signal_feature_df is not None
        and isinstance(signal_feature_df, pd.DataFrame)
        and len(signal_feature_df) > 0
        and "dt" in signal_feature_df.columns
        and "entry_time" in result.columns
    ):
        try:
            from .entry_pattern_bridge import match_trades_to_signals
            matched = match_trades_to_signals(
                trades=result,
                signals_df=signal_feature_df,
                options={},
            )
            # Rename corpus columns to signal_ prefix for the trade feature matrix
            rename_map: Dict[str, str] = {
                "matched_family": "signal_family",
                "matched_structure": "signal_structure",
                "signal_corpus_win_rate": "corpus_win_rate",
                "signal_corpus_avg_ticks": "corpus_avg_ticks",
                "match_quality": "signal_match_quality",
            }
            for old_c, new_c in rename_map.items():
                if old_c in matched.columns:
                    result[new_c] = matched[old_c].values
        except Exception:
            pass  # Signal corpus bridge is best-effort

    return result
