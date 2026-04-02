from __future__ import annotations

"""
MAE/MFE/ETD Profile
====================
Computes per-trade and aggregate MAE/MFE/ETD distributions from pkg.trades.
NT8 exports mae/mfe/etd as dollar values directly.

Purpose: drive exit parameter bounds for exit_discovery.py rather than
using arbitrary hardcoded ranges.

Key insight:
  - MAE p80 of WINNING trades → natural stop level (stops tighter than this cut winners)
  - MFE p60 → diminishing-returns target level
  - ETD p80 → trailing stop distance calibration
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PERCENTILES = [10, 25, 50, 60, 75, 80, 90, 95]


def _safe_float(v: Any) -> Optional[float]:
    """Convert a value to float, returning None if not finite or not convertible."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _percentile_table(series: pd.Series, label: str) -> Dict[str, Optional[float]]:
    """Compute percentile table for a numeric series."""
    valid = pd.to_numeric(series, errors="coerce").dropna()
    if len(valid) == 0:
        return {f"{label}_p{p}": None for p in _PERCENTILES}
    result: Dict[str, Optional[float]] = {}
    for p in _PERCENTILES:
        result[f"{label}_p{p}"] = _safe_float(float(valid.quantile(p / 100.0)))
    return result


def _dollars_to_points(dollars: Optional[float], tick_value: float, tick_size: float) -> Optional[float]:
    """Convert a dollar value to points: dollars / tick_value * tick_size."""
    if dollars is None or tick_value == 0:
        return None
    pts = dollars / tick_value * tick_size
    return _safe_float(pts)


