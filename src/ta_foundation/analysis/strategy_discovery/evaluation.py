from __future__ import annotations

"""
Strategy Evaluation
====================
Computes comprehensive performance metrics on a cost-normalized trade set.
Reads 'profit_net' column produced by validation.apply_cost_model().

Also computes session-level and asymmetry breakdowns directly from trades.
Regime breakdown requires bars_with_regime (passed optionally).

All outputs are JSON-safe — no DataFrames in returned dict.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    """Convert a value to float, returning None if not finite or not convertible."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _session_label(hour: int) -> str:
    """Map an entry hour (local) to a named session bucket."""
    if hour == 7:
        return "pre_open"
    if hour == 8:
        return "us_open"
    if hour in (9, 10, 11):
        return "us_morning"
    if hour in (12, 13):
        return "us_midday"
    if hour in (14, 15):
        return "us_afternoon"
    return "other"


def _tz_to_naive_utc(s: pd.Series) -> pd.Series:
    """Strip timezone for merge_asof: convert to tz-naive UTC."""
    s = pd.to_datetime(s)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


def _direction_sub_metrics(sub: pd.DataFrame, profit_col: str) -> Dict[str, Any]:
    """Compute basic metrics for a directional subset."""
    profits = pd.to_numeric(sub[profit_col], errors="coerce").dropna()
    n = int(len(profits))
    if n == 0:
        return {"n_trades": 0, "profit_factor": None, "win_rate": None, "net_profit": None, "avg_trade": None}
    n_winners = int((profits > 0).sum())
    win_rate = _safe_float(n_winners / n) if n > 0 else None
    net_profit = _safe_float(profits.sum())
    avg_trade = _safe_float(profits.mean())
    gross_profit = float(profits[profits > 0].sum())
    gross_loss = abs(float(profits[profits < 0].sum()))
    profit_factor = _safe_float(gross_profit / gross_loss) if gross_loss > 0 else None
    return {
        "n_trades": n,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "net_profit": net_profit,
        "avg_trade": avg_trade,
    }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def compute_profit_factor(profits: pd.Series) -> Optional[float]:
    """
    Profit factor = gross_profit / gross_loss.
    Returns None when there are no losing trades.
    """
    gross_profit = float(profits[profits > 0].sum())
    gross_loss = abs(float(profits[profits < 0].sum()))
    if gross_loss > 0:
        return _safe_float(gross_profit / gross_loss)
    return None


def compute_max_drawdown(profits: pd.Series) -> float:
    """
    Compute maximum peak-to-trough drawdown on a cumulative equity curve.
    Returns a positive number representing the worst drop.
    """
    arr = pd.to_numeric(profits, errors="coerce").fillna(0.0).values
    if len(arr) == 0:
        return 0.0
    cumulative = np.cumsum(arr)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    return float(np.max(drawdowns))


