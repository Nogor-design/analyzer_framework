from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from ta_foundation.core.model import AnalysisPackage


@dataclass(frozen=True)
class SessionWindow:
    label: str
    start: time  # inclusive
    end: time    # exclusive


DEFAULT_SESSION_WINDOWS: Tuple[SessionWindow, ...] = (
    SessionWindow("London", time(0, 0), time(4, 0)),
    SessionWindow("NY Pre→Early", time(4, 0), time(10, 30)),
    SessionWindow("NY Late", time(10, 30), time(16, 0)),
    SessionWindow("Asia", time(16, 0), time(23, 59, 59)),
)


def _safe_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    cols = list(df.columns)
    low = {c.lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
    for c in candidates:
        c2 = c.lower()
        if c2 in low:
            return low[c2]
    return None


def _parse_time_hhmm(s: str) -> time:
    hh, mm = (s or "").strip().split(":")
    return time(int(hh), int(mm))


def parse_session_windows(options: Dict[str, Any]) -> Tuple[SessionWindow, ...]:
    wins = options.get("session_windows")
    if not wins:
        return DEFAULT_SESSION_WINDOWS

    out: List[SessionWindow] = []
    for w in wins:
        try:
            out.append(
                SessionWindow(
                    label=str(w["label"]),
                    start=_parse_time_hhmm(str(w["start"])),
                    end=_parse_time_hhmm(str(w["end"])),
                )
            )
        except Exception:
            continue
    return tuple(out) if out else DEFAULT_SESSION_WINDOWS


def _ensure_dt_series(s: pd.Series) -> pd.Series:
    # Contract: tz-aware America/Denver on ingest. Defensive conversion anyway.
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s, errors="coerce")
    return s


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def infer_session_from_trades(
    trades: pd.DataFrame,
    windows: Iterable[SessionWindow],
    *,
    fallback: str = "Unclassified",
    restrict_to_date: Optional[date] = None,
) -> str:
    if trades is None or len(trades) == 0:
        return fallback

    col = _safe_col(trades, ["Entry time", "Entry Time", "entry_time"])
    if not col:
        return fallback

    s = _ensure_dt_series(trades[col]).dropna()
    if restrict_to_date is not None:
        try:
            s = s[s.dt.date == restrict_to_date]
        except Exception:
            s = s.iloc[0:0]

    if len(s) == 0:
        return fallback

    counts = {w.label: 0 for w in windows}

    def in_window(tt: time, w: SessionWindow) -> bool:
        if w.start <= w.end:
            return (tt >= w.start) and (tt < w.end)
        return (tt >= w.start) or (tt < w.end)

    for ts in s:
        try:
            tt = ts.timetz().replace(tzinfo=None)
        except Exception:
            continue
        for w in windows:
            if in_window(tt, w):
                counts[w.label] += 1
                break

    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else fallback


def infer_market_root(trades: pd.DataFrame, *, fallback: str = "Unknown") -> str:
    if trades is None or len(trades) == 0:
        return fallback
    col = _safe_col(trades, ["Instrument"])
    if not col:
        return fallback
    try:
        v = trades[col].dropna().astype(str).iloc[0]
    except Exception:
        return fallback
    v = (v or "").strip()
    if not v:
        return fallback
    return v.split()[0].strip().upper() or fallback


def _sum_profit(trades: pd.DataFrame, start_d: date, end_d: date) -> Optional[float]:
    if trades is None or len(trades) == 0:
        return None
    pcol = _safe_col(trades, ["Profit", "profit", "PnL", "pnl", "Net profit", "net_profit"])
    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
    if not pcol or not tcol:
        return None

    ts = _ensure_dt_series(trades[tcol])
    try:
        mask = ts.notna() & (ts.dt.date >= start_d) & (ts.dt.date <= end_d)
    except Exception:
        return None

    s = pd.to_numeric(trades.loc[mask, pcol], errors="coerce").dropna()
    if len(s) == 0:
        return 0.0
    return float(s.sum())


