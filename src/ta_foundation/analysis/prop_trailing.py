from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class PropDaySnapshot:
    day: date

    equity_open: float
    equity_close: float

    hwm_open: float
    hwm_close: float

    trail_open: float
    trail_close: float

    buffer_open: float
    buffer_close: float

    # Blended “worst case” buffer across all modeled kill-zones
    min_buffer: float

    # Pure liquidation-risk metric (intratrade trough only, MAE-based)
    worst_trough_buffer: float

    # Trail movement during that day (ratchet amount)
    trail_move_today: float  # trail_close - trail_open, clamped >= 0


def _safe_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = list(df.columns)
    low = {c.lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
    for c in candidates:
        k = c.lower()
        if k in low:
            return low[k]
    return None


def _ensure_dt_series(s: pd.Series) -> pd.Series:
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s, errors="coerce")
    return s


def _buffer(equity: float, trail: float) -> float:
    return float(equity - trail)


def _compute_trade_peaks(
    trades: pd.DataFrame,
    *,
    start_balance: float,
    trailing_dd: float,
    mode: str,  # "continuous" | "daily_reset"
) -> Dict[date, PropDaySnapshot]:
    """
    Contract:
      - trail starts at: start_balance - trailing_dd
      - hwm starts at: start_balance
      - per trade, peak equity = equity_prev + MFE; if peak > hwm -> ratchet hwm, trail=hwm-dd
      - trough equity = equity_prev + MAE; this is liquidation-risk buffer vs current trail (pre-ratchet)
      - realized close equity = equity_prev + Profit
      - buffer = equity - trail
    """
    if trades is None or len(trades) == 0:
        return {}

    pcol = _safe_col(trades, ["Profit", "profit", "PnL", "pnl", "Net profit", "net_profit"])
    mfecol = _safe_col(trades, ["MFE", "mfe"])
    maecol = _safe_col(trades, ["MAE", "mae"])
    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
    if not tcol:
        tcol = _safe_col(trades, ["Entry time", "Entry Time", "entry_time"])

    if not (pcol and mfecol and maecol and tcol):
        return {}

    df = trades.copy()
    df["_ts"] = _ensure_dt_series(df[tcol])
    df["_profit"] = pd.to_numeric(df[pcol], errors="coerce")
    df["_mfe"] = pd.to_numeric(df[mfecol], errors="coerce")
    df["_mae"] = pd.to_numeric(df[maecol], errors="coerce")
    df = df.dropna(subset=["_ts", "_profit", "_mfe", "_mae"]).sort_values("_ts")
    if len(df) == 0:
        return {}

    df["_day"] = df["_ts"].dt.date

    out: Dict[date, PropDaySnapshot] = {}

    def reset_account_state():
        equity0 = float(start_balance)
        hwm0 = float(start_balance)
        trail0 = float(start_balance - trailing_dd)
        return equity0, hwm0, trail0

    equity, hwm, trail = reset_account_state()

    current_day: Optional[date] = None
    day_equity_open = equity
    day_hwm_open = hwm
    day_trail_open = trail

    # initialize mins at open state
    day_min_buffer = _buffer(equity, trail)
    day_worst_trough = _buffer(equity, trail)  # if no trades, equals open buffer

    def close_day(d: date):
        out[d] = PropDaySnapshot(
            day=d,
            equity_open=day_equity_open,
            equity_close=equity,
            hwm_open=day_hwm_open,
            hwm_close=hwm,
            trail_open=day_trail_open,
            trail_close=trail,
            buffer_open=_buffer(day_equity_open, day_trail_open),
            buffer_close=_buffer(equity, trail),
            min_buffer=float(day_min_buffer),
            worst_trough_buffer=float(day_worst_trough),
            trail_move_today=max(0.0, float(trail - day_trail_open)),
        )

    for _, row in df.iterrows():
        d: date = row["_day"]

        if current_day is None:
            current_day = d
            if mode == "daily_reset":
                equity, hwm, trail = reset_account_state()
            day_equity_open = equity
            day_hwm_open = hwm
            day_trail_open = trail
            day_min_buffer = _buffer(equity, trail)
            day_worst_trough = _buffer(equity, trail)

        if d != current_day:
            close_day(current_day)
            current_day = d
            if mode == "daily_reset":
                equity, hwm, trail = reset_account_state()
            day_equity_open = equity
            day_hwm_open = hwm
            day_trail_open = trail
            day_min_buffer = _buffer(equity, trail)
            day_worst_trough = _buffer(equity, trail)

        profit = float(row["_profit"])
        mfe = float(row["_mfe"])
        mae = float(row["_mae"])

        # Trough buffer (liquidation risk), conservative: uses pre-ratchet trail
        # IMPORTANT: In NinjaTrader exports MAE is often a POSITIVE magnitude (e.g. 350 = went against you $350).
        # Therefore adverse excursion must be SUBTRACTED from equity.
        adverse = abs(mae)
        equity_trough = equity - adverse
        trough_buf = _buffer(equity_trough, trail)

        day_worst_trough = min(day_worst_trough, trough_buf)
        day_min_buffer = min(day_min_buffer, trough_buf)

        # Peak pre-ratchet
        equity_peak = equity + mfe
        day_min_buffer = min(day_min_buffer, _buffer(equity_peak, trail))

        # Ratchet (only on new HWM)
        if equity_peak > hwm:
            hwm = equity_peak
            trail = hwm - trailing_dd

        # Peak post-ratchet
        day_min_buffer = min(day_min_buffer, _buffer(equity_peak, trail))

        # Realized close
        equity = equity + profit
        day_min_buffer = min(day_min_buffer, _buffer(equity, trail))

    if current_day is not None:
        close_day(current_day)

    return out


def compute_prop_trailing_states(
    trades: pd.DataFrame,
    *,
    start_balance: float,
    trailing_dd: float,
) -> Dict[str, Dict[date, PropDaySnapshot]]:
    return {
        "continuous": _compute_trade_peaks(
            trades,
            start_balance=start_balance,
            trailing_dd=trailing_dd,
            mode="continuous",
        ),
        "daily_reset": _compute_trade_peaks(
            trades,
            start_balance=start_balance,
            trailing_dd=trailing_dd,
            mode="daily_reset",
        ),
    }
