from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.momentum_board import recent_weekdays

TZ_DENVER = ZoneInfo("America/Denver")


@dataclass(frozen=True)
class LifecycleWindow:
    label: str
    days: List[date]
    pnl: float
    active_days: int
    win_days: int
    loss_days: int
    no_trade_days: int
    profit_factor: Optional[float]
    win_rate: Optional[float]
    max_drawdown: float
    worst_day: float
    avg_active_day: Optional[float]


def _outcomes_by_date(pkg: AnalysisPackage) -> Dict[date, Dict[str, Any]]:
    raw = derive_daily_outcomes_for_package(pkg).get("by_date", {}) or {}
    out: Dict[date, Dict[str, Any]] = {}
    for key, value in raw.items():
        try:
            out[date.fromisoformat(str(key))] = dict(value or {})
        except Exception:
            continue
    return out


def _max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(float(worst))


def _window_metrics(
    outcome_map: Dict[date, Dict[str, Any]],
    *,
    label: str,
    days: List[date],
) -> LifecycleWindow:
    pnls: List[float] = []
    active_days = 0
    win_days = 0
    loss_days = 0
    no_trade_days = 0
    gross_win = 0.0
    gross_loss = 0.0
    worst_day = 0.0

    for day in days:
        payload = outcome_map.get(day)
        if not payload:
            pnls.append(0.0)
            no_trade_days += 1
            continue
        trades = int(payload.get("trades") or 0)
        pnl = float(payload.get("net_profit") or 0.0)
        if trades <= 0:
            pnls.append(0.0)
            no_trade_days += 1
            continue
        pnls.append(pnl)
        active_days += 1
        worst_day = min(worst_day, pnl)
        if pnl > 0:
            win_days += 1
            gross_win += pnl
        elif pnl < 0:
            loss_days += 1
            gross_loss += abs(pnl)

    denom = win_days + loss_days
    win_rate = (win_days / denom) if denom else None
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win <= 0 else float("inf"))
    pnl_total = float(sum(pnls))
    avg_active_day = (pnl_total / active_days) if active_days else None

    return LifecycleWindow(
        label=label,
        days=days,
        pnl=pnl_total,
        active_days=active_days,
        win_days=win_days,
        loss_days=loss_days,
        no_trade_days=no_trade_days,
        profit_factor=profit_factor,
        win_rate=win_rate,
        max_drawdown=_max_drawdown(pnls),
        worst_day=float(worst_day),
        avg_active_day=avg_active_day,
    )


def _risk_category(best: Optional[LifecycleWindow], *, risk_budget: float) -> str:
    if best is None or best.pnl <= 0 or best.active_days <= 0:
        return "blocked"
    budget = max(float(risk_budget), 1.0)
    risk = max(best.max_drawdown, abs(best.worst_day), 0.0)
    ratio = risk / budget
    if ratio <= 0.20:
        return "conservative"
    if ratio <= 0.40:
        return "balanced"
    if ratio <= 0.70:
        return "tactical"
    if ratio <= 1.00:
        return "aggressive"
    return "blocked"


def _lifecycle_state(w2: LifecycleWindow, w3: LifecycleWindow, w4: LifecycleWindow) -> str:
    pf2 = w2.profit_factor or 0.0
    pf3 = w3.profit_factor or 0.0
    pf4 = w4.profit_factor or 0.0

    if w2.active_days < 2 and w3.active_days < 3:
        return "inactive"
    if w2.pnl > 0 and w3.pnl > 0 and pf2 >= 1.15 and pf3 >= 1.10:
        return "in_favor"
    if w2.pnl > 0 and pf2 >= 1.10 and w2.active_days >= 2:
        return "emerging"
    if w4.pnl > 0 and w2.pnl <= 0:
        return "cooling"
    if w2.pnl <= 0 and w3.pnl <= 0 and w4.pnl <= 0:
        return "blocked"
    if pf4 >= 1.10 and w4.pnl > 0:
        return "research_only"
    return "weak"


def _tradability(state: str, risk_category: str) -> str:
    if risk_category == "blocked" or state in {"blocked", "inactive", "weak"}:
        return "do_not_trade"
    if state == "in_favor":
        return "trade_candidate"
    if state == "emerging":
        return "small_size_watch"
    if state == "cooling":
        return "pause_or_reduce"
    return "paper_or_research"


