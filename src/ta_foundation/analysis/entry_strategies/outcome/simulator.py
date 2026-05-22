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


def count_outcome_modes(outcome_cfg: Optional[Dict[str, Any]] = None) -> int:
    """Number of outcome-mode cells ``simulate_outcomes`` expands per entry.

    ATR contributes exactly one mode (``target_mult``/``stop_mult`` are
    scalars); the tick grid contributes ``len(take_profit) * len(stop)``.
    Mirrors the enabled-checks in ``simulate_outcomes`` so a sweep can size its
    parameter grid without running the simulation.
    """
    cfg = {**DEFAULT_OUTCOME_CONFIG, **(outcome_cfg or {})}
    n = 0
    atr_cfg = cfg.get("atr", {}) or {}
    if atr_cfg.get("enabled", True):
        n += 1
    tick_cfg = cfg.get("ticks", {}) or {}
    if tick_cfg.get("enabled", True):
        tp = tick_cfg.get("take_profit", [30, 60, 100])
        sl = tick_cfg.get("stop", [30, 40, 50])
        n_tp = len(tp) if isinstance(tp, (list, tuple)) else 1
        n_sl = len(sl) if isinstance(sl, (list, tuple)) else 1
        n += n_tp * n_sl
    return max(1, n)


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
    Conservative tie-breaking: if both target and stop are hit in the same bar -> loss.
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
    timing_mode: str = "break_extreme",
) -> Tuple[Optional[int], Optional[pd.Timestamp]]:
    """
    Scan up to *fill_timeout_bars* 1m bars from *signal_idx* to fill a pending
    entry order. The trigger side depends on the order type:

    break_extreme places a STOP beyond the signal bar's extreme -- it fills
    when price breaks out to the trigger (long: bar_high >= price).
    body_midpoint places a LIMIT inside the bar's body -- it fills when price
    pulls back to the limit (long: bar_low <= price).

    Applying the break_extreme rule to body_midpoint (the old behaviour)
    fabricated an instant fill at a price better than market and inflated
    every body_midpoint candidate.

    Returns (bar_idx_of_fill, fill_dt) or (None, None) if not filled.
    """
    end = min(signal_idx + fill_timeout_bars + 1, len(bars_1m))
    is_limit = timing_mode == "body_midpoint"
    for i in range(signal_idx + 1, end):
        bar = bars_1m.iloc[i]
        bar_high = float(bar["high"])
        bar_low  = float(bar["low"])
        if direction == 1:
            filled = bar_low <= limit_price if is_limit else bar_high >= limit_price
        else:
            filled = bar_high >= limit_price if is_limit else bar_low <= limit_price
        if filled:
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
# Vectorized forward-scan helpers (used by simulate_tick_outcomes fast path)
# ---------------------------------------------------------------------------

