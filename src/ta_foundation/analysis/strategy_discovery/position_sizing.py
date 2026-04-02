from __future__ import annotations

"""
Position Sizing
===============
Phase 9 — Computes optimal position-sizing fractions for a strategy using
Kelly criterion, Monte Carlo ruin-risk simulation, and regime-conditional
breakdowns.

Answers the practical question: "How much capital should I risk per trade?"

Algorithm
---------
1. Compute basic trade statistics (win rate, avg win, avg loss, payoff ratio).
2. Compute full Kelly fraction:  f* = WR - (1-WR) / payoff_ratio
3. Evaluate fractional Kelly variants: 0.25f, 0.50f, 0.75f, 1.00f.
4. For each fraction, run Monte Carlo (pure numpy, seeded):
     - Simulate n_trades × n_sim trade sequences by sampling with replacement
     - Track equity curve per simulation
     - Compute: expected CAGR (as total return), P(ruin), P(>20% drawdown),
       median max-drawdown, 5th percentile ending equity
5. Recommend the fraction that maximises expected growth while keeping
   P(ruin) < 5% and P(DD>20%) < 20%.
6. Regime-conditional: compute Kelly per regime label (requires feature_df
   with 'regime' column joined to trades).

Entry point: run_position_sizing(pkg, options, feature_df) → JSON-safe dict
Attaches to: pkg.metadata["derived"]["strategy_discovery"]["position_sizing"]
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":           True,
    "profit_col":        "profit_net",
    "n_sim":             500,          # Monte Carlo simulations
    "n_trades_sim":      200,          # trade sequence length per simulation
    "ruin_threshold":    0.20,         # equity drop fraction = "ruin"
    "dd_warn_threshold": 0.20,         # drawdown fraction for warning
    "kelly_fractions":   [0.25, 0.50, 0.75, 1.00],
    "min_trades":        20,
    "random_seed":       42,
}

_REGIME_MIN_TRADES = 10   # minimum per-regime trades for conditional Kelly


# ---------------------------------------------------------------------------
# Core Kelly math
# ---------------------------------------------------------------------------

def compute_kelly(win_rate: float, avg_win: float, avg_loss: float) -> Optional[float]:
    """
    Full Kelly fraction.  Returns None when inputs are invalid.

    f* = WR - (1 - WR) / (avg_win / avg_loss)
       = WR * (1 + payoff) - 1) / payoff    (equivalent form)

    avg_loss is the absolute value of the average losing trade.
    """
    if avg_loss <= 0 or avg_win <= 0:
        return None
    if not (0.0 < win_rate < 1.0):
        return None
    payoff = avg_win / avg_loss
    f = win_rate - (1.0 - win_rate) / payoff
    if not np.isfinite(f):
        return None
    return float(max(0.0, f))    # Kelly is only valid ≥ 0


# ---------------------------------------------------------------------------
# Monte Carlo simulation (vectorised)
# ---------------------------------------------------------------------------

def _simulate_equity_paths(
    profits: np.ndarray,
    fraction: float,
    initial_equity: float,
    n_trades: int,
    n_sim: int,
    ruin_threshold: float,
    rng: np.random.Generator,
) -> Dict[str, Any]:
    """
    Simulate n_sim independent equity paths of length n_trades.

    Each trade's P&L is scaled by `fraction` (treated as fraction of current
    equity bet, so each trade result is profit * fraction / avg_abs_profit,
    normalised to produce a % return per trade).

    We use a multiplicative model:
      equity[t+1] = equity[t] * (1 + fraction * pct_return[t])
    where pct_return[t] = profit[t] / avg_abs_profit.

    Returns per-simulation statistics.
    """
    if fraction <= 0 or len(profits) == 0:
        return {
            "median_final_equity":  initial_equity,
            "p5_final_equity":      initial_equity,
            "expected_return":      0.0,
            "p_ruin":               0.0,
            "p_dd_warn":            0.0,
            "median_max_drawdown":  0.0,
        }

    # Normalise profit to % return per unit of equity risked
    avg_abs = float(np.mean(np.abs(profits)))
    if avg_abs == 0:
        avg_abs = 1.0
    pct_returns = profits / avg_abs   # dimensionless: +1 = win equal to avg_abs

    # Sample indices: shape (n_sim, n_trades)
    idx = rng.integers(0, len(pct_returns), size=(n_sim, n_trades))
    sampled = pct_returns[idx]   # (n_sim, n_trades)

    # Multiplicative equity: equity[t+1] = equity[t] * (1 + f * r[t])
    # Clamp to avoid equity going negative within a step
    factors = np.clip(1.0 + fraction * sampled, 0.001, None)   # (n_sim, n_trades)
    equity_paths = initial_equity * np.cumprod(factors, axis=1)   # (n_sim, n_trades)

    # Prepend initial equity
    eq_full = np.hstack([
        np.full((n_sim, 1), initial_equity),
        equity_paths,
    ])   # (n_sim, n_trades + 1)

    final_equity = eq_full[:, -1]   # (n_sim,)

    # Running peak for drawdown
    peak = np.maximum.accumulate(eq_full, axis=1)   # (n_sim, n_trades+1)
    drawdown = (peak - eq_full) / peak               # fraction below peak
    max_dd   = drawdown.max(axis=1)                  # (n_sim,)

    ruin_mask = max_dd >= ruin_threshold
    dd_warn_mask = max_dd >= ruin_threshold   # same threshold here; caller picks

    return {
        "median_final_equity":  float(np.median(final_equity)),
        "p5_final_equity":      float(np.percentile(final_equity, 5)),
        "expected_return":      float(np.mean(final_equity / initial_equity) - 1.0),
        "p_ruin":               float(ruin_mask.mean()),
        "p_dd_warn":            float(dd_warn_mask.mean()),
        "median_max_drawdown":  float(np.median(max_dd)),
        "p95_max_drawdown":     float(np.percentile(max_dd, 95)),
    }


# ---------------------------------------------------------------------------
# Per-fraction sizing summary
# ---------------------------------------------------------------------------

def _evaluate_fraction(
    profits: np.ndarray,
    full_kelly: float,
    fraction_of_kelly: float,
    initial_equity: float,
    n_sim: int,
    n_trades_sim: int,
    ruin_threshold: float,
    dd_warn_threshold: float,
    rng: np.random.Generator,
) -> Dict[str, Any]:
    f = full_kelly * fraction_of_kelly
    sim = _simulate_equity_paths(
        profits=profits,
        fraction=f,
        initial_equity=initial_equity,
        n_trades=n_trades_sim,
        n_sim=n_sim,
        ruin_threshold=ruin_threshold,
        rng=rng,
    )
    return {
        "fraction_of_kelly":   round(fraction_of_kelly, 2),
        "kelly_fraction":      round(f, 4),
        "median_final_equity": round(sim["median_final_equity"], 2),
        "p5_final_equity":     round(sim["p5_final_equity"], 2),
        "expected_return_pct": round(sim["expected_return"] * 100, 2),
        "p_ruin":              round(sim["p_ruin"], 4),
        "p_dd_warn":           round(sim["p_dd_warn"], 4),
        "median_max_drawdown_pct": round(sim["median_max_drawdown"] * 100, 2),
        "p95_max_drawdown_pct":    round(sim["p95_max_drawdown"] * 100, 2),
        "safe":  (sim["p_ruin"] < 0.05 and sim["p_dd_warn"] < 0.20),
    }


# ---------------------------------------------------------------------------
# Regime-conditional Kelly
# ---------------------------------------------------------------------------

def _regime_conditional_kelly(
    feature_df: pd.DataFrame,
    profit_col: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Compute Kelly fraction per regime label.
    Requires 'regime' and profit_col columns.
    """
    if "regime" not in feature_df.columns:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    profit = pd.to_numeric(feature_df[profit_col], errors="coerce")
    for regime_label, sub_idx in feature_df.groupby("regime").groups.items():
        sub_profit = profit.loc[sub_idx].dropna()
        n = len(sub_profit)
        if n < _REGIME_MIN_TRADES:
            continue
        winners = sub_profit[sub_profit > 0]
        losers  = sub_profit[sub_profit <= 0]
        wr = float(len(winners) / n) if n > 0 else 0.0
        avg_win  = float(winners.mean()) if len(winners) > 0 else 0.0
        avg_loss = float(abs(losers.mean())) if len(losers) > 0 else 0.0
        kelly_f  = compute_kelly(wr, avg_win, avg_loss)
        result[str(regime_label)] = {
            "n_trades":     n,
            "win_rate":     round(wr, 4),
            "avg_win":      round(avg_win, 2),
            "avg_loss":     round(avg_loss, 2),
            "payoff_ratio": round(avg_win / avg_loss, 4) if avg_loss > 0 else None,
            "kelly_full":   round(kelly_f, 4) if kelly_f is not None else None,
            "kelly_half":   round(kelly_f * 0.5, 4) if kelly_f is not None else None,
        }
    return result


