from __future__ import annotations

"""
Context Statistics Aggregator
==============================
Computes grouped excursion and trade statistics by context dimension
(volume bucket, candle structure bucket, volatility bucket, interactions).

Design
------
  Input  : combined_fwd — all forward-excursion rows, already enriched by
           context_enricher.py (context columns flow through via forward_window.py)
  Output : JSON-safe dict with volume_context, structure_context,
           volatility_context, and interactions sub-keys.

Trade statistics
  Win/loss is evaluated directly from fav_ticks / adv_ticks using a
  primary target percent (first in trade_cfg["target"]["percents"] or 50).
  "continuation win" = fav_ticks >= target_ticks
  "reverse win"      = adv_ticks >= target_ticks
  This is computed here independently of trade_analyzer.py, so context stats
  are available even when trade_analysis is disabled (they just omit win rates).
"""

from typing import Any, Dict, List, Optional, Tuple

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sr(v: Any, d: int = 1) -> Optional[float]:
    if v is None:
        return None
    try:
        fv = float(v)
        return None if fv != fv else round(fv, d)
    except Exception:
        return None


def _agg_excursion(grp: pd.DataFrame) -> Dict[str, Any]:
    fav = grp["fav_ticks"].dropna()
    adv = grp["adv_ticks"].dropna()
    n   = len(grp)
    return {
        "n_observations": n,
        "mean_fav_ticks":   _sr(fav.mean()),
        "median_fav_ticks": _sr(fav.median()),
        "mean_adv_ticks":   _sr(adv.mean()),
        "median_adv_ticks": _sr(adv.median()),
    }


def _agg_trade(
    grp: pd.DataFrame,
    sig_col: str,
    target_pct: float,
) -> Dict[str, Any]:
    """
    Compute continuation and reverse win rates from excursion data.

    Uses fav_ticks (continuation) and adv_ticks (reverse) against
    target_ticks = signal_candle_ticks * target_pct / 100.
    """
    idx = grp.index[
        grp["fav_ticks"].notna() &
        grp["adv_ticks"].notna() &
        grp[sig_col].notna()
    ]
    if len(idx) == 0:
        return {}

    sig  = grp.loc[idx, sig_col].values.astype(float)
    fav  = grp.loc[idx, "fav_ticks"].values.astype(float)
    adv  = grp.loc[idx, "adv_ticks"].values.astype(float)
    tgt  = sig * target_pct / 100.0

    n          = len(sig)
    cont_wins  = int((fav >= tgt).sum())
    rev_wins   = int((adv >= tgt).sum())
    cont_wr    = round(cont_wins / n * 100, 1) if n > 0 else None
    rev_wr     = round(rev_wins  / n * 100, 1) if n > 0 else None

    if cont_wr is not None and rev_wr is not None:
        better = "continuation" if cont_wr > rev_wr + 2 else (
                 "reverse"      if rev_wr  > cont_wr + 2 else "tie")
    else:
        better = None

    return {
        "target_pct":          target_pct,
        "cont_wins":           cont_wins,
        "rev_wins":            rev_wins,
        "cont_win_rate":       cont_wr,
        "rev_win_rate":        rev_wr,
        "better_mode":         better,
    }


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score confidence interval for a proportion (wins/n). Returns (lo, hi) as percentages."""
    if n <= 0:
        return None, None
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return round(lo * 100, 1), round(hi * 100, 1)


def _lift_vs_baseline(bucket_wr: Optional[float], baseline_wr: Optional[float]) -> Optional[float]:
    """Lift in percentage points relative to population baseline."""
    if bucket_wr is None or baseline_wr is None:
        return None
    return round(bucket_wr - baseline_wr, 1)


def _deduplicate_events(fwd_df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove duplicate event rows that arise from overlapping combos.
    Unique event identity: (dt, direction, tf_minutes).
    Keeps the first occurrence to avoid double-counting context effects.
    """
    dedup_cols = [c for c in ("dt", "direction", "tf_minutes") if c in fwd_df.columns]
    if not dedup_cols:
        return fwd_df
    return fwd_df.drop_duplicates(subset=dedup_cols, keep="first").reset_index(drop=True)