def _avg_triplet(trades: pd.DataFrame, d: date) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if trades is None or len(trades) == 0:
        return (None, None, None)

    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time", "Entry time", "Entry Time", "entry_time"])
    if not tcol:
        return (None, None, None)

    ts = _ensure_dt_series(trades[tcol])
    try:
        mask = ts.notna() & (ts.dt.date == d)
    except Exception:
        return (None, None, None)

    sub = trades.loc[mask]
    if len(sub) == 0:
        return (None, None, None)

    mae_c = _safe_col(sub, ["MAE", "mae", "Avg MAE", "avg_mae"])
    mfe_c = _safe_col(sub, ["MFE", "mfe", "Avg MFE", "avg_mfe"])
    etd_c = _safe_col(sub, ["ETD", "etd", "Avg ETD", "avg_etd"])

    def mean_of(col: Optional[str]) -> Optional[float]:
        if not col:
            return None
        v = pd.to_numeric(sub[col], errors="coerce").dropna()
        return float(v.mean()) if len(v) else None

    return (mean_of(mae_c), mean_of(mfe_c), mean_of(etd_c))


def pick_default_target_date(packages: Dict[str, AnalysisPackage]) -> Optional[date]:
    best: Optional[date] = None

    for pkg in packages.values():
        if not pkg:
            continue

        df = getattr(pkg, "daily", None)
        if df is not None and len(df) > 0:
            dcol = _safe_col(df, ["date", "Date", "Period"])
            if dcol:
                s = _ensure_dt_series(df[dcol]).dropna()
                if len(s):
                    cand = s.dt.date.max()
                    if best is None or (cand and cand > best):
                        best = cand

    if best is not None:
        return best

    for pkg in packages.values():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue
        tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
        if not tcol:
            continue
        s = _ensure_dt_series(trades[tcol]).dropna()
        if len(s):
            cand = s.dt.date.max()
            if best is None or (cand and cand > best):
                best = cand

    return best


def compute_daily_week_context(target: date) -> Dict[str, date]:
    ws = _week_start_monday(target)
    return {
        "target": target,
        "wtd_start": ws,
        "wtd_end": target,
        "last_week_start": ws - timedelta(days=7),
        "last_week_end": ws - timedelta(days=1),
        "week_start": ws,
        "week_end": ws + timedelta(days=6),
    }


# -----------------------------
# Consistency / heatmap helpers
# -----------------------------

def _collect_trading_days_from_trades(packages: Dict[str, AnalysisPackage]) -> List[date]:
    days: set[date] = set()
    for pkg in packages.values():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue
        tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
        if not tcol:
            continue
        ts = _ensure_dt_series(trades[tcol]).dropna()
        if len(ts) == 0:
            continue
        try:
            for d in ts.dt.date.unique():
                if isinstance(d, date):
                    days.add(d)
        except Exception:
            continue
    return sorted(days)


def get_recent_trading_days(packages: Dict[str, AnalysisPackage], *, end_date: date, lookback_days: int) -> List[date]:
    all_days = _collect_trading_days_from_trades(packages)
    all_days = [d for d in all_days if d <= end_date]
    if lookback_days <= 0:
        return all_days
    return all_days[-lookback_days:]


def daily_pnl_vector(trades: pd.DataFrame, days: List[date]) -> List[Optional[float]]:
    if trades is None or len(trades) == 0:
        return [None for _ in days]

    pcol = _safe_col(trades, ["Profit", "profit", "PnL", "pnl", "Net profit", "net_profit"])
    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
    if not pcol or not tcol:
        return [None for _ in days]

    ts = _ensure_dt_series(trades[tcol])
    pnl = pd.to_numeric(trades[pcol], errors="coerce")

    out: List[Optional[float]] = []
    for d in days:
        try:
            mask = ts.notna() & (ts.dt.date == d)
        except Exception:
            out.append(None)
            continue
        s = pnl.loc[mask].dropna()
        if len(s) == 0:
            out.append(None)
        else:
            out.append(float(s.sum()))
    return out


