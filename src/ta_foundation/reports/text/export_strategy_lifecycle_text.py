from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
import json
import math

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.strategy_lifecycle_board import (
    LifecycleWindow,
    build_strategy_lifecycle_board,
)


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pf(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100.0:.0f}%"


def _window_line(label: str, window: LifecycleWindow) -> str:
    return (
        f"{label}: {_fmt_money(window.pnl, sign=True)} "
        f"PF {_fmt_pf(window.profit_factor)} "
        f"WR {_fmt_pct(window.win_rate)} "
        f"Active {window.active_days}/{len(window.days)} "
        f"DD {_fmt_money(window.max_drawdown)}"
    )


def export_strategy_lifecycle_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Strategy Lifecycle Board",
) -> Path:
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    board = build_strategy_lifecycle_board(
        packages,
        as_of=as_of,
        risk_budget=float(options.get("risk_budget", 2500.0)),
        top_n=int(options.get("top_n", 30)),
    )

    rows = board["rows"]
    if not rows:
        output_path.write_text("No daily outcomes were available to build a lifecycle board.\n", encoding="utf-8")
        return output_path

    sep = "=" * 80
    sub = "-" * 80
    summary = board["summary"]
    lines: list[str] = [
        sep,
        title,
        sep,
        f"As of:       {board['as_of'].isoformat()}",
        f"Risk budget: {_fmt_money(board['risk_budget'])}",
        "Purpose:     Find strategies that are in favor now, with explicit risk posture.",
        (
            "Coverage:    "
            f"Trade {summary['trade_candidates']} | "
            f"Small-size watch {summary['small_size_watch']} | "
            f"Pause/reduce {summary['pause_or_reduce']} | "
            f"Do-not-trade {summary['do_not_trade']}"
        ),
        "",
        sub,
    ]

    for row in rows:
        best = row["best_window"]
        best_label = best.label if best else "-"
        best_pnl = _fmt_money(best.pnl, sign=True) if best else "-"
        lines.append(f"#{row['rank']}  {row['run_id']}")
        lines.append(
            f"  Action:     {row['tradability']}   "
            f"State: {row['lifecycle_state']}   "
            f"Risk: {row['risk_category']}   "
            f"Score: {round(row['score'])}"
        )
        lines.append(f"  Best hot window: {best_label} ({best_pnl})")
        lines.append("  " + _window_line("2w", row["windows"]["2w"]))
        lines.append("  " + _window_line("3w", row["windows"]["3w"]))
        lines.append("  " + _window_line("4w", row["windows"]["4w"]))
        lines.append(sub)

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path


def _window_to_json(window: LifecycleWindow) -> Dict[str, Any]:
    def finite(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None

    return {
        "label": window.label,
        "start": window.days[0].isoformat() if window.days else None,
        "end": window.days[-1].isoformat() if window.days else None,
        "pnl": finite(window.pnl),
        "active_days": window.active_days,
        "win_days": window.win_days,
        "loss_days": window.loss_days,
        "no_trade_days": window.no_trade_days,
        "profit_factor": finite(window.profit_factor),
        "win_rate": finite(window.win_rate),
        "max_drawdown": finite(window.max_drawdown),
        "worst_day": finite(window.worst_day),
        "avg_active_day": finite(window.avg_active_day),
    }


def export_strategy_lifecycle_json(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
) -> Path:
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    board = build_strategy_lifecycle_board(
        packages,
        as_of=as_of,
        risk_budget=float(options.get("risk_budget", 2500.0)),
        top_n=int(options.get("top_n", 30)),
    )
    payload = {
        "as_of": board["as_of"].isoformat(),
        "risk_budget": board["risk_budget"],
        "summary": dict(board["summary"], best=None),
        "rows": [
            {
                "rank": row["rank"],
                "run_id": row["run_id"],
                "score": row["score"],
                "lifecycle_state": row["lifecycle_state"],
                "risk_category": row["risk_category"],
                "tradability": row["tradability"],
                "best_window": (
                    _window_to_json(row["best_window"])
                    if row["best_window"] is not None else None
                ),
                "windows": {
                    label: _window_to_json(window)
                    for label, window in row["windows"].items()
                },
            }
            for row in board["rows"]
        ],
    }
    output_path.write_text(
        json.dumps(payload, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return output_path
