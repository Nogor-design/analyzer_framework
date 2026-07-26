from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage

TZ_DENVER = ZoneInfo("America/Denver")


@dataclass(frozen=True)
class MomentumSnapshot:
    days: List[date]
    pnl: float
    active_days: int
    win_days: int
    loss_days: int
    no_trade_days: int
    avg_daily: Optional[float]
    win_rate: Optional[float]
    pnls: List[Optional[float]]


def recent_weekdays(count: int, *, end_day: Optional[date] = None) -> List[date]:
    if count <= 0:
        return []

    cursor = end_day or datetime.now(TZ_DENVER).date()
    out: List[date] = []
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    out.reverse()
    return out


def _outcomes_by_date(pkg: AnalysisPackage) -> Dict[date, Dict[str, Any]]:
    raw = derive_daily_outcomes_for_package(pkg).get("by_date", {}) or {}
    out: Dict[date, Dict[str, Any]] = {}
    for key, value in raw.items():
        try:
            out[date.fromisoformat(str(key))] = dict(value or {})
        except Exception:
            continue
    return out


def _window_snapshot(
    outcome_map: Dict[date, Dict[str, Any]],
    days: Iterable[date],
) -> MomentumSnapshot:
    pnl_total = 0.0
    active_days = 0
    win_days = 0
    loss_days = 0
    no_trade_days = 0
    pnls: List[Optional[float]] = []
    day_list = list(days)

    for day in day_list:
        payload = outcome_map.get(day)
        if not payload:
            no_trade_days += 1
            pnls.append(None)
            continue

        net_profit = float(payload.get("net_profit") or 0.0)
        trades = int(payload.get("trades") or 0)
        status = str(payload.get("status") or "").upper()

        pnl_total += net_profit
        pnls.append(net_profit if trades > 0 else None)

        if trades <= 0 or status == "NO_TRADE":
            no_trade_days += 1
            continue

        active_days += 1
        if net_profit > 0:
            win_days += 1
        elif net_profit < 0:
            loss_days += 1

    denom = win_days + loss_days
    win_rate = (win_days / denom) if denom > 0 else None
    avg_daily = (pnl_total / active_days) if active_days > 0 else None

    return MomentumSnapshot(
        days=day_list,
        pnl=float(pnl_total),
        active_days=active_days,
        win_days=win_days,
        loss_days=loss_days,
        no_trade_days=no_trade_days,
        avg_daily=avg_daily,
        win_rate=win_rate,
        pnls=pnls,
    )


def _robust_scale(values: Iterable[float], *, floor: float) -> float:
    clean = [abs(float(v)) for v in values if v is not None]
    clean = [v for v in clean if v > 0]
    if not clean:
        return floor
    return max(float(median(clean)), floor)