# ---------------------------------------------------------------------------
# Recommendation logic
# ---------------------------------------------------------------------------

def _recommend_fraction(
    fractions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Recommend the highest safe fraction (p_ruin < 5%, p_dd_warn < 20%)
    that maximises expected_return_pct.
    """
    safe_options = [f for f in fractions if f.get("safe")]
    if not safe_options:
        # None are safe — recommend the one with lowest ruin risk
        best = min(fractions, key=lambda f: f.get("p_ruin", 1.0))
        return {
            "recommended_fraction_of_kelly": best.get("fraction_of_kelly"),
            "recommended_kelly_fraction":    best.get("kelly_fraction"),
            "reasoning": (
                "No fraction passes the safety thresholds. "
                f"Least risky option is {best.get('fraction_of_kelly')}× Kelly "
                f"(p_ruin={best.get('p_ruin', 'n/a'):.1%})."
            ),
        }

    best = max(safe_options, key=lambda f: f.get("expected_return_pct", 0.0))
    return {
        "recommended_fraction_of_kelly": best.get("fraction_of_kelly"),
        "recommended_kelly_fraction":    best.get("kelly_fraction"),
        "reasoning": (
            f"Recommended {best.get('fraction_of_kelly')}× Kelly "
            f"(f={best.get('kelly_fraction', 0):.3f}): "
            f"expected return {best.get('expected_return_pct', 0):.1f}% over "
            f"{DEFAULT_OPTIONS['n_trades_sim']} trades, "
            f"p_ruin={best.get('p_ruin', 0):.1%}, "
            f"median max-DD={best.get('median_max_drawdown_pct', 0):.1f}%."
        ),
    }


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
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_position_sizing(
    pkg: Any,
    options: Dict[str, Any],
    *,
    feature_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Compute position-sizing analysis for a strategy package.

    Parameters
    ----------
    pkg        : AnalysisPackage
    options    : strategy_discovery.position_sizing config block
    feature_df : feature matrix (used for regime-conditional Kelly);
                 falls back to pkg.assets["strategy_discovery"]["feature_matrix"]

    Returns
    -------
    JSON-safe dict:
      trade_stats          : {n_trades, win_rate, avg_win, avg_loss, payoff_ratio}
      kelly_full           : full Kelly fraction
      kelly_fractions      : [{fraction_of_kelly, kelly_fraction, sim stats, safe}]
      recommendation       : {recommended_fraction_of_kelly, reasoning}
      regime_conditional   : {regime_label: {kelly_full, win_rate, ...}}
      diagnostics          : {n_trades, profit_col_used, n_sim, issues}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    min_trades      = int(opts.get("min_trades", 20))
    profit_col      = str(opts.get("profit_col", "profit_net"))
    n_sim           = int(opts.get("n_sim", 500))
    n_trades_sim    = int(opts.get("n_trades_sim", 200))
    ruin_thresh     = float(opts.get("ruin_threshold", 0.20))
    dd_warn_thresh  = float(opts.get("dd_warn_threshold", 0.20))
    kelly_fracs     = [float(x) for x in (opts.get("kelly_fractions") or [0.25, 0.50, 0.75, 1.00])]
    seed            = int(opts.get("random_seed", 42))
    initial_equity  = float(opts.get("initial_equity", 10_000.0))

    diag: Dict[str, Any] = {
        "n_trades":       0,
        "profit_col_used": profit_col,
        "n_sim":          n_sim,
        "issues":         [],
    }

    # ---- resolve feature_df ----
    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets  = pkg_assets.get("strategy_discovery") or {}
        feature_df = sd_assets.get("feature_matrix") if isinstance(sd_assets, dict) else None

    # ---- resolve trades + profit column ----
    # Prefer the feature_df (already has profit_net merged in), else fall back to pkg.trades
    source_df: Optional[pd.DataFrame] = None
    if feature_df is not None and isinstance(feature_df, pd.DataFrame) and profit_col in feature_df.columns:
        source_df = feature_df
    else:
        # Try pkg.trades
        trades = getattr(pkg, "trades", None)
        if trades is not None and isinstance(trades, pd.DataFrame) and len(trades) > 0:
            source_df = trades
            if profit_col not in source_df.columns:
                if "profit" in source_df.columns:
                    profit_col = "profit"
                    diag["profit_col_used"] = profit_col
                else:
                    diag["issues"].append(f"profit column '{profit_col}' not found")
                    return _make_json_safe({
                        "trade_stats": {}, "kelly_full": None,
                        "kelly_fractions": [], "recommendation": {},
                        "regime_conditional": {}, "diagnostics": diag,
                    })

    if source_df is None or len(source_df) == 0:
        diag["issues"].append("no trade data available")
        return _make_json_safe({
            "trade_stats": {}, "kelly_full": None,
            "kelly_fractions": [], "recommendation": {},
            "regime_conditional": {}, "diagnostics": diag,
        })

    profit = pd.to_numeric(source_df[profit_col], errors="coerce").dropna()
    n = len(profit)
    diag["n_trades"] = n

    if n < min_trades:
        diag["issues"].append(f"insufficient trades: {n} < {min_trades}")
        return _make_json_safe({
            "trade_stats": {}, "kelly_full": None,
            "kelly_fractions": [], "recommendation": {},
            "regime_conditional": {}, "diagnostics": diag,
        })

    # ---- trade statistics ----
    winners = profit[profit > 0]
    losers  = profit[profit <= 0]
    win_rate  = float(len(winners) / n)
    avg_win   = float(winners.mean()) if len(winners) > 0 else 0.0
    avg_loss  = float(abs(losers.mean())) if len(losers) > 0 else 0.0
    payoff    = avg_win / avg_loss if avg_loss > 0 else None

    trade_stats = {
        "n_trades":    n,
        "n_winners":   int(len(winners)),
        "n_losers":    int(len(losers)),
        "win_rate":    round(win_rate, 4),
        "avg_win":     round(avg_win, 2),
        "avg_loss":    round(avg_loss, 2),
        "payoff_ratio": round(payoff, 4) if payoff is not None else None,
        "expectancy":  round(float(profit.mean()), 2),
        "std_profit":  round(float(profit.std()), 2),
    }

    # ---- Kelly fraction ----
    kelly_full = compute_kelly(win_rate, avg_win, avg_loss)

    if kelly_full is None or kelly_full == 0.0:
        diag["issues"].append(
            "Kelly fraction is zero or undefined (edge < 0 or invalid inputs)"
        )
        return _make_json_safe({
            "trade_stats": trade_stats, "kelly_full": kelly_full,
            "kelly_fractions": [], "recommendation": {
                "recommended_fraction_of_kelly": 0.0,
                "reasoning": "Strategy has no positive edge — no position sizing recommended.",
            },
            "regime_conditional": {}, "diagnostics": diag,
        })

    # ---- Monte Carlo ----
    rng = np.random.default_rng(seed)
    profit_arr = profit.to_numpy(dtype=float)

    frac_results: List[Dict[str, Any]] = []
    for fok in kelly_fracs:
        res = _evaluate_fraction(
            profits=profit_arr,
            full_kelly=kelly_full,
            fraction_of_kelly=fok,
            initial_equity=initial_equity,
            n_sim=n_sim,
            n_trades_sim=n_trades_sim,
            ruin_threshold=ruin_thresh,
            dd_warn_threshold=dd_warn_thresh,
            rng=rng,
        )
        frac_results.append(res)

    # ---- recommendation ----
    recommendation = _recommend_fraction(frac_results)

    # ---- regime-conditional Kelly ----
    regime_conditional: Dict[str, Dict[str, Any]] = {}
    if feature_df is not None and isinstance(feature_df, pd.DataFrame):
        try:
            regime_conditional = _regime_conditional_kelly(feature_df, profit_col)
        except Exception as exc:
            diag["issues"].append(f"regime Kelly failed: {exc}")

    return _make_json_safe({
        "trade_stats":       trade_stats,
        "kelly_full":        round(kelly_full, 4),
        "kelly_fractions":   frac_results,
        "recommendation":    recommendation,
        "regime_conditional": regime_conditional,
        "diagnostics":       diag,
    })
