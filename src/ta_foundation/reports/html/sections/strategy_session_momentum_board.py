from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections._wlr_strip import render_wlr_strip
from ta_foundation.reports.session_momentum_board import build_strategy_session_momentum_board


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _metric_cls(value: float) -> str:
    return "tf-sess-metric tf-sess-metric--pos" if value > 0 else ("tf-sess-metric tf-sess-metric--neg" if value < 0 else "tf-sess-metric tf-sess-metric--flat")


def _status_badge(status: str) -> str:
    safe = escape(status)
    return f'<span class="tf-sess-status tf-sess-status--{safe}">{safe}</span>'


def _panel_variant(slug: str) -> str:
    safe = str(slug or "unclassified").strip().lower().replace("_", "-")
    return f"tf-sess-panel--{safe}"


def _render_group_table(rows: List[Dict[str, Any]], strip_days_iso: List[str], *, options: Dict[str, Any]) -> str:
    if not rows:
        return '<div class="tf-sess-empty">No matching strategies yet.</div>'

    out: List[str] = []
    out.append('<div class="tf-sess-table-wrap"><table class="tf-sess-table">')
    out.append(
        "<thead><tr>"
        "<th>Rank</th>"
        "<th>Strategy</th>"
        "<th>Recent</th>"
        "<th>Window</th>"
        "<th>Stackability</th>"
        "<th>5D</th>"
        "<th>Delta 5D</th>"
        "<th>10D</th>"
        "<th>Score</th>"
        "<th>Status</th>"
        "</tr></thead><tbody>"
    )
    for row in rows:
        strip_html = render_wlr_strip(
            row["run_id"],
            row["pkg"],
            strip_days_iso,
            box_px=int(options.get("strip_box_px", 10)),
            gap_px=int(options.get("strip_gap_px", 3)),
            show_legend=False,
        )
        recent5 = row["recent5"]
        recent10 = row["recent10"]
        active_window = row.get("active_window") or {}
        stackability = row.get("stackability") or {}
        group_rank = row.get(f"group_rank_{row['session_slug']}", row.get("overall_rank", row.get("rank", "")))
        out.append("<tr>")
        out.append(f"<td>#{int(group_rank)}</td>")
        out.append(
            "<td>"
            f'<div class="tf-sess-name">{escape(str(row["run_id"]))}</div>'
            f'<div class="tf-sess-sub">{escape(str(row["session_label"]))} | '
            f'10D win rate {escape(f"{(recent10.win_rate or 0.0) * 100.0:.0f}%" if recent10.win_rate is not None else "-")} | '
            f'5D active {recent5.active_days}/{len(recent5.days)}</div>'
            "</td>"
        )
        out.append(f"<td>{strip_html}</td>")
        out.append(
            "<td>"
            f'<div class="tf-sess-window">{escape(str(active_window.get("label") or "-"))}</div>'
            f'<div class="tf-sess-sub">{escape(str(active_window.get("duration_label") or "-"))}</div>'
            "</td>"
        )
        out.append(
            "<td>"
            f'<div class="tf-sess-stack tf-sess-stack--{escape(str(stackability.get("status") or "unknown"))}">{escape(str(stackability.get("label") or "-"))}</div>'
            f'<div class="tf-sess-sub">{escape(str(stackability.get("detail") or "-"))}</div>'
            "</td>"
        )
        out.append(f'<td><div class="{_metric_cls(recent5.pnl)}">{escape(_fmt_money(recent5.pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="{_metric_cls(row["delta5"])}">{escape(_fmt_money(row["delta5"], sign=True))}</div></td>')
        out.append(f'<td><div class="{_metric_cls(recent10.pnl)}">{escape(_fmt_money(recent10.pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="tf-sess-score">{round(float(row["score"]))}</div></td>')
        out.append(f"<td>{_status_badge(str(row['status']))}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def render_strategy_session_momentum_board(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    as_of = None
    as_of_raw = str(options.get("as_of_date") or "").strip()
    if as_of_raw:
        try:
            from datetime import date

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

    strip_days_iso = [d.isoformat() for d in board["strip_days"]]

    if not board["rows"]:
        return "<div><em>No daily outcomes were available to build a session momentum board.</em></div>"

    for row in board["rows"]:
        pkg = row["pkg"]
        md = getattr(pkg, "metadata", None) or {}
        derived = md.setdefault("derived", {})
        if not isinstance(derived.get("daily_outcomes"), dict):
            derived["daily_outcomes"] = derive_daily_outcomes_for_package(pkg)
        pkg.metadata = md

    css = """
    <style>
      .tf-sess {
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .tf-sess-hero {
        border-radius: 20px;
        padding: 20px 22px;
        background:
          radial-gradient(circle at top left, rgba(34,197,94,0.14), transparent 26%),
          radial-gradient(circle at top right, rgba(59,130,246,0.14), transparent 28%),
          linear-gradient(180deg, rgba(21, 28, 45, 0.96), rgba(12, 18, 31, 0.94));
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: 0 18px 42px rgba(0,0,0,0.18);
      }
      .tf-sess-title {
        font-size: 28px;
        font-weight: 950;
        letter-spacing: 0.2px;
        color: #f8fafc;
      }
      .tf-sess-subtitle {
        margin-top: 6px;
        font-size: 14px;
        color: rgba(226,232,240,0.80);
        line-height: 1.45;
      }
      .tf-sess-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 14px;
      }
      .tf-sess-pill {
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.04);
        color: rgba(241,245,249,0.88);
        font-size: 12px;
        font-weight: 800;
      }
      .tf-sess-panel {
        border-radius: 18px;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(15,23,42,0.78);
        position: relative;
      }
      .tf-sess-panel::before {
        content: "";
        position: absolute;
        inset: 0 0 auto 0;
        height: 4px;
        opacity: 0.95;
        background: linear-gradient(90deg, #60a5fa, #22c55e);
      }
      .tf-sess-panel-head {
        padding: 14px 16px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      }
      .tf-sess-panel--overall::before { background: linear-gradient(90deg, #4ade80, #facc15); }
      .tf-sess-panel--asia::before { background: linear-gradient(90deg, #38bdf8, #60a5fa); }
      .tf-sess-panel--london-early::before { background: linear-gradient(90deg, #4ade80, #22c55e); }
      .tf-sess-panel--london-late::before { background: linear-gradient(90deg, #2dd4bf, #22d3ee); }
      .tf-sess-panel--pre-market::before { background: linear-gradient(90deg, #f59e0b, #f97316); }
      .tf-sess-panel--overlap::before { background: linear-gradient(90deg, #fb7185, #f43f5e); }
      .tf-sess-panel--ny-open::before { background: linear-gradient(90deg, #ef4444, #f97316); }
      .tf-sess-panel--midday::before { background: linear-gradient(90deg, #a78bfa, #818cf8); }
      .tf-sess-panel--power-hour::before { background: linear-gradient(90deg, #facc15, #f59e0b); }
      .tf-sess-panel--unclassified::before { background: linear-gradient(90deg, #94a3b8, #64748b); }
      .tf-sess-panel-title {
        font-size: 18px;
        font-weight: 900;
        color: #f8fafc;
      }
      .tf-sess-panel-sub {
        margin-top: 4px;
        color: rgba(148,163,184,0.92);
        font-size: 12px;
      }
      .tf-sess-grid {
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .tf-sess-table-wrap {
        overflow-x: auto;
      }
      .tf-sess-table {
        width: 100%;
        border-collapse: collapse;
        min-width: 980px;
      }
      .tf-sess-table th,
      .tf-sess-table td {
        padding: 11px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        vertical-align: middle;
        color: #e5edf8;
        background: rgba(16, 24, 39, 0.94);
      }
      .tf-sess-table th {
        background: rgba(15,23,42,0.96);
        color: rgba(226,232,240,0.74);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        text-align: left;
      }
      .tf-sess-table tbody tr:nth-child(odd) td {
        background: rgba(69, 78, 97, 0.96);
      }
      .tf-sess-table tbody tr:nth-child(even) td {
        background: rgba(29, 37, 55, 0.98);
      }
      .tf-sess-table tbody tr:hover td {
        background: rgba(38, 52, 78, 0.98);
      }
      .tf-sess-name {
        font-size: 15px;
        font-weight: 850;
        color: #f8fafc;
        line-height: 1.15;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .tf-sess-sub {
        margin-top: 4px;
        font-size: 12px;
        color: rgba(201,214,232,0.86);
      }
      .tf-sess-window {
        font-size: 15px;
        font-weight: 850;
        color: #f8fafc;
        white-space: nowrap;
      }
      .tf-sess-stack {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.18);
        font-size: 12px;
        font-weight: 900;
        white-space: nowrap;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
      }
      .tf-sess-stack--safe { background: rgba(22,163,74,0.26); color: #d1fae5; }
      .tf-sess-stack--conflict { background: rgba(239,68,68,0.22); color: #fee2e2; }
      .tf-sess-stack--unknown { background: rgba(148,163,184,0.18); color: #e2e8f0; }
      .tf-sess-metric {
        font-size: 17px;
        font-weight: 900;
        white-space: nowrap;
      }
      .tf-sess-metric--pos { color: #4ade80; }
      .tf-sess-metric--neg { color: #f87171; }
      .tf-sess-metric--flat { color: #e2e8f0; }
      .tf-sess-score {
        font-size: 22px;
        font-weight: 950;
        color: #fff8e7;
        text-shadow: 0 1px 0 rgba(0,0,0,0.18);
      }
      .tf-sess-status {
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
      .tf-sess-status--Improving,
      .tf-sess-status--Strong { background: rgba(22,163,74,0.26); color: #d1fae5; }
      .tf-sess-status--Stable { background: rgba(59,130,246,0.24); color: #dbeafe; }
      .tf-sess-status--Cooling { background: rgba(245,158,11,0.24); color: #fef3c7; }
      .tf-sess-status--Recovering { background: rgba(14,165,233,0.24); color: #e0f2fe; }
      .tf-sess-status--Weak,
      .tf-sess-status--Inactive { background: rgba(239,68,68,0.22); color: #fee2e2; }
      .tf-sess-empty {
        padding: 16px;
        color: rgba(148,163,184,0.92);
        font-style: italic;
      }
    </style>
    """

    out: List[str] = [css, '<div class="tf-sess">']
    out.append(
        '<div class="tf-sess-hero">'
        '<div class="tf-sess-title">Strategy Session Momentum Board</div>'
        f'<div class="tf-sess-subtitle">The same favorability score as the main momentum board, but grouped by the time-family each strategy belongs to. '
        f'Primary grouping comes from the run naming convention like Dawn/Rise/Prime/Coil/War/Rage/Drift/Close, with a Start_Time fallback when the name does not match.</div>'
        '<div class="tf-sess-pills">'
        f'<div class="tf-sess-pill">As of {board["as_of"].isoformat()}</div>'
        f'<div class="tf-sess-pill">Overall top {len(board["overall_rows"])}</div>'
        f'<div class="tf-sess-pill">Session top {int(options.get("top_n_per_session", 5))}</div>'
        f'<div class="tf-sess-pill">Recent strip: last {len(strip_days_iso)} trading days ending {board["as_of"].isoformat()}</div>'
        f'<div class="tf-sess-pill">Stackability compares each row against the top {int(board["overlap_compare_top_n"])} overall bots</div>'
        "</div>"
        "</div>"
    )

    out.append(f'<div class="tf-sess-panel {_panel_variant("overall")}">')
    out.append(
        '<div class="tf-sess-panel-head">'
        '<div class="tf-sess-panel-title">Best All Around</div>'
        '<div class="tf-sess-panel-sub">Top strategies across every session family using the same current favorability score.</div>'
        "</div>"
    )
    out.append(_render_group_table(board["overall_rows"], strip_days_iso, options=options))
    out.append("</div>")

    out.append('<div class="tf-sess-grid">')
    for group in board["groups"]:
        token_hint = ""
        if group["single_token"] or group["multi_token"]:
            token_hint = f'Naming tokens: {group["single_token"]} / {group["multi_token"]}'
        else:
            token_hint = "No naming token match or time fallback."
        out.append(f'<div class="tf-sess-panel {_panel_variant(group["slug"])}">')
        out.append(
            '<div class="tf-sess-panel-head">'
            f'<div class="tf-sess-panel-title">{escape(group["label"])}</div>'
            f'<div class="tf-sess-panel-sub">{escape(token_hint)} | Matched strategies: {int(group["count"])}</div>'
            "</div>"
        )
        out.append(_render_group_table(group["rows"], strip_days_iso, options=options))
        out.append("</div>")
    out.append("</div></div>")

    return "\n".join(out)