def _build_forward_windows(
    bar_indices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    n_bars: int,
    max_bars: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Build 2D forward-scan windows for all signals in one vectorized pass.

    Parameters
    ----------
    bar_indices : (n,) **signal** bar positions. The scan starts at
        bar_indices + 1 -- for a next_open entry that bar IS the entry bar
        (the entry fills at the next bar's open), so the entry bar's full
        range is included in the TP/SL scan. This is correct, not a skip.
    highs, lows, closes : (n_bars,) price arrays
    n_bars : total number of bars
    max_bars : timeout window size

    Returns
    -------
    fw_h, fw_l, fw_c : (n, max_bars) arrays; NaN for out-of-range positions
    in_bounds        : (n, max_bars) bool mask
    last_valid_col   : (n,) index of last valid bar per signal
    """
    scan_starts = bar_indices + 1                              # (n,)
    j = np.arange(max_bars)                                    # (max_bars,)
    all_idx = scan_starts[:, None] + j[None, :]               # (n, max_bars)
    in_bounds = all_idx < n_bars                               # (n, max_bars)
    clipped = np.clip(all_idx, 0, n_bars - 1)

    fw_h = np.where(in_bounds, highs[clipped], np.nan)
    fw_l = np.where(in_bounds, lows[clipped], np.nan)
    fw_c = np.where(in_bounds, closes[clipped], np.nan)

    last_valid_col = in_bounds.sum(axis=1).astype(np.int64) - 1
    last_valid_col = np.clip(last_valid_col, 0, max_bars - 1)

    return fw_h, fw_l, fw_c, in_bounds, last_valid_col


def _resolve_outcomes_vectorized(
    ep: np.ndarray,
    dirs: np.ndarray,
    tp_p: np.ndarray,
    sl_p: np.ndarray,
    fw_h: np.ndarray,
    fw_l: np.ndarray,
    fw_c: np.ndarray,
    in_bounds: np.ndarray,
    last_valid_col: np.ndarray,
    max_bars: int,
    timeout_result: str,
    tick_size: float,
    tick_value: float,
    comm: float,
    slip_ticks: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Vectorized outcome resolution for one (tp, sl) combo.

    Returns
    -------
    res_arr    : (n,) str array "win" | "loss" | "timeout"
    pticks     : (n,) profit in ticks
    pnet       : (n,) net P&L in dollars
    exit_col   : (n,) column index in fw_h/fw_l where exit occurred
    """
    nan_m = ~in_bounds                                         # (n, max_bars)
    dirs_2d = dirs[:, None]                                    # (n, 1)
    tp_2d = tp_p[:, None]
    sl_2d = sl_p[:, None]

    # Target / stop hit masks
    t_hit = np.where(dirs_2d == 1, fw_h >= tp_2d, fw_l <= tp_2d) & ~nan_m
    s_hit = np.where(dirs_2d == 1, fw_l <= sl_2d, fw_h >= sl_2d) & ~nan_m

    # First bar where each condition fires
    ft = np.argmax(t_hit, axis=1)           # (n,) — 0 if never
    fs = np.argmax(s_hit, axis=1)
    has_t = t_hit.any(axis=1)
    has_s = s_hit.any(axis=1)

    ft_eff = np.where(has_t, ft, max_bars)  # max_bars = "never"
    fs_eff = np.where(has_s, fs, max_bars)

    # Outcome classification (conservative: both-same-bar -> loss)
    both_same = has_t & has_s & (ft == fs)
    is_win     = has_t & ~both_same & (ft_eff < fs_eff)
    is_loss    = (~is_win) & (has_s | both_same)
    is_timeout = ~is_win & ~is_loss

    # Exit prices
    n = len(ep)
    arange_n = np.arange(n)
    if timeout_result == "at_close":
        timeout_ep = fw_c[arange_n, last_valid_col]
    elif timeout_result == "neutral":
        timeout_ep = fw_c[arange_n, last_valid_col]
    else:  # "loss" conservative
        timeout_ep = sl_p

    exit_p = np.where(is_win, tp_p, np.where(is_loss, sl_p, timeout_ep))

    # Profit ticks
    pticks = (exit_p - ep) * dirs / tick_size
    if timeout_result == "neutral":
        pticks = np.where(is_timeout, 0.0, pticks)

    # Net P&L
    cost = 2.0 * comm + 2.0 * slip_ticks * tick_size * (tick_value / tick_size)
    pnet = pticks * tick_value - cost

    # Exit column index (for exit timestamp lookup)
    exit_col = np.where(is_win, ft_eff, np.where(is_loss, fs_eff, last_valid_col))
    exit_col = exit_col.clip(0, max_bars - 1).astype(np.int64)

    res_arr = np.where(is_win, "win", np.where(is_loss, "loss", "timeout"))
    return res_arr, pticks, pnet, exit_col


def _simulate_tick_outcomes_slow(
    pending: pd.DataFrame,
    bars: pd.DataFrame,
    cmp_arr: np.ndarray,
    combos: list,
    max_bars: int,
    timeout_result: str,
    tick_size: float,
    tick_value: float,
    comm: float,
    slip_ticks: float,
    sig_dt_col: str,
) -> pd.DataFrame:
    """Row-by-row fallback for limit-order entries (break_extreme / body_midpoint)."""
    all_records: List[Dict[str, Any]] = []
    for _, row in pending.iterrows():
        signal_dt   = row[sig_dt_col]
        direction   = int(row["direction"])
        timing_mode = str(row.get("timing_mode", "next_open"))

        sig_val        = _ts_to_utc_ns(signal_dt)
        signal_bar_idx = int(np.searchsorted(cmp_arr, sig_val, side="left"))
        if signal_bar_idx >= len(bars):
            continue

        limit_price       = float(row.get("limit_price", np.nan))
        fill_timeout_bars = int(row.get("fill_timeout_bars", 3))
        if np.isnan(limit_price):
            continue
        fill_idx, fill_dt = _fill_limit_order(
            limit_price, direction, bars, signal_bar_idx, fill_timeout_bars, timing_mode
        )
        if fill_idx is None:
            continue
        entry_price   = limit_price
        entry_bar_idx = fill_idx
        fill_bars_n   = fill_idx - signal_bar_idx
        entry_dt      = fill_dt
        fill_bar_high = float(bars.iloc[entry_bar_idx]["high"])
        fill_bar_low  = float(bars.iloc[entry_bar_idx]["low"])

        for tp_ticks, sl_ticks in combos:
            stop_price   = entry_price - direction * sl_ticks * tick_size
            target_price = entry_price + direction * tp_ticks * tick_size

            # The bar the limit fills on is live exposure: once filled, the
            # stop can be hit on that same bar. A limit entry placed after a
            # sweep routinely fills into a bar that still carries a large
            # adverse excursion, so the fill bar itself must be checked --
            # scanning only from entry_bar_idx + 1 silently ignored it and
            # inflated every limit-entry candidate. Conservative: assume the
            # adverse side resolves first within the fill bar.
            if (fill_bar_low <= stop_price if direction == 1
                    else fill_bar_high >= stop_price):
                result       = "loss"
                exit_price   = stop_price
                profit_ticks = (exit_price - entry_price) * direction / tick_size
                exit_dt      = entry_dt
            else:
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


# ---------------------------------------------------------------------------
# Public: simulate one outcome config on a set of pending entries
# ---------------------------------------------------------------------------

def simulate_atr_outcomes(
    pending: pd.DataFrame,
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Simulate ATR-based outcomes (target = entry +/- target_mult * ATR).

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
                limit_price, direction, bars, signal_bar_idx,
                fill_timeout_bars, timing_mode,
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

        # A limit entry is live exposure on the bar it fills on -- check that
        # bar's adverse extreme before scanning forward (mirrors the fixed
        # tick path). next_open enters at the bar open, so its whole bar is
        # already covered by the entry_bar_idx + 1 scan and needs no check.
        fill_bar_stopped = False
        if timing_mode != "next_open":
            fb = bars.iloc[entry_bar_idx]
            fill_bar_stopped = (
                float(fb["low"]) <= stop_price if direction == 1
                else float(fb["high"]) >= stop_price
            )

        # Resolve outcome
        if fill_bar_stopped:
            result       = "loss"
            exit_price   = stop_price
            profit_ticks = (exit_price - entry_price) * direction / tick_size
            exit_dt      = entry_dt
        else:
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

    Fast path (vectorized numpy):
        Used for ``next_open`` entries.  Builds a 2-D forward-scan window
        (n_signals x max_bars) with fancy indexing, then resolves all combos
        with array operations — no Python loops over bars.

    Slow path (row-by-row fallback):
        Used for limit-order entries (``break_extreme`` / ``body_midpoint``).
        Identical to the original implementation.
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

    bars    = _build_1m_lookup(bars_1m)
    cmp_arr = _dt_to_utc_ns(bars["dt"])
    n_bars  = len(bars)
    highs   = bars["high"].values.astype(np.float64)
    lows    = bars["low"].values.astype(np.float64)
    closes  = bars["close"].values.astype(np.float64)
    bar_dts = bars["dt"].values

    pending = pending.reset_index(drop=True)
    _sig_dt_col = "signal_dt" if "signal_dt" in pending.columns else "dt"
    combos = list(product(tp_list, sl_list))

    # Split next_open (fast vectorized) vs limit orders (slow row-by-row)
    if "timing_mode" in pending.columns:
        no_mask = (pending["timing_mode"] == "next_open").values
    else:
        no_mask = np.ones(len(pending), dtype=bool)

    fast_idx = np.where(no_mask)[0]
    slow_idx = np.where(~no_mask)[0]

    result_parts: List[pd.DataFrame] = []

    # ----------------------------------------------------------------
    # FAST PATH: next_open entries — fully vectorized
    # ----------------------------------------------------------------
    if len(fast_idx) > 0:
        fast_df = pending.iloc[fast_idx].reset_index(drop=True)

        # 1. Bar index for each signal (vectorized searchsorted)
        sig_ns  = _dt_to_utc_ns(fast_df[_sig_dt_col])
        b_idx   = np.searchsorted(cmp_arr, sig_ns, side="left")

        # Filter: drop signals past end of bar data or with NaN entry_price
        ep_raw = pd.to_numeric(fast_df.get("entry_price", pd.Series(dtype=float)), errors="coerce").values
        valid_mask = (b_idx < n_bars) & ~np.isnan(ep_raw)
        vi = np.where(valid_mask)[0]

        if len(vi) > 0:
            base_df = fast_df.iloc[vi].reset_index(drop=True)
            b       = b_idx[vi]
            ep      = ep_raw[vi]
            dirs    = base_df["direction"].values.astype(np.int64)

            # Entry timestamps — tz conversion done once in bulk
            entry_dt_col = "entry_time" if "entry_time" in base_df.columns else _sig_dt_col
            entry_times  = _tz_aware(base_df[entry_dt_col]).values  # numpy array of tz-aware ts

            # 2. Build forward windows (vectorized fancy indexing)
            fw_h, fw_l, fw_c, in_bounds, last_valid_col = _build_forward_windows(
                b, highs, lows, closes, n_bars, max_bars
            )
            scan_starts = b + 1

            # 3. Resolve each (tp, sl) combo
            for tp_ticks, sl_ticks in combos:
                tp_p = ep + dirs * (tp_ticks * tick_size)  # (n_valid,)
                sl_p = ep - dirs * (sl_ticks * tick_size)

                res_arr, pticks, pnet, exit_col = _resolve_outcomes_vectorized(
                    ep, dirs, tp_p, sl_p,
                    fw_h, fw_l, fw_c, in_bounds, last_valid_col,
                    max_bars, timeout_result, tick_size, tick_value, comm, slip_ticks,
                )

                # Exit timestamps (vectorized)
                exit_global = np.clip(scan_starts + exit_col, 0, n_bars - 1)
                exit_times  = _tz_aware(pd.Series(bar_dts[exit_global])).values

                # 4. Build result DataFrame — no Python loop
                df_combo = base_df.copy()
                df_combo["entry_time"]   = entry_times
                df_combo["exit_time"]    = exit_times
                df_combo["market_pos"]   = np.where(dirs == 1, "Long", "Short")
                df_combo["profit_net"]   = pnet
                df_combo["profit_ticks"] = pticks
                df_combo["entry_price"]  = ep
                df_combo["exit_price"]   = np.where(res_arr == "win", tp_p,
                                               np.where(res_arr == "loss", sl_p,
                                                   (fw_c[np.arange(len(vi)), last_valid_col]
                                                    if timeout_result in ("at_close", "neutral")
                                                    else sl_p)))
                df_combo["stop_price"]   = sl_p
                df_combo["target_price"] = tp_p
                df_combo["result"]       = res_arr
                df_combo["outcome_mode"] = f"ticks_{tp_ticks}_{sl_ticks}"
                df_combo["fill_bars"]    = 0
                df_combo["tp_ticks"]     = tp_ticks
                df_combo["sl_ticks"]     = sl_ticks

                result_parts.append(df_combo)

    # ----------------------------------------------------------------
    # SLOW PATH: limit-order entries (break_extreme / body_midpoint)
    # ----------------------------------------------------------------
    if len(slow_idx) > 0:
        slow_trades = _simulate_tick_outcomes_slow(
            pending.iloc[slow_idx],
            bars, cmp_arr, combos,
            max_bars, timeout_result, tick_size, tick_value, comm, slip_ticks,
            _sig_dt_col,
        )
        if not slow_trades.empty:
            result_parts.append(slow_trades)

    if not result_parts:
        return pd.DataFrame()
    return pd.concat(result_parts, ignore_index=True)


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
