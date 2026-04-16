from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.momentum_board import build_strategy_momentum_board


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100.0:.0f}%"


def export_strategy_momentum_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Strategy Momentum Board",
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

    board = build_strategy_momentum_board(
        packages,
        as_of=as_of,
        strip_days=int(options.get("strip_days", 5)),
        top_n=int(options.get("top_n", 24)),
    )

    rows = board["rows"]
    as_of_date = board["as_of"]
    summary = board["summary"]

    if not rows:
        output_path.write_text("No daily outcomes were available to build a momentum board.\n", encoding="utf-8")
        return output_path

    lines: list[str] = []
    sep = "=" * 80
    sub = "-" * 80

    lines.append(sep)
    lines.append(title)
    lines.append(sep)
    lines.append(f"As of:        {as_of_date.isoformat()}")
    lines.append("Windows:      5D vs previous 5D, plus 10D and 20D trading-day context")
    lines.append("Scoring:      recent 5D PnL + 10D support + 5D improvement + recent activity")
    lines.append(
        f"Coverage:     Improving {int(summary.get('improving_count') or 0)} | "
        f"Strong {int(summary.get('strong_count') or 0)} | "
        f"Inactive {int(summary.get('inactive_count') or 0)}"
    )
    lines.append("")

    best = summary.get("best_now")
    if best:
        lines.append(f"Best Now:     {best['run_id']}  (Score {round(best['score'])}, Status {best['status']})")
    improver = summary.get("biggest_improver")
    if improver:
        lines.append(f"Improver:     {improver['run_id']}  (Delta 5D {_fmt_money(improver['delta5'], sign=True)})")
    consistent = summary.get("most_consistent")
    if consistent:
        lines.append(
            f"Consistent:   {consistent['run_id']}  "
            f"(10D WR {_fmt_pct(consistent['recent10'].win_rate)}, "
            f"Active {consistent['recent10'].active_days}/{len(consistent['recent10'].days)})"
        )
    cooling = summary.get("cooling_off")
    if cooling:
        lines.append(f"Needs Review: {cooling['run_id']}  (Delta 5D {_fmt_money(cooling['delta5'], sign=True)})")

    lines.append("")
    lines.append(sub)

    for row in rows:
        recent5 = row["recent5"]
        prev5 = row["prev5"]
        recent10 = row["recent10"]
        recent20 = row["recent20"]

        lines.append(f"#{row['rank']}  {row['run_id']}")
        lines.append(
            f"  Score:      {round(row['score'])}   "
            f"Status: {row['status']}"
        )
        lines.append(
            f"  5D:         {_fmt_money(recent5.pnl, sign=True):>8}   "
            f"Prev 5D: {_fmt_money(prev5.pnl, sign=True):>8}   "
            f"Delta 5D: {_fmt_money(row['delta5'], sign=True):>8}"
        )
        lines.append(
            f"  10D / 20D:  {_fmt_money(recent10.pnl, sign=True):>8}   "
            f"{_fmt_money(recent20.pnl, sign=True):>8}"
        )
        lines.append(
            f"  Activity:   5D {recent5.active_days}/{len(recent5.days)}   "
            f"10D {recent10.active_days}/{len(recent10.days)}   "
            f"10D WR {_fmt_pct(recent10.win_rate)}"
        )
        lines.append(
            f"  Avg Daily:  {_fmt_money(recent5.avg_daily, sign=True)}   "
            f"Recent outcomes W{recent5.win_days} L{recent5.loss_days} N{recent5.no_trade_days}"
        )
        lines.append(sub)

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path

