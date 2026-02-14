# src/ta_foundation/analysis/apex_trailing_model.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Any

import pandas as pd


@dataclass(frozen=True)
class InstrumentSpec:
    tick_size: float
    tick_value: float  # $ per tick, per contract


@dataclass(frozen=True)
class ApexParams:
    starting_balance: float = 50_000.0
    trailing_drawdown: float = 2_500.0
    lock_profit: float = 2_600.0  # when peak reaches start + lock_profit, trail stops moving
    # If Apex rules change, you can generalize later.


def _parse_money(x: Any) -> float:
    """
    Parses NinjaTrader-style money strings:
      "($365.00)" -> -365.0
      "$255.00"   -> 255.0
      "0"         -> 0.0
    """
    if x is None:
        return 0.0
    s = str(x).strip()
    if s == "":
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").strip()
    try:
        v = float(s)
    except Exception:
        v = 0.0
    return -v if neg else v


def _pos_is_long(pos: Any) -> Optional[bool]:
    if pos is None:
        return None
    s = str(pos).strip().lower()
    if s == "long":
        return True
    if s == "short":
        return False
    return None


def _safe_col(df: pd.DataFrame, *names: str) -> Optional[str]:
    if df is None or df.empty:
        return None
    m = {c.lower(): c for c in df.columns}
    for n in names:
        k = n.lower()
        if k in m:
            return m[k]
    return None


def _bars_for_trade_window(
    bars_df: pd.DataFrame,
    entry_dt: pd.Timestamp,
    exit_dt: pd.Timestamp,
    pad_minutes: int = 0,
) -> pd.DataFrame:
    if bars_df is None or bars_df.empty:
        return pd.DataFrame()
    if "dt" not in bars_df.columns:
        return pd.DataFrame()

    start = entry_dt - timedelta(minutes=pad_minutes)
    end = exit_dt + timedelta(minutes=pad_minutes)
    w = bars_df[(bars_df["dt"] >= start) & (bars_df["dt"] <= end)].copy()
    if not w.empty:
        w = w.sort_values("dt").reset_index(drop=True)
    return w