def _tanh_like(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    x = value / scale
    return x / (1.0 + abs(x))


def _status_for_row(
    recent5_pnl: float,
    prev5_pnl: float,
    recent10_pnl: float,
    active5: int,
    active10: int,
    win_rate10: Optional[float],
) -> str:
    if active10 <= 1 or active5 == 0:
        return "Inactive"

    delta5 = recent5_pnl - prev5_pnl
    threshold = max(150.0, 0.15 * max(abs(prev5_pnl), abs(recent5_pnl), 1.0))
    wr10 = win_rate10 if win_rate10 is not None else 0.5

    if recent5_pnl > 0 and delta5 >= threshold and active5 >= 2:
        return "Improving"
    if recent5_pnl > 0 and wr10 >= 0.55:
        return "Strong"
    if recent10_pnl > 0 and delta5 <= -threshold:
        return "Cooling"
    if recent5_pnl < 0 and delta5 >= threshold:
        return "Recovering"
    if abs(recent5_pnl) <= threshold * 0.5 and active10 >= 3:
        return "Stable"
    return "Weak"


def _favorability_score(
    *,
    recent5_pnl: float,
    recent10_pnl: float,
    delta5: float,
    active5: int,
    win_rate10: Optional[float],
    scale5: float,
    scale10: float,
    scale_delta: float,
) -> float:
    wr10 = win_rate10 if win_rate10 is not None else 0.45
    active_score = ((active5 / 5.0) - 0.45) * 18.0
    consistency_score = (wr10 - 0.5) * 22.0

    score = (
        50.0
        + 24.0 * _tanh_like(recent5_pnl, scale5)
        + 13.0 * _tanh_like(recent10_pnl, scale10)
        + 17.0 * _tanh_like(delta5, scale_delta)
        + active_score
        + consistency_score
    )
    return max(0.0, min(100.0, score))


def build_strategy_momentum_board(
    packages: Dict[str, AnalysisPackage],
    *,
    as_of: Optional[date] = None,
    strip_days: int = 5,
    score_windows: tuple[int, int, int] = (5, 10, 20),
    top_n: int = 24,
) -> Dict[str, Any]:
    as_of_date = as_of or datetime.now(TZ_DENVER).date()
    win5, win10, win20 = score_windows

    recent5_days = recent_weekdays(win5, end_day=as_of_date)
    prev5_anchor = recent5_days[0] - timedelta(days=1) if recent5_days else as_of_date
    prev5_days = recent_weekdays(win5, end_day=prev5_anchor)
    recent10_days = recent_weekdays(win10, end_day=as_of_date)
    recent20_days = recent_weekdays(win20, end_day=as_of_date)
    strip_days_list = recent_weekdays(strip_days, end_day=as_of_date)

    rows: List[Dict[str, Any]] = []
    for run_id, pkg in sorted((packages or {}).items()):
        outcome_map = _outcomes_by_date(pkg)
        if not outcome_map:
            continue

        snap5 = _window_snapshot(outcome_map, recent5_days)
        prev5 = _window_snapshot(outcome_map, prev5_days)
        snap10 = _window_snapshot(outcome_map, recent10_days)
        snap20 = _window_snapshot(outcome_map, recent20_days)

        row = {
            "run_id": run_id,
            "pkg": pkg,
            "as_of": as_of_date,
            "strip_days": strip_days_list,
            "recent5": snap5,
            "prev5": prev5,
            "recent10": snap10,
            "recent20": snap20,
            "delta5": float(snap5.pnl - prev5.pnl),
            "activity_ratio5": (snap5.active_days / len(snap5.days)) if snap5.days else 0.0,
        }
        row["status"] = _status_for_row(
            recent5_pnl=snap5.pnl,
            prev5_pnl=prev5.pnl,
            recent10_pnl=snap10.pnl,
            active5=snap5.active_days,
            active10=snap10.active_days,
            win_rate10=snap10.win_rate,
        )
        rows.append(row)

    scale5 = _robust_scale((r["recent5"].pnl for r in rows), floor=350.0)
    scale10 = _robust_scale((r["recent10"].pnl for r in rows), floor=600.0)
    scale_delta = _robust_scale((r["delta5"] for r in rows), floor=250.0)

    for row in rows:
        row["score"] = _favorability_score(
            recent5_pnl=row["recent5"].pnl,
            recent10_pnl=row["recent10"].pnl,
            delta5=row["delta5"],
            active5=row["recent5"].active_days,
            win_rate10=row["recent10"].win_rate,
            scale5=scale5,
            scale10=scale10,
            scale_delta=scale_delta,
        )

    rows.sort(
        key=lambda r: (
            -(r.get("score") or 0.0),
            -(r["recent5"].pnl),
            -(r["delta5"]),
            r["run_id"].lower(),
        )
    )

    if top_n > 0:
        rows = rows[:top_n]

    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    improving = sum(1 for r in rows if r["status"] == "Improving")
    strong = sum(1 for r in rows if r["status"] == "Strong")
    inactive = sum(1 for r in rows if r["status"] == "Inactive")

    best_now = rows[0] if rows else None
    improver_candidates = [r for r in rows if r["delta5"] > 0]
    biggest_improver = max(
        improver_candidates,
        key=lambda r: (r["delta5"], r["recent5"].pnl),
        default=None,
    )
    most_consistent = max(
        rows,
        key=lambda r: (
            -1.0 if r["recent10"].win_rate is None else r["recent10"].win_rate,
            r["recent10"].active_days,
            r["recent10"].pnl,
        ),
        default=None,
    )

    cooling_candidates = [r for r in rows if r["delta5"] < 0 and r["status"] in {"Cooling", "Weak", "Recovering", "Strong", "Stable"}]
    cooling_off = min(cooling_candidates, key=lambda r: (r["delta5"], r["recent5"].pnl), default=None)

    return {
        "as_of": as_of_date,
        "strip_days": strip_days_list,
        "recent5_days": recent5_days,
        "prev5_days": prev5_days,
        "recent10_days": recent10_days,
        "recent20_days": recent20_days,
        "rows": rows,
        "summary": {
            "best_now": best_now,
            "biggest_improver": biggest_improver,
            "most_consistent": most_consistent,
            "cooling_off": cooling_off,
            "improving_count": improving,
            "strong_count": strong,
            "inactive_count": inactive,
            "scale5": scale5,
            "scale10": scale10,
            "scale_delta": scale_delta,
        },
    }