def _compute_direction_stats(
    sub: pd.DataFrame,
    tick_value: float,
    tick_size: float,
) -> Dict[str, Any]:
    """Compute MAE/MFE/ETD stats for a subset of trades."""
    result: Dict[str, Any] = {}

    n = len(sub)
    result["n_trades"] = n

    if n == 0:
        result["n_winners"] = 0
        result["n_losers"] = 0
        return result

    profit = pd.to_numeric(sub.get("profit"), errors="coerce") if "profit" in sub.columns else pd.Series(dtype=float)
    winners = sub[profit > 0] if len(profit) > 0 else sub.iloc[:0]
    losers = sub[profit <= 0] if len(profit) > 0 else sub.iloc[:0]

    result["n_winners"] = int(len(winners))
    result["n_losers"] = int(len(losers))

    for col_label, data in [
        ("mae_winners", winners.get("mae") if "mae" in sub.columns else pd.Series(dtype=float)),
        ("mfe_all", sub.get("mfe") if "mfe" in sub.columns else pd.Series(dtype=float)),
        ("etd_all", sub.get("etd") if "etd" in sub.columns else pd.Series(dtype=float)),
    ]:
        tbl = _percentile_table(pd.to_numeric(data, errors="coerce").abs() if data is not None else pd.Series(dtype=float), col_label)
        result.update(tbl)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_mae_mfe_profile(
    trades: pd.DataFrame,
    *,
    tick_value: float = 5.0,
    tick_size: float = 0.25,
    min_trades: int = 10,
) -> Dict[str, Any]:
    """
    Compute MAE/MFE/ETD distributions from trades DataFrame.

    Parameters
    ----------
    trades      : pkg.trades with mae, mfe, etd, profit, market_pos columns
    tick_value  : dollar value per tick (NQ = $5)
    tick_size   : minimum price increment (NQ = 0.25)
    min_trades  : minimum trades required for meaningful stats

    Returns
    -------
    dict with keys:
      distributions     : percentile tables for mae/mfe/etd (all trades + winners only)
      exit_parameter_bounds : dict with stop/target/trail bounds in $ and points
      by_direction      : breakdown by Long/Short
      diagnostics       : coverage, n_trades, n_winners, missing counts
    """
    diag: Dict[str, Any] = {
        "n_trades": 0,
        "n_winners": 0,
        "n_losers": 0,
        "mae_coverage": 0.0,
        "mfe_coverage": 0.0,
        "etd_coverage": 0.0,
        "issues": [],
        "sufficient_data": False,
    }

    # Guard: trades must be a non-empty DataFrame
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        diag["issues"].append("no trades available")
        return {
            "distributions": {},
            "exit_parameter_bounds": {},
            "by_direction": {},
            "diagnostics": diag,
        }

    n_trades = len(trades)
    diag["n_trades"] = n_trades

    if n_trades < min_trades:
        diag["issues"].append(f"insufficient trades: {n_trades} < {min_trades}")

    # Coverage diagnostics
    for col in ("mae", "mfe", "etd"):
        if col in trades.columns:
            valid_count = int(pd.to_numeric(trades[col], errors="coerce").notna().sum())
            diag[f"{col}_coverage"] = round(valid_count / n_trades, 4) if n_trades > 0 else 0.0
        else:
            diag[f"{col}_coverage"] = 0.0
            diag["issues"].append(f"column '{col}' not present in trades")

    # Profit series
    profit = pd.to_numeric(trades.get("profit"), errors="coerce") if "profit" in trades.columns else pd.Series(np.nan, index=trades.index)
    winners = trades[profit > 0]
    losers = trades[profit <= 0]

    n_winners = int(len(winners))
    n_losers = int(len(losers))
    diag["n_winners"] = n_winners
    diag["n_losers"] = n_losers
    diag["sufficient_data"] = n_trades >= min_trades

    # --- Distributions ---
    distributions: Dict[str, Any] = {}

    mae_winners_series = (
        pd.to_numeric(winners["mae"], errors="coerce").abs()
        if "mae" in trades.columns and len(winners) > 0
        else pd.Series(dtype=float)
    )
    mfe_all_series = (
        pd.to_numeric(trades["mfe"], errors="coerce").abs()
        if "mfe" in trades.columns
        else pd.Series(dtype=float)
    )
    etd_all_series = (
        pd.to_numeric(trades["etd"], errors="coerce").abs()
        if "etd" in trades.columns
        else pd.Series(dtype=float)
    )

    distributions.update(_percentile_table(mae_winners_series, "mae_winners"))
    distributions.update(_percentile_table(mfe_all_series, "mfe_all"))
    distributions.update(_percentile_table(etd_all_series, "etd_all"))

    # Also compute mae for all trades and losers
    if "mae" in trades.columns:
        distributions.update(_percentile_table(pd.to_numeric(trades["mae"], errors="coerce").abs(), "mae_all"))
        if len(losers) > 0:
            distributions.update(_percentile_table(pd.to_numeric(losers["mae"], errors="coerce").abs(), "mae_losers"))

    # --- Exit parameter bounds ---
    mae_winners_p50 = _safe_float(
        mae_winners_series.quantile(0.50) if len(mae_winners_series.dropna()) > 0 else None
    )
    mae_winners_p80 = _safe_float(
        mae_winners_series.quantile(0.80) if len(mae_winners_series.dropna()) > 0 else None
    )
    mae_winners_p90 = _safe_float(
        mae_winners_series.quantile(0.90) if len(mae_winners_series.dropna()) > 0 else None
    )

    mfe_all_p25 = _safe_float(
        mfe_all_series.quantile(0.25) if len(mfe_all_series.dropna()) > 0 else None
    )
    mfe_all_p60 = _safe_float(
        mfe_all_series.quantile(0.60) if len(mfe_all_series.dropna()) > 0 else None
    )
    mfe_all_p90 = _safe_float(
        mfe_all_series.quantile(0.90) if len(mfe_all_series.dropna()) > 0 else None
    )

    etd_all_p50 = _safe_float(
        etd_all_series.quantile(0.50) if len(etd_all_series.dropna()) > 0 else None
    )
    etd_all_p80 = _safe_float(
        etd_all_series.quantile(0.80) if len(etd_all_series.dropna()) > 0 else None
    )

    # MFE/MAE ratio
    mae_natural = mae_winners_p80
    mfe_natural = mfe_all_p60
    mfe_mae_ratio: Optional[float] = None
    if mae_natural is not None and mfe_natural is not None and mae_natural > 0:
        mfe_mae_ratio = _safe_float(mfe_natural / mae_natural)

    exit_parameter_bounds: Dict[str, Any] = {
        "stop_min_usd": mae_winners_p50,
        "stop_natural_usd": mae_winners_p80,
        "stop_max_usd": mae_winners_p90,
        "target_min_usd": mfe_all_p25,
        "target_natural_usd": mfe_all_p60,
        "target_max_usd": mfe_all_p90,
        "trail_activation_usd": etd_all_p50,
        "trail_distance_usd": etd_all_p80,
        # Points conversions
        "stop_min_pts": _dollars_to_points(mae_winners_p50, tick_value, tick_size),
        "stop_natural_pts": _dollars_to_points(mae_winners_p80, tick_value, tick_size),
        "stop_max_pts": _dollars_to_points(mae_winners_p90, tick_value, tick_size),
        "target_min_pts": _dollars_to_points(mfe_all_p25, tick_value, tick_size),
        "target_natural_pts": _dollars_to_points(mfe_all_p60, tick_value, tick_size),
        "target_max_pts": _dollars_to_points(mfe_all_p90, tick_value, tick_size),
        "trail_activation_pts": _dollars_to_points(etd_all_p50, tick_value, tick_size),
        "trail_distance_pts": _dollars_to_points(etd_all_p80, tick_value, tick_size),
        "mfe_mae_ratio": mfe_mae_ratio,
    }

    # --- By-direction breakdown ---
    by_direction: Dict[str, Any] = {}
    if "market_pos" in trades.columns:
        for direction_label in ("Long", "Short"):
            mask = trades["market_pos"].astype(str).str.strip().str.lower().isin(
                ("long", "1") if direction_label == "Long" else ("short", "-1")
            )
            sub = trades[mask]
            if len(sub) == 0:
                by_direction[direction_label] = {"n_trades": 0}
                continue
            by_direction[direction_label] = _compute_direction_stats(sub, tick_value, tick_size)
    else:
        diag["issues"].append("column 'market_pos' not present — by_direction breakdown skipped")

    return {
        "distributions": distributions,
        "exit_parameter_bounds": exit_parameter_bounds,
        "by_direction": by_direction,
        "diagnostics": diag,
    }
