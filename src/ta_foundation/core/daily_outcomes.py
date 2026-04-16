from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, Optional

import pandas as pd


@dataclass(frozen=True)
class DailyOutcome:
    """Canonical per-day outcome for a run."""
    day: date
    status: str  # "WIN" | "LOSS" | "NO_TRADE" | "FLAT"
    net_profit: float
    trades: int


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    """
    Find a column by tolerant matching.
    We normalize by lower-casing and removing non-alnum.
    """
    if df is None or df.empty:
        return None

    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower().strip() if ch.isalnum())

    cols = {norm(c): c for c in df.columns}
    for cand in candidates:
        key = norm(cand)
        if key in cols:
            return cols[key]
    return None


def _as_midnight_local_date(series: pd.Series) -> pd.Series:
    """
    Converts a date-like column to Python date (not datetime).
    Assumes caller already localized datetimes to America/Denver per contracts.
    """
    s = pd.to_datetime(series, errors="coerce")
    # If tz-aware, keep local date; if naive, just take date.
    return s.dt.date


def derive_daily_outcomes_for_package(pkg: Any) -> Dict[str, Any]:
    """
    Derive daily outcomes for a single AnalysisPackage.

    Output format (JSON-safe, stable):
      {
        "by_date": {
           "YYYY-MM-DD": {"status": "...", "net_profit": 123.0, "trades": 4}
        },
        "source": "analysis_by_day" | "trades"
      }

    Preferred input: pkg.analysis_by_day (pd.DataFrame)
    Fallback input: pkg.trades (pd.DataFrame)

    This function does NOT mutate pkg; pipeline should assign into
    pkg.metadata["derived"]["daily_outcomes"].
    """
    # --- Try Analysis-by-Day first ---
    daily_df: Optional[pd.DataFrame] = None
    daily_source = "analysis_by_day"
    for attr_name, source_name in (
        ("analysis_by_day", "analysis_by_day"),
        ("daily", "daily"),
        ("daily_analysis", "daily"),
        ("analysis_daily", "daily"),
    ):
        candidate = getattr(pkg, attr_name, None)
        if isinstance(candidate, pd.DataFrame) and not candidate.empty:
            daily_df = candidate
            daily_source = source_name
            break

    if isinstance(daily_df, pd.DataFrame) and not daily_df.empty:
        day_col = _find_col(daily_df, ["Period", "Date", "Day", "date"])
        np_col = _find_col(daily_df, ["Net profit", "Net Profit", "NetProfit", "net_profit"])
        trades_col = _find_col(
            daily_df,
            ["Total trades", "Trades", "Total Trades", "# Trades", "trade_count"],
        )

        if day_col and np_col:
            df = daily_df.copy()

            days = _as_midnight_local_date(df[day_col])
            net_profit = pd.to_numeric(df[np_col], errors="coerce").fillna(0.0)

            if trades_col:
                trades = pd.to_numeric(df[trades_col], errors="coerce").fillna(0).astype(int)
            else:
                # If no trades column, infer trades>0 if net_profit != 0 (best-effort).
                trades = (net_profit != 0.0).astype(int)

            out: Dict[str, Any] = {"by_date": {}, "source": daily_source}
            for d, npv, tr in zip(days, net_profit, trades):
                if pd.isna(d):
                    continue
                status = _status_from_net_profit_and_trades(float(npv), int(tr))
                out["by_date"][d.isoformat()] = {
                    "status": status,
                    "net_profit": float(npv),
                    "trades": int(tr),
                }
            return out

    # --- Fallback: aggregate trades by exit date ---
    trades_df: Optional[pd.DataFrame] = getattr(pkg, "trades", None)
    if not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return {"by_date": {}, "source": "none"}

    exit_col = _find_col(trades_df, ["Exit time", "ExitTime", "Exit date", "ExitDate", "Exit"])
    pnl_col = _find_col(trades_df, ["Profit", "P&L", "PnL", "Profit (Currency)", "ProfitCurrency"])

    if not exit_col or not pnl_col:
        # Can't compute anything reliable.
        return {"by_date": {}, "source": "trades"}

    df = trades_df.copy()
    exit_dt = pd.to_datetime(df[exit_col], errors="coerce")
    exit_day = exit_dt.dt.date

    pnl = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)

    g = pd.DataFrame({"day": exit_day, "pnl": pnl})
    g = g.dropna(subset=["day"])

    grouped = g.groupby("day", sort=True).agg(net_profit=("pnl", "sum"), trades=("pnl", "size")).reset_index()

    out: Dict[str, Any] = {"by_date": {}, "source": "trades"}
    for _, row in grouped.iterrows():
        d: date = row["day"]
        npv = float(row["net_profit"])
        tr = int(row["trades"])
        status = _status_from_net_profit_and_trades(npv, tr)
        out["by_date"][d.isoformat()] = {"status": status, "net_profit": npv, "trades": tr}

    return out


def _status_from_net_profit_and_trades(net_profit: float, trades: int) -> str:
    """
    Status classification:
      - NO_TRADE: trades == 0
      - WIN: net_profit > 0
      - LOSS: net_profit < 0
      - FLAT: net_profit == 0 and trades > 0
    """
    if trades <= 0:
        return "NO_TRADE"
    if net_profit > 0:
        return "WIN"
    if net_profit < 0:
        return "LOSS"
    return "FLAT"
