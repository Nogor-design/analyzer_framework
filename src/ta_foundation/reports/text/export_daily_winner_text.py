from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.daily_winner_board import build_daily_winner_board


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100.0:.0f}%"


def export_daily_winner_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Daily Winner Insight",
) -> Path:
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    target_date = None
    target_raw = str(options.get("target_date") or "").strip()
    if target_raw:
        try:
            target_date = date.fromisoformat(target_raw)
        except Exception:
            target_date = None

    board = build_daily_winner_board(
        packages,
        target_date=target_date,
        top_n=int(options.get("top_n", 10)),
        strip_days=int(options.get("strip_days", 5)),
    )

    if not board["rows"] or board["target_date"] is None:
        output_path.write_text("No target day with daily outcomes could be inferred.\n", encoding="utf-8")
        return output_path

    lines: list[str] = []
    sep = "=" * 80
    sub = "-" * 80
    winner = board["winner"]
    runner_up = board["runner_up"]
    summary = board["summary"]

    lines.append(sep)
    lines.append(title)
    lines.append(sep)
    lines.append(f"Target Date:   {board['target_date'].isoformat()}")
    lines.append("Ranking:       target-day PnL first, then recent 5D / 10D support")
    lines.append("")
    lines.append(f"Winner:        {winner['run_id']}  Day {_fmt_money(winner.get('day_profit'), sign=True)}")
    lines.append(f"Session:       {winner['session_label']}  ({winner['session_source']})")
    lines.append(f"Window:        {winner['active_window']['label']}  ({winner['active_window']['duration_label']})")
    if runner_up:
        lines.append(
            f"Lead Over #2:  {_fmt_money(summary.get('lead_amount'), sign=True)}  "
            f"vs {runner_up['run_id']} at {_fmt_money(runner_up.get('day_profit'), sign=True)}"
        )
    strongest = summary.get("strongest_support")
    if strongest:
        lines.append(
            f"Support:       {strongest['run_id']} had the strongest recent support "
            f"(5D {_fmt_money(strongest['recent5'].pnl, sign=True)}, 10D {_fmt_money(strongest['recent10'].pnl, sign=True)})"
        )
    if summary.get("dominant_session"):
        lines.append(
            f"Top-5 Tilt:    {summary['dominant_session']} "
            f"({int(summary.get('dominant_session_count') or 0)} of top 5)"
        )

    lines.append("")
    lines.append(sub)
    for row in board["rows"]:
        gap = None
        if winner.get("day_profit") is not None and row.get("day_profit") is not None:
            gap = float(row["day_profit"] - winner["day_profit"])
        lines.append(
            f"#{row['daily_rank']}  {row['run_id']}  "
            f"Day {_fmt_money(row.get('day_profit'), sign=True)}  "
            f"Gap {_fmt_money(gap, sign=True)}"
        )
        lines.append(
            f"  Session {row['session_label']} | Window {row['active_window']['label']} ({row['active_window']['duration_label']}) | Status {row['day_status']}"
        )
        lines.append(
            f"  5D {_fmt_money(row['recent5'].pnl, sign=True)} | "
            f"10D {_fmt_money(row['recent10'].pnl, sign=True)} | "
            f"10D WR {_fmt_pct(row['recent10'].win_rate)}"
        )
        lines.append(sub)

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
