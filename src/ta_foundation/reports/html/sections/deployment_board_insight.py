from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from ta_foundation.reports.deployment_board import build_deployment_board_insight
from ta_foundation.reports.html.sections._wlr_strip import render_wlr_strip


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}%"


def _metric_cls(value: Optional[float]) -> str:
    val = float(value or 0.0)
    return "tf-db-metric tf-db-metric--pos" if val > 0 else ("tf-db-metric tf-db-metric--neg" if val < 0 else "tf-db-metric tf-db-metric--flat")


def _status_badge(status: str) -> str:
    safe = escape(str(status or "-"))
    return f'<span class="tf-db-status tf-db-status--{safe}">{safe}</span>'


def _today_cell(row: Dict[str, Any]) -> str:
    status = str(row.get("today_status") or "-").upper()
    if status == "NO_TRADE":
        return '<div class="tf-db-name">No Trade</div><div class="tf-db-sub">No fills today</div>'
    if status == "UNMATCHED":
        return '<div class="tf-db-name">-</div><div class="tf-db-sub">No matched package</div>'
    profit = row.get("today_profit")
    return (
        f'<div class="{_metric_cls(profit)}">{escape(_fmt_money(profit, sign=True))}</div>'
        f'<div class="tf-db-sub">{escape(status or "-")}</div>'
    )


