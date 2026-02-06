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

    # Best single “prop survivability” metric we can compute from trade-boundary states:
    # minimum buffer observed at trade boundaries (after each trade close, and also at intratrade peak)
    # within that day.
    min_buffer: float

    trail_move_today: float  # hwm_close - hwm_open, clamped >= 0


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


def _buffer(equity: float, hwm: float, trailing_dd: float) -> float:
    # buffer = equity - (hwm - dd)
    return equity - (hwm - trailing_dd)


def _compute_trade_peaks(
    trades: pd.DataFrame,
    *,
    start_balance: float,
    trailing_dd: float,
    mode: str,  # "continuous" | "daily_reset"
) -> Dict[date, PropDaySnapshot]:
    """
    Prop trailing model using per-trade MFE (intraday unrealized peak) and Profit (realized close).

    What we can compute accurately from trade-boundary data:
      - HWM ratchet using equity_peak = equity_prev + MFE
      - equity_close using equity_prev + Profit
      - trail = HWM - trailing_dd
      - buffer at key boundary points
      - per-day min_buffer at those boundary points

    Note:
      This does NOT include intra-trade adverse excursion beyond the close (needs MAE in $ or bar data).
      For prop decisioning, min_buffer-at-boundaries is still highly actionable and consistent.
    """
    if trades is None or len(trades) == 0:
        return {}

    pcol = _safe_col(trades, ["Profit", "profit", "PnL", "pnl", "Net profit", "net_profit"])
    mfecol = _safe_col(trades, ["MFE", "mfe", "Avg MFE", "avg_mfe"])
    # Time ordering: prefer Exit time (realized), fallback Entry time.
    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
    if not tcol:
        tcol = _safe_col(trades, ["Entry time", "Entry Time", "entry_time"])

    if not (pcol and mfecol and tcol):
        return {}

    df = trades.copy()
    df["_ts"] = _ensure_dt_series(df[tcol])
    df["_profit"] = pd.to_numeric(df[pcol], errors="coerce")
    df["_mfe"] = pd.to_numeric(df[mfecol], errors="coerce")
    df = df.dropna(subset=["_ts", "_profit", "_mfe"]).sort_values("_ts")
    if len(df) == 0:
        return {}

    df["_day"] = df["_ts"].dt.date

    out: Dict[date, PropDaySnapshot] = {}

    equity = float(start_balance)
    hwm = float(start_balance)

    current_day: Optional[date] = None

    day_equity_open = equity
    day_hwm_open = hwm
    day_min_buffer = _buffer(equity, hwm, trailing_dd)

    def close_day(d: date, equity_close: float, hwm_close: float, equity_open: float, hwm_open: float, min_buf: float):
        trail_open = hwm_open - trailing_dd
        trail_close = hwm_close - trailing_dd
        out[d] = PropDaySnapshot(
            day=d,
            equity_open=equity_open,
            equity_close=equity_close,
            hwm_open=hwm_open,
            hwm_close=hwm_close,
            trail_open=trail_open,
            trail_close=trail_close,
            buffer_open=_buffer(equity_open, hwm_open, trailing_dd),
            buffer_close=_buffer(equity_close, hwm_close, trailing_dd),
            min_buffer=min_buf,
            trail_move_today=max(0.0, hwm_close - hwm_open),
        )

    for _, row in df.iterrows():
        d: date = row["_day"]

        if current_day is None:
            current_day = d
            if mode == "daily_reset":
                equity = float(start_balance)
                hwm = float(start_balance)
            day_equity_open = equity
            day_hwm_open = hwm
            day_min_buffer = _buffer(equity, hwm, trailing_dd)

        if d != current_day:
            close_day(current_day, equity, hwm, day_equity_open, day_hwm_open, day_min_buffer)

            current_day = d
            if mode == "daily_reset":
                equity = float(start_balance)
                hwm = float(start_balance)
            day_equity_open = equity
            day_hwm_open = hwm
            day_min_buffer = _buffer(equity, hwm, trailing_dd)

        profit = float(row["_profit"])
        mfe = float(row["_mfe"])

        # Intratrade peak equity (prop-firm accurate per your contract)
        equity_peak = equity + mfe

        # Buffer at peak depends on whether peak sets a new HWM.
        # If it sets a new HWM, buffer_at_peak becomes exactly trailing_dd (since trail = peak - dd).
        # If not, buffer_at_peak = equity_peak - (hwm - dd).
        buffer_at_peak_pre = _buffer(equity_peak, hwm, trailing_dd)

        # Update HWM if a new peak is made
        if equity_peak > hwm:
            hwm = equity_peak

        # After updating HWM, compute buffer_at_peak with updated HWM (should be dd if new high)
        buffer_at_peak = _buffer(equity_peak, hwm, trailing_dd)
        day_min_buffer = min(day_min_buffer, buffer_at_peak_pre, buffer_at_peak)

        # Realized close
        equity = equity + profit
        buffer_at_close = _buffer(equity, hwm, trailing_dd)
        day_min_buffer = min(day_min_buffer, buffer_at_close)

    if current_day is not None:
        close_day(current_day, equity, hwm, day_equity_open, day_hwm_open, day_min_buffer)

    return out


def compute_prop_trailing_states(
    trades: pd.DataFrame,
    *,
    start_balance: float,
    trailing_dd: float,
) -> Dict[str, Dict[date, PropDaySnapshot]]:
    """
    Returns:
      {
        "continuous": {date -> snapshot},
        "daily_reset": {date -> snapshot},
      }
    """
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