def _temporal_stability(
    fwd_df: pd.DataFrame,
    bucket_col: str,
    sig_col: str,
    target_pct: float,
    n_splits: int = 3,
    min_n_per_split: int = 10,
) -> Optional[float]:
    """
    Compute stability score for a bucket column across time splits.
    Returns mean Spearman-like rank correlation of WR ordering across splits, or None.
    Range: 0 (unstable) to 1 (perfectly stable).
    """
    if "dt" not in fwd_df.columns or bucket_col not in fwd_df.columns:
        return None
    df_sorted = fwd_df.sort_values("dt").reset_index(drop=True)
    seg_len = max(1, len(df_sorted) // n_splits)
    split_wrs: List[Dict[str, Optional[float]]] = []
    for i in range(n_splits):
        seg = df_sorted.iloc[i * seg_len: (i + 1) * seg_len] if i < n_splits - 1 else df_sorted.iloc[i * seg_len:]
        seg_rows: Dict[str, Optional[float]] = {}
        for bkt, grp in seg.groupby(bucket_col, sort=False):
            trade_data = _agg_trade(grp, sig_col, target_pct)
            if grp[sig_col].notna().sum() >= min_n_per_split:
                seg_rows[str(bkt)] = trade_data.get("cont_win_rate")
        split_wrs.append(seg_rows)
    if len(split_wrs) < 2:
        return None
    # All buckets seen across splits
    all_buckets = set().union(*[d.keys() for d in split_wrs])
    if len(all_buckets) < 2:
        return None
    # Compute bucket rank correlation across splits
    correlations: List[float] = []
    for i in range(len(split_wrs) - 1):
        common = [b for b in all_buckets if split_wrs[i].get(b) is not None and split_wrs[i + 1].get(b) is not None]
        if len(common) < 2:
            continue
        v1 = [split_wrs[i][b] or 0.0 for b in common]
        v2 = [split_wrs[i + 1][b] or 0.0 for b in common]
        r1 = _rank_list(v1)
        r2 = _rank_list(v2)
        n = len(common)
        cov = sum((r1[j] - (n + 1) / 2) * (r2[j] - (n + 1) / 2) for j in range(n))
        var = sum((r - (n + 1) / 2) ** 2 for r in r1) * sum((r - (n + 1) / 2) ** 2 for r in r2)
        if var > 0:
            correlations.append(cov / math.sqrt(var))
    if not correlations:
        return None
    raw = sum(correlations) / len(correlations)
    return round((raw + 1) / 2, 3)  # normalise to [0, 1]


def _rank_list(values: List[float]) -> List[float]:
    """Return rank positions (1-based) for a list of values."""
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    for rank, (orig_idx, _) in enumerate(sorted_vals, 1):
        ranks[orig_idx] = float(rank)
    return ranks


def _bucket_rows(
    fwd_df: pd.DataFrame,
    bucket_col: str,
    sig_col: str,
    target_pct: float,
    include_trade: bool,
    baseline_cont_wr: Optional[float] = None,
    baseline_rev_wr: Optional[float] = None,
) -> List[Dict]:
    """Aggregate rows by a single bucket column."""
    if bucket_col not in fwd_df.columns:
        return []

    rows = []
    for bkt_label, grp in fwd_df.groupby(bucket_col, sort=False):
        rec: Dict[str, Any] = {bucket_col: bkt_label}
        rec.update(_agg_excursion(grp))
        if include_trade:
            trade_data = _agg_trade(grp, sig_col, target_pct)
            rec.update(trade_data)
            # Wilson CI for cont win rate
            n_trade = rec.get("n_observations", 0)
            cont_wins = trade_data.get("cont_wins", 0)
            rev_wins  = trade_data.get("rev_wins", 0)
            ci_lo, ci_hi = _wilson_ci(cont_wins, n_trade)
            rec["cont_wr_ci_lo"] = ci_lo
            rec["cont_wr_ci_hi"] = ci_hi
            # Lift vs baseline
            rec["cont_lift_pp"] = _lift_vs_baseline(trade_data.get("cont_win_rate"), baseline_cont_wr)
            rec["rev_lift_pp"]  = _lift_vs_baseline(trade_data.get("rev_win_rate"), baseline_rev_wr)
        rows.append(rec)

    # Sort: put "unknown" / "other" last, rest alphabetically
    rows.sort(key=lambda r: (r[bucket_col] in ("unknown", "other"), r[bucket_col]))
    return rows


def _interaction_rows(
    fwd_df: pd.DataFrame,
    col_a: str,
    col_b: str,
    sig_col: str,
    target_pct: float,
    include_trade: bool,
    min_n: int = 5,
) -> List[Dict]:
    """Aggregate rows by two bucket columns (cross-tabulation)."""
    if col_a not in fwd_df.columns or col_b not in fwd_df.columns:
        return []

    rows = []
    for (a, b), grp in fwd_df.groupby([col_a, col_b], sort=False):
        if len(grp) < min_n:
            continue
        rec: Dict[str, Any] = {col_a: a, col_b: b}
        rec.update(_agg_excursion(grp))
        if include_trade:
            rec.update(_agg_trade(grp, sig_col, target_pct))
        rows.append(rec)

    rows.sort(key=lambda r: (r[col_a], r[col_b]))
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_context_analysis(
    combined_fwd: pd.DataFrame,
    vol_cfg:    Dict,
    struct_cfg: Dict,
    volat_cfg:  Dict,
    trade_cfg:  Optional[Dict] = None,
    directional_cfg:  Optional[Dict] = None,
    ma_vwap_cfg:      Optional[Dict] = None,
    key_level_cfg:    Optional[Dict] = None,
    trend_state_cfg:  Optional[Dict] = None,
    exhaustion_cfg:   Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Compute all context-grouped statistics from the enriched forward-excursion data.

    Parameters
    ----------
    combined_fwd : concatenation of all fwd_df DataFrames (all combos/TFs/windows)
                   already enriched with context columns.
    vol_cfg      : volume_context config
    struct_cfg   : candle_structure_context config
    volat_cfg    : volatility_context config
    trade_cfg    : trade_analysis config (optional); used for target_pct only

    Returns
    -------
    JSON-safe dict stored under result["context_analysis"].
    """
    if combined_fwd is None or combined_fwd.empty:
        return {"enabled": False}

    # Deduplicate events to avoid double-counting identical signals across combos
    deduped_fwd = _deduplicate_events(combined_fwd)

    # Determine trade settings
    trade_enabled   = bool((trade_cfg or {}).get("enabled", False))
    target_pcts     = [float(p) for p in (trade_cfg or {}).get("target", {}).get("percents", [50])]
    primary_tgt_pct = target_pcts[0] if target_pcts else 50.0
    signal_basis    = (trade_cfg or {}).get("signal_candle_basis", "range")
    sig_col         = "size_ticks" if signal_basis == "range" else "body_ticks"
    if sig_col not in deduped_fwd.columns:
        sig_col = "size_ticks"

    include_trade = trade_enabled

    result: Dict[str, Any] = {"enabled": True, "primary_target_pct": primary_tgt_pct}

    # Compute population-level baseline win rates for lift computation
    baseline_cont_wr: Optional[float] = None
    baseline_rev_wr:  Optional[float] = None
    if include_trade:
        pop_trade = _agg_trade(deduped_fwd, sig_col, primary_tgt_pct)
        baseline_cont_wr = pop_trade.get("cont_win_rate")
        baseline_rev_wr  = pop_trade.get("rev_win_rate")
    result["population_baseline"] = {
        "cont_win_rate": baseline_cont_wr,
        "rev_win_rate":  baseline_rev_wr,
        "n_events":      len(deduped_fwd),
    }

    def _brows(df: pd.DataFrame, col: str) -> List[Dict]:
        return _bucket_rows(df, col, sig_col, primary_tgt_pct, include_trade,
                            baseline_cont_wr, baseline_rev_wr)

    def _irows(df: pd.DataFrame, ca: str, cb: str, min_n: int = 5) -> List[Dict]:
        return _interaction_rows(df, ca, cb, sig_col, primary_tgt_pct, include_trade, min_n)

    # ------------------------------------------------------------------
    # 1. Volume context
    # ------------------------------------------------------------------
    if vol_cfg.get("enabled") and "vol_bucket" in deduped_fwd.columns:
        result["volume_context"] = {
            "enabled": True,
            "by_vol_bucket": _brows(deduped_fwd, "vol_bucket"),
        }
    else:
        result["volume_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 2. Candle structure context
    # ------------------------------------------------------------------
    struct_enabled = struct_cfg.get("enabled") and any(
        c in deduped_fwd.columns
        for c in ("close_pos_bucket", "body_range_bucket", "wick_bucket")
    )
    if struct_enabled:
        result["structure_context"] = {
            "enabled": True,
            "by_close_pos":  _brows(deduped_fwd, "close_pos_bucket"),
            "by_body_range": _brows(deduped_fwd, "body_range_bucket"),
            "by_wick":       _brows(deduped_fwd, "wick_bucket"),
        }
        if "candle_bucket" in deduped_fwd.columns:
            result["structure_context"]["by_size_x_close_pos"] = _irows(
                deduped_fwd, "candle_bucket", "close_pos_bucket",
            )
    else:
        result["structure_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 3. Volatility context
    # ------------------------------------------------------------------
    volat_enabled = volat_cfg.get("enabled") and any(
        c in deduped_fwd.columns for c in ("atr_bucket", "range_avg_bucket")
    )
    if volat_enabled:
        result["volatility_context"] = {
            "enabled":             True,
            "by_atr_bucket":       _brows(deduped_fwd, "atr_bucket"),
            "by_range_avg_bucket": _brows(deduped_fwd, "range_avg_bucket"),
        }
    else:
        result["volatility_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 4. Session context
    # ------------------------------------------------------------------
    from ta_foundation.analysis.large_candle_excursion.session_classifier import SESSION_ORDER

    if "session_bucket" in deduped_fwd.columns:
        order_map = {s: i for i, s in enumerate(SESSION_ORDER)}
        session_rows = _brows(deduped_fwd, "session_bucket")
        session_rows.sort(
            key=lambda r: (order_map.get(r["session_bucket"], 99), r["session_bucket"])
        )
        session_x_size: List[Dict] = []
        if "candle_bucket" in deduped_fwd.columns:
            session_x_size = _irows(deduped_fwd, "session_bucket", "candle_bucket")
            session_x_size.sort(
                key=lambda r: (order_map.get(r["session_bucket"], 99), r["candle_bucket"])
            )
        result["session_context"] = {
            "enabled": True,
            "by_session": session_rows,
            "by_session_x_size": session_x_size,
        }
    else:
        result["session_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 5. Directional context (new)
    # ------------------------------------------------------------------
    dir_enabled = (directional_cfg or {}).get("enabled") and any(
        c in deduped_fwd.columns for c in ("prev_direction_bucket", "streak_bucket", "engulf_bucket")
    )
    if dir_enabled:
        result["directional_context"] = {
            "enabled": True,
            "by_prev_direction": _brows(deduped_fwd, "prev_direction_bucket"),
            "by_streak":         _brows(deduped_fwd, "streak_bucket"),
            "by_engulf":         _brows(deduped_fwd, "engulf_bucket"),
        }
    else:
        result["directional_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 6. MA / VWAP location context (new)
    # ------------------------------------------------------------------
    ma_vwap_enabled = (ma_vwap_cfg or {}).get("enabled") and any(
        c in deduped_fwd.columns
        for c in ("ma100_location_bucket", "ma200_location_bucket", "vwap_location_bucket",
                  "ma100_ext_bucket", "vwap_ext_bucket")
    )
    if ma_vwap_enabled:
        ma_block: Dict[str, Any] = {"enabled": True}
        for col in ("ma100_location_bucket", "ma200_location_bucket", "vwap_location_bucket",
                    "ma100_ext_bucket", "ma200_ext_bucket", "vwap_ext_bucket"):
            if col in deduped_fwd.columns:
                ma_block[f"by_{col}"] = _brows(deduped_fwd, col)
        result["ma_vwap_context"] = ma_block
    else:
        result["ma_vwap_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 7. Key level context (new)
    # ------------------------------------------------------------------
    level_enabled = (key_level_cfg or {}).get("enabled") and any(
        c in deduped_fwd.columns
        for c in ("nearest_level_type", "level_interaction_label")
    )
    if level_enabled:
        level_block: Dict[str, Any] = {"enabled": True}
        for col in ("nearest_level_type", "level_interaction_label"):
            if col in deduped_fwd.columns:
                level_block[f"by_{col}"] = _brows(deduped_fwd, col)
        # Temporal stability for level interaction
        if "level_interaction_label" in deduped_fwd.columns and include_trade:
            stab = _temporal_stability(deduped_fwd, "level_interaction_label", sig_col, primary_tgt_pct)
            level_block["temporal_stability"] = stab
        result["key_level_context"] = level_block
    else:
        result["key_level_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 8. Trend state context (new)
    # ------------------------------------------------------------------
    trend_enabled = (trend_state_cfg or {}).get("enabled") and \
        "trend_alignment_label" in deduped_fwd.columns
    if trend_enabled:
        trend_block: Dict[str, Any] = {"enabled": True}
        for col in ("trend_alignment_label", "ma100_slope_label", "ma200_slope_label"):
            if col in deduped_fwd.columns:
                trend_block[f"by_{col}"] = _brows(deduped_fwd, col)
        result["trend_state_context"] = trend_block
    else:
        result["trend_state_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 9. Interaction tables (existing + new)
    # ------------------------------------------------------------------
    interactions: Dict[str, Any] = {}

    if vol_cfg.get("enabled") and struct_cfg.get("enabled"):
        if "vol_bucket" in deduped_fwd.columns and "close_pos_bucket" in deduped_fwd.columns:
            interactions["vol_x_close_pos"] = _irows(deduped_fwd, "vol_bucket", "close_pos_bucket")

    if vol_cfg.get("enabled"):
        if "vol_bucket" in deduped_fwd.columns and "candle_bucket" in deduped_fwd.columns:
            interactions["vol_x_size"] = _irows(deduped_fwd, "vol_bucket", "candle_bucket")

    if volat_cfg.get("enabled"):
        if "atr_bucket" in deduped_fwd.columns and "candle_bucket" in deduped_fwd.columns:
            interactions["size_x_atr"] = _irows(deduped_fwd, "candle_bucket", "atr_bucket")

    if vol_cfg.get("enabled") and struct_cfg.get("enabled"):
        if "vol_bucket" in deduped_fwd.columns and "wick_bucket" in deduped_fwd.columns:
            interactions["vol_x_wick"] = _irows(deduped_fwd, "vol_bucket", "wick_bucket")

    # New signal-candle interactions
    if (directional_cfg or {}).get("enabled"):
        if "prev_direction_bucket" in deduped_fwd.columns and "candle_bucket" in deduped_fwd.columns:
            interactions["prev_direction_x_size"] = _irows(
                deduped_fwd, "prev_direction_bucket", "candle_bucket",
            )
    if (ma_vwap_cfg or {}).get("enabled"):
        if "vwap_ext_bucket" in deduped_fwd.columns and "candle_bucket" in deduped_fwd.columns:
            interactions["vwap_ext_x_size"] = _irows(
                deduped_fwd, "vwap_ext_bucket", "candle_bucket",
            )
        if "ma100_location_bucket" in deduped_fwd.columns and "trend_alignment_label" in deduped_fwd.columns:
            interactions["ma100_location_x_trend"] = _irows(
                deduped_fwd, "ma100_location_bucket", "trend_alignment_label",
            )
    if (key_level_cfg or {}).get("enabled"):
        if "session_bucket" in deduped_fwd.columns and "level_interaction_label" in deduped_fwd.columns:
            interactions["session_x_level_interaction"] = _irows(
                deduped_fwd, "session_bucket", "level_interaction_label",
            )
        if "prev_direction_bucket" in deduped_fwd.columns and "level_interaction_label" in deduped_fwd.columns:
            interactions["prev_direction_x_level"] = _irows(
                deduped_fwd, "prev_direction_bucket", "level_interaction_label",
            )

    result["interactions"] = interactions

    # ------------------------------------------------------------------
    # Diagnostics — per-module coverage summary
    # ------------------------------------------------------------------
    diagnostics: Dict[str, Any] = {}

    def _module_diag(
        name: str,
        enabled_flag: bool,
        cfg: Optional[Dict],
        bucket_cols: List[str],
        failure_reasons: List[str],
    ) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "module": name,
            "enabled_in_config": bool((cfg or {}).get("enabled", False)),
            "enabled_flag_passed": bool(enabled_flag),
            "failure_reasons": failure_reasons[:],
        }
        n_total = len(combined_fwd)
        present_cols = [c for c in bucket_cols if c in combined_fwd.columns]
        if present_cols:
            col = present_cols[0]
            classified = int((combined_fwd[col].notna() & (combined_fwd[col] != "unknown")).sum())
            d["n_events_examined"] = n_total
            d["n_events_classified"] = classified
            d["coverage_pct"] = round(classified / n_total * 100, 1) if n_total > 0 else 0.0
            distinct = sorted(combined_fwd[col].dropna().unique().tolist())
            d["distinct_buckets"] = [str(x) for x in distinct if x != "unknown"]
            d["n_distinct_buckets"] = len(d["distinct_buckets"])
            d["contributed_results"] = bool(enabled_flag and classified > 0)
        else:
            d["n_events_examined"] = n_total
            d["n_events_classified"] = 0
            d["coverage_pct"] = 0.0
            d["distinct_buckets"] = []
            d["n_distinct_buckets"] = 0
            d["contributed_results"] = False
            if not failure_reasons:
                d["failure_reasons"] = ["columns not found in forward excursion data"]
        return d

    # Collect failure reasons per module
    def _col_failures(cols: List[str]) -> List[str]:
        missing = [c for c in cols if c not in combined_fwd.columns]
        if missing:
            return [f"missing columns: {', '.join(missing)}"]
        return []

    dir_cols   = ["prev_direction_bucket", "streak_bucket", "engulf_bucket",
                  "directional_context_label"]
    ma_cols    = ["ma100_location_bucket", "vwap_location_bucket", "ma100_ext_bucket",
                  "vwap_signed_bucket", "ma100_signed_bucket"]
    lvl_cols   = ["nearest_level_type", "level_interaction_label"]
    trend_cols = ["trend_alignment_label"]
    exh_cols   = ["exhaustion_label"]

    dir_reasons: List[str] = []
    if not (directional_cfg or {}).get("enabled"):
        dir_reasons.append("not enabled in config (set directional_context.enabled: true)")
    else:
        dir_reasons.extend(_col_failures(dir_cols))

    ma_reasons: List[str] = []
    if not (ma_vwap_cfg or {}).get("enabled"):
        ma_reasons.append("not enabled in config (set ma_vwap_context.enabled: true)")
    else:
        ma_reasons.extend(_col_failures(ma_cols))

    lvl_reasons: List[str] = []
    if not (key_level_cfg or {}).get("enabled"):
        lvl_reasons.append("not enabled in config (set key_level_context.enabled: true)")
    else:
        lvl_reasons.extend(_col_failures(lvl_cols))

    trend_reasons: List[str] = []
    if not (trend_state_cfg or {}).get("enabled"):
        trend_reasons.append("not enabled in config (set trend_state_context.enabled: true)")
    else:
        trend_reasons.extend(_col_failures(trend_cols))

    exh_enabled = bool((exhaustion_cfg or {}).get("enabled"))
    exh_reasons: List[str] = []
    if not exh_enabled:
        exh_reasons.append("not enabled in config (set exhaustion_context.enabled: true)")
    else:
        exh_reasons.extend(_col_failures(exh_cols))

    diagnostics["directional_context"]  = _module_diag(
        "directional_context", dir_enabled, directional_cfg, dir_cols, dir_reasons)
    diagnostics["ma_vwap_context"]       = _module_diag(
        "ma_vwap_context", ma_vwap_enabled, ma_vwap_cfg, ma_cols, ma_reasons)
    diagnostics["key_level_context"]     = _module_diag(
        "key_level_context", level_enabled, key_level_cfg, lvl_cols, lvl_reasons)
    diagnostics["trend_state_context"]   = _module_diag(
        "trend_state_context", trend_enabled, trend_state_cfg, trend_cols, trend_reasons)
    diagnostics["exhaustion_context"]    = _module_diag(
        "exhaustion_context", exh_enabled, exhaustion_cfg, exh_cols, exh_reasons)

    # Volume / structure / volatility diagnostics (existing modules)
    vol_reasons: List[str] = []
    if not vol_cfg.get("enabled"):
        vol_reasons.append("not enabled in config")
    elif "vol_bucket" not in combined_fwd.columns:
        vol_reasons.append("missing vol_bucket column (volume data absent?)")
    diagnostics["volume_context"] = _module_diag(
        "volume_context", vol_cfg.get("enabled") and "vol_bucket" in combined_fwd.columns,
        vol_cfg, ["vol_bucket"], vol_reasons)

    diagnostics["_summary"] = {
        "total_events_in_combined_fwd": len(combined_fwd),
        "total_deduplicated_events": len(deduped_fwd),
        "modules_contributing": sum(
            1 for d in diagnostics.values()
            if isinstance(d, dict) and d.get("contributed_results")
        ),
        "modules_enabled": sum(
            1 for d in diagnostics.values()
            if isinstance(d, dict) and d.get("enabled_in_config")
        ),
    }
    result["diagnostics"] = diagnostics

    return result