def compute_evaluation_metrics(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
) -> Dict[str, Any]:
    """
    Compute comprehensive performance metrics on a cost-normalized trade set.

    Parameters
    ----------
    trades     : DataFrame with profit_net, market_pos, entry_time, exit_time columns
    profit_col : column containing net profit per trade (default: 'profit_net')

    Returns
    -------
    JSON-safe dict of performance metrics.
    """
    result: Dict[str, Any] = {}

    # Guard
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        return {"error": "no trades provided", "n_trades": 0}

    if profit_col not in trades.columns:
        return {"error": f"profit column '{profit_col}' not found", "n_trades": len(trades)}

    profits = pd.to_numeric(trades[profit_col], errors="coerce").dropna()
    n_trades = int(len(profits))
    n_winners = int((profits > 0).sum())
    n_losers = int((profits < 0).sum())

    result["n_trades"] = n_trades
    result["n_winners"] = n_winners
    result["n_losers"] = n_losers

    # Core P&L metrics
    net_profit = _safe_float(profits.sum())
    gross_profit = _safe_float(float(profits[profits > 0].sum()))
    gross_loss = _safe_float(float(profits[profits < 0].sum()))  # negative number
    result["net_profit"] = net_profit
    result["gross_profit"] = gross_profit
    result["gross_loss"] = gross_loss

    result["profit_factor"] = compute_profit_factor(profits)

    win_rate = _safe_float(n_winners / n_trades) if n_trades > 0 else None
    result["win_rate"] = win_rate

    avg_trade = _safe_float(profits.mean()) if n_trades > 0 else None
    winner_profits = profits[profits > 0]
    loser_profits = profits[profits < 0]
    avg_winner = _safe_float(winner_profits.mean()) if len(winner_profits) > 0 else None
    avg_loser = _safe_float(loser_profits.mean()) if len(loser_profits) > 0 else None
    result["avg_trade"] = avg_trade
    result["avg_winner"] = avg_winner
    result["avg_loser"] = avg_loser

    # Expectancy = win_rate * avg_winner + (1 - win_rate) * avg_loser
    if win_rate is not None and avg_winner is not None and avg_loser is not None:
        result["expectancy"] = _safe_float(win_rate * avg_winner + (1.0 - win_rate) * avg_loser)
    else:
        result["expectancy"] = avg_trade  # fallback to avg_trade

    result["max_drawdown"] = _safe_float(compute_max_drawdown(profits))

    # Average duration in minutes
    avg_duration_min: Optional[float] = None
    if "entry_time" in trades.columns and "exit_time" in trades.columns:
        try:
            entry_dt = pd.to_datetime(trades["entry_time"])
            exit_dt = pd.to_datetime(trades["exit_time"])
            durations = (exit_dt - entry_dt).dt.total_seconds() / 60.0
            valid_durations = durations.dropna()
            if len(valid_durations) > 0:
                avg_duration_min = _safe_float(valid_durations.mean())
        except Exception:
            pass
    result["avg_duration_min"] = avg_duration_min

    # Equity curve (capped at 500 points)
    cumulative = profits.cumsum().tolist()
    if len(cumulative) > 500:
        step = max(1, len(cumulative) // 500)
        sampled: List[float] = cumulative[::step]
        # Always include the last point
        if sampled[-1] != cumulative[-1]:
            sampled.append(cumulative[-1])
        equity_curve = [_safe_float(v) for v in sampled]
    else:
        equity_curve = [_safe_float(v) for v in cumulative]
    result["equity_curve"] = equity_curve

    # By-direction breakdown
    by_direction: Dict[str, Any] = {}
    if "market_pos" in trades.columns:
        try:
            mp = trades["market_pos"].astype(str).str.strip().str.lower()
            for label, values in [("Long", ("long", "1")), ("Short", ("short", "-1"))]:
                mask = mp.isin(values)
                sub = trades[mask]
                if profit_col in sub.columns:
                    by_direction[label] = _direction_sub_metrics(sub, profit_col)
                else:
                    by_direction[label] = {"n_trades": int(len(sub))}
        except Exception:
            pass
    result["by_direction"] = by_direction

    # By-session breakdown
    by_session: Dict[str, Any] = {}
    if "entry_time" in trades.columns:
        try:
            entry_dt = pd.to_datetime(trades["entry_time"])
            # Use local hour (America/Denver)
            if entry_dt.dt.tz is not None:
                local_dt = entry_dt.dt.tz_convert("America/Denver")
            else:
                local_dt = entry_dt
            hours = local_dt.dt.hour
            session_labels = hours.map(_session_label)

            working = trades.copy()
            working["_session"] = session_labels.values
            working["_profit"] = pd.to_numeric(working[profit_col], errors="coerce")

            for sess, grp in working.groupby("_session"):
                sess_profits = grp["_profit"].dropna()
                n_s = int(len(sess_profits))
                if n_s == 0:
                    by_session[str(sess)] = {"n_trades": 0, "win_rate": None, "net_profit": None, "avg_trade": None, "profit_factor": None}
                    continue
                n_w = int((sess_profits > 0).sum())
                by_session[str(sess)] = {
                    "n_trades": n_s,
                    "win_rate": _safe_float(n_w / n_s),
                    "net_profit": _safe_float(sess_profits.sum()),
                    "avg_trade": _safe_float(sess_profits.mean()),
                    "profit_factor": compute_profit_factor(sess_profits),
                }
        except Exception:
            pass
    result["by_session"] = by_session

    return result


def compute_regime_breakdown(
    trades: pd.DataFrame,
    bars_with_regime: pd.DataFrame,
    profit_col: str = "profit_net",
) -> Dict[str, Any]:
    """
    Join trades to bars_with_regime by entry_time using merge_asof (backward),
    then group by regime column.

    Returns dict keyed by regime label, each with:
      n_trades, win_rate, net_profit, avg_trade, profit_factor

    Returns {} when either DataFrame is missing/empty or required columns absent.
    """
    # Guards
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        return {}
    if bars_with_regime is None or not isinstance(bars_with_regime, pd.DataFrame) or len(bars_with_regime) == 0:
        return {}
    if "dt" not in bars_with_regime.columns or "regime" not in bars_with_regime.columns:
        return {}
    if "entry_time" not in trades.columns:
        return {}
    if profit_col not in trades.columns:
        return {}

    try:
        # Normalize timestamps to naive UTC for merge_asof
        trades_work = trades.copy()
        trades_work["_entry_utc"] = _tz_to_naive_utc(pd.to_datetime(trades_work["entry_time"]))
        trades_work = trades_work.sort_values("_entry_utc").reset_index(drop=True)

        bars_work = bars_with_regime.copy()
        bars_work["_dt_utc"] = _tz_to_naive_utc(pd.to_datetime(bars_work["dt"]))
        bars_work = bars_work.sort_values("_dt_utc").reset_index(drop=True)

        # Columns to bring from bars_with_regime
        bars_cols = ["_dt_utc", "regime"]

        merged = pd.merge_asof(
            trades_work,
            bars_work[bars_cols],
            left_on="_entry_utc",
            right_on="_dt_utc",
            direction="backward",
        )

        result: Dict[str, Any] = {}
        for regime_label, grp in merged.groupby("regime"):
            regime_profits = pd.to_numeric(grp[profit_col], errors="coerce").dropna()
            n_s = int(len(regime_profits))
            if n_s == 0:
                result[str(regime_label)] = {"n_trades": 0, "win_rate": None, "net_profit": None, "avg_trade": None, "profit_factor": None}
                continue
            n_w = int((regime_profits > 0).sum())
            result[str(regime_label)] = {
                "n_trades": n_s,
                "win_rate": _safe_float(n_w / n_s),
                "net_profit": _safe_float(regime_profits.sum()),
                "avg_trade": _safe_float(regime_profits.mean()),
                "profit_factor": compute_profit_factor(regime_profits),
            }
        return result

    except Exception:
        return {}
