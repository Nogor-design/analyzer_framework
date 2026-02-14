from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple

import pandas as pd


@dataclass(frozen=True)
class DailyMatrix:
    """
    Aligned per-day performance across runs.

    dates:
      tz-aware timestamps normalized to midnight America/Denver (per ingest contract).
    pnl:
      DataFrame indexed by dates, columns=run_id, values=float net pnl, NaN for no data day.
    traded:
      DataFrame indexed by dates, columns=run_id, values=bool (True if traded day).
    cum:
      DataFrame cumulative sum of pnl (NaNs treated as 0 for cum calculation).
    combined_pnl:
      Series sum across runs per day (NaNs treated as 0).
    combined_cum:
      Series cumulative sum of combined_pnl.
    active_count:
      Series number of bots traded per day.
    """
    dates: pd.DatetimeIndex
    pnl: pd.DataFrame
    traded: pd.DataFrame
    cum: pd.DataFrame
    combined_pnl: pd.Series
    combined_cum: pd.Series
    active_count: pd.Series


def _norm_col(c: str) -> str:
    c = c.strip().lower()
    out = []
    for ch in c:
        out.append(ch if ch.isalnum() else "_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _find_col(cols: List[str], *candidates: str) -> Optional[str]:
    norm_map = {_norm_col(c): c for c in cols}
    for cand in candidates:
        key = _norm_col(cand)
        if key in norm_map:
            return norm_map[key]
    return None


def _first_existing_attr(pkg: object, names: List[str]):
    for n in names:
        v = getattr(pkg, n, None)
        if v is not None:
            return v
    return None


def _extract_daily_df(pkg: object) -> Optional[pd.DataFrame]:
    """
    Extract the daily analysis dataframe from an AnalysisPackage in a tolerant way.
    Expected canonical columns typically include: Period/Date, Net profit, Trades, Cum. net profit.
    """
    df = _first_existing_attr(pkg, ["daily", "daily_analysis", "analysis_daily", "analysis_by_day"])
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame) or len(df) == 0:
        return None
    return df


def _extract_trades_df(pkg: object) -> Optional[pd.DataFrame]:
    df = _first_existing_attr(pkg, ["trades", "trades_df"])
    if df is None:
        return None
    if not isinstance(df, pd.DataFrame) or len(df) == 0:
        return None
    return df


def _canonicalize_daily(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    cols = list(df.columns)

    date_col = _find_col(cols, "date", "period")
    pnl_col = _find_col(cols, "net_profit", "net profit", "netpnl", "pnl", "profit")
    trades_col = _find_col(cols, "trades", "total trades", "number of trades")
    cum_col = _find_col(cols, "cum_net_profit", "cum. net profit", "cum net profit", "cumulative net profit")

    if date_col is None or pnl_col is None:
        return None

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col])

    # Ingest contract says tz-aware America/Denver already.
    # If a caller violated the contract, we do not “guess” a timezone here.
    out["net_profit"] = pd.to_numeric(df[pnl_col], errors="coerce")

    if trades_col is not None:
        out["trades"] = pd.to_numeric(df[trades_col], errors="coerce").fillna(0).astype(int)

    if cum_col is not None:
        out["cum_net_profit"] = pd.to_numeric(df[cum_col], errors="coerce")

    out = out.sort_values("date").reset_index(drop=True)

    # Ensure one row per date (defensive)
    if "trades" in out.columns:
        out = out.groupby("date", as_index=False).agg(net_profit=("net_profit", "sum"), trades=("trades", "sum"))
    else:
        out = out.groupby("date", as_index=False).agg(net_profit=("net_profit", "sum"))

    return out.sort_values("date").reset_index(drop=True)


def _fallback_daily_from_trades(trades: pd.DataFrame) -> Optional[pd.DataFrame]:
    cols = list(trades.columns)
    exit_col = _find_col(cols, "exit_time", "exit time", "time exit", "exit")
    pnl_col = _find_col(cols, "profit", "pnl", "net_profit", "net profit")

    if exit_col is None or pnl_col is None:
        return None

    df = trades.copy()
    df["exit_time"] = pd.to_datetime(df[exit_col])
    df["profit"] = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)

    # NinjaTrader local time already localized upstream; normalize to midnight (tz-aware)
    day = df["exit_time"].dt.normalize()
    g = df.groupby(day, dropna=False)["profit"].agg(["sum", "count"]).reset_index()
    g.columns = ["date", "net_profit", "trades"]
    return g.sort_values("date").reset_index(drop=True)


def build_daily_matrix(packages: Dict[str, object]) -> DailyMatrix:
    per_run: Dict[str, pd.DataFrame] = {}

    for run_id, pkg in packages.items():
        daily_raw = _extract_daily_df(pkg)
        daily = _canonicalize_daily(daily_raw) if daily_raw is not None else None

        if daily is None:
            trades = _extract_trades_df(pkg)
            if trades is not None:
                daily = _fallback_daily_from_trades(trades)

        if daily is None or len(daily) == 0:
            continue

        per_run[run_id] = daily

    if not per_run:
        dates = pd.DatetimeIndex([])
        empty = pd.DataFrame(index=dates)
        return DailyMatrix(
            dates=dates,
            pnl=empty,
            traded=empty.astype(bool),
            cum=empty,
            combined_pnl=pd.Series(dtype=float),
            combined_cum=pd.Series(dtype=float),
            active_count=pd.Series(dtype=int),
        )

    all_dates = pd.DatetimeIndex(sorted(set().union(*[set(df["date"]) for df in per_run.values()])))
    pnl = pd.DataFrame(index=all_dates)
    traded = pd.DataFrame(index=all_dates)

    for run_id, df in per_run.items():
        s = df.set_index("date")["net_profit"].reindex(all_dates)
        pnl[run_id] = s

        if "trades" in df.columns:
            t = df.set_index("date")["trades"].reindex(all_dates).fillna(0).astype(int)
            traded[run_id] = t > 0
        else:
            # If we have a net_profit row for the date, treat it as traded
            traded[run_id] = s.notna()

    cum = pnl.fillna(0.0).cumsum()
    combined_pnl = pnl.fillna(0.0).sum(axis=1)
    combined_cum = combined_pnl.cumsum()
    active_count = traded.sum(axis=1).astype(int)

    return DailyMatrix(
        dates=all_dates,
        pnl=pnl,
        traded=traded,
        cum=cum,
        combined_pnl=combined_pnl,
        combined_cum=combined_cum,
        active_count=active_count,
    )
