from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections._wlr_strip import render_wlr_strip
from ta_foundation.reports.momentum_board import build_strategy_momentum_board


def _fmt_money(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    prefix = ""
    if sign:
        prefix = "+" if value > 0 else ""
    return f"{prefix}{value:,.0f}"


def _fmt_days(active: int, total: int) -> str:
    return f"{active}/{total}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100.0:.0f}%"


def _summary_card(title: str, row: Optional[Dict[str, Any]], detail: str, accent: str) -> str:
    if not row:
        return (
            f'<div class="tf-momo-card tf-momo-card--{accent}">'
            f'<div class="tf-momo-card-k">{escape(title)}</div>'
            f'<div class="tf-momo-card-v">-</div>'
            f'<div class="tf-momo-card-sub">No qualifying strategy</div>'
            f"</div>"
        )

    run_id = escape(str(row["run_id"]))
    status = escape(str(row.get("status") or ""))
    return (
        f'<div class="tf-momo-card tf-momo-card--{accent}">'
        f'<div class="tf-momo-card-k">{escape(title)}</div>'
        f'<div class="tf-momo-card-v">{run_id}</div>'
        f'<div class="tf-momo-card-sub">{escape(detail)} | {status}</div>'
        f"</div>"
    )


def _sparkline(pnls: List[Optional[float]]) -> str:
    values = [0.0 if v is None else float(v) for v in pnls]
    scale = max((abs(v) for v in values), default=1.0) or 1.0
    bars: List[str] = []
    for v in pnls:
        if v is None:
            bars.append('<span class="tf-momo-bar tf-momo-bar--none" title="No trade"></span>')
            continue
        height = max(10, int(10 + (abs(float(v)) / scale) * 28))
        cls = "tf-momo-bar tf-momo-bar--up" if v > 0 else ("tf-momo-bar tf-momo-bar--down" if v < 0 else "tf-momo-bar tf-momo-bar--flat")
        bars.append(f'<span class="{cls}" style="height:{height}px" title="{_fmt_money(v, sign=True)}"></span>')
    return f'<div class="tf-momo-spark">{"".join(bars)}</div>'


def render_strategy_momentum_board(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    top_n = int(options.get("top_n", 24))
    strip_days = int(options.get("strip_days", 5))
    as_of_raw = str(options.get("as_of_date") or "").strip()
    as_of = None
    if as_of_raw:
        try:
            from datetime import date

            as_of = date.fromisoformat(as_of_raw)
        except Exception:
            as_of = None

    board = build_strategy_momentum_board(
        packages,
        as_of=as_of,
        strip_days=strip_days,
        top_n=top_n,
    )
    rows = board["rows"]
    summary = board["summary"]
    as_of_date = board["as_of"]
    strip_days_iso = [d.isoformat() for d in board["strip_days"]]

    if not rows:
        return "<div><em>No daily outcomes were available to build a momentum board.</em></div>"

    for row in rows:
        pkg = row["pkg"]
        md = getattr(pkg, "metadata", None) or {}
        derived = md.setdefault("derived", {})
        if not isinstance(derived.get("daily_outcomes"), dict):
            derived["daily_outcomes"] = derive_daily_outcomes_for_package(pkg)
        pkg.metadata = md

    css = """
    <style>
      .tf-momo {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .tf-momo-hero {
        border-radius: 18px;
        padding: 18px 20px;
        background:
          radial-gradient(circle at top right, rgba(83, 180, 122, 0.16), transparent 28%),
          linear-gradient(180deg, rgba(22, 30, 46, 0.96), rgba(14, 18, 28, 0.94));
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: 0 16px 40px rgba(0,0,0,0.18);
      }
      .tf-momo-title {
        font-size: 28px;
        font-weight: 900;
        letter-spacing: 0.2px;
        color: #f8fafc;
      }
      .tf-momo-subtitle {
        margin-top: 6px;
        color: rgba(226,232,240,0.80);
        font-size: 14px;
        line-height: 1.45;
      }
      .tf-momo-summary {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }
      .tf-momo-card {
        border-radius: 16px;
        padding: 14px 16px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(255,255,255,0.04);
        box-shadow: 0 10px 24px rgba(15,23,42,0.12);
        position: relative;
        overflow: hidden;
      }
      .tf-momo-card::before {
        content: "";
        position: absolute;
        inset: 0 auto auto 0;
        width: 100%;
        height: 4px;
        opacity: 0.92;
      }
      .tf-momo-card--best {
        background:
          radial-gradient(circle at top right, rgba(250,204,21,0.14), transparent 34%),
          linear-gradient(180deg, rgba(24, 58, 44, 0.98), rgba(19, 32, 28, 0.98));
        border-color: rgba(74, 222, 128, 0.34);
      }
      .tf-momo-card--best::before {
        background: linear-gradient(90deg, #4ade80, #facc15);
      }
      .tf-momo-card--rise {
        background:
          radial-gradient(circle at top right, rgba(96,165,250,0.18), transparent 32%),
          linear-gradient(180deg, rgba(28, 46, 76, 0.98), rgba(20, 28, 44, 0.98));
        border-color: rgba(96,165,250,0.34);
      }
      .tf-momo-card--rise::before {
        background: linear-gradient(90deg, #60a5fa, #38bdf8);
      }
      .tf-momo-card--steady {
        background:
          radial-gradient(circle at top right, rgba(45,212,191,0.18), transparent 32%),
          linear-gradient(180deg, rgba(19, 53, 60, 0.98), rgba(18, 29, 38, 0.98));
        border-color: rgba(45,212,191,0.34);
      }
      .tf-momo-card--steady::before {
        background: linear-gradient(90deg, #2dd4bf, #22d3ee);
      }
      .tf-momo-card--risk {
        background:
          radial-gradient(circle at top right, rgba(248,113,113,0.16), transparent 32%),
          linear-gradient(180deg, rgba(70, 32, 32, 0.98), rgba(34, 22, 22, 0.98));
        border-color: rgba(248,113,113,0.34);
      }
      .tf-momo-card--risk::before {
        background: linear-gradient(90deg, #f87171, #fb7185);
      }
      .tf-momo-card-k {
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.4px;
        text-transform: uppercase;
        color: rgba(226,232,240,0.74);
      }
      .tf-momo-card-v {
        margin-top: 8px;
        font-size: 20px;
        line-height: 1.15;
        font-weight: 900;
        color: #f8fafc;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .tf-momo-card-sub {
        margin-top: 8px;
        color: rgba(226,232,240,0.86);
        font-size: 13px;
      }
      .tf-momo-coverage {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 14px;
      }
      .tf-momo-pill {
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.2px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.05);
        color: rgba(241,245,249,0.88);
      }
      .tf-momo-table-wrap {
        overflow-x: auto;
        border-radius: 18px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(15,23,42,0.78);
      }
      .tf-momo-table {
        width: 100%;
        border-collapse: collapse;
        min-width: 1180px;
      }
      .tf-momo-table th,
      .tf-momo-table td {
        padding: 12px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        vertical-align: middle;
      }
      .tf-momo-table th {
        position: sticky;
        top: 0;
        background: rgba(15,23,42,0.96);
        color: rgba(226,232,240,0.76);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.45px;
        text-align: left;
      }
      .tf-momo-table tr:nth-child(even) td {
        background: rgba(255,255,255,0.02);
      }
      .tf-momo-rank {
        font-weight: 900;
        color: rgba(226,232,240,0.92);
        width: 52px;
      }
      .tf-momo-name {
        min-width: 200px;
      }
      .tf-momo-name-main {
        font-size: 16px;
        font-weight: 800;
        color: #f8fafc;
        line-height: 1.15;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .tf-momo-name-sub {
        margin-top: 6px;
        font-size: 12px;
        color: rgba(148,163,184,0.88);
      }
      .tf-momo-metric {
        font-size: 18px;
        font-weight: 900;
        white-space: nowrap;
      }
      .tf-momo-metric--pos { color: #4ade80; }
      .tf-momo-metric--neg { color: #f87171; }
      .tf-momo-metric--flat { color: #e2e8f0; }
      .tf-momo-submetric {
        font-size: 12px;
        color: rgba(148,163,184,0.92);
        margin-top: 4px;
      }
      .tf-momo-score {
        font-size: 22px;
        font-weight: 950;
        color: #f8fafc;
      }
      .tf-momo-status {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 900;
        letter-spacing: 0.25px;
        border: 1px solid rgba(255,255,255,0.12);
        white-space: nowrap;
      }
      .tf-momo-status--Improving,
      .tf-momo-status--Strong { background: rgba(22, 163, 74, 0.16); color: #86efac; }
      .tf-momo-status--Stable { background: rgba(59, 130, 246, 0.16); color: #93c5fd; }
      .tf-momo-status--Cooling { background: rgba(245, 158, 11, 0.16); color: #fcd34d; }
      .tf-momo-status--Recovering { background: rgba(14, 165, 233, 0.16); color: #7dd3fc; }
      .tf-momo-status--Weak,
      .tf-momo-status--Inactive { background: rgba(239, 68, 68, 0.14); color: #fca5a5; }
      .tf-momo-spark {
        display: flex;
        align-items: flex-end;
        gap: 4px;
        height: 42px;
        min-width: 120px;
      }
      .tf-momo-bar {
        flex: 1 1 0;
        min-width: 7px;
        border-radius: 999px 999px 3px 3px;
        opacity: 0.96;
      }
      .tf-momo-bar--up { background: linear-gradient(180deg, #4ade80, #15803d); }
      .tf-momo-bar--down { background: linear-gradient(180deg, #f87171, #b91c1c); }
      .tf-momo-bar--flat { background: linear-gradient(180deg, #cbd5e1, #64748b); }
      .tf-momo-bar--none { background: rgba(148,163,184,0.28); height: 10px; }
      @media (max-width: 980px) {
        .tf-momo-summary {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 640px) {
        .tf-momo-summary {
          grid-template-columns: minmax(0, 1fr);
        }
      }
    </style>
    """

    out: List[str] = [css, '<div class="tf-momo">']
    out.append(
        '<div class="tf-momo-hero">'
        '<div class="tf-momo-title">Strategy Momentum Board</div>'
        f'<div class="tf-momo-subtitle">Recent favorability ranked through {as_of_date.isoformat()}. '
        'Scores weight recent 5-day performance, 10-day support, improvement versus the prior 5 trading days, '
        'and how active the strategy has been lately.</div>'
        "</div>"
    )

    out.append('<div class="tf-momo-summary">')
    out.append(
        _summary_card(
            "Best Now",
            summary.get("best_now"),
            f'Score {_fmt_money(summary.get("best_now", {}).get("score") if summary.get("best_now") else None)}',
            "best",
        )
    )
    out.append(
        _summary_card(
            "Biggest Improver",
            summary.get("biggest_improver"),
            f'Delta 5D {_fmt_money(summary.get("biggest_improver", {}).get("delta5") if summary.get("biggest_improver") else None, sign=True)}',
            "rise",
        )
    )
    most_consistent = summary.get("most_consistent")
    out.append(
        _summary_card(
            "Most Consistent",
            most_consistent,
            (
                f'10D win rate {_fmt_pct(most_consistent["recent10"].win_rate)}'
                if most_consistent
                else "No qualifying strategy"
            ),
            "steady",
        )
    )
    cooling = summary.get("cooling_off")
    out.append(
        _summary_card(
            "Needs Review",
            cooling,
            (
                f'Delta 5D {_fmt_money(cooling["delta5"], sign=True)}'
                if cooling
                else "No qualifying strategy"
            ),
            "risk",
        )
    )
    out.append("</div>")

    out.append('<div class="tf-momo-coverage">')
    out.append(f'<div class="tf-momo-pill">Improving: {int(summary.get("improving_count") or 0)}</div>')
    out.append(f'<div class="tf-momo-pill">Strong: {int(summary.get("strong_count") or 0)}</div>')
    out.append(f'<div class="tf-momo-pill">Inactive: {int(summary.get("inactive_count") or 0)}</div>')
    out.append(f'<div class="tf-momo-pill">Recent strip: last {len(strip_days_iso)} trading days ending {as_of_date.isoformat()}</div>')
    out.append("</div>")

    out.append('<div class="tf-momo-table-wrap"><table class="tf-momo-table">')
    out.append(
        "<thead><tr>"
        "<th>Rank</th>"
        "<th>Strategy</th>"
        "<th>Recent</th>"
        "<th>5D</th>"
        "<th>Prev 5D</th>"
        "<th>Delta 5D</th>"
        "<th>10D</th>"
        "<th>20D</th>"
        "<th>Active</th>"
        "<th>Avg Daily</th>"
        "<th>Score</th>"
        "<th>Status</th>"
        "<th>Trend</th>"
        "</tr></thead><tbody>"
    )

    for row in rows:
        recent5 = row["recent5"]
        recent10 = row["recent10"]
        recent20 = row["recent20"]
        score_value = round(float(row["score"]))

        def metric_cls(value: float) -> str:
            return "tf-momo-metric tf-momo-metric--pos" if value > 0 else ("tf-momo-metric tf-momo-metric--neg" if value < 0 else "tf-momo-metric tf-momo-metric--flat")

        strip_html = render_wlr_strip(
            row["run_id"],
            row["pkg"],
            strip_days_iso,
            box_px=int(options.get("strip_box_px", 12)),
            gap_px=int(options.get("strip_gap_px", 3)),
            show_legend=False,
        )

        status = escape(str(row["status"]))
        out.append("<tr>")
        out.append(f'<td class="tf-momo-rank">#{int(row["rank"])}</td>')
        out.append(
            '<td class="tf-momo-name">'
            f'<div class="tf-momo-name-main">{escape(str(row["run_id"]))}</div>'
            f'<div class="tf-momo-name-sub">10D win rate {escape(_fmt_pct(recent10.win_rate))} | '
            f'Activity {escape(_fmt_days(recent10.active_days, len(recent10.days)))}</div>'
            "</td>"
        )
        out.append(f"<td>{strip_html}</td>")
        out.append(
            f'<td><div class="{metric_cls(recent5.pnl)}">{escape(_fmt_money(recent5.pnl, sign=True))}</div>'
            f'<div class="tf-momo-submetric">W{recent5.win_days} L{recent5.loss_days} N{recent5.no_trade_days}</div></td>'
        )
        out.append(f'<td><div class="{metric_cls(row["prev5"].pnl)}">{escape(_fmt_money(row["prev5"].pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="{metric_cls(row["delta5"])}">{escape(_fmt_money(row["delta5"], sign=True))}</div></td>')
        out.append(f'<td><div class="{metric_cls(recent10.pnl)}">{escape(_fmt_money(recent10.pnl, sign=True))}</div></td>')
        out.append(f'<td><div class="{metric_cls(recent20.pnl)}">{escape(_fmt_money(recent20.pnl, sign=True))}</div></td>')
        out.append(
            "<td>"
            f'<div class="tf-momo-metric tf-momo-metric--flat">{escape(_fmt_days(recent5.active_days, len(recent5.days)))}</div>'
            f'<div class="tf-momo-submetric">10D {escape(_fmt_days(recent10.active_days, len(recent10.days)))}</div>'
            "</td>"
        )
        out.append(f'<td><div class="{metric_cls(recent5.avg_daily or 0.0)}">{escape(_fmt_money(recent5.avg_daily, sign=True))}</div></td>')
        out.append(f'<td><div class="tf-momo-score">{score_value}</div></td>')
        out.append(f'<td><span class="tf-momo-status tf-momo-status--{status}">{status}</span></td>')
        out.append(f"<td>{_sparkline(recent10.pnls)}</td>")
        out.append("</tr>")

    out.append("</tbody></table></div></div>")
    return "\n".join(out)

