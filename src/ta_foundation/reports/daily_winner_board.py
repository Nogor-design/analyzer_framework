from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from ta_foundation.analysis.leaderboards import pick_default_target_date
from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.momentum_board import build_strategy_momentum_board
from ta_foundation.reports.session_momentum_board import (
    active_window_for_pkg,
    classify_strategy_session,
)


def build_daily_winner_board(
    packages: Dict[str, AnalysisPackage],
    *,
    target_date: Optional[date] = None,
    top_n: int = 10,
    strip_days: int = 5,
) -> Dict[str, Any]:
    resolved_target = target_date or pick_default_target_date(packages)
    if resolved_target is None:
        return {
            "target_date": None,
            "rows": [],
            "winner": None,
            "runner_up": None,
            "summary": {},
        }

    momentum = build_strategy_momentum_board(
        packages,
        as_of=resolved_target,
        strip_days=strip_days,
        top_n=0,
    )

    rows: List[Dict[str, Any]] = []
    target_iso = resolved_target.isoformat()
    for row in momentum["rows"]:
        pkg = row["pkg"]
        outcomes = derive_daily_outcomes_for_package(pkg).get("by_date", {}) or {}
        payload = outcomes.get(target_iso) or {}
        day_profit = payload.get("net_profit")
        trades = int(payload.get("trades") or 0)
        status = str(payload.get("status") or ("NO_TRADE" if trades <= 0 else "")).upper()

        session_info = classify_strategy_session(row["run_id"], pkg)
        active_window = active_window_for_pkg(pkg)

        row["target_date"] = resolved_target
        row["day_profit"] = (float(day_profit) if day_profit is not None else None)
        row["day_trades"] = trades
        row["day_status"] = status
        row["session_label"] = session_info["label"]
        row["session_source"] = session_info["source"]
        row["active_window"] = active_window
        rows.append(row)

    rows.sort(
        key=lambda r: (
            r["day_profit"] is None,
            -(r["day_profit"] or float("-inf")),
            -(r["recent5"].pnl),
            -(r["score"] or 0.0),
            r["run_id"].lower(),
        )
    )

    if top_n > 0:
        rows = rows[:top_n]

    for idx, row in enumerate(rows, start=1):
        row["daily_rank"] = idx

    winner = rows[0] if rows else None
    runner_up = rows[1] if len(rows) > 1 else None

    lead_amount = None
    if winner and runner_up and winner.get("day_profit") is not None and runner_up.get("day_profit") is not None:
        lead_amount = float(winner["day_profit"] - runner_up["day_profit"])

    strongest_support = max(
        rows[: min(len(rows), 5)],
        key=lambda r: (r["recent10"].pnl, r["recent5"].pnl, r["score"]),
        default=None,
    )

    session_counts: Dict[str, int] = {}
    for row in rows[: min(len(rows), 5)]:
        session_counts[row["session_label"]] = session_counts.get(row["session_label"], 0) + 1
    dominant_session = max(session_counts.items(), key=lambda kv: kv[1], default=(None, 0))

    return {
        "target_date": resolved_target,
        "strip_days": momentum["strip_days"],
        "rows": rows,
        "winner": winner,
        "runner_up": runner_up,
        "summary": {
            "lead_amount": lead_amount,
            "strongest_support": strongest_support,
            "dominant_session": dominant_session[0],
            "dominant_session_count": dominant_session[1],
        },
    }
