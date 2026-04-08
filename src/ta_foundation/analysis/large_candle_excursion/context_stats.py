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

from typing import Any, Dict, List, Optional

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


def _bucket_rows(
    fwd_df: pd.DataFrame,
    bucket_col: str,
    sig_col: str,
    target_pct: float,
    include_trade: bool,
) -> List[Dict]:
    """Aggregate rows by a single bucket column."""
    if bucket_col not in fwd_df.columns:
        return []

    rows = []
    for bkt_label, grp in fwd_df.groupby(bucket_col, sort=False):
        rec: Dict[str, Any] = {bucket_col: bkt_label}
        rec.update(_agg_excursion(grp))
        if include_trade:
            rec.update(_agg_trade(grp, sig_col, target_pct))
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

    # Determine trade settings
    trade_enabled   = bool((trade_cfg or {}).get("enabled", False))
    target_pcts     = [float(p) for p in (trade_cfg or {}).get("target", {}).get("percents", [50])]
    primary_tgt_pct = target_pcts[0] if target_pcts else 50.0
    signal_basis    = (trade_cfg or {}).get("signal_candle_basis", "range")
    sig_col         = "size_ticks" if signal_basis == "range" else "body_ticks"
    if sig_col not in combined_fwd.columns:
        sig_col = "size_ticks"

    include_trade = trade_enabled

    result: Dict[str, Any] = {"enabled": True, "primary_target_pct": primary_tgt_pct}

    # ------------------------------------------------------------------
    # 1. Volume context
    # ------------------------------------------------------------------
    if vol_cfg.get("enabled") and "vol_bucket" in combined_fwd.columns:
        result["volume_context"] = {
            "enabled": True,
            "by_vol_bucket": _bucket_rows(combined_fwd, "vol_bucket", sig_col,
                                          primary_tgt_pct, include_trade),
        }
    else:
        result["volume_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 2. Candle structure context
    # ------------------------------------------------------------------
    struct_enabled = struct_cfg.get("enabled") and any(
        c in combined_fwd.columns
        for c in ("close_pos_bucket", "body_range_bucket", "wick_bucket")
    )
    if struct_enabled:
        result["structure_context"] = {
            "enabled": True,
            "by_close_pos":   _bucket_rows(combined_fwd, "close_pos_bucket",
                                           sig_col, primary_tgt_pct, include_trade),
            "by_body_range":  _bucket_rows(combined_fwd, "body_range_bucket",
                                           sig_col, primary_tgt_pct, include_trade),
            "by_wick":        _bucket_rows(combined_fwd, "wick_bucket",
                                           sig_col, primary_tgt_pct, include_trade),
        }
        # size × close-position interaction
        if "candle_bucket" in combined_fwd.columns:
            result["structure_context"]["by_size_x_close_pos"] = _interaction_rows(
                combined_fwd, "candle_bucket", "close_pos_bucket",
                sig_col, primary_tgt_pct, include_trade,
            )
    else:
        result["structure_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 3. Volatility context
    # ------------------------------------------------------------------
    volat_enabled = volat_cfg.get("enabled") and any(
        c in combined_fwd.columns for c in ("atr_bucket", "range_avg_bucket")
    )
    if volat_enabled:
        result["volatility_context"] = {
            "enabled":            True,
            "by_atr_bucket":      _bucket_rows(combined_fwd, "atr_bucket",
                                               sig_col, primary_tgt_pct, include_trade),
            "by_range_avg_bucket":_bucket_rows(combined_fwd, "range_avg_bucket",
                                               sig_col, primary_tgt_pct, include_trade),
        }
    else:
        result["volatility_context"] = {"enabled": False}

    # ------------------------------------------------------------------
    # 4. Interaction tables
    # ------------------------------------------------------------------
    interactions: Dict[str, Any] = {}

    if vol_cfg.get("enabled") and struct_cfg.get("enabled"):
        if "vol_bucket" in combined_fwd.columns and "close_pos_bucket" in combined_fwd.columns:
            interactions["vol_x_close_pos"] = _interaction_rows(
                combined_fwd, "vol_bucket", "close_pos_bucket",
                sig_col, primary_tgt_pct, include_trade,
            )

    if vol_cfg.get("enabled"):
        if "vol_bucket" in combined_fwd.columns and "candle_bucket" in combined_fwd.columns:
            interactions["vol_x_size"] = _interaction_rows(
                combined_fwd, "vol_bucket", "candle_bucket",
                sig_col, primary_tgt_pct, include_trade,
            )

    if volat_cfg.get("enabled"):
        if "atr_bucket" in combined_fwd.columns and "candle_bucket" in combined_fwd.columns:
            interactions["size_x_atr"] = _interaction_rows(
                combined_fwd, "candle_bucket", "atr_bucket",
                sig_col, primary_tgt_pct, include_trade,
            )

    if vol_cfg.get("enabled") and struct_cfg.get("enabled"):
        if "vol_bucket" in combined_fwd.columns and "wick_bucket" in combined_fwd.columns:
            interactions["vol_x_wick"] = _interaction_rows(
                combined_fwd, "vol_bucket", "wick_bucket",
                sig_col, primary_tgt_pct, include_trade,
            )

    result["interactions"] = interactions
    return result