def consistency_metrics(pnls: List[Optional[float]]) -> Dict[str, Any]:
    vals = [v for v in pnls if v is not None]
    if not vals:
        return {
            "trade_days": 0,
            "green_days": 0,
            "red_days": 0,
            "flat_days": 0,
            "win_rate": None,
            "worst_day": None,
            "max_red_streak": 0,
        }

    green = sum(1 for v in vals if v > 0)
    red = sum(1 for v in vals if v < 0)
    flat = sum(1 for v in vals if v == 0)

    denom = green + red
    win_rate = (green / denom) if denom > 0 else None
    worst = min(vals) if vals else None

    max_streak = 0
    cur = 0
    for v in pnls:
        if v is None:
            continue
        if v < 0:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    return {
        "trade_days": len(vals),
        "green_days": green,
        "red_days": red,
        "flat_days": flat,
        "win_rate": win_rate,
        "worst_day": worst,
        "max_red_streak": max_streak,
    }


# -----------------------------
# Prop trailing drawdown helpers
# -----------------------------

def intraday_trailing_dd_metrics(trades: pd.DataFrame, d: date) -> Dict[str, Optional[float]]:
    """
    Computes realized intraday equity curve metrics for a given date.

    For prop "trailing drawdown moved up":
      - trail_move_day := max(0, peak_runup_day)
      - peak_runup_day := max cumulative realized PnL during the day (starting at 0)

    Also returns:
      - end_pnl_day: realized cumulative PnL by end of day (sum of day trade PnLs)
      - max_dd_day: max peak-to-trough drawdown within the day (realized)

    Notes:
    - Uses trade 'Exit time' (preferred) to order realized PnL.
    - If your prop firm uses *intraday unrealized* highs, you'd need bar data; this is realized-only.
    """
    if trades is None or len(trades) == 0:
        return {"trail_move_day": None, "peak_runup_day": None, "end_pnl_day": None, "max_dd_day": None}

    pcol = _safe_col(trades, ["Profit", "profit", "PnL", "pnl", "Net profit", "net_profit"])
    tcol = _safe_col(trades, ["Exit time", "Exit Time", "exit_time"])
    if not pcol or not tcol:
        return {"trail_move_day": None, "peak_runup_day": None, "end_pnl_day": None, "max_dd_day": None}

    ts = _ensure_dt_series(trades[tcol])
    pnl = pd.to_numeric(trades[pcol], errors="coerce")

    try:
        mask = ts.notna() & (ts.dt.date == d)
    except Exception:
        return {"trail_move_day": None, "peak_runup_day": None, "end_pnl_day": None, "max_dd_day": None}

    sub = trades.loc[mask].copy()
    if len(sub) == 0:
        return {"trail_move_day": 0.0, "peak_runup_day": 0.0, "end_pnl_day": 0.0, "max_dd_day": 0.0}

    sub["_ts"] = _ensure_dt_series(sub[tcol])
    sub["_pnl"] = pd.to_numeric(sub[pcol], errors="coerce")
    sub = sub.dropna(subset=["_ts", "_pnl"]).sort_values("_ts")
    if len(sub) == 0:
        return {"trail_move_day": 0.0, "peak_runup_day": 0.0, "end_pnl_day": 0.0, "max_dd_day": 0.0}

    eq = sub["_pnl"].cumsum()  # day starts at 0
    peak = eq.cummax()
    dd = eq - peak  # <= 0
    peak_runup = float(eq.max()) if len(eq) else 0.0
    end_pnl = float(eq.iloc[-1]) if len(eq) else 0.0
    max_dd = float(dd.min()) if len(dd) else 0.0  # negative number (e.g., -350)

    trail_move = max(0.0, peak_runup)

    return {
        "trail_move_day": trail_move,
        "peak_runup_day": peak_runup,
        "end_pnl_day": end_pnl,
        "max_dd_day": max_dd,
    }