def simulate_apex_trailing_for_run(
    *,
    trades_df: pd.DataFrame,
    minute_bars_df: pd.DataFrame,
    spec: InstrumentSpec,
    apex: ApexParams,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Returns:
      ledger_df: one row per trade with intratrade buffer/peak/trail stats
      summary: run-level survival summary

    Assumptions:
      - trades_df entry/exit times are tz-aware America/Denver (your ingest contract)
      - minute_bars_df dt is tz-aware America/Denver (your market ingest)
      - Profit column is realized PnL per trade in dollars
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(), {
            "message": "No trades.",
        }

    # Required columns from your _Trades.csv sample
    c_entry_time = _safe_col(trades_df, "Entry time", "entry_time")
    c_exit_time = _safe_col(trades_df, "Exit time", "exit_time")
    c_entry_price = _safe_col(trades_df, "Entry price", "entry_price")
    c_exit_price = _safe_col(trades_df, "Exit price", "exit_price")
    c_qty = _safe_col(trades_df, "Qty", "qty")
    c_pos = _safe_col(trades_df, "Market pos.", "Market pos", "market_pos")
    c_profit = _safe_col(trades_df, "Profit", "profit")
    c_trade_num = _safe_col(trades_df, "Trade number", "trade_number")

    missing = [n for n, c in [
        ("Entry time", c_entry_time),
        ("Exit time", c_exit_time),
        ("Entry price", c_entry_price),
        ("Qty", c_qty),
        ("Market pos.", c_pos),
        ("Profit", c_profit),
    ] if c is None]
    if missing:
        return pd.DataFrame(), {
            "message": f"Missing required trade columns: {', '.join(missing)}",
        }

    # Sort trades by entry time
    t = trades_df.copy()
    t = t.sort_values(c_entry_time).reset_index(drop=True)

    peak_equity = apex.starting_balance
    trail_locked = False
    locked_trail_level = (apex.starting_balance + apex.lock_profit) - apex.trailing_drawdown  # 50,100 by default

    def trail_level_for_peak(pk: float) -> float:
        nonlocal trail_locked
        if (not trail_locked) and (pk >= apex.starting_balance + apex.lock_profit):
            trail_locked = True
        if trail_locked:
            return locked_trail_level
        return pk - apex.trailing_drawdown

    # Helper for PnL from price move
    def pnl_dollars(price_diff_points: float, qty: float) -> float:
        ticks = price_diff_points / spec.tick_size
        return ticks * spec.tick_value * qty

    # Run state
    realized_equity = apex.starting_balance
    run_min_buffer = float("inf")
    run_min_buffer_trade = None

    # Rolling realized 60-min window (approx using trade exit times)
    # We'll compute later from realized equity series.

    rows: list[dict[str, Any]] = []

    # Build realized equity series at each trade exit
    realized_points: list[tuple[pd.Timestamp, float]] = []

    # Minute-level conservative equity samples (risk curve)
    # We record "equity at adverse" each minute while in a trade.
    equity_samples: list[tuple[pd.Timestamp, float]] = []

    for i, tr in t.iterrows():
        entry_dt = tr[c_entry_time]
        exit_dt = tr[c_exit_time]
        if pd.isna(entry_dt) or pd.isna(exit_dt):
            continue

        qty = float(tr[c_qty]) if not pd.isna(tr[c_qty]) else 0.0
        entry_px = float(tr[c_entry_price]) if not pd.isna(tr[c_entry_price]) else 0.0
        pos_long = _pos_is_long(tr[c_pos])
        realized_pnl = _parse_money(tr[c_profit])

        trade_num = int(tr[c_trade_num]) if (c_trade_num and not pd.isna(tr[c_trade_num])) else (i + 1)

        # Window of bars during trade
        bw = _bars_for_trade_window(minute_bars_df, entry_dt, exit_dt, pad_minutes=0)

        # Intratrade simulation (conservative intrabar ordering):
        # for each minute:
        #   1) assume favorable extreme happens first -> update peak_equity
        #   2) compute trail
        #   3) assume adverse extreme happens -> compute buffer
        trade_peak_equity = peak_equity
        trade_min_buffer = float("inf")
        trade_peak_unreal = 0.0
        trade_worst_unreal = 0.0

        if bw.empty or pos_long is None or qty == 0.0:
            # Fallback: no bars, cannot simulate intratrade
            # Use realized only (not ideal, but safe)
            trail_lvl = trail_level_for_peak(peak_equity)
            buffer_close = realized_equity - trail_lvl
            trade_min_buffer = buffer_close
        else:
            for _, bar in bw.iterrows():
                hi = float(bar["high"])
                lo = float(bar["low"])

                if pos_long:
                    favorable = pnl_dollars(hi - entry_px, qty)
                    adverse = pnl_dollars(lo - entry_px, qty)
                else:
                    # short: favorable is move down (entry - low), adverse is move up (entry - high)
                    favorable = pnl_dollars(entry_px - lo, qty)
                    adverse = pnl_dollars(entry_px - hi, qty)

                # 1) Favorable first -> potential new peak
                candidate_peak_equity = realized_equity + favorable
                if candidate_peak_equity > peak_equity:
                    peak_equity = candidate_peak_equity

                # Track trade-local extremes
                if favorable > trade_peak_unreal:
                    trade_peak_unreal = favorable
                if adverse < trade_worst_unreal:
                    trade_worst_unreal = adverse

                # 2) Trail based on current peak
                trail_lvl = trail_level_for_peak(peak_equity)

                # 3) Adverse second -> test buffer
                equity_at_adverse = realized_equity + adverse
                # Record minute-level risk equity for time-based drawdown analytics
                # (tz-aware dt from market bars)
                equity_samples.append((bar["dt"], float(equity_at_adverse)))
                buffer_now = equity_at_adverse - trail_lvl
                if buffer_now < trade_min_buffer:
                    trade_min_buffer = buffer_now

            trade_peak_equity = peak_equity

        # Update realized equity at close
        realized_equity += realized_pnl
        realized_points.append((exit_dt, realized_equity))

        # Buffer at close
        trail_lvl_close = trail_level_for_peak(peak_equity)
        buffer_close = realized_equity - trail_lvl_close

        if trade_min_buffer < run_min_buffer:
            run_min_buffer = trade_min_buffer
            run_min_buffer_trade = trade_num

        rows.append({
            "trade_number": trade_num,
            "entry_time": entry_dt,
            "exit_time": exit_dt,
            "qty": qty,
            "pos": "Long" if pos_long else "Short" if pos_long is False else "",
            "entry_price": entry_px,
            "realized_pnl": realized_pnl,
            "realized_equity_close": realized_equity,
            "peak_equity_after_trade": peak_equity,
            "trail_locked": trail_locked,
            "trail_level_close": trail_lvl_close,
            "buffer_close": buffer_close,
            "trade_peak_unreal": trade_peak_unreal,
            "trade_worst_unreal": trade_worst_unreal,
            "min_buffer_intratrade": trade_min_buffer,
        })

    ledger = pd.DataFrame(rows)
    if ledger.empty:
        return ledger, {"message": "No valid trades to simulate."}

    # -------------------------
    # Streaks (trade-level)
    # -------------------------
    pnl = ledger["realized_pnl"].fillna(0.0).values
    max_losing_trade_streak = 0
    cur = 0
    for v in pnl:
        if v < 0:
            cur += 1
            if cur > max_losing_trade_streak:
                max_losing_trade_streak = cur
        else:
            cur = 0

    # -------------------------
    # Streaks (day-level)
    # Use Denver-local exit date; exit_time is already tz-aware America/Denver per your ingest contract.
    # -------------------------
    daily = ledger[["exit_time", "realized_pnl"]].copy()
    daily["day"] = pd.to_datetime(daily["exit_time"]).dt.date
    daily_pnl = daily.groupby("day", as_index=False)["realized_pnl"].sum().sort_values("day")

    # -------------------------
    # Worst 60-minute INTRATRADE drawdown (correct metric for Apex)
    # Uses minute-level conservative equity samples (equity at adverse each minute).
    # -------------------------
    worst_60m_intratrade_drawdown = 0.0  # negative number = drawdown
    worst_60m_intratrade_start = None
    worst_60m_intratrade_trough_time = None
    worst_60m_intratrade_trough_equity = None

    if len(equity_samples) >= 2:
        eq_series = pd.DataFrame(equity_samples, columns=["dt", "equity"]).dropna()
        # Ensure dt is datetime64[ns, tz] and sorted
        eq_series["dt"] = pd.to_datetime(eq_series["dt"])
        eq_series = eq_series.sort_values("dt").reset_index(drop=True)

        dts = eq_series["dt"]
        eqs = eq_series["equity"]

        # Convert timestamps to int64 ns for robust window indexing (works with tz-aware)
        dts = eq_series["dt"]
        eqs = eq_series["equity"]

        # Convert tz-aware timestamps to int64 nanoseconds safely across pandas versions.
        # .astype("int64") works for datetime64[ns, tz] in current pandas.
        dt_ns = dts.astype("int64").to_numpy()
        eq_vals = eqs.to_numpy(dtype="float64")

        window_ns = int(pd.Timedelta(minutes=60).value)

        # Use numpy searchsorted on the dt_ns array
        for i in range(len(dt_ns)):
            start_ns = int(dt_ns[i])
            end_ns = start_ns + window_ns

            # last index within end_ns
            j = int(dt_ns.searchsorted(end_ns, side="right") - 1)
            if j <= i:
                continue

            start_eq = float(eq_vals[i])

            window_slice = eq_vals[i: j + 1]
            trough_idx_rel = int(window_slice.argmin())
            trough_idx = i + trough_idx_rel
            trough_eq = float(eq_vals[trough_idx])

            dd = trough_eq - start_eq
            if dd < worst_60m_intratrade_drawdown:
                worst_60m_intratrade_drawdown = dd
                worst_60m_intratrade_start = dts.iloc[i]
                worst_60m_intratrade_trough_time = dts.iloc[trough_idx]
                worst_60m_intratrade_trough_equity = trough_eq

    max_losing_day_streak = 0
    max_winning_day_streak = 0
    cur_loss = 0
    cur_win = 0
    for v in daily_pnl["realized_pnl"].values:
        if v < 0:
            cur_loss += 1
            cur_win = 0
            if cur_loss > max_losing_day_streak:
                max_losing_day_streak = cur_loss
        elif v > 0:
            cur_win += 1
            cur_loss = 0
            if cur_win > max_winning_day_streak:
                max_winning_day_streak = cur_win
        else:
            # flat day breaks both
            cur_loss = 0
            cur_win = 0

    # -------------------------
    # Worst 60-minute realized DRAWDOWN (corrected)
    # We want the deepest trough within the 60m window relative to equity at window start.
    # (Endpoint-to-endpoint can hide real risk.)
    # -------------------------
    realized_series = pd.DataFrame(realized_points, columns=["dt", "equity"]).sort_values("dt").reset_index(
        drop=True)

    worst_60m_drawdown = 0.0  # negative number = drawdown
    worst_60m_start = None
    worst_60m_trough_time = None
    worst_60m_trough_equity = None

    if len(realized_series) >= 2:
        dts = realized_series["dt"]
        eqs = realized_series["equity"]

        for i in range(len(realized_series)):
            start_dt = dts.iloc[i]
            start_eq = float(eqs.iloc[i])

            end_limit = start_dt + timedelta(minutes=60)

            # indices in window [i .. j]
            # (searchsorted requires monotonic dts; we have sorted)
            j = dts.searchsorted(end_limit, side="right") - 1
            if j <= i:
                continue

            window_eq = eqs.iloc[i: j + 1]
            trough_eq = float(window_eq.min())
            trough_idx = int(window_eq.idxmin())

            dd = trough_eq - start_eq  # negative if drawdown
            if dd < worst_60m_drawdown:
                worst_60m_drawdown = dd
                worst_60m_start = start_dt
                worst_60m_trough_time = dts.iloc[trough_idx]
                worst_60m_trough_equity = trough_eq

    # -------------------------
    # Pre-lock / post-lock info
    # -------------------------
    lock_reached = bool(ledger["trail_locked"].any())
    lock_trade = None
    if lock_reached:
        lock_trade = int(ledger.loc[ledger["trail_locked"] == True, "trade_number"].iloc[0])

    summary = {
        "starting_balance": apex.starting_balance,
        "trailing_drawdown": apex.trailing_drawdown,
        "lock_profit": apex.lock_profit,
        "locked_trail_level": (apex.starting_balance + apex.lock_profit) - apex.trailing_drawdown,
        "final_realized_equity": float(ledger["realized_equity_close"].iloc[-1]),
        "final_realized_pnl": float(ledger["realized_equity_close"].iloc[-1] - apex.starting_balance),

        "min_buffer_intratrade": float(run_min_buffer),
        "min_buffer_trade_number": int(run_min_buffer_trade) if run_min_buffer_trade is not None else None,

        # ✅ clarify what streak means
        "max_losing_trade_streak": int(max_losing_trade_streak),
        "max_losing_day_streak": int(max_losing_day_streak),
        "max_winning_day_streak": int(max_winning_day_streak),

        # ✅ corrected: worst intra-window drawdown, not endpoint change
        "worst_60m_realized_drawdown": float(worst_60m_drawdown),
        "worst_60m_start": worst_60m_start,
        "worst_60m_trough_time": worst_60m_trough_time,
        "worst_60m_trough_equity": worst_60m_trough_equity,
        "worst_60m_intratrade_drawdown": float(worst_60m_intratrade_drawdown),
        "worst_60m_intratrade_start": worst_60m_intratrade_start,
        "worst_60m_intratrade_trough_time": worst_60m_intratrade_trough_time,
        "worst_60m_intratrade_trough_equity": worst_60m_intratrade_trough_equity,

        "trail_lock_reached": lock_reached,
        "trail_lock_trade_number": lock_trade,
    }
    return ledger, summary
