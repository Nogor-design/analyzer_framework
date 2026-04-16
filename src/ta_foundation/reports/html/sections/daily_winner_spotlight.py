from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.daily_winner_board import build_daily_winner_board
from ta_foundation.reports.html.sections._wlr_strip import render_wlr_strip


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100.0:.0f}%"


def _metric_cls(value: Optional[float]) -> str:
    val = float(value or 0.0)
    return "tf-dw-metric tf-dw-metric--pos" if val > 0 else ("tf-dw-metric tf-dw-metric--neg" if val < 0 else "tf-dw-metric tf-dw-metric--flat")


def _status_badge(status: str) -> str:
    safe = escape(status)
    return f'<span class="tf-dw-status tf-dw-status--{safe}">{safe}</span>'


def render_daily_winner_spotlight(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    target_date = None
    target_raw = str(options.get("target_date") or "").strip()
    if target_raw:
        try:
            from datetime import date

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
        return "<div><em>No target day with daily outcomes could be inferred.</em></div>"

    strip_days_iso = [d.isoformat() for d in board["strip_days"]]
    for row in board["rows"]:
        pkg = row["pkg"]
        md = getattr(pkg, "metadata", None) or {}
        derived = md.setdefault("derived", {})
        if not isinstance(derived.get("daily_outcomes"), dict):
            derived["daily_outcomes"] = derive_daily_outcomes_for_package(pkg)
        pkg.metadata = md

    winner = board["winner"]
    runner_up = board["runner_up"]
    summary = board["summary"]

    css = """
    <style>
      .tf-dw {
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .tf-dw-hero {
        border-radius: 22px;
        padding: 20px 22px;
        background:
          radial-gradient(circle at top right, rgba(34,197,94,0.16), transparent 28%),
          radial-gradient(circle at top left, rgba(250,204,21,0.12), transparent 24%),
          linear-gradient(180deg, rgba(18, 28, 46, 0.96), rgba(11, 18, 30, 0.95));
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: 0 18px 42px rgba(0,0,0,0.18);
      }
      .tf-dw-title {
        font-size: 28px;
        font-weight: 950;
        color: #f8fafc;
      }
      .tf-dw-subtitle {
        margin-top: 6px;
        color: rgba(226,232,240,0.82);
        font-size: 14px;
        line-height: 1.45;
      }
      .tf-dw-summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }
      .tf-dw-card {
        border-radius: 16px;
        padding: 14px 16px;
        background:
          linear-gradient(180deg, rgba(34, 44, 66, 0.98), rgba(22, 29, 43, 0.98));
        border: 1px solid rgba(100,116,139,0.28);
        box-shadow: 0 10px 24px rgba(15,23,42,0.12);
        position: relative;
        overflow: hidden;
      }
      .tf-dw-card::before {
        content: "";
        position: absolute;
        inset: 0 auto auto 0;
        width: 100%;
        height: 4px;
        opacity: 0.92;
      }
      .tf-dw-card--winner {
        background:
          radial-gradient(circle at top right, rgba(250,204,21,0.14), transparent 34%),
          linear-gradient(180deg, rgba(24, 58, 44, 0.98), rgba(19, 32, 28, 0.98));
        border-color: rgba(74, 222, 128, 0.34);
      }
      .tf-dw-card--winner::before {
        background: linear-gradient(90deg, #4ade80, #facc15);
      }
      .tf-dw-card--lead {
        background:
          radial-gradient(circle at top right, rgba(96,165,250,0.18), transparent 32%),
          linear-gradient(180deg, rgba(28, 46, 76, 0.98), rgba(20, 28, 44, 0.98));
        border-color: rgba(96,165,250,0.34);
      }
      .tf-dw-card--lead::before {
        background: linear-gradient(90deg, #60a5fa, #38bdf8);
      }
      .tf-dw-card--support {
        background:
          radial-gradient(circle at top right, rgba(45,212,191,0.18), transparent 32%),
          linear-gradient(180deg, rgba(19, 53, 60, 0.98), rgba(18, 29, 38, 0.98));
        border-color: rgba(45,212,191,0.34);
      }
      .tf-dw-card--support::before {
        background: linear-gradient(90deg, #2dd4bf, #22d3ee);
      }
      .tf-dw-card--session {
        background:
          radial-gradient(circle at top right, rgba(251,191,36,0.18), transparent 32%),
          linear-gradient(180deg, rgba(66, 46, 24, 0.98), rgba(35, 28, 18, 0.98));
        border-color: rgba(251,191,36,0.34);
      }
      .tf-dw-card--session::before {
        background: linear-gradient(90deg, #f59e0b, #f97316);
      }
      .tf-dw-card-k {
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.35px;
        color: rgba(191,219,254,0.88);
      }
      .tf-dw-card-v {
        margin-top: 8px;
        font-size: 20px;
        font-weight: 900;
        color: #f8fafc;
        line-height: 1.15;
        overflow-wrap: anywhere;
        text-shadow: 0 1px 0 rgba(0,0,0,0.18);
      }
      .tf-dw-card-sub {
        margin-top: 8px;
        color: rgba(226,232,240,0.86);
        font-size: 13px;
        line-height: 1.35;
      }
      .tf-dw-table-wrap {
        overflow-x: auto;
        border-radius: 18px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(15,23,42,0.78);
      }
      .tf-dw-table {
        width: 100%;
        border-collapse: collapse;
        min-width: 1120px;
      }
      .tf-dw-table th,
      .tf-dw-table td {
        padding: 12px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        vertical-align: middle;
        color: #e5edf8;
      }
      .tf-dw-table th {
        background: rgba(15,23,42,0.96);
        color: rgba(226,232,240,0.74);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        text-align: left;
      }
      .tf-dw-table tbody tr:nth-child(odd) td {
        background: rgba(69, 78, 97, 0.96);
      }
      .tf-dw-table tbody tr:nth-child(even) td {
        background: rgba(29, 37, 55, 0.98);
      }
      .tf-dw-table tbody tr:hover td {
        background: rgba(38, 52, 78, 0.98);
      }
      .tf-dw-name {
        font-size: 16px;
        font-weight: 850;
        color: #f8fafc;
        line-height: 1.12;
        overflow-wrap: anywhere;
      }
      .tf-dw-sub {
        margin-top: 4px;
        font-size: 12px;
        color: rgba(201,214,232,0.86);
      }
      .tf-dw-metric {
        font-size: 18px;
        font-weight: 900;
        white-space: nowrap;
      }
      .tf-dw-metric--pos { color: #4ade80; }
      .tf-dw-metric--neg { color: #f87171; }
      .tf-dw-metric--flat { color: #e2e8f0; }
      .tf-dw-status {
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
      .tf-dw-status--WIN { background: rgba(22,163,74,0.26); color: #d1fae5; }
      .tf-dw-status--LOSS { background: rgba(239,68,68,0.22); color: #fee2e2; }
      .tf-dw-status--NO_TRADE,
      .tf-dw-status--FLAT { background: rgba(148,163,184,0.18); color: #e2e8f0; }
      @media (max-width: 980px) {
        .tf-dw-summary {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 640px) {
        .tf-dw-summary {
          grid-template-columns: minmax(0, 1fr);
        }
      }
    </style>
    """

    out: List[str] = [css, '<div class="tf-dw">']
    out.append(
        '<div class="tf-dw-hero">'
        '<div class="tf-dw-title">Daily Winner Insight</div>'
        f'<div class="tf-dw-subtitle">Spotlight on the strongest strategy for {board["target_date"].isoformat()} with ranking context from the rest of the field. '
        'The table ranks by target-day PnL first, then recent support from 5D and 10D performance.</div>'
        "</div>"
    )

    out.append('<div class="tf-dw-summary">')
    out.append(
        '<div class="tf-dw-card tf-dw-card--winner">'
        '<div class="tf-dw-card-k">Winner</div>'
        f'<div class="tf-dw-card-v">{escape(str(winner["run_id"]))}</div>'
        f'<div class="tf-dw-card-sub">Day {_fmt_money(winner.get("day_profit"), sign=True)} | {escape(str(winner["session_label"]))} | {escape(str(winner["active_window"]["label"]))}</div>'
        '</div>'
    )
    if runner_up:
        out.append(
            '<div class="tf-dw-card tf-dw-card--lead">'
            '<div class="tf-dw-card-k">Lead Over #2</div>'
            f'<div class="tf-dw-card-v">{_fmt_money(summary.get("lead_amount"), sign=True)}</div>'
            f'<div class="tf-dw-card-sub">Runner-up: {escape(str(runner_up["run_id"]))} at {_fmt_money(runner_up.get("day_profit"), sign=True)}</div>'
            '</div>'
        )
    else:
        out.append(
            '<div class="tf-dw-card tf-dw-card--lead"><div class="tf-dw-card-k">Lead Over #2</div><div class="tf-dw-card-v">-</div><div class="tf-dw-card-sub">Only one qualifying strategy</div></div>'
        )
    strongest = summary.get("strongest_support")
    out.append(
        '<div class="tf-dw-card tf-dw-card--support">'
        '<div class="tf-dw-card-k">Strongest Recent Support</div>'
        f'<div class="tf-dw-card-v">{escape(str(strongest["run_id"])) if strongest else "-"}</div>'
        f'<div class="tf-dw-card-sub">5D {_fmt_money(strongest["recent5"].pnl, sign=True) if strongest else "-"} | 10D {_fmt_money(strongest["recent10"].pnl, sign=True) if strongest else "-"}</div>'
        '</div>'
    )
    dom_session = summary.get("dominant_session")
    out.append(
        '<div class="tf-dw-card tf-dw-card--session">'
        '<div class="tf-dw-card-k">Top-5 Session Tilt</div>'
        f'<div class="tf-dw-card-v">{escape(str(dom_session or "-"))}</div>'
        f'<div class="tf-dw-card-sub">{int(summary.get("dominant_session_count") or 0)} of the top 5 came from this session family</div>'
        '</div>'
    )
    out.append("</div>")

    out.append('<div class="tf-dw-table-wrap"><table class="tf-dw-table">')
    out.append(
        "<thead><tr>"
        "<th>Rank</th>"
        "<th>Strategy</th>"
        "<th>Target Day</th>"
        "<th>Gap To #1</th>"
        "<th>Recent</th>"
        "<th>5D</th>"
        "<th>10D</th>"
        "<th>Session</th>"
        "<th>Window</th>"
        "<th>Status</th>"
        "</tr></thead><tbody>"
    )
    for row in board["rows"]:
        strip_html = render_wlr_strip(
            row["run_id"],
            row["pkg"],
            strip_days_iso,
            box_px=int(options.get("strip_box_px", 12)),
            gap_px=int(options.get("strip_gap_px", 3)),
            show_legend=False,
        )
        winner_day = winner.get("day_profit") if winner else None
        gap = None
        if winner_day is not None and row.get("day_profit") is not None:
            gap = float(row["day_profit"] - winner_day)
        out.append("<tr>")
        out.append(f'<td>#{int(row["daily_rank"])}</td>')
        out.append(
            "<td>"
            f'<div class="tf-dw-name">{escape(str(row["run_id"]))}</div>'
            f'<div class="tf-dw-sub">10D win rate {_fmt_pct(row["recent10"].win_rate)} | 5D active {row["recent5"].active_days}/{len(row["recent5"].days)}</div>'
            "</td>"
        )
        out.append(f'<td><div class="{_metric_cls(row.get("day_profit"))}">{escape(_fmt_money(row.get("day_profit"), sign=True))}</div></td>')
        out.append(f'<td><div class="{_metric_cls(gap)}">{escape(_fmt_money(gap, sign=True))}</div></td>')
        out.append(f"<td>{strip_html}</td>")
        out.append(f'<td><div class="{_metric_cls(row["recent5"].pnl)}">{escape(_fmt_money(row["recent5"].pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="{_metric_cls(row["recent10"].pnl)}">{escape(_fmt_money(row["recent10"].pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="tf-dw-name">{escape(str(row["session_label"]))}</div><div class="tf-dw-sub">{escape(str(row["session_source"]))}</div></td>')
        out.append(f'<td><div class="tf-dw-name">{escape(str(row["active_window"]["label"]))}</div><div class="tf-dw-sub">{escape(str(row["active_window"]["duration_label"]))}</div></td>')
        out.append(f'<td>{_status_badge(str(row.get("day_status") or "-"))}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div></div>")
    return "\n".join(out)
