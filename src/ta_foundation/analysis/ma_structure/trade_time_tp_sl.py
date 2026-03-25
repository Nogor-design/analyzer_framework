from __future__ import annotations

"""
trade_time_tp_sl.py
-------------------
Simulates TP/SL hit outcomes starting from each actual trade's entry bar.

This is fundamentally different from the structural segment TP/SL engine
(tp_sl_engine.py), which scores candidates using MA cross-segments drawn
from the full bar history.  This module scores candidates using the actual
trade entries in pkg.trades as the simulation start points.

The question answered here:
  "Given the trades this bot actually took, what TP/SL (in ATR units) would
   have produced the best outcomes — measured from the precise entry bar?"

Output
------
candidates : DataFrame
    One row per (group, tp_atr, sl_atr).  group is "all", "long", or "short".
    Columns: group, tp_atr, sl_atr, sample_n, n_decisive, n_tp_hit, n_sl_hit,
             win_rate, expectancy_atr, avg_bars_to_tp, avg_bars_to_sl,
             pct_neither.

per_trade : DataFrame
    One row per (trade, tp_atr, sl_atr).  Useful for diagnostics and
    per-trade reporting.
    Columns: trade_id, direction, entry_price, atr_at_entry, tp_atr, sl_atr,
             first_hit, bars_to_hit, within_window.
"""

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_col(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        if str(name).strip().lower() in lowered:
            return lowered[str(name).strip().lower()]
    return None


def _compute_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    close = pd.to_numeric(bars["close"], errors="coerce")
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _get_direction(row: pd.Series, market_pos_col: Optional[str]) -> int:
    """Return +1 (long), -1 (short), or 0 (unknown)."""
    if market_pos_col:
        val = str(row.get(market_pos_col) or "").strip().lower()
        if "long" in val:
            return 1
        if "short" in val:
            return -1
    ep = pd.to_numeric(pd.Series([row.get("entry_price", np.nan)]), errors="coerce").iloc[0]
    xp = pd.to_numeric(pd.Series([row.get("exit_price", np.nan)]), errors="coerce").iloc[0]
    if pd.notna(ep) and pd.notna(xp):
        return 1 if float(xp) >= float(ep) else -1
    return 0


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------

def score_trade_time_tp_sl(
    trades: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    tp_grid: list[float],
    sl_grid: list[float],
    atr_period: int = 14,
    max_bars_forward: int = 120,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Simulate TP/SL outcomes from each trade's entry bar forward through
    the market bars.

    Parameters
    ----------
    trades : DataFrame
        pkg.trades — must contain entry_time, entry_price, and ideally
        market_pos or exit_price for direction detection.
    bars : DataFrame
        Shared market bars (tz-aware DatetimeIndex or timestamp column).
    tp_grid : list[float]
        TP distances to test, expressed as ATR multiples.
    sl_grid : list[float]
        SL distances to test, expressed as ATR multiples.
    atr_period : int
        Rolling ATR period (default 14).
    max_bars_forward : int
        Maximum bars to look ahead from entry before recording "neither".

    Returns
    -------
    candidates : DataFrame  — aggregated per (group, tp_atr, sl_atr)
    per_trade  : DataFrame  — individual outcome per (trade_idx, tp_atr, sl_atr)
    """
    if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
        return pd.DataFrame(), pd.DataFrame()
    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(), pd.DataFrame()
    if not tp_grid or not sl_grid:
        return pd.DataFrame(), pd.DataFrame()

    # --- resolve column names (tolerant) ---
    entry_time_col = _find_col(trades, ["entry_time", "Entry time", "Entry Time", "entry_dt"])
    entry_price_col = _find_col(trades, ["entry_price", "Entry price", "Entry Price"])
    market_pos_col = _find_col(trades, ["market_pos", "Market pos.", "market position", "Market Position"])
    trade_id_col = _find_col(trades, ["trade_id", "Trade number", "trade number", "TradeNumber"])

    if not entry_time_col or not entry_price_col:
        return pd.DataFrame(), pd.DataFrame()

    # --- prepare bars ---
    bars_local = bars.copy().sort_index()
    if "timestamp" not in bars_local.columns:
        bars_local["timestamp"] = bars_local.index

    atr_series = _compute_atr(bars_local, period=atr_period)
    # Use the DatetimeIndex directly — avoids .values stripping tz info on
    # DatetimeTZDtype columns in older pandas versions.
    bar_ts_index = bars_local.index
    bars_tz = bar_ts_index.tz  # may be None if bars are somehow tz-naive
    high_arr = pd.to_numeric(bars_local["high"], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    low_arr = pd.to_numeric(bars_local["low"], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    atr_arr = atr_series.to_numpy(dtype=float, na_value=np.nan)
    n_bars = len(bars_local)

    per_trade_rows: list[dict] = []

    for t_idx, trade in trades.iterrows():
        entry_ts = pd.to_datetime(trade.get(entry_time_col), errors="coerce")
        entry_price_raw = pd.to_numeric(
            pd.Series([trade.get(entry_price_col)]), errors="coerce"
        ).iloc[0]

        if pd.isna(entry_ts) or pd.isna(entry_price_raw):
            continue
        if getattr(entry_ts, "tzinfo", None) is None:
            continue

        # Normalise entry_ts to bars timezone so searchsorted comparisons are valid
        if bars_tz is not None:
            try:
                entry_ts = entry_ts.tz_convert(bars_tz)
            except Exception:
                continue

        direction = _get_direction(trade, market_pos_col)
        if direction == 0:
            continue

        entry_price = float(entry_price_raw)
        trade_id = trade.get(trade_id_col) if trade_id_col else t_idx

        # Locate the entry bar (last bar at or before entry_ts)
        pos = bar_ts_index.searchsorted(entry_ts, side="right") - 1
        if pos < 0 or pos >= n_bars:
            continue

        atr_val = atr_arr[pos]
        if np.isnan(atr_val) or atr_val <= 0.0:
            continue
        atr = atr_val

        # Forward window starts one bar after entry (don't use entry bar itself)
        start_bar = pos + 1
        end_bar = min(start_bar + max_bars_forward, n_bars)
        if start_bar >= end_bar:
            continue

        fwd_high = high_arr[start_bar:end_bar]
        fwd_low = low_arr[start_bar:end_bar]
        fwd_len = end_bar - start_bar

        for tp_mult in tp_grid:
            tp_dist = float(tp_mult) * atr
            for sl_mult in sl_grid:
                sl_dist = float(sl_mult) * atr

                if direction > 0:
                    tp_price = entry_price + tp_dist
                    sl_price = entry_price - sl_dist
                else:
                    tp_price = entry_price - tp_dist
                    sl_price = entry_price + sl_dist

                first_hit = "neither"
                bars_to_hit = fwd_len

                for b in range(fwd_len):
                    h = fwd_high[b]
                    lo = fwd_low[b]
                    if np.isnan(h) or np.isnan(lo):
                        continue

                    if direction > 0:
                        hit_tp = h >= tp_price
                        hit_sl = lo <= sl_price
                    else:
                        hit_tp = lo <= tp_price
                        hit_sl = h >= sl_price

                    if hit_tp and hit_sl:
                        # Both touched same bar — assume TP first (conservative;
                        # a future refinement can use tick data for ordering).
                        first_hit = "tp"
                        bars_to_hit = b + 1
                        break
                    elif hit_tp:
                        first_hit = "tp"
                        bars_to_hit = b + 1
                        break
                    elif hit_sl:
                        first_hit = "sl"
                        bars_to_hit = b + 1
                        break

                per_trade_rows.append({
                    "trade_id": trade_id,
                    "direction": direction,
                    "entry_price": entry_price,
                    "atr_at_entry": atr,
                    "tp_atr": float(tp_mult),
                    "sl_atr": float(sl_mult),
                    "first_hit": first_hit,
                    "bars_to_hit": int(bars_to_hit),
                    "within_window": first_hit != "neither",
                })

    per_trade = pd.DataFrame(per_trade_rows)
    if per_trade.empty:
        return pd.DataFrame(), per_trade

    # --- aggregate into candidates ---
    groups: list[tuple[str, pd.DataFrame]] = [("all", per_trade)]
    long_df = per_trade[per_trade["direction"] == 1]
    short_df = per_trade[per_trade["direction"] == -1]
    if not long_df.empty:
        groups.append(("long", long_df))
    if not short_df.empty:
        groups.append(("short", short_df))

    cand_rows: list[dict] = []
    for group_label, gdf in groups:
        for (tp_mult, sl_mult), cell in gdf.groupby(["tp_atr", "sl_atr"], sort=True):
            decisive = cell[cell["first_hit"] != "neither"]
            n_total = len(cell)
            n_decisive = len(decisive)
            if n_decisive == 0:
                continue

            n_tp = int((decisive["first_hit"] == "tp").sum())
            n_sl = int((decisive["first_hit"] == "sl").sum())
            win_rate = n_tp / n_decisive
            expectancy = win_rate * float(tp_mult) - (1.0 - win_rate) * float(sl_mult)

            tp_times = decisive.loc[decisive["first_hit"] == "tp", "bars_to_hit"]
            sl_times = decisive.loc[decisive["first_hit"] == "sl", "bars_to_hit"]

            cand_rows.append({
                "group": group_label,
                "tp_atr": float(tp_mult),
                "sl_atr": float(sl_mult),
                "sample_n": n_total,
                "n_decisive": n_decisive,
                "n_tp_hit": n_tp,
                "n_sl_hit": n_sl,
                "win_rate": float(win_rate),
                "expectancy_atr": float(expectancy),
                "avg_bars_to_tp": float(tp_times.mean()) if len(tp_times) > 0 else np.nan,
                "avg_bars_to_sl": float(sl_times.mean()) if len(sl_times) > 0 else np.nan,
                "pct_neither": float((cell["first_hit"] == "neither").sum()) / n_total,
            })

    if not cand_rows:
        return pd.DataFrame(), per_trade

    candidates = pd.DataFrame(cand_rows).sort_values(
        ["group", "expectancy_atr", "win_rate"],
        ascending=[True, False, False],
    ).reset_index(drop=True)

    return candidates, per_trade
