from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.deployment_board import build_deployment_board_insight


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}%"


def export_deployment_board_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Deployment Board Insight",
) -> Path:
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    board_text_path = str(options.get("board_text_path") or "").strip()
    if not board_text_path:
        output_path.write_text("No deployment board text file was configured.\n", encoding="utf-8")
        return output_path

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    board = build_deployment_board_insight(
        packages,
        board_text_path=board_text_path,
        as_of=as_of,
        strip_days=int(options.get("strip_days", 5)),
    )

    rows = board["rows"]
    parsed = board["parsed"]
    summary = board["summary"]

    lines: list[str] = []
    sep = "=" * 80
    sub = "-" * 80

    lines.append(sep)
    lines.append(title)
    lines.append(sep)
    lines.append(f"As Of:         {board['as_of'].isoformat()}")
    lines.append(f"Board File:    {board['board_path']}")
    if parsed.get("current_regime_line"):
        lines.append(f"Regime Note:   {parsed['current_regime_line']}")
    if parsed.get("signer"):
        lines.append(f"Signer:        {parsed['signer']}")
    lines.append(
        "Coverage:      "
        f"Primary {len(parsed['sections']['primary'])} | "
        f"Secondary {len(parsed['sections']['secondary'])} | "
        f"Reserve {len(parsed['sections']['reserve'])}"
    )
    top_pick = summary.get("top_pick")
    if top_pick:
        lines.append(
            f"Top Pick:      {top_pick['run_id']} | {top_pick['tier'].title()} | "
            f"{top_pick['board_window']['label']} | Today "
            f"{_fmt_money(top_pick.get('today_profit'), sign=True) if top_pick.get('today_profit') is not None else (top_pick.get('today_status') or '-')}"
        )
    strongest = summary.get("strongest_support")
    if strongest:
        lines.append(
            f"Support:       {strongest['run_id']} | "
            f"5D {_fmt_money(strongest['recent5'].pnl, sign=True)} | "
            f"10D {_fmt_money(strongest['recent10'].pnl, sign=True)}"
        )
    lines.append("")
    lines.append(sub)

    for row in rows:
        lines.append(
            f"#{row['board_rank']}  {row['tier'].title()} #{row['tier_rank']}  {row['run_id']}"
        )
        lines.append(
            f"  Board Window {row['board_window']['label']} ({row['board_window']['duration_label']}) | "
            f"Trigger {_fmt_pct(row.get('trigger_odds'))} | Success {_fmt_pct(row.get('success_odds'))} | R/R {row.get('rr_text') or '-'}"
        )
        lines.append(
            f"  Session {row.get('session_label') or '-'} | "
            f"Today {_fmt_money(row.get('today_profit'), sign=True) if row.get('today_profit') is not None else (row.get('today_status') or '-')} | "
            f"5D {_fmt_money(row['recent5'].pnl if row.get('recent5') else None, sign=True)} | "
            f"10D {_fmt_money(row['recent10'].pnl if row.get('recent10') else None, sign=True)} | "
            f"Status {row.get('status') or '-'}"
        )
        stackability = row.get("stackability") or {}
        lines.append(
            f"  Stackability {stackability.get('label') or '-'} | {stackability.get('detail') or '-'}"
        )
        lines.append(f"  Reason {row.get('reason') or '-'}")
        lines.append(sub)

    if parsed.get("deployment_law"):
        lines.append("DEPLOYMENT LAW")
        lines.append(sub)
        for item in parsed["deployment_law"]:
            lines.append(f"- {item}")
        lines.append("")

    if parsed.get("summary_text"):
        lines.append("SUMMARY")
        lines.append(sub)
        lines.append(parsed["summary_text"])
        lines.append("")

    lines.append("ORIGINAL BOARD TEXT")
    lines.append(sub)
    lines.append(parsed.get("raw_text") or "")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path