# -----------------------------
# Main row builders
# -----------------------------

def build_daily_leaderboard_rows(
    packages: Dict[str, AnalysisPackage],
    *,
    target: date,
    windows: Tuple[SessionWindow, ...],
    fallback_session: str = "Unclassified",
    fallback_market: str = "Unknown",
    lookback_days: int = 10,
) -> List[Dict[str, Any]]:
    ctx = compute_daily_week_context(target)
    recent_days = get_recent_trading_days(packages, end_date=target, lookback_days=lookback_days)

    rows: List[Dict[str, Any]] = []
    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        if not pkg:
            continue

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg else {}
        card_uri = derived.get("card_image_uri")

        trades = getattr(pkg, "trades", None)

        session = (
            infer_session_from_trades(trades, windows, fallback=fallback_session, restrict_to_date=target)
            if trades is not None
            else fallback_session
        )
        market = infer_market_root(trades, fallback=fallback_market) if trades is not None else fallback_market

        day_profit = _sum_profit(trades, target, target) if trades is not None else None
        wtd_profit = _sum_profit(trades, ctx["wtd_start"], ctx["wtd_end"]) if trades is not None else None
        last_week_profit = _sum_profit(trades, ctx["last_week_start"], ctx["last_week_end"]) if trades is not None else None

        avg_mae, avg_mfe, avg_etd = _avg_triplet(trades, target) if trades is not None else (None, None, None)

        pnls = daily_pnl_vector(trades, recent_days) if trades is not None else [None for _ in recent_days]
        cm = consistency_metrics(pnls)

        ddm = intraday_trailing_dd_metrics(trades, target) if trades is not None else {
            "trail_move_day": None, "peak_runup_day": None, "end_pnl_day": None, "max_dd_day": None
        }

        rows.append(
            {
                "run_id": run_id,
                "card_uri": card_uri,
                "session": session,
                "market": market,
                "day_profit": day_profit,
                "wtd_profit": wtd_profit,
                "last_week_profit": last_week_profit,
                "avg_mae": avg_mae,
                "avg_mfe": avg_mfe,
                "avg_etd": avg_etd,
                # consistency window
                "recent_days": recent_days,
                "recent_pnls": pnls,
                "trade_days": cm["trade_days"],
                "green_days": cm["green_days"],
                "red_days": cm["red_days"],
                "flat_days": cm["flat_days"],
                "win_rate": cm["win_rate"],
                "worst_day": cm["worst_day"],
                "max_red_streak": cm["max_red_streak"],
                # prop trailing dd movement
                "trail_move_day": ddm.get("trail_move_day"),
                "peak_runup_day": ddm.get("peak_runup_day"),
                "end_pnl_day": ddm.get("end_pnl_day"),
                "max_dd_day": ddm.get("max_dd_day"),
            }
        )
    return rows


def build_weekly_leaderboard_rows(
    packages: Dict[str, AnalysisPackage],
    *,
    week_start: date,
    week_end: date,
    windows: Tuple[SessionWindow, ...],
    fallback_session: str = "Unclassified",
    fallback_market: str = "Unknown",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        if not pkg:
            continue

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg else {}
        card_uri = derived.get("card_image_uri")

        trades = getattr(pkg, "trades", None)
        session = (
            infer_session_from_trades(trades, windows, fallback=fallback_session, restrict_to_date=None)
            if trades is not None
            else fallback_session
        )
        market = infer_market_root(trades, fallback=fallback_market) if trades is not None else fallback_market

        week_profit = _sum_profit(trades, week_start, week_end) if trades is not None else None

        rows.append(
            {
                "run_id": run_id,
                "card_uri": card_uri,
                "session": session,
                "market": market,
                "week_profit": week_profit,
            }
        )
    return rows
