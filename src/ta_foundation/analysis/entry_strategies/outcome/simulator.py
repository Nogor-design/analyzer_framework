from __future__ import annotations

"""
Outcome Simulator
=================
Forward-scans 1-minute bars to:
  1. Fill pending limit/stop-limit entries (break_extreme, body_midpoint modes).
  2. Resolve each filled entry as WIN, LOSS, or TIMEOUT.

Two outcome modes
-----------------
  atr   — stop = entry ± stop_mult * ATR;  target = entry ± target_mult * ATR
           ATR is read from the signal bar's ``signal_atr`` column.

  ticks — stop = entry ± sl_ticks * tick_size;  target = entry ± tp_ticks * tick_size
           Supports a grid of (tp_ticks, sl_ticks) combos in one pass.

Output
------
Returns a DataFrame of synthetic trades compatible with the existing
``evaluation.compute_evaluation_metrics()`` contract:

  entry_time    tz-aware America/Denver
  exit_time     tz-aware America/Denver
  market_pos    "Long" | "Short"
  profit_net    net P&L in dollars (after simple cost model)
  profit_ticks  gross ticks
  entry_price
  exit_price
  stop_price
  target_price
  result        "win" | "loss" | "timeout"
  timing_mode   carried from pending entry
  outcome_mode  "atr_{target}x{stop}" | "ticks_{tp}_{sl}"
  fill_bars     bars from signal to fill (0 for next_open)
  + all candle feature columns from the pending entry row

Timeout handling
----------------
  timeout_result = "loss"     count as loss at stop_price (conservative)
  timeout_result = "at_close" P&L at timeout bar close
  timeout_result = "neutral"  excluded from win/loss counts (profit_net = 0)
"""

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OUTCOME_CONFIG: Dict[str, Any] = {
    "atr": {
        "enabled": True,
        "target_mult": 1.5,
        "stop_mult": 1.0,
    },
    "ticks": {
        "enabled": True,
        "take_profit": [30, 60, 100],
        "stop": [30, 40, 50],
    },
    "max_bars_timeout": 20,
    "timeout_result": "loss",       # "loss" | "at_close" | "neutral"
    "tick_size": 0.25,
    "tick_value": 5.00,
    "commission_per_side": 2.09,    # dollars per contract per side
    "slippage_ticks": 1,            # ticks per side
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tz_aware(dt_series: pd.Series, tz: str = "America/Denver") -> pd.Series:
    s = pd.to_datetime(dt_series)
    if s.dt.tz is None:
        return s.dt.tz_localize(tz)
    return s.dt.tz_convert(tz)


def _net_profit(
    profit_ticks: float,
    tick_value: float,
    commission_per_side: float,
    slippage_ticks: float,
    tick_size: float,
) -> float:
    gross = profit_ticks * tick_value
    commission_cost = 2.0 * commission_per_side
    slippage_cost   = 2.0 * slippage_ticks * tick_size * (tick_value / tick_size)
    return gross - commission_cost - slippage_cost


def _resolve_outcome(
    entry_price: float,
    direction: int,
    stop_price: float,
    target_price: float,
    bars_1m: pd.DataFrame,
    start_idx: int,
    max_bars: int,
    timeout_result: str,
    tick_size: float,
    tick_value: float,
    commission_per_side: float,
    slippage_ticks: float,
) -> Tuple[str, float, float, pd.Timestamp]:
    """
    Scan forward from *start_idx* in *bars_1m* to resolve win/loss/timeout.

    Returns (result, profit_ticks, exit_price, exit_dt).
    Conservative tie-breaking: if both target and stop are hit in the same bar → loss.
    """
    end_idx = min(start_idx + max_bars, len(bars_1m))

    for i in range(start_idx, end_idx):
        bar = bars_1m.iloc[i]
        bar_high = float(bar["high"])
        bar_low  = float(bar["low"])
        bar_dt   = bar["dt"]

        if direction == 1:
            stop_hit   = bar_low  <= stop_price
            target_hit = bar_high >= target_price
        else:
            stop_hit   = bar_high >= stop_price
            target_hit = bar_low  <= target_price

        if stop_hit and target_hit:
            # Both in same bar — conservative: take loss
            exit_p = stop_price
            ticks  = (exit_p - entry_price) * direction / tick_size
            return "loss", ticks, exit_p, bar_dt

        if target_hit:
            exit_p = target_price
            ticks  = (exit_p - entry_price) * direction / tick_size
            return "win", ticks, exit_p, bar_dt

        if stop_hit:
            exit_p = stop_price
            ticks  = (exit_p - entry_price) * direction / tick_size
            return "loss", ticks, exit_p, bar_dt

    # Timeout
    last_bar = bars_1m.iloc[min(end_idx - 1, len(bars_1m) - 1)]
    last_dt  = last_bar["dt"]
    if timeout_result == "at_close":
        exit_p = float(last_bar["close"])
        ticks  = (exit_p - entry_price) * direction / tick_size
        return "timeout", ticks, exit_p, last_dt
    elif timeout_result == "neutral":
        return "timeout", 0.0, float(last_bar["close"]), last_dt
    else:  # "loss" — conservative: assume stop hit
        exit_p = stop_price
        ticks  = (exit_p - entry_price) * direction / tick_size
        return "timeout", ticks, exit_p, last_dt


def _fill_limit_order(
    limit_price: float,
    direction: int,
    bars_1m: pd.DataFrame,
    signal_idx: int,
    fill_timeout_bars: int,
) -> Tuple[Optional[int], Optional[pd.Timestamp]]:
    """
    Scan up to *fill_timeout_bars* 1m bars from *signal_idx* to fill a limit order.

    Long limit (break_extreme): fills when bar_high >= limit_price
    Short limit (break_extreme): fills when bar_low <= limit_price
    Body midpoint (both dirs): fills when bar range includes limit_price

    Returns (bar_idx_of_fill, fill_dt) or (None, None) if not filled.
    """
    end = min(signal_idx + fill_timeout_bars + 1, len(bars_1m))
    for i in range(signal_idx + 1, end):
        bar = bars_1m.iloc[i]
        bar_high = float(bar["high"])
        bar_low  = float(bar["low"])
        if direction == 1:
            if bar_high >= limit_price:
                return i, bar["dt"]
        else:
            if bar_low <= limit_price:
                return i, bar["dt"]
    return None, None


def _build_1m_lookup(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """Sort and reset index for positional scanning."""
    return bars_1m.sort_values("dt").reset_index(drop=True)


def _dt_to_utc_ns(dt_series: pd.Series) -> np.ndarray:
    """
    Convert any datetime Series (tz-aware or tz-naive) to tz-naive UTC
    nanosecond integer array for fast searchsorted comparisons.

    Forces nanosecond resolution explicitly so results are consistent with
    pd.Timestamp.value on pandas 2.x where default resolution may be
    microseconds.
    """
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    # astype("datetime64[ns]") forces ns resolution before int conversion
    return s.astype("datetime64[ns]").astype("int64").values


def _ts_to_utc_ns(ts: Any) -> int:
    """Convert a single Timestamp (tz-aware or tz-naive) to UTC nanoseconds."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    # .value is always nanoseconds in pandas Timestamp
    return int(t.value)


# ---------------------------------------------------------------------------
# Public: simulate one outcome config on a set of pending entries
# ---------------------------------------------------------------------------

def simulate_atr_outcomes(
    pending: pd.DataFrame,
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Simulate ATR-based outcomes (target = entry ± target_mult * ATR).

    Parameters
    ----------
    pending  : pending-entries DataFrame from signals.emit_entries()
               Must have: signal_dt or dt, direction, entry_price or limit_price,
                          timing_mode, signal_atr (or atr)
    bars_1m  : 1-minute bars with dt, open, high, low, close
    config   : outcome config dict (merged with DEFAULT_OUTCOME_CONFIG)

    Returns
    -------
    Synthetic trades DataFrame.
    """
    cfg = {**DEFAULT_OUTCOME_CONFIG, **(config or {})}
    atr_cfg         = cfg.get("atr", {})
    target_mult     = float(atr_cfg.get("target_mult", 1.5))
    stop_mult       = float(atr_cfg.get("stop_mult", 1.0))
    max_bars        = int(cfg["max_bars_timeout"])
    timeout_result  = str(cfg["timeout_result"])
    tick_size       = float(cfg["tick_size"])
    tick_value      = float(cfg["tick_value"])
    comm            = float(cfg["commission_per_side"])
    slip_ticks      = float(cfg["slippage_ticks"])

    outcome_mode = f"atr_{target_mult}x{stop_mult}"

    if pending is None or pending.empty or bars_1m is None or bars_1m.empty:
        return pd.DataFrame()

    bars = _build_1m_lookup(bars_1m)
    cmp_arr = _dt_to_utc_ns(bars["dt"])

    records: List[Dict[str, Any]] = []
    _sig_dt_col = "signal_dt" if "signal_dt" in pending.columns else "dt"
    _atr_col    = "signal_atr" if "signal_atr" in pending.columns else "atr"

    for _, row in pending.iterrows():
        signal_dt    = row[_sig_dt_col]
        direction    = int(row["direction"])
        timing_mode  = str(row.get("timing_mode", "next_open"))
        atr_val      = float(row[_atr_col]) if _atr_col in row.index and pd.notna(row[_atr_col]) else None

        if atr_val is None or atr_val <= 0:
            continue

        sig_val    = _ts_to_utc_ns(signal_dt)
        candidates = np.searchsorted(cmp_arr, sig_val, side="left")
        if candidates >= len(bars):
            continue
        signal_bar_idx = int(candidates)

        # Determine entry price
        if timing_mode == "next_open":
            entry_price = float(row.get("entry_price", np.nan))
            if np.isnan(entry_price):
                continue
            entry_bar_idx = signal_bar_idx
            fill_bars_n   = 0
            entry_dt      = row.get("entry_time", signal_dt)
        else:
            # Limit order — need to fill
            limit_price       = float(row.get("limit_price", np.nan))
            fill_timeout_bars = int(row.get("fill_timeout_bars", 3))
            if np.isnan(limit_price):
                continue
            fill_idx, fill_dt = _fill_limit_order(
                limit_price, direction, bars, signal_bar_idx, fill_timeout_bars
            )
            if fill_idx is None:
                continue  # unfilled
            entry_price   = limit_price
            entry_bar_idx = fill_idx
            fill_bars_n   = fill_idx - signal_bar_idx
            entry_dt      = fill_dt

        # Compute stop and target
        stop_price   = entry_price - direction * stop_mult   * atr_val
        target_price = entry_price + direction * target_mult * atr_val

        # Resolve outcome
        result, profit_ticks, exit_price, exit_dt = _resolve_outcome(
            entry_price, direction, stop_price, target_price,
            bars, entry_bar_idx + 1, max_bars,
            timeout_result, tick_size, tick_value, comm, slip_ticks,
        )

        pnet = _net_profit(profit_ticks, tick_value, comm, slip_ticks, tick_size)

        rec = dict(row)
        rec.update({
            "entry_time":   _tz_aware(pd.Series([entry_dt])).iloc[0],
            "exit_time":    _tz_aware(pd.Series([exit_dt])).iloc[0],
            "market_pos":   "Long" if direction == 1 else "Short",
            "profit_net":   pnet,
            "profit_ticks": profit_ticks,
            "entry_price":  entry_price,
            "exit_price":   exit_price,
            "stop_price":   stop_price,
            "target_price": target_price,
            "result":       result,
            "outcome_mode": outcome_mode,
            "fill_bars":    fill_bars_n,
        })
        records.append(rec)

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).reset_index(drop=True)


def simulate_tick_outcomes(
    pending: pd.DataFrame,
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Simulate fixed-tick outcomes over a grid of (tp_ticks, sl_ticks) combos.

    Returns one synthetic trade per pending entry per (tp, sl) combo.
    The ``outcome_mode`` column encodes which combo: ``"ticks_{tp}_{sl}"``.
    """
    cfg = {**DEFAULT_OUTCOME_CONFIG, **(config or {})}
    tick_cfg        = cfg.get("ticks", {})
    tp_list         = [int(v) for v in tick_cfg.get("take_profit", [30, 60, 100])]
    sl_list         = [int(v) for v in tick_cfg.get("stop", [30, 40, 50])]
    max_bars        = int(cfg["max_bars_timeout"])
    timeout_result  = str(cfg["timeout_result"])
    tick_size       = float(cfg["tick_size"])
    tick_value      = float(cfg["tick_value"])
    comm            = float(cfg["commission_per_side"])
    slip_ticks      = float(cfg["slippage_ticks"])

    if pending is None or pending.empty or bars_1m is None or bars_1m.empty:
        return pd.DataFrame()

    bars = _build_1m_lookup(bars_1m)
    cmp_arr = _dt_to_utc_ns(bars["dt"])

    _sig_dt_col = "signal_dt" if "signal_dt" in pending.columns else "dt"
    combos = list(product(tp_list, sl_list))

    all_records: List[Dict[str, Any]] = []

    for _, row in pending.iterrows():
        signal_dt   = row[_sig_dt_col]
        direction   = int(row["direction"])
        timing_mode = str(row.get("timing_mode", "next_open"))

        sig_val    = _ts_to_utc_ns(signal_dt)
        candidates = int(np.searchsorted(cmp_arr, sig_val, side="left"))
        if candidates >= len(bars):
            continue
        signal_bar_idx = candidates

        # Determine entry price (shared across all combos)
        if timing_mode == "next_open":
            entry_price = float(row.get("entry_price", np.nan))
            if np.isnan(entry_price):
                continue
            entry_bar_idx = signal_bar_idx
            fill_bars_n   = 0
            entry_dt      = row.get("entry_time", signal_dt)
        else:
            limit_price       = float(row.get("limit_price", np.nan))
            fill_timeout_bars = int(row.get("fill_timeout_bars", 3))
            if np.isnan(limit_price):
                continue
            fill_idx, fill_dt = _fill_limit_order(
                limit_price, direction, bars, signal_bar_idx, fill_timeout_bars
            )
            if fill_idx is None:
                continue
            entry_price   = limit_price
            entry_bar_idx = fill_idx
            fill_bars_n   = fill_idx - signal_bar_idx
            entry_dt      = fill_dt

        for tp_ticks, sl_ticks in combos:
            stop_price   = entry_price - direction * sl_ticks * tick_size
            target_price = entry_price + direction * tp_ticks * tick_size

            result, profit_ticks, exit_price, exit_dt = _resolve_outcome(
                entry_price, direction, stop_price, target_price,
                bars, entry_bar_idx + 1, max_bars,
                timeout_result, tick_size, tick_value, comm, slip_ticks,
            )

            pnet = _net_profit(profit_ticks, tick_value, comm, slip_ticks, tick_size)

            rec = dict(row)
            rec.update({
                "entry_time":   _tz_aware(pd.Series([entry_dt])).iloc[0],
                "exit_time":    _tz_aware(pd.Series([exit_dt])).iloc[0],
                "market_pos":   "Long" if direction == 1 else "Short",
                "profit_net":   pnet,
                "profit_ticks": profit_ticks,
                "entry_price":  entry_price,
                "exit_price":   exit_price,
                "stop_price":   stop_price,
                "target_price": target_price,
                "result":       result,
                "outcome_mode": f"ticks_{tp_ticks}_{sl_ticks}",
                "fill_bars":    fill_bars_n,
                "tp_ticks":     tp_ticks,
                "sl_ticks":     sl_ticks,
            })
            all_records.append(rec)

    if not all_records:
        return pd.DataFrame()
    return pd.DataFrame(all_records).reset_index(drop=True)


def simulate_outcomes(
    pending: pd.DataFrame,
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Run both ATR and tick-grid outcome simulations and return combined results.

    Parameters
    ----------
    pending : pending-entries from signals.emit_entries()
    bars_1m : 1-minute bars for forward scanning
    config  : outcome config dict (see DEFAULT_OUTCOME_CONFIG)

    Returns
    -------
    Combined DataFrame of all synthetic trades from enabled outcome modes.
    """
    cfg = {**DEFAULT_OUTCOME_CONFIG, **(config or {})}
    parts: List[pd.DataFrame] = []

    if cfg.get("atr", {}).get("enabled", True):
        atr_trades = simulate_atr_outcomes(pending, bars_1m, cfg)
        if not atr_trades.empty:
            parts.append(atr_trades)

    if cfg.get("ticks", {}).get("enabled", True):
        tick_trades = simulate_tick_outcomes(pending, bars_1m, cfg)
        if not tick_trades.empty:
            parts.append(tick_trades)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)
