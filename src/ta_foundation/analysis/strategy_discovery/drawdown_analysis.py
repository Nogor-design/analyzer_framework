from __future__ import annotations

"""
Drawdown Analysis
=================
Computes peak-to-trough equity curve metrics from the historical trade record.

Answers: "How bad did it get, and how long did recovery take?"

Complements the Monte Carlo simulation in position_sizing (which estimates
future paths) with deterministic analysis of what *actually* happened.

Metrics computed
----------------
Overall:
  max_drawdown_usd        — largest peak-to-trough drop in dollars
  max_drawdown_pct        — same as % of peak equity
  max_dd_start            — ISO date when peak was set
  max_dd_trough           — ISO date of trough
  max_dd_recovery         — ISO date of recovery to new equity high (or None)
  recovery_bars           — trades from trough to recovery (None if unrecovered)
  longest_drawdown_bars   — longest time (in trades) spent below prior peak
  recovery_factor         — net_profit / |max_drawdown_usd|  (higher = better)
  ulcer_index             — RMS of percentage drawdowns (penalises duration)
  avg_drawdown_pct        — mean of all per-trade drawdown percentages

Streak analysis:
  max_consecutive_losses  — largest losing streak (count)
  max_loss_streak_usd     — total $ lost in the worst losing streak
  avg_loss_streak_len     — average losing streak length
  max_consecutive_wins    — largest winning streak (count)
  avg_win_streak_len      — average winning streak length

Rolling window (default: 20-trade window):
  rolling_max_dd_pct      — list of {period_end, max_dd_pct} per window

Regime breakdown (when bars_with_regime provided):
  by_regime               — {regime: {max_dd_pct, max_consecutive_losses}}

Entry point: run_drawdown_analysis(pkg, options, profit_col, bars_with_regime)
Attaches to: discovery_block["drawdown_analysis"]
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":          True,
    "initial_equity":   10_000.0,
    "min_trades":       10,
    "profit_col":       "profit_net",
    "rolling_window":   20,       # trades per rolling window for max-DD series
    "rolling_step":     10,       # stride between windows
}


# ---------------------------------------------------------------------------
# Equity curve helpers
# ---------------------------------------------------------------------------

def _build_equity_curve(
    profits: np.ndarray,
    initial: float = 10_000.0,
) -> np.ndarray:
    """Cumulative equity starting at `initial`."""
    return initial + np.cumsum(profits)


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    """
    Returns a 1-D array of drawdown percentages at each point.
    dd[i] = (peak[i] - equity[i]) / peak[i]   (0 = at all-time-high, positive = below peak)
    """
    running_peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_peak > 0, (running_peak - equity) / running_peak, 0.0)
    return dd


def _max_drawdown(
    profits: np.ndarray,
    initial: float,
) -> Tuple[float, float, int, int]:
    """
    Returns (max_dd_usd, max_dd_pct, peak_idx, trough_idx).
    Uses O(n) sweep.
    """
    equity = _build_equity_curve(profits, initial)
    peak_val = equity[0]
    peak_idx = 0
    best_peak_idx = 0
    best_trough_idx = 0
    best_dd_usd = 0.0

    for i in range(1, len(equity)):
        if equity[i] > peak_val:
            peak_val = equity[i]
            peak_idx = i
        dd = peak_val - equity[i]
        if dd > best_dd_usd:
            best_dd_usd = dd
            best_peak_idx   = peak_idx
            best_trough_idx = i

    peak_equity = equity[best_peak_idx]
    best_dd_pct = best_dd_usd / peak_equity if peak_equity > 0 else 0.0
    return float(best_dd_usd), float(best_dd_pct), int(best_peak_idx), int(best_trough_idx)


def _recovery_idx(equity: np.ndarray, peak_equity: float, trough_idx: int) -> Optional[int]:
    """Index of first trade after trough where equity surpasses peak_equity."""
    for i in range(trough_idx + 1, len(equity)):
        if equity[i] >= peak_equity:
            return i
    return None


def _longest_drawdown_bars(equity: np.ndarray) -> int:
    """
    Longest continuous stretch (in trade count) where equity is below
    its prior peak.
    """
    running_peak = np.maximum.accumulate(equity)
    below_peak = equity < running_peak
    max_run = 0
    cur_run = 0
    for b in below_peak:
        if b:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_run = 0
    return int(max_run)


def _ulcer_index(dd_series: np.ndarray) -> float:
    """Root mean square of the drawdown percentage series."""
    return float(np.sqrt(np.mean(dd_series ** 2)))


# ---------------------------------------------------------------------------
# Streak analysis
# ---------------------------------------------------------------------------

def _streak_stats(profits: np.ndarray) -> Dict[str, Any]:
    """
    Compute win/loss streak statistics.
    Returns dict with max_consecutive_losses, max_loss_streak_usd,
    avg_loss_streak_len, max_consecutive_wins, avg_win_streak_len.
    """
    is_win = profits > 0
    loss_streaks: List[Tuple[int, float]] = []   # (length, total_usd)
    win_streaks:  List[int] = []

    cur_loss_len = 0
    cur_loss_usd = 0.0
    cur_win_len  = 0

    for i, w in enumerate(is_win):
        if w:
            if cur_loss_len > 0:
                loss_streaks.append((cur_loss_len, cur_loss_usd))
                cur_loss_len = 0
                cur_loss_usd = 0.0
            cur_win_len += 1
        else:
            if cur_win_len > 0:
                win_streaks.append(cur_win_len)
                cur_win_len = 0
            cur_loss_len += 1
            cur_loss_usd += float(profits[i])

    # flush
    if cur_loss_len > 0:
        loss_streaks.append((cur_loss_len, cur_loss_usd))
    if cur_win_len > 0:
        win_streaks.append(cur_win_len)

    if not loss_streaks:
        max_loss_len = 0
        max_loss_usd = 0.0
        avg_loss_len = 0.0
    else:
        max_loss_len = int(max(s[0] for s in loss_streaks))
        worst         = min(loss_streaks, key=lambda s: s[1])
        max_loss_usd  = float(worst[1])
        avg_loss_len  = float(np.mean([s[0] for s in loss_streaks]))

    if not win_streaks:
        max_win_len  = 0
        avg_win_len  = 0.0
    else:
        max_win_len  = int(max(win_streaks))
        avg_win_len  = float(np.mean(win_streaks))

    return {
        "max_consecutive_losses": max_loss_len,
        "max_loss_streak_usd":    round(max_loss_usd, 2),
        "avg_loss_streak_len":    round(avg_loss_len, 2),
        "max_consecutive_wins":   max_win_len,
        "avg_win_streak_len":     round(avg_win_len, 2),
        "n_loss_streaks":         len(loss_streaks),
        "n_win_streaks":          len(win_streaks),
    }


# ---------------------------------------------------------------------------
# Rolling window max-DD
# ---------------------------------------------------------------------------

def _rolling_max_dd(
    profits: np.ndarray,
    entry_times: Optional[pd.Series],
    window: int,
    step: int,
    initial: float,
) -> List[Dict[str, Any]]:
    """
    Compute rolling max-DD over `window`-trade windows with `step` stride.
    Returns list of {period_end, max_dd_pct}.
    """
    n = len(profits)
    result = []
    for start in range(0, n - window + 1, step):
        chunk = profits[start: start + window]
        _, dd_pct, _, _ = _max_drawdown(chunk, initial)
        period_end = None
        if entry_times is not None and len(entry_times) > start + window - 1:
            t = entry_times.iloc[start + window - 1]
            try:
                period_end = str(pd.to_datetime(t).date())
            except Exception:
                period_end = str(t)
        result.append({
            "window_start_idx": int(start),
            "period_end":       period_end,
            "max_dd_pct":       round(dd_pct * 100, 2),
        })
    return result


# ---------------------------------------------------------------------------
# Regime breakdown
# ---------------------------------------------------------------------------

def _regime_breakdown(
    profits: np.ndarray,
    regimes: pd.Series,
    initial: float,
) -> Dict[str, Any]:
    """
    Per-regime max_dd_pct and max_consecutive_losses.
    """
    result: Dict[str, Any] = {}
    regime_arr = regimes.values
    unique = np.unique(regime_arr)
    for reg in unique:
        mask = regime_arr == reg
        p = profits[mask]
        if len(p) < 3:
            continue
        _, dd_pct, _, _ = _max_drawdown(p, initial)
        streaks = _streak_stats(p)
        result[str(reg)] = {
            "n_trades":              int(mask.sum()),
            "max_dd_pct":            round(dd_pct * 100, 2),
            "max_consecutive_losses": streaks["max_consecutive_losses"],
        }
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_drawdown_analysis(
    pkg: Any,
    options: Dict[str, Any],
    *,
    profit_col: str = "profit_net",
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Compute deterministic drawdown analysis from the historical trade record.

    Returns JSON-safe dict:
      overall        : {max_drawdown_usd, max_drawdown_pct, max_dd_start,
                        max_dd_trough, max_dd_recovery, recovery_bars,
                        longest_drawdown_bars, recovery_factor, ulcer_index,
                        avg_drawdown_pct, net_profit}
      streaks        : {max_consecutive_losses, max_loss_streak_usd,
                        avg_loss_streak_len, max_consecutive_wins,
                        avg_win_streak_len, n_loss_streaks, n_win_streaks}
      rolling_max_dd : [{window_start_idx, period_end, max_dd_pct}, ...]
      by_regime      : {regime: {n_trades, max_dd_pct, max_consecutive_losses}}
      diagnostics    : {n_trades, initial_equity, profit_col_used, issues}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    initial_equity = float(opts.get("initial_equity", 10_000.0))
    min_trades     = int(opts.get("min_trades", 10))
    rolling_window = int(opts.get("rolling_window", 20))
    rolling_step   = int(opts.get("rolling_step", 10))
    profit_col     = str(opts.get("profit_col", profit_col))

    diag: Dict[str, Any] = {
        "initial_equity":  initial_equity,
        "profit_col_used": profit_col,
        "n_trades":        0,
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
        t = t.sort_values("entry_time").reset_index(drop=True)
    t = t.dropna(subset=[profit_col]).reset_index(drop=True)

    n = len(t)
    diag["n_trades"] = int(n)

    if n < min_trades:
        diag["issues"].append(f"insufficient trades: {n} < {min_trades}")
        return _empty(diag)

    profits = t[profit_col].values.astype(float)
    entry_times = t["entry_time"] if "entry_time" in t.columns else None

    # ---- overall drawdown ----
    equity = _build_equity_curve(profits, initial_equity)
    dd_usd, dd_pct, peak_idx, trough_idx = _max_drawdown(profits, initial_equity)

    # Peak and trough dates
    def _dt_str(idx: int) -> Optional[str]:
        if entry_times is None or idx >= len(entry_times):
            return None
        try:
            return str(pd.to_datetime(entry_times.iloc[idx]).date())
        except Exception:
            return None

    peak_equity  = float(equity[peak_idx])
    rec_idx      = _recovery_idx(equity, peak_equity, trough_idx)
    recovery_bars = int(rec_idx - trough_idx) if rec_idx is not None else None

    dd_series     = _drawdown_series(equity)
    ulcer         = _ulcer_index(dd_series)
    avg_dd_pct    = float(np.mean(dd_series)) * 100
    net_profit    = float(profits.sum())
    rec_factor    = net_profit / dd_usd if dd_usd > 0 else None

    longest_dd    = _longest_drawdown_bars(equity)

    overall = {
        "max_drawdown_usd":      round(dd_usd, 2),
        "max_drawdown_pct":      round(dd_pct * 100, 2),
        "max_dd_start":          _dt_str(peak_idx),
        "max_dd_trough":         _dt_str(trough_idx),
        "max_dd_recovery":       _dt_str(rec_idx) if rec_idx is not None else None,
        "recovery_bars":         recovery_bars,
        "longest_drawdown_bars": longest_dd,
        "recovery_factor":       round(rec_factor, 3) if rec_factor is not None else None,
        "ulcer_index":           round(ulcer * 100, 3),   # expressed as %
        "avg_drawdown_pct":      round(avg_dd_pct, 3),
        "net_profit":            round(net_profit, 2),
        "initial_equity":        initial_equity,
    }

    # ---- streak analysis ----
    streaks = _streak_stats(profits)

    # ---- rolling max-DD ----
    rolling = _rolling_max_dd(profits, entry_times, rolling_window, rolling_step, initial_equity)

    # ---- regime breakdown ----
    by_regime: Dict[str, Any] = {}
    if bars_with_regime is not None and "regime" in bars_with_regime.columns and entry_times is not None:
        try:
            et = pd.to_datetime(entry_times)
            if et.dt.tz is not None:
                et_naive = et.dt.tz_convert("UTC").dt.tz_localize(None)
            else:
                et_naive = et

            b = bars_with_regime.copy()
            b_dt = pd.to_datetime(b["dt"])
            if b_dt.dt.tz is not None:
                b_dt = b_dt.dt.tz_convert("UTC").dt.tz_localize(None)
            b["_dt_naive"] = b_dt

            joined = pd.merge_asof(
                pd.DataFrame({"_entry_naive": et_naive.values}).sort_values("_entry_naive"),
                b[["_dt_naive", "regime"]].sort_values("_dt_naive"),
                left_on="_entry_naive",
                right_on="_dt_naive",
                direction="backward",
            )
            regime_series = pd.Series(joined["regime"].values, name="regime")
            by_regime = _regime_breakdown(profits, regime_series, initial_equity)
        except Exception as exc:
            diag["issues"].append(f"regime breakdown failed: {exc}")

    return _make_json_safe({
        "overall":        overall,
        "streaks":        streaks,
        "rolling_max_dd": rolling,
        "by_regime":      by_regime,
        "diagnostics":    diag,
    })


def _empty(diag: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "overall":        {},
        "streaks":        {},
        "rolling_max_dd": [],
        "by_regime":      {},
        "diagnostics":    diag,
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
