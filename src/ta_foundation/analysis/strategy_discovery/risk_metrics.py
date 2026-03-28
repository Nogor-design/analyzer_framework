from __future__ import annotations

"""
Risk-Adjusted Performance Metrics
==================================
Computes industry-standard risk-adjusted return metrics from the trade record.

Metrics
-------
Sharpe ratio:
  (mean_per_trade_return - rf_per_trade) / std_per_trade_return  * sqrt(N_annual)
  where rf_per_trade is the risk-free rate prorated to per-trade frequency.

Sortino ratio:
  (mean_per_trade_return - rf_per_trade) / downside_std  * sqrt(N_annual)
  downside_std uses only trades below the minimum acceptable return (MAR = 0).

Calmar ratio:
  annualized_return / max_drawdown_pct
  where annualized_return is estimated from total return and date span.

Omega ratio:
  sum(max(r - threshold, 0)) / sum(max(threshold - r, 0))
  Uses threshold = 0 (break-even per trade). > 1 means more gain mass above
  threshold than loss mass below it; same information as PF but return-weighted.

MAR ratio (Managed Accounts Report):
  annualized_return / max_drawdown_pct  (same formula as Calmar here)

Additional:
  annualized_return_pct  — estimated compound annual return as %
  total_return_pct       — (net_profit / initial_equity) * 100
  downside_deviation     — per-trade downside std (annualized)
  trades_per_year        — estimated from date span

All ratios are annualized using the per-trade sqrt(N_annual) scale factor
(also called the "information ratio" convention for per-trade returns).

Entry point: run_risk_metrics(pkg, options, profit_col, initial_equity)
Attaches to: discovery_block["risk_metrics"]
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":         True,
    "profit_col":      "profit_net",
    "initial_equity":  10_000.0,
    "risk_free_rate":  0.05,     # annual risk-free rate (e.g. 5%)
    "mar_rate":        0.0,      # Sortino MAR per-trade (0 = break-even)
    "min_trades":      10,
    "trading_days":    252,      # assumed trading days per year for annualisation
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _trades_per_year(entry_times: pd.Series, n: int) -> float:
    """Estimate average trades per year from date span. Falls back to n if span unavailable."""
    try:
        t = pd.to_datetime(entry_times).dropna()
        if len(t) < 2:
            return float(n)
        span_days = (t.max() - t.min()).days
        if span_days <= 0:
            return float(n)
        return float(n) / (span_days / 365.25)
    except Exception:
        return float(n)


def _annualization_factor(trades_per_year: float) -> float:
    """sqrt(N_annual) scale factor for per-trade Sharpe/Sortino."""
    return float(np.sqrt(max(trades_per_year, 1.0)))


def _max_drawdown_pct(profits: np.ndarray, initial: float) -> float:
    """Peak-to-trough max drawdown as a fraction (0–1)."""
    equity = initial + np.cumsum(profits)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (peak - equity) / peak, 0.0)
    return float(np.max(dd))


def _annualized_return(
    net_profit: float,
    initial_equity: float,
    trades_per_year: float,
    n_trades: int,
) -> float:
    """
    CAGR-style annualized return.
    total_return = net_profit / initial_equity
    annualized = (1 + total_return)^(trades_per_year / n_trades) - 1
    """
    if initial_equity <= 0 or n_trades <= 0:
        return 0.0
    total_ret = net_profit / initial_equity
    if total_ret <= -1.0:
        return -1.0
    years = n_trades / max(trades_per_year, 1.0)
    if years <= 0:
        return 0.0
    try:
        ann = float((1.0 + total_ret) ** (1.0 / years) - 1.0)
        return ann if np.isfinite(ann) else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------

def sharpe_ratio(
    per_trade_returns: np.ndarray,
    rf_per_trade: float,
    ann_factor: float,
) -> Optional[float]:
    """Annualized Sharpe ratio from per-trade return series."""
    excess = per_trade_returns - rf_per_trade
    std = float(np.std(excess))
    if std == 0:
        return None
    raw = float(np.mean(excess)) / std
    return float(raw * ann_factor)


def sortino_ratio(
    per_trade_returns: np.ndarray,
    rf_per_trade: float,
    mar_per_trade: float,
    ann_factor: float,
) -> Optional[float]:
    """
    Annualized Sortino ratio.
    Uses downside deviation relative to MAR (minimum acceptable return).
    """
    excess_mean = float(np.mean(per_trade_returns)) - rf_per_trade
    downside = per_trade_returns[per_trade_returns < mar_per_trade] - mar_per_trade
    if len(downside) == 0:
        return None   # no losing trades — ratio is undefined (infinite)
    downside_std = float(np.sqrt(np.mean(downside ** 2)))
    if downside_std == 0:
        return None
    return float((excess_mean / downside_std) * ann_factor)


def calmar_ratio(
    annualized_return: float,
    max_dd_pct: float,
) -> Optional[float]:
    """
    Calmar ratio: annualized_return / max_drawdown_pct.
    Both expressed as fractions (not percentages).
    """
    if max_dd_pct <= 0:
        return None
    v = annualized_return / max_dd_pct
    return float(v) if np.isfinite(v) else None


def omega_ratio(
    per_trade_returns: np.ndarray,
    threshold: float = 0.0,
) -> Optional[float]:
    """
    Omega ratio at given threshold.
    sum(max(r - threshold, 0)) / sum(max(threshold - r, 0))
    """
    gains  = np.sum(np.maximum(per_trade_returns - threshold, 0.0))
    losses = np.sum(np.maximum(threshold - per_trade_returns, 0.0))
    if losses == 0:
        return None  # no losses below threshold — ratio is undefined
    v = float(gains / losses)
    return v if np.isfinite(v) else None


def downside_deviation(
    per_trade_returns: np.ndarray,
    mar: float,
    ann_factor: float,
) -> Optional[float]:
    """Annualized downside deviation (semi-standard deviation below MAR)."""
    below = per_trade_returns[per_trade_returns < mar] - mar
    if len(below) == 0:
        return 0.0
    dd = float(np.sqrt(np.mean(below ** 2)))
    return float(dd * ann_factor)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_risk_metrics(
    pkg: Any,
    options: Dict[str, Any],
    *,
    profit_col: str = "profit_net",
) -> Dict[str, Any]:
    """
    Compute risk-adjusted performance metrics for a strategy package.

    Returns JSON-safe dict:
      sharpe_ratio           : annualized Sharpe
      sortino_ratio          : annualized Sortino
      calmar_ratio           : annualized return / max DD
      omega_ratio            : gain mass / loss mass above 0
      mar_ratio              : same as calmar_ratio (alias)
      annualized_return_pct  : estimated CAGR %
      total_return_pct       : net_profit / initial_equity * 100
      downside_deviation     : annualized downside std
      trades_per_year        : estimated
      diagnostics            : {n_trades, profit_col_used, initial_equity, issues}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    initial_equity  = float(opts.get("initial_equity", 10_000.0))
    rf_annual       = float(opts.get("risk_free_rate", 0.05))
    mar_annual      = float(opts.get("mar_rate", 0.0))
    min_trades      = int(opts.get("min_trades", 10))
    profit_col      = str(opts.get("profit_col", profit_col))

    diag: Dict[str, Any] = {
        "n_trades":        0,
        "profit_col_used": profit_col,
        "initial_equity":  initial_equity,
        "issues":          [],
    }

    # ---- resolve trades ----
    trades = getattr(pkg, "trades", None)
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        diag["issues"].append("no trade data available")
        return _empty(diag)

    if profit_col not in trades.columns:
        if "profit" in trades.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return _empty(diag)

    t = trades.copy()
    t[profit_col] = pd.to_numeric(t[profit_col], errors="coerce")
    if "entry_time" in t.columns:
        t = t.sort_values("entry_time")
    t = t.dropna(subset=[profit_col]).reset_index(drop=True)

    n = len(t)
    diag["n_trades"] = int(n)

    if n < min_trades:
        diag["issues"].append(f"insufficient trades: {n} < {min_trades}")
        return _empty(diag)

    profits = t[profit_col].values.astype(float)
    entry_times = t["entry_time"] if "entry_time" in t.columns else None

    # ---- per-trade returns as fraction of initial equity ----
    # r[i] = profit[i] / initial_equity  (dimensionless fraction)
    per_trade_r = profits / initial_equity

    # ---- annualisation ----
    tpy = _trades_per_year(entry_times, n) if entry_times is not None else float(n)
    ann_f = _annualization_factor(tpy)

    # Per-trade prorated risk-free and MAR rates
    rf_per_trade  = rf_annual / max(tpy, 1.0)
    mar_per_trade = mar_annual / max(tpy, 1.0)

    # ---- overall return ----
    net_profit = float(profits.sum())
    total_ret_pct = (net_profit / initial_equity) * 100.0
    ann_ret = _annualized_return(net_profit, initial_equity, tpy, n)
    ann_ret_pct = ann_ret * 100.0

    # ---- max drawdown ----
    max_dd_frac = _max_drawdown_pct(profits, initial_equity)   # 0–1

    # ---- ratios ----
    sharpe  = sharpe_ratio(per_trade_r, rf_per_trade, ann_f)
    sortino = sortino_ratio(per_trade_r, rf_per_trade, mar_per_trade, ann_f)
    calmar  = calmar_ratio(ann_ret, max_dd_frac)
    omega   = omega_ratio(per_trade_r, threshold=0.0)
    dd_ann  = downside_deviation(per_trade_r, mar_per_trade, ann_f)

    def _r(v: Optional[float], d: int = 3) -> Optional[float]:
        if v is None:
            return None
        return round(v, d)

    return _make_json_safe({
        "sharpe_ratio":         _r(sharpe, 3),
        "sortino_ratio":        _r(sortino, 3),
        "calmar_ratio":         _r(calmar, 3),
        "omega_ratio":          _r(omega, 3),
        "mar_ratio":            _r(calmar, 3),   # alias
        "annualized_return_pct": _r(ann_ret_pct, 2),
        "total_return_pct":     _r(total_ret_pct, 2),
        "downside_deviation":   _r(dd_ann, 4),
        "max_drawdown_pct":     _r(max_dd_frac * 100.0, 2),
        "trades_per_year":      _r(tpy, 1),
        "net_profit":           _r(net_profit, 2),
        "diagnostics":          diag,
    })


def _empty(diag: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None,
        "omega_ratio": None, "mar_ratio": None,
        "annualized_return_pct": None, "total_return_pct": None,
        "downside_deviation": None, "max_drawdown_pct": None,
        "trades_per_year": None, "net_profit": None,
        "diagnostics": diag,
    }


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------

def _make_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    return obj
