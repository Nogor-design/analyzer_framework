from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MaxDrawdownRecovery:
    run_id: str
    max_drawdown: float  # positive number (e.g., 1234.56)
    peak_time: pd.Timestamp
    trough_time: pd.Timestamp
    recovery_time: Optional[pd.Timestamp]  # first time equity >= prior peak
    recovery_duration: Optional[pd.Timedelta]  # recovery_time - trough_time
    recovered: bool


def _ensure_tz_aware(series: pd.Series, tz: str = "America/Denver") -> pd.Series:
    """
    Enforce tz-aware index for time series.
    Contract: all timestamps are localized on ingest to America/Denver.
    This function is a guardrail if upstream data ever arrives naive.
    """
    idx = series.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("Equity series index must be a DatetimeIndex.")
    if idx.tz is None:
        series = series.copy()
        series.index = series.index.tz_localize(tz)
    return series


def get_equity_series_from_package(pkg) -> Optional[pd.Series]:
    """
    Preferred: pkg.daily['date'] + pkg.daily['cum_net_profit'].
    Fallback: pkg.trades['exit_time'] cumulative sum of pkg.trades['profit'].

    Returns
    -------
    pd.Series indexed by tz-aware timestamps with equity values (float).
    """
    # Daily preferred (stability)
    daily = getattr(pkg, "daily", None)
    if daily is not None and isinstance(daily, pd.DataFrame) and not daily.empty:
        # tolerate column naming variations by normalizing keys
        colmap = {c.lower().strip(): c for c in daily.columns}
        date_col = colmap.get("date") or colmap.get("period")
        equity_col = colmap.get("cum_net_profit") or colmap.get("cum. net profit") or colmap.get("cum net profit")
        if date_col and equity_col:
            df = daily[[date_col, equity_col]].dropna()
            if not df.empty:
                s = pd.Series(df[equity_col].astype(float).values, index=pd.to_datetime(df[date_col]))
                s = s.sort_index()
                s = _ensure_tz_aware(s)
                # De-dup any repeated timestamps by taking last
                s = s[~s.index.duplicated(keep="last")]
                return s

    # Trades fallback
    trades = getattr(pkg, "trades", None)
    if trades is not None and isinstance(trades, pd.DataFrame) and not trades.empty:
        colmap = {c.lower().strip(): c for c in trades.columns}
        t_col = colmap.get("exit_time") or colmap.get("exit time") or colmap.get("time")  # be conservative
        p_col = colmap.get("profit") or colmap.get("pnl") or colmap.get("net_profit") or colmap.get("net profit")
        if t_col and p_col:
            df = trades[[t_col, p_col]].dropna()
            if not df.empty:
                times = pd.to_datetime(df[t_col])
                profits = df[p_col].astype(float).values
                s = pd.Series(np.cumsum(profits), index=times).sort_index()
                s = _ensure_tz_aware(s)
                s = s[~s.index.duplicated(keep="last")]
                return s

    return None


def compute_drawdown_curve(equity: pd.Series) -> pd.DataFrame:
    """
    Compute drawdown curve:
      - peak: running max of equity
      - drawdown: equity - peak (<= 0)
      - drawdown_pct: drawdown / peak (NaN when peak == 0)

    Returns a DataFrame indexed like equity.
    """
    equity = equity.astype(float).copy()
    equity = _ensure_tz_aware(equity)

    peak = equity.cummax()
    dd = equity - peak

    # drawdown % can be noisy around 0; keep as optional diagnostic
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.where(peak.values != 0.0, dd.values / peak.values, np.nan)

    out = pd.DataFrame(
        {
            "equity": equity.values,
            "peak": peak.values,
            "drawdown": dd.values,
            "drawdown_pct": dd_pct,
        },
        index=equity.index,
    )
    return out


def max_drawdown_and_recovery(run_id: str, dd: pd.DataFrame) -> Optional[MaxDrawdownRecovery]:
    """
    Identify:
      - max drawdown trough (most negative drawdown)
      - peak time corresponding to the peak prior to trough
      - recovery time: first index AFTER trough where equity >= prior peak
      - recovery duration: recovery_time - trough_time
    """
    if dd is None or dd.empty:
        return None

    # If series never draws down (monotonic), drawdown min is 0 -> treat as no drawdown but still define peak/trough.
    trough_idx = dd["drawdown"].idxmin()
    trough_row = dd.loc[trough_idx]

    # Find the peak value just before/at trough (running peak at trough time)
    peak_val_at_trough = float(trough_row["peak"])

    # Peak time: last time up to trough where equity == that peak
    pre = dd.loc[:trough_idx]
    peak_times = pre.index[pre["equity"] == peak_val_at_trough]
    peak_time = peak_times[-1] if len(peak_times) else pre.index[0]

    max_dd = float(-trough_row["drawdown"])  # positive number

    # Recovery: first time after trough where equity >= peak_val_at_trough
    post = dd.loc[trough_idx:]
    rec_candidates = post.index[post["equity"] >= peak_val_at_trough]
    recovery_time = None
    if len(rec_candidates) > 0:
        # If the first candidate is the trough_idx itself and trough is at/above peak (rare), take it anyway.
        recovery_time = rec_candidates[0]

    recovered = recovery_time is not None
    recovery_duration = (recovery_time - trough_idx) if recovered else None

    return MaxDrawdownRecovery(
        run_id=run_id,
        max_drawdown=max_dd,
        peak_time=pd.Timestamp(peak_time),
        trough_time=pd.Timestamp(trough_idx),
        recovery_time=pd.Timestamp(recovery_time) if recovered else None,
        recovery_duration=recovery_duration,
        recovered=recovered,
    )