def _score(best: Optional[LifecycleWindow], w2: LifecycleWindow, *, risk_budget: float) -> float:
    if best is None or best.active_days <= 0:
        return 0.0
    risk = max(best.max_drawdown, abs(best.worst_day), risk_budget * 0.10, 1.0)
    pf = best.profit_factor if best.profit_factor not in (None, float("inf")) else 3.0
    pf = min(float(pf or 0.0), 3.0)
    rr = max(-2.0, min(4.0, best.pnl / risk))
    recency = 1.0 if w2.pnl > 0 else -0.5
    active = min(best.active_days / max(len(best.days), 1), 1.0)
    score = 45.0 + 18.0 * rr + 10.0 * (pf - 1.0) + 12.0 * recency + 15.0 * active
    return max(0.0, min(100.0, score))


def build_strategy_lifecycle_board(
    packages: Dict[str, AnalysisPackage],
    *,
    as_of: Optional[date] = None,
    risk_budget: float = 2500.0,
    top_n: int = 30,
) -> Dict[str, Any]:
    """Classify strategies by current tradability, risk bucket, and hot window.

    This is intentionally less absolute than the hardening gate. It answers:
    "Is this strategy in favor over the last 2-4 weeks, and what size/risk
    posture would be acceptable if we choose to run it?"
    """
    package_outcomes: Dict[str, Dict[date, Dict[str, Any]]] = {}
    latest_day: Optional[date] = None
    for run_id, pkg in sorted((packages or {}).items()):
        outcome_map = _outcomes_by_date(pkg)
        package_outcomes[run_id] = outcome_map
        if outcome_map:
            run_latest = max(outcome_map)
            latest_day = run_latest if latest_day is None else max(latest_day, run_latest)

    as_of_date = as_of or latest_day or datetime.now(TZ_DENVER).date()
    specs = (("2w", 10), ("3w", 15), ("4w", 20))
    window_days = {label: recent_weekdays(n, end_day=as_of_date) for label, n in specs}

    rows: List[Dict[str, Any]] = []
    for run_id, pkg in sorted((packages or {}).items()):
        outcome_map = package_outcomes.get(run_id, {})
        if not outcome_map:
            continue
        windows = {
            label: _window_metrics(outcome_map, label=label, days=days)
            for label, days in window_days.items()
        }
        candidates = [
            w for w in windows.values()
            if w.pnl > 0 and w.active_days >= (2 if w.label == "2w" else 3)
        ]
        best = max(
            candidates,
            key=lambda w: (
                w.pnl / max(w.max_drawdown, abs(w.worst_day), risk_budget * 0.10, 1.0),
                w.pnl,
                w.active_days,
            ),
            default=None,
        )
        state = _lifecycle_state(windows["2w"], windows["3w"], windows["4w"])
        risk_category = _risk_category(best, risk_budget=risk_budget)
        tradability = _tradability(state, risk_category)
        row = {
            "run_id": run_id,
            "pkg": pkg,
            "as_of": as_of_date,
            "windows": windows,
            "best_window": best,
            "lifecycle_state": state,
            "risk_category": risk_category,
            "tradability": tradability,
            "score": _score(best, windows["2w"], risk_budget=risk_budget),
            "risk_budget": float(risk_budget),
        }
        rows.append(row)

    tradability_order = {
        "trade_candidate": 0,
        "small_size_watch": 1,
        "paper_or_research": 2,
        "pause_or_reduce": 3,
        "do_not_trade": 4,
    }
    risk_order = {
        "conservative": 0,
        "balanced": 1,
        "tactical": 2,
        "aggressive": 3,
        "blocked": 4,
    }
    rows.sort(
        key=lambda r: (
            tradability_order.get(r["tradability"], 9),
            risk_order.get(r["risk_category"], 9),
            -float(r["score"]),
            r["run_id"].lower(),
        )
    )
    if top_n > 0:
        rows = rows[:top_n]
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    return {
        "as_of": as_of_date,
        "risk_budget": float(risk_budget),
        "window_days": window_days,
        "rows": rows,
        "summary": {
            "trade_candidates": sum(1 for r in rows if r["tradability"] == "trade_candidate"),
            "small_size_watch": sum(1 for r in rows if r["tradability"] == "small_size_watch"),
            "pause_or_reduce": sum(1 for r in rows if r["tradability"] == "pause_or_reduce"),
            "do_not_trade": sum(1 for r in rows if r["tradability"] == "do_not_trade"),
            "best": rows[0] if rows else None,
        },
    }
