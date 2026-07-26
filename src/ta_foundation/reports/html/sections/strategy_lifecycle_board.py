from __future__ import annotations

from datetime import date
from html import escape
from typing import Any, Dict, Optional

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


def _window_cell(window: LifecycleWindow) -> str:
    return (
        f'<div class="tf-life-window">'
        f'<strong>{escape(window.label)}</strong>'
        f'<span>{escape(_fmt_money(window.pnl, sign=True))}</span>'
        f'<small>PF {escape(_fmt_pf(window.profit_factor))} | '
        f'Active {window.active_days}/{len(window.days)} | '
        f'DD {escape(_fmt_money(window.max_drawdown))}</small>'
        f'</div>'
    )


def render_strategy_lifecycle_board(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

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
        return "<div><em>No daily outcomes were available to build a lifecycle board.</em></div>"

    css = """
    <style>
      .tf-life { display:flex; flex-direction:column; gap:14px; }
      .tf-life-hero { padding:16px 18px; border:1px solid #d7dee8; background:#f8fafc; }
      .tf-life-title { font-size:24px; font-weight:900; color:#172033; }
      .tf-life-sub { margin-top:5px; color:#526070; font-size:13px; }
      .tf-life-grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:10px; }
      .tf-life-card { padding:12px; border:1px solid #d7dee8; background:#fff; }
      .tf-life-card-k { font-size:11px; text-transform:uppercase; color:#64748b; font-weight:800; }
      .tf-life-card-v { margin-top:4px; font-size:22px; font-weight:900; color:#0f172a; }
      .tf-life-table { width:100%; border-collapse:collapse; background:#fff; border:1px solid #d7dee8; }
      .tf-life-table th, .tf-life-table td { padding:10px; border-bottom:1px solid #e5eaf0; vertical-align:top; text-align:left; }
      .tf-life-table th { font-size:11px; text-transform:uppercase; color:#64748b; background:#f8fafc; }
      .tf-life-rank { font-weight:900; color:#0f172a; }
      .tf-life-run { font-weight:800; color:#172033; overflow-wrap:anywhere; }
      .tf-life-pill { display:inline-block; padding:3px 7px; border-radius:999px; font-size:11px; font-weight:800; border:1px solid #cbd5e1; }
      .tf-life-pill--trade_candidate { color:#166534; background:#dcfce7; border-color:#86efac; }
      .tf-life-pill--small_size_watch { color:#075985; background:#e0f2fe; border-color:#7dd3fc; }
      .tf-life-pill--paper_or_research { color:#854d0e; background:#fef9c3; border-color:#fde68a; }
      .tf-life-pill--pause_or_reduce { color:#9a3412; background:#ffedd5; border-color:#fdba74; }
      .tf-life-pill--do_not_trade { color:#991b1b; background:#fee2e2; border-color:#fca5a5; }
      .tf-life-window { display:flex; flex-direction:column; gap:2px; min-width:120px; }
      .tf-life-window strong { color:#0f172a; }
      .tf-life-window span { font-weight:800; }
      .tf-life-window small { color:#64748b; }
    </style>
    """

    summary = board["summary"]
    cards = (
        f'<div class="tf-life-grid">'
        f'<div class="tf-life-card"><div class="tf-life-card-k">Trade Candidates</div><div class="tf-life-card-v">{summary["trade_candidates"]}</div></div>'
        f'<div class="tf-life-card"><div class="tf-life-card-k">Small-Size Watch</div><div class="tf-life-card-v">{summary["small_size_watch"]}</div></div>'
        f'<div class="tf-life-card"><div class="tf-life-card-k">Pause / Reduce</div><div class="tf-life-card-v">{summary["pause_or_reduce"]}</div></div>'
        f'<div class="tf-life-card"><div class="tf-life-card-k">Do Not Trade</div><div class="tf-life-card-v">{summary["do_not_trade"]}</div></div>'
        f'</div>'
    )

    body = []
    for row in rows:
        best = row["best_window"]
        best_label = "-" if best is None else f"{best.label} { _fmt_money(best.pnl, sign=True) }"
        action = escape(str(row["tradability"]))
        body.append(
            "<tr>"
            f'<td class="tf-life-rank">#{row["rank"]}</td>'
            f'<td><div class="tf-life-run">{escape(str(row["run_id"]))}</div>'
            f'<div>Score {round(float(row["score"]))} | Best {escape(best_label)}</div></td>'
            f'<td><span class="tf-life-pill tf-life-pill--{action}">{action}</span>'
            f'<div>{escape(str(row["lifecycle_state"]))}</div></td>'
            f'<td>{escape(str(row["risk_category"]))}</td>'
            f'<td>{_window_cell(row["windows"]["2w"])}</td>'
            f'<td>{_window_cell(row["windows"]["3w"])}</td>'
            f'<td>{_window_cell(row["windows"]["4w"])}</td>'
            "</tr>"
        )

    return (
        css
        + '<div class="tf-life">'
        + '<div class="tf-life-hero">'
        + '<div class="tf-life-title">Strategy Lifecycle Board</div>'
        + f'<div class="tf-life-sub">As of {board["as_of"].isoformat()} | Risk budget {_fmt_money(board["risk_budget"])} | 2w/3w/4w in-favor scan.</div>'
        + '</div>'
        + cards
        + '<table class="tf-life-table"><thead><tr>'
        + '<th>Rank</th><th>Strategy</th><th>Action</th><th>Risk</th><th>2w</th><th>3w</th><th>4w</th>'
        + '</tr></thead><tbody>'
        + ''.join(body)
        + '</tbody></table></div>'
    )
