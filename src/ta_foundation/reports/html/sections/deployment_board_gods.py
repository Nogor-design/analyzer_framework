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


def _today_label(row: Dict[str, Any]) -> str:
    status = str(row.get("today_status") or "-").upper()
    if status == "NO_TRADE":
        return "No Trade"
    if status == "UNMATCHED":
        return "Unmatched"
    profit = row.get("today_profit")
    if profit is None:
        return status or "-"
    return _fmt_money(profit, sign=True)


def _today_class(row: Dict[str, Any]) -> str:
    status = str(row.get("today_status") or "").upper()
    if status == "NO_TRADE":
        return "tf-dbg-chip tf-dbg-chip--muted"
    profit = float(row.get("today_profit") or 0.0)
    if profit > 0:
        return "tf-dbg-chip tf-dbg-chip--win"
    if profit < 0:
        return "tf-dbg-chip tf-dbg-chip--loss"
    return "tf-dbg-chip tf-dbg-chip--muted"


def render_deployment_board_gods(ctx: Dict[str, Any]) -> str:
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

    tier_groups = {
        "primary": [row for row in rows if str(row.get("tier")) == "primary"],
        "secondary": [row for row in rows if str(row.get("tier")) == "secondary"],
        "reserve": [row for row in rows if str(row.get("tier")) == "reserve"],
    }

    top_pick = summary.get("top_pick")
    strongest_support = summary.get("strongest_support")

    css = """
    <style>
      .tf-dbg {
        display: flex;
        flex-direction: column;
        gap: 18px;
      }
      .tf-dbg-hero {
        border-radius: 24px;
        padding: 22px 24px;
        background:
          radial-gradient(circle at top left, rgba(251,146,60,0.18), transparent 24%),
          radial-gradient(circle at top right, rgba(250,204,21,0.14), transparent 26%),
          linear-gradient(135deg, rgba(28,21,13,0.98), rgba(19,24,39,0.98) 48%, rgba(60,25,14,0.95));
        border: 1px solid rgba(251,191,36,0.18);
        box-shadow: 0 20px 44px rgba(0,0,0,0.22);
      }
      .tf-dbg-title {
        font-size: 30px;
        font-weight: 950;
        color: #fff7ed;
      }
      .tf-dbg-subtitle {
        margin-top: 8px;
        color: rgba(255,237,213,0.84);
        font-size: 14px;
        line-height: 1.5;
        max-width: 980px;
      }
      .tf-dbg-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 16px;
      }
      .tf-dbg-pill,
      .tf-dbg-chip {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 800;
        border: 1px solid rgba(255,255,255,0.10);
      }
      .tf-dbg-pill {
        background: rgba(255,255,255,0.05);
        color: rgba(255,247,237,0.90);
      }
      .tf-dbg-chip--win { background: rgba(34,197,94,0.20); color: #dcfce7; border-color: rgba(34,197,94,0.30); }
      .tf-dbg-chip--loss { background: rgba(239,68,68,0.20); color: #fee2e2; border-color: rgba(239,68,68,0.30); }
      .tf-dbg-chip--muted { background: rgba(148,163,184,0.16); color: #e2e8f0; border-color: rgba(148,163,184,0.24); }
      .tf-dbg-summary {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }
      .tf-dbg-summary-card,
      .tf-dbg-panel,
      .tf-dbg-card {
        border-radius: 20px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(15,23,42,0.82);
        box-shadow: 0 14px 32px rgba(0,0,0,0.18);
      }
      .tf-dbg-summary-card {
        padding: 16px 18px;
        background:
          radial-gradient(circle at top right, rgba(250,204,21,0.10), transparent 32%),
          linear-gradient(180deg, rgba(36,24,16,0.98), rgba(20,28,42,0.98));
      }
      .tf-dbg-summary-k {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.35px;
        font-weight: 900;
        color: rgba(251,191,36,0.88);
      }
      .tf-dbg-summary-v {
        margin-top: 8px;
        font-size: 21px;
        font-weight: 950;
        color: #fff7ed;
        line-height: 1.15;
        overflow-wrap: anywhere;
      }
      .tf-dbg-summary-sub {
        margin-top: 8px;
        color: rgba(226,232,240,0.82);
        font-size: 13px;
        line-height: 1.4;
      }
      .tf-dbg-panel-head {
        padding: 16px 18px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      }
      .tf-dbg-panel-title {
        font-size: 20px;
        font-weight: 950;
        color: #fff7ed;
      }
      .tf-dbg-panel-sub {
        margin-top: 4px;
        color: rgba(226,232,240,0.78);
        font-size: 12px;
      }
      .tf-dbg-card-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 16px;
        padding: 16px;
      }
      .tf-dbg-card {
        overflow: hidden;
        background:
          radial-gradient(circle at top right, rgba(251,146,60,0.12), transparent 24%),
          linear-gradient(180deg, rgba(31,41,55,0.98), rgba(17,24,39,0.98));
      }
      .tf-dbg-card-inner {
        display: grid;
        grid-template-columns: 280px minmax(0, 1fr);
        gap: 0;
      }
      .tf-dbg-art {
        position: relative;
        min-height: 340px;
        background:
          radial-gradient(circle at center, rgba(251,146,60,0.22), transparent 40%),
          linear-gradient(180deg, rgba(18,18,18,0.95), rgba(10,10,10,0.98));
        border-right: 1px solid rgba(255,255,255,0.08);
      }
      .tf-dbg-art img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }
      .tf-dbg-art-fallback {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 18px;
        text-align: center;
        color: rgba(255,237,213,0.78);
        font-weight: 900;
        letter-spacing: 0.08em;
      }
      .tf-dbg-body {
        padding: 18px 20px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .tf-dbg-head {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: flex-start;
      }
      .tf-dbg-run {
        font-size: 27px;
        line-height: 1.05;
        font-weight: 950;
        color: #fff7ed;
        overflow-wrap: anywhere;
      }
      .tf-dbg-head-main {
        min-width: 0;
        flex: 1 1 auto;
      }
      .tf-dbg-head-badges {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 8px;
        flex: 0 1 320px;
      }
      .tf-dbg-meta {
        margin-top: 6px;
        color: rgba(251,191,36,0.86);
        font-size: 13px;
        font-weight: 800;
        letter-spacing: 0.02em;
      }
      .tf-dbg-metrics {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }
      .tf-dbg-metric-box {
        border-radius: 14px;
        padding: 12px 12px;
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
      }
      .tf-dbg-metric-k {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.34px;
        font-weight: 900;
        color: rgba(191,219,254,0.84);
      }
      .tf-dbg-metric-v {
        margin-top: 6px;
        font-size: 22px;
        font-weight: 950;
        color: #f8fafc;
      }
      .tf-dbg-metric-v--pos { color: #4ade80; }
      .tf-dbg-metric-v--neg { color: #f87171; }
      .tf-dbg-metric-v--flat { color: #e2e8f0; }
      .tf-dbg-metric-sub {
        margin-top: 4px;
        font-size: 12px;
        color: rgba(226,232,240,0.76);
      }
      .tf-dbg-strip {
        padding: 10px 12px;
        border-radius: 14px;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-dbg-reason {
        color: rgba(226,232,240,0.88);
        font-size: 14px;
        line-height: 1.55;
      }
      .tf-dbg-footer {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      }
      .tf-dbg-list {
        margin: 0;
        padding-left: 18px;
        color: rgba(226,232,240,0.88);
      }
      .tf-dbg-list li + li { margin-top: 8px; }
      .tf-dbg-raw {
        white-space: pre-wrap;
        font-family: Consolas, "SFMono-Regular", monospace;
        font-size: 12px;
        line-height: 1.55;
        color: rgba(226,232,240,0.88);
      }
      @media (max-width: 1180px) {
        .tf-dbg-summary,
        .tf-dbg-metrics,
        .tf-dbg-footer {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 860px) {
        .tf-dbg-card-inner {
          grid-template-columns: minmax(0, 1fr);
        }
        .tf-dbg-art {
          min-height: 280px;
          border-right: 0;
          border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .tf-dbg-head,
        .tf-dbg-summary,
        .tf-dbg-metrics,
        .tf-dbg-footer {
          grid-template-columns: minmax(0, 1fr);
        }
        .tf-dbg-head {
          flex-direction: column;
          align-items: stretch;
        }
        .tf-dbg-head-badges {
          justify-content: flex-start;
        }
      }
    </style>
    """

    out: List[str] = [css, '<div class="tf-dbg">']
    signer_pill = (
        f'<div class="tf-dbg-pill">Signed by {escape(str(parsed.get("signer")))}</div>'
        if parsed.get("signer")
        else ""
    )
    out.append(
        '<div class="tf-dbg-hero">'
        '<div class="tf-dbg-title">Deployment Board Pantheon</div>'
        f'<div class="tf-dbg-subtitle">{escape(parsed.get("current_regime_line") or parsed.get("summary_text") or "Visual recommendation board using the same deployment memo data, enriched with live package results and god art.")}</div>'
        '<div class="tf-dbg-pills">'
        f'<div class="tf-dbg-pill">As of {board["as_of"].isoformat()}</div>'
        f'<div class="tf-dbg-pill">Board file: {escape(board["board_path"].name)}</div>'
        f'<div class="tf-dbg-pill">Primary / Secondary / Reserve: {len(tier_groups["primary"])} / {len(tier_groups["secondary"])} / {len(tier_groups["reserve"])}</div>'
        f"{signer_pill}"
        "</div></div>"
    )

    board_bias: list[str] = []
    if parsed.get("breakout_pct") is not None:
        board_bias.append(f'Breakout {parsed["breakout_pct"]:.0f}%')
    if parsed.get("regression_pct") is not None:
        board_bias.append(f'Regression {parsed["regression_pct"]:.0f}%')

    out.append('<div class="tf-dbg-summary">')
    out.append(
        '<div class="tf-dbg-summary-card">'
        '<div class="tf-dbg-summary-k">First Chosen</div>'
        f'<div class="tf-dbg-summary-v">{escape(str(top_pick["run_id"])) if top_pick else "-"}</div>'
        f'<div class="tf-dbg-summary-sub">{escape(str((top_pick or {}).get("board_window", {}).get("label") or "-"))} | Today {escape(_today_label(top_pick or {}))}</div>'
        '</div>'
    )
    out.append(
        '<div class="tf-dbg-summary-card">'
        '<div class="tf-dbg-summary-k">Strongest Recent Support</div>'
        f'<div class="tf-dbg-summary-v">{escape(str(strongest_support["run_id"])) if strongest_support else "-"}</div>'
        f'<div class="tf-dbg-summary-sub">5D {_fmt_money((strongest_support.get("recent5").pnl if strongest_support and strongest_support.get("recent5") else None), sign=True)} | 10D {_fmt_money((strongest_support.get("recent10").pnl if strongest_support and strongest_support.get("recent10") else None), sign=True)}</div>'
        '</div>'
    )
    out.append(
        '<div class="tf-dbg-summary-card">'
        '<div class="tf-dbg-summary-k">Board Bias</div>'
        f'<div class="tf-dbg-summary-v">{escape(" | ".join(board_bias) if board_bias else "Narrative Board")}</div>'
        f'<div class="tf-dbg-summary-sub">{escape(parsed.get("summary_text") or "-")}</div>'
        '</div>'
    )
    out.append("</div>")

    tier_labels = {"primary": "Primary Gods", "secondary": "Secondary Support", "reserve": "Reserve Bench"}
    for tier_key in ("primary", "secondary", "reserve"):
        tier_rows = tier_groups[tier_key]
        out.append('<section class="tf-dbg-panel">')
        out.append(
            '<div class="tf-dbg-panel-head">'
            f'<div class="tf-dbg-panel-title">{escape(tier_labels[tier_key])}</div>'
            f'<div class="tf-dbg-panel-sub">{len(tier_rows)} recommendation{"s" if len(tier_rows) != 1 else ""} in this tier.</div>'
            '</div>'
        )
        out.append('<div class="tf-dbg-card-grid">')
        if not tier_rows:
            out.append('<div class="tf-dbg-panel-sub">No entries in this tier.</div>')
        for row in tier_rows:
            strip_html = (
                render_wlr_strip(
                    row["run_id"],
                    row["pkg"],
                    strip_days_iso,
                    box_px=int(options.get("strip_box_px", 12)),
                    gap_px=int(options.get("strip_gap_px", 3)),
                    show_legend=False,
                )
                if row.get("pkg") is not None
                else '<div class="tf-dbg-panel-sub">No matched package for recent strip.</div>'
            )
            image_html = (
                f'<img src="{row["run_image_uri"]}" alt="{escape(str(row["run_id"]))}" />'
                if row.get("run_image_uri")
                else f'<div class="tf-dbg-art-fallback">{escape(str(row["run_id"]))}</div>'
            )
            today_chip = _today_class(row)
            status_chip = "tf-dbg-chip tf-dbg-chip--muted"
            out.append(
                '<article class="tf-dbg-card">'
                '<div class="tf-dbg-card-inner">'
                '<div class="tf-dbg-art">'
                f'{image_html}'
                '</div>'
                '<div class="tf-dbg-body">'
                '<div class="tf-dbg-head">'
                '<div class="tf-dbg-head-main">'
                f'<div class="tf-dbg-run">{escape(str(row["run_id"]))}</div>'
                f'<div class="tf-dbg-meta">{escape(str(row.get("session_label") or "-"))} | {escape(str((row.get("board_window") or {}).get("label") or "-"))}</div>'
                '</div>'
                '<div class="tf-dbg-head-badges">'
                f'<span class="tf-dbg-chip tf-dbg-chip--muted">{escape(str(row["tier"]).title())} #{int(row["tier_rank"])}</span>'
                f'<span class="{today_chip}">Today {escape(_today_label(row))}</span>'
                f'<span class="{status_chip}">{escape(str(row.get("status") or "-"))}</span>'
                '</div>'
                '</div>'
                '<div class="tf-dbg-metrics">'
                '<div class="tf-dbg-metric-box">'
                '<div class="tf-dbg-metric-k">Trigger</div>'
                f'<div class="tf-dbg-metric-v">{_fmt_pct(row.get("trigger_odds"))}</div>'
                '<div class="tf-dbg-metric-sub">Odds from the board memo</div>'
                '</div>'
                '<div class="tf-dbg-metric-box">'
                '<div class="tf-dbg-metric-k">Success</div>'
                f'<div class="tf-dbg-metric-v">{_fmt_pct(row.get("success_odds"))}</div>'
                '<div class="tf-dbg-metric-sub">Memo confidence</div>'
                '</div>'
                '<div class="tf-dbg-metric-box">'
                '<div class="tf-dbg-metric-k">5D</div>'
                f'<div class="tf-dbg-metric-v {"tf-dbg-metric-v--pos" if (row.get("recent5").pnl if row.get("recent5") else 0) > 0 else ("tf-dbg-metric-v--neg" if (row.get("recent5").pnl if row.get("recent5") else 0) < 0 else "tf-dbg-metric-v--flat")}">{escape(_fmt_money(row.get("recent5").pnl if row.get("recent5") else None, sign=True))}</div>'
                '<div class="tf-dbg-metric-sub">Recent momentum</div>'
                '</div>'
                '<div class="tf-dbg-metric-box">'
                '<div class="tf-dbg-metric-k">10D</div>'
                f'<div class="tf-dbg-metric-v {"tf-dbg-metric-v--pos" if (row.get("recent10").pnl if row.get("recent10") else 0) > 0 else ("tf-dbg-metric-v--neg" if (row.get("recent10").pnl if row.get("recent10") else 0) < 0 else "tf-dbg-metric-v--flat")}">{escape(_fmt_money(row.get("recent10").pnl if row.get("recent10") else None, sign=True))}</div>'
                f'<div class="tf-dbg-metric-sub">R/R {escape(str(row.get("rr_text") or "-"))}</div>'
                '</div>'
                '</div>'
                f'<div class="tf-dbg-strip">{strip_html}</div>'
                f'<div class="tf-dbg-reason">{escape(str(row.get("reason") or "No reason text found."))}</div>'
                f'<div class="tf-dbg-panel-sub">Today status: {escape(str(row.get("today_status") or "-"))} | Overlap view: {escape(str((row.get("stackability") or {}).get("label") or "-"))}</div>'
                '</div>'
                '</div>'
                '</article>'
            )
        out.append('</div></section>')

    out.append('<div class="tf-dbg-footer">')
    out.append(
        '<section class="tf-dbg-panel">'
        '<div class="tf-dbg-panel-head"><div class="tf-dbg-panel-title">Deployment Law</div><div class="tf-dbg-panel-sub">Guardrails from the memo.</div></div>'
        '<div class="tf-dbg-card-grid">'
        f'{"<ul class=\"tf-dbg-list\">" + "".join(f"<li>{escape(str(item))}</li>" for item in parsed.get("deployment_law") or []) + "</ul>" if parsed.get("deployment_law") else "<div class=\"tf-dbg-panel-sub\">No deployment-law bullets were found.</div>"}'
        '</div></section>'
    )
    out.append(
        '<section class="tf-dbg-panel">'
        '<div class="tf-dbg-panel-head"><div class="tf-dbg-panel-title">Board Summary</div><div class="tf-dbg-panel-sub">Narrative takeaway and original memo.</div></div>'
        f'<div class="tf-dbg-card-grid"><div class="tf-dbg-reason">{escape(parsed.get("summary_text") or "-")}</div><div class="tf-dbg-raw">{escape(parsed.get("raw_text") or "")}</div></div>'
        '</section>'
    )
    out.append('</div></div>')
    return "\n".join(out)
