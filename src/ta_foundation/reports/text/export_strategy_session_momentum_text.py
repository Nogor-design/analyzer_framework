from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.session_momentum_board import build_strategy_session_momentum_board


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def export_strategy_session_momentum_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Strategy Session Momentum Board",
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

    board = build_strategy_session_momentum_board(
        packages,
        as_of=as_of,
        strip_days=int(options.get("strip_days", 5)),
        overall_top_n=int(options.get("overall_top_n", 5)),
        top_n_per_session=int(options.get("top_n_per_session", 5)),
        overlap_compare_top_n=int(options.get("overlap_compare_top_n", 3)),
    )

    if not board["rows"]:
        output_path.write_text("No daily outcomes were available to build a session momentum board.\n", encoding="utf-8")
        return output_path

    lines: list[str] = []
    sep = "=" * 80
    sub = "-" * 80

    lines.append(sep)
    lines.append(title)
    lines.append(sep)
    lines.append(f"As of:        {board['as_of'].isoformat()}")
    lines.append("Grouping:     Name tokens first, Start_Time fallback when needed")
    lines.append("Scoring:      same favorability score as Strategy Momentum Board")
    lines.append("Windows:      each row includes the bot trading window to spot overlap quickly")
    lines.append(f"Stackability: each row is compared against the top {board['overlap_compare_top_n']} overall bots")
    lines.append("")

    lines.append("BEST ALL AROUND")
    lines.append(sub)
    for row in board["overall_rows"]:
        recent5 = row["recent5"]
        recent10 = row["recent10"]
        lines.append(
            f"#{row['overall_rank']}  {row['run_id']}  "
            f"[{row['session_label']}]  Score {round(row['score'])}  Status {row['status']}"
        )
        lines.append(
            f"  Window {row['active_window']['label']} ({row['active_window']['duration_label']})   "
            f"Source {row['session_source']}"
        )
        lines.append(
            f"  Stackability {row['stackability']['label']}   {row['stackability']['detail']}"
        )
        lines.append(
            f"  5D {_fmt_money(recent5.pnl, sign=True)}   "
            f"Delta 5D {_fmt_money(row['delta5'], sign=True)}   "
            f"10D {_fmt_money(recent10.pnl, sign=True)}"
        )
        lines.append(sub)

    for group in board["groups"]:
        lines.append("")
        lines.append(group["label"].upper())
        lines.append(sub)
        if not group["rows"]:
            lines.append("No matching strategies yet.")
            continue
        for row in group["rows"]:
            recent5 = row["recent5"]
            recent10 = row["recent10"]
            group_rank = row.get(f"group_rank_{row['session_slug']}", row.get("rank", ""))
            lines.append(
                f"#{group_rank}  {row['run_id']}  "
                f"Score {round(row['score'])}  Status {row['status']}  Source {row['session_source']}"
            )
            lines.append(
                f"  Window {row['active_window']['label']} ({row['active_window']['duration_label']})"
            )
            lines.append(
                f"  Stackability {row['stackability']['label']}   {row['stackability']['detail']}"
            )
            lines.append(
                f"  5D {_fmt_money(recent5.pnl, sign=True)}   "
                f"Delta 5D {_fmt_money(row['delta5'], sign=True)}   "
                f"10D {_fmt_money(recent10.pnl, sign=True)}"
            )
            lines.append(sub)

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