def render_deployment_board_insight(ctx: Dict[str, Any]) -> str:
    options: Dict[str, Any] = ctx.get("options") or {}
    packages = ctx.get("packages", {}) or {}

    board_text_path = str(options.get("board_text_path") or "").strip()
    if not board_text_path:
        return "<div><em>No deployment board text file was configured. Set options.board_text_path in the report YAML.</em></div>"

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            from datetime import date

            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    try:
        board = build_deployment_board_insight(
            packages,
            board_text_path=board_text_path,
            as_of=as_of,
            strip_days=int(options.get("strip_days", 5)),
        )
    except FileNotFoundError:
        return f"<div><em>Deployment board file not found: {escape(board_text_path)}</em></div>"

    rows = board["rows"]
    parsed = board["parsed"]
    summary = board["summary"]
    strip_days_iso = [d.isoformat() for d in board["strip_days"]]

    if not rows:
        return "<div><em>No deployment recommendations were found in the board text.</em></div>"

    top_pick = summary.get("top_pick")
    strongest_support = summary.get("strongest_support")
    cleanest_stack = summary.get("cleanest_stack")
    earliest_window = summary.get("earliest_window")

    css = """
    <style>
      .tf-db {
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .tf-db-hero {
        border-radius: 22px;
        padding: 20px 22px;
        background:
          radial-gradient(circle at top right, rgba(34,197,94,0.16), transparent 28%),
          radial-gradient(circle at top left, rgba(250,204,21,0.12), transparent 24%),
          linear-gradient(180deg, rgba(18, 28, 46, 0.96), rgba(11, 18, 30, 0.95));
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: 0 18px 42px rgba(0,0,0,0.18);
      }
      .tf-db-title {
        font-size: 28px;
        font-weight: 950;
        color: #f8fafc;
      }
      .tf-db-subtitle {
        margin-top: 6px;
        color: rgba(226,232,240,0.82);
        font-size: 14px;
        line-height: 1.45;
      }
      .tf-db-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 14px;
      }
      .tf-db-pill {
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 800;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.05);
        color: rgba(241,245,249,0.88);
      }
      .tf-db-summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }
      .tf-db-card {
        border-radius: 16px;
        padding: 14px 16px;
        border: 1px solid rgba(100,116,139,0.28);
        box-shadow: 0 10px 24px rgba(15,23,42,0.12);
        position: relative;
        overflow: hidden;
      }
      .tf-db-card::before {
        content: "";
        position: absolute;
        inset: 0 auto auto 0;
        width: 100%;
        height: 4px;
        opacity: 0.92;
      }
      .tf-db-card--winner {
        background:
          radial-gradient(circle at top right, rgba(250,204,21,0.14), transparent 34%),
          linear-gradient(180deg, rgba(24, 58, 44, 0.98), rgba(19, 32, 28, 0.98));
        border-color: rgba(74, 222, 128, 0.34);
      }
      .tf-db-card--winner::before { background: linear-gradient(90deg, #4ade80, #facc15); }
      .tf-db-card--support {
        background:
          radial-gradient(circle at top right, rgba(45,212,191,0.18), transparent 32%),
          linear-gradient(180deg, rgba(19, 53, 60, 0.98), rgba(18, 29, 38, 0.98));
        border-color: rgba(45,212,191,0.34);
      }
      .tf-db-card--support::before { background: linear-gradient(90deg, #2dd4bf, #22d3ee); }
      .tf-db-card--board {
        background:
          radial-gradient(circle at top right, rgba(96,165,250,0.18), transparent 32%),
          linear-gradient(180deg, rgba(28, 46, 76, 0.98), rgba(20, 28, 44, 0.98));
        border-color: rgba(96,165,250,0.34);
      }
      .tf-db-card--board::before { background: linear-gradient(90deg, #60a5fa, #38bdf8); }
      .tf-db-card--stack {
        background:
          radial-gradient(circle at top right, rgba(251,191,36,0.18), transparent 32%),
          linear-gradient(180deg, rgba(66, 46, 24, 0.98), rgba(35, 28, 18, 0.98));
        border-color: rgba(251,191,36,0.34);
      }
      .tf-db-card--stack::before { background: linear-gradient(90deg, #f59e0b, #f97316); }
      .tf-db-card-k {
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.35px;
        color: rgba(191,219,254,0.88);
      }
      .tf-db-card-v {
        margin-top: 8px;
        font-size: 20px;
        font-weight: 900;
        color: #f8fafc;
        line-height: 1.15;
        overflow-wrap: anywhere;
      }
      .tf-db-card-sub {
        margin-top: 8px;
        color: rgba(226,232,240,0.86);
        font-size: 13px;
        line-height: 1.35;
      }
      .tf-db-table-wrap,
      .tf-db-panel {
        overflow-x: auto;
        border-radius: 18px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(15,23,42,0.78);
      }
      .tf-db-table {
        width: 100%;
        border-collapse: collapse;
        min-width: 1260px;
      }
      .tf-db-table th,
      .tf-db-table td {
        padding: 12px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        vertical-align: middle;
        color: #e5edf8;
      }
      .tf-db-table th {
        background: rgba(15,23,42,0.96);
        color: rgba(226,232,240,0.74);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        text-align: left;
      }
      .tf-db-table tbody tr:nth-child(odd) td { background: rgba(69, 78, 97, 0.96); }
      .tf-db-table tbody tr:nth-child(even) td { background: rgba(29, 37, 55, 0.98); }
      .tf-db-table tbody tr:hover td { background: rgba(38, 52, 78, 0.98); }
      .tf-db-name {
        font-size: 16px;
        font-weight: 850;
        color: #f8fafc;
        line-height: 1.12;
        overflow-wrap: anywhere;
      }
      .tf-db-sub {
        margin-top: 4px;
        font-size: 12px;
        color: rgba(201,214,232,0.86);
      }
      .tf-db-metric {
        font-size: 18px;
        font-weight: 900;
        white-space: nowrap;
      }
      .tf-db-metric--pos { color: #4ade80; }
      .tf-db-metric--neg { color: #f87171; }
      .tf-db-metric--flat { color: #e2e8f0; }
      .tf-db-status,
      .tf-db-stack {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.18);
        font-size: 12px;
        font-weight: 900;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
      }
      .tf-db-status--Improving,
      .tf-db-status--Strong,
      .tf-db-status--WIN { background: rgba(22,163,74,0.26); color: #d1fae5; }
      .tf-db-status--Stable { background: rgba(59,130,246,0.24); color: #dbeafe; }
      .tf-db-status--Cooling { background: rgba(245,158,11,0.24); color: #fef3c7; }
      .tf-db-status--Recovering { background: rgba(14,165,233,0.24); color: #e0f2fe; }
      .tf-db-status--NO_TRADE,
      .tf-db-status--UNMATCHED { background: rgba(148,163,184,0.18); color: #e2e8f0; }
      .tf-db-status--Weak,
      .tf-db-status--Inactive,
      .tf-db-status--Unmatched { background: rgba(148,163,184,0.18); color: #e2e8f0; }
      .tf-db-stack--safe { background: rgba(22,163,74,0.26); color: #d1fae5; }
      .tf-db-stack--conflict { background: rgba(239,68,68,0.22); color: #fee2e2; }
      .tf-db-stack--unknown { background: rgba(148,163,184,0.18); color: #e2e8f0; }
      .tf-db-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      }
      .tf-db-panel-head {
        padding: 14px 16px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      }
      .tf-db-panel-title {
        font-size: 18px;
        font-weight: 900;
        color: #f8fafc;
      }
      .tf-db-panel-sub {
        margin-top: 4px;
        color: rgba(148,163,184,0.92);
        font-size: 12px;
      }
      .tf-db-panel-body {
        padding: 16px;
      }
      .tf-db-reasons {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }
      .tf-db-reason {
        border-radius: 16px;
        padding: 14px 16px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(255,255,255,0.03);
      }
      .tf-db-reason-tier {
        font-size: 11px;
        font-weight: 900;
        text-transform: uppercase;
        letter-spacing: 0.35px;
        color: rgba(125,211,252,0.92);
      }
      .tf-db-reason-title {
        margin-top: 6px;
        font-size: 16px;
        font-weight: 900;
        color: #f8fafc;
        line-height: 1.15;
        overflow-wrap: anywhere;
      }
      .tf-db-reason-body {
        margin-top: 8px;
        color: rgba(226,232,240,0.84);
        font-size: 13px;
        line-height: 1.45;
      }
      .tf-db-list {
        margin: 0;
        padding-left: 18px;
        color: rgba(226,232,240,0.88);
      }
      .tf-db-list li + li { margin-top: 8px; }
      .tf-db-raw {
        white-space: pre-wrap;
        font-family: Consolas, "SFMono-Regular", monospace;
        font-size: 12px;
        line-height: 1.55;
        color: rgba(226,232,240,0.90);
      }
      @media (max-width: 1180px) {
        .tf-db-summary,
        .tf-db-reasons {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 900px) {
        .tf-db-grid {
          grid-template-columns: minmax(0, 1fr);
        }
      }
      @media (max-width: 680px) {
        .tf-db-summary,
        .tf-db-reasons {
          grid-template-columns: minmax(0, 1fr);
        }
      }
    </style>
    """

    out: List[str] = [css, '<div class="tf-db">']
    hero_note = escape(parsed.get("current_regime_line") or "Deployment board parsed from text recommendation file.")
    intro_note = escape(" ".join(parsed.get("intro_lines") or []))
    signer_pill = (
        f'<div class="tf-db-pill">Signed by {escape(str(parsed.get("signer")))}</div>'
        if parsed.get("signer")
        else ""
    )
    out.append(
        '<div class="tf-db-hero">'
        '<div class="tf-db-title">Deployment Board Insight</div>'
        f'<div class="tf-db-subtitle">{hero_note}'
        f'{" " + intro_note if intro_note else ""}</div>'
        '<div class="tf-db-pills">'
        f'<div class="tf-db-pill">As of {board["as_of"].isoformat()}</div>'
        f'<div class="tf-db-pill">Board file: {escape(board["board_path"].name)}</div>'
        f'<div class="tf-db-pill">Recommendations: {len(rows)}</div>'
        f'<div class="tf-db-pill">Primary / Secondary / Reserve: {len(parsed["sections"]["primary"])} / {len(parsed["sections"]["secondary"])} / {len(parsed["sections"]["reserve"])}</div>'
        f"{signer_pill}"
        "</div></div>"
    )

    posture = parsed.get("account_posture") or {}
    board_bias = []
    if parsed.get("breakout_pct") is not None:
        board_bias.append(f'Breakout {parsed["breakout_pct"]:.0f}%')
    if parsed.get("regression_pct") is not None:
        board_bias.append(f'Regression {parsed["regression_pct"]:.0f}%')
    if posture:
        board_bias.append(f'Accounts {int(posture.get("active") or 0)}/{int(posture.get("capacity") or 0)}')

    out.append('<div class="tf-db-summary">')
    out.append(
        '<div class="tf-db-card tf-db-card--winner">'
        '<div class="tf-db-card-k">Top Deployment</div>'
        f'<div class="tf-db-card-v">{escape(str(top_pick["run_id"])) if top_pick else "-"}</div>'
        f'<div class="tf-db-card-sub">{escape(str(top_pick["tier"]).title()) if top_pick else "-"} | '
        f'{escape(str((top_pick or {}).get("board_window", {}).get("label") or "-"))} | '
        f'Today {escape(_fmt_money((top_pick or {}).get("today_profit"), sign=True) if (top_pick or {}).get("today_profit") is not None else str((top_pick or {}).get("today_status") or "-"))}</div>'
        '</div>'
    )
    out.append(
        '<div class="tf-db-card tf-db-card--support">'
        '<div class="tf-db-card-k">Strongest Recent Support</div>'
        f'<div class="tf-db-card-v">{escape(str(strongest_support["run_id"])) if strongest_support else "-"}</div>'
        f'<div class="tf-db-card-sub">5D {_fmt_money((strongest_support.get("recent5").pnl if strongest_support and strongest_support.get("recent5") else None), sign=True)} | '
        f'10D {_fmt_money((strongest_support.get("recent10").pnl if strongest_support and strongest_support.get("recent10") else None), sign=True)}</div>'
        '</div>'
    )
    out.append(
        '<div class="tf-db-card tf-db-card--board">'
        '<div class="tf-db-card-k">Board Bias</div>'
        f'<div class="tf-db-card-v">{escape(" | ".join(board_bias) if board_bias else "Narrative Board")}</div>'
        f'<div class="tf-db-card-sub">{escape(parsed.get("summary_text") or parsed.get("current_regime_line") or "-")}</div>'
        '</div>'
    )
    out.append(
        '<div class="tf-db-card tf-db-card--stack">'
        '<div class="tf-db-card-k">Cleanest Early Stack</div>'
        f'<div class="tf-db-card-v">{escape(str(cleanest_stack["run_id"])) if cleanest_stack else (escape(str(earliest_window["run_id"])) if earliest_window else "-")}</div>'
        f'<div class="tf-db-card-sub">{escape(str((cleanest_stack or earliest_window or {}).get("stackability", {}).get("label") or (earliest_window or {}).get("board_window", {}).get("label") or "-"))}</div>'
        '</div>'
    )
    out.append("</div>")

    out.append('<div class="tf-db-table-wrap"><table class="tf-db-table">')
    out.append(
        "<thead><tr>"
        "<th>Rank</th>"
        "<th>Tier</th>"
        "<th>Strategy</th>"
        "<th>Board Window</th>"
        "<th>R/R</th>"
        "<th>Today</th>"
        "<th>Recent</th>"
        "<th>5D</th>"
        "<th>10D</th>"
        "<th>Session</th>"
        "<th>Status</th>"
        "</tr></thead><tbody>"
    )
    for row in rows:
        if row.get("pkg") is not None:
            strip_html = render_wlr_strip(
                row["run_id"],
                row["pkg"],
                strip_days_iso,
                box_px=int(options.get("strip_box_px", 12)),
                gap_px=int(options.get("strip_gap_px", 3)),
                show_legend=False,
            )
        else:
            strip_html = '<div class="tf-db-sub">No matched package</div>'

        out.append("<tr>")
        out.append(f'<td>#{int(row["board_rank"])}</td>')
        out.append(f'<td><div class="tf-db-name">{escape(str(row["tier"]).title())}</div><div class="tf-db-sub">#{int(row["tier_rank"])}</div></td>')
        out.append(
            "<td>"
            f'<div class="tf-db-name">{escape(str(row["run_id"]))}</div>'
            f'<div class="tf-db-sub">{escape(row.get("reason") or "-")}</div>'
            "</td>"
        )
        out.append(
            "<td>"
            f'<div class="tf-db-name">{escape(str((row.get("board_window") or {}).get("label") or "-"))}</div>'
            f'<div class="tf-db-sub">{escape(str((row.get("board_window") or {}).get("duration_label") or "-"))}</div>'
            "</td>"
        )
        out.append(f'<td><div class="tf-db-name">{escape(str(row.get("rr_text") or "-"))}</div></td>')
        out.append(f"<td>{_today_cell(row)}</td>")
        out.append(f"<td>{strip_html}</td>")
        out.append(f'<td><div class="{_metric_cls(row.get("recent5").pnl if row.get("recent5") else None)}">{escape(_fmt_money(row.get("recent5").pnl if row.get("recent5") else None, sign=True))}</div></td>')
        out.append(f'<td><div class="{_metric_cls(row.get("recent10").pnl if row.get("recent10") else None)}">{escape(_fmt_money(row.get("recent10").pnl if row.get("recent10") else None, sign=True))}</div></td>')
        out.append(
            "<td>"
            f'<div class="tf-db-name">{escape(str(row.get("session_label") or "-"))}</div>'
            f'<div class="tf-db-sub">{escape(str(row.get("session_source") or "-"))}</div>'
            "</td>"
        )
        out.append(f'<td>{_status_badge(str(row.get("status") or "-"))}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div>")

    out.append('<div class="tf-db-panel">')
    out.append(
        '<div class="tf-db-panel-head">'
        '<div class="tf-db-panel-title">Per-Bot Reasons</div>'
        '<div class="tf-db-panel-sub">The original ranking narrative, preserved per recommendation.</div>'
        '</div>'
    )
    out.append('<div class="tf-db-panel-body"><div class="tf-db-reasons">')
    for row in rows:
        out.append(
            '<div class="tf-db-reason">'
            f'<div class="tf-db-reason-tier">{escape(str(row["tier"]).title())} #{int(row["tier_rank"])}</div>'
            f'<div class="tf-db-reason-title">{escape(str(row["run_id"]))}</div>'
            f'<div class="tf-db-sub">Window {escape(str((row.get("board_window") or {}).get("label") or "-"))} | '
            f'Trigger {_fmt_pct(row.get("trigger_odds"))} | Success {_fmt_pct(row.get("success_odds"))} | '
            f'R/R {escape(str(row.get("rr_text") or "-"))}</div>'
            f'<div class="tf-db-sub">Today {escape(str(row.get("today_status") or "-"))} '
            f'{escape(_fmt_money(row.get("today_profit"), sign=True)) if row.get("today_profit") is not None else ""} | '
            f'{escape(str((row.get("stackability") or {}).get("label") or "-"))}</div>'
            f'<div class="tf-db-reason-body">{escape(str(row.get("reason") or "No reason text found."))}</div>'
            '</div>'
        )
    out.append("</div></div></div>")

    out.append('<div class="tf-db-grid">')
    out.append('<div class="tf-db-panel">')
    out.append(
        '<div class="tf-db-panel-head">'
        '<div class="tf-db-panel-title">Deployment Law</div>'
        '<div class="tf-db-panel-sub">Guardrails copied from the text board.</div>'
        '</div>'
    )
    out.append('<div class="tf-db-panel-body">')
    if parsed.get("deployment_law"):
        out.append('<ul class="tf-db-list">')
        for item in parsed["deployment_law"]:
            out.append(f"<li>{escape(str(item))}</li>")
        out.append("</ul>")
    else:
        out.append('<div class="tf-db-sub">No deployment-law bullets were found.</div>')
    out.append("</div></div>")

    out.append('<div class="tf-db-panel">')
    out.append(
        '<div class="tf-db-panel-head">'
        '<div class="tf-db-panel-title">Board Summary</div>'
        '<div class="tf-db-panel-sub">Closing narrative from the recommendation file.</div>'
        '</div>'
    )
    out.append(f'<div class="tf-db-panel-body"><div class="tf-db-card-sub">{escape(parsed.get("summary_text") or "No summary text found.")}</div></div></div>')
    out.append("</div>")

    out.append('<div class="tf-db-panel">')
    out.append(
        '<div class="tf-db-panel-head">'
        '<div class="tf-db-panel-title">Original Board Text</div>'
        '<div class="tf-db-panel-sub">Full source text used to build this report.</div>'
        '</div>'
    )
    out.append(f'<div class="tf-db-panel-body"><div class="tf-db-raw">{escape(parsed.get("raw_text") or "")}</div></div></div>')
    out.append("</div>")

    return "\n".join(out)
