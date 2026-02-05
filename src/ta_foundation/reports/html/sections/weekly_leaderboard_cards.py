from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from ta_foundation.analysis.leaderboards import (
    build_weekly_leaderboard_rows,
    compute_daily_week_context,
    parse_session_windows,
    pick_default_target_date,
)
from ta_foundation.core.model import AnalysisPackage


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _parse_week_ending(options: Dict[str, Any], packages: Dict[str, AnalysisPackage]) -> Optional[date]:
    # options.week_ending: "YYYY-MM-DD" (preferred)
    we = (options.get("week_ending") or "").strip()
    if we:
        try:
            return date.fromisoformat(we)
        except Exception:
            pass
    # fallback: same default date logic as daily
    return pick_default_target_date(packages)


def render_weekly_leaderboard_cards(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    top_n = int(options.get("top_n", 12))
    hide_missing_cards = bool(options.get("hide_missing_cards", True))
    fallback_session = str(options.get("fallback_session_label", "Unclassified"))
    fallback_market = str(options.get("fallback_market_label", "Unknown"))

    windows = parse_session_windows(options)

    week_ending = _parse_week_ending(options, packages)
    if not week_ending:
        return "<div><em>No week could be inferred (no daily/trades timestamps found).</em></div>"

    ctx_dates = compute_daily_week_context(week_ending)
    week_start = ctx_dates["week_start"]
    week_end = ctx_dates["week_end"]

    rows = build_weekly_leaderboard_rows(
        packages,
        week_start=week_start,
        week_end=week_end,
        windows=windows,
        fallback_session=fallback_session,
        fallback_market=fallback_market,
    )

    if hide_missing_cards:
        rows = [r for r in rows if r.get("card_uri")]

    # Sort overall top-N by week profit
    rows_sorted = sorted(rows, key=lambda x: (x.get("week_profit") is None, x.get("week_profit") or 0.0), reverse=True)
    if top_n > 0:
        rows_sorted = rows_sorted[:top_n]

    css = """
    <style>
      .tf-weekly { display: flex; flex-direction: column; gap: 14px; }
      .tf-weekly-head {
        padding: 12px 14px;
        border-radius: 14px;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.06);
        display: flex;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }
      .tf-weekly-title { font-weight: 700; font-size: 1.02rem; }
      .tf-weekly-range { opacity: 0.85; font-size: 0.9rem; }
      .tf-weekly-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
        align-items: start;
      }
      .tf-weekly-item {
        border-radius: 14px;
        overflow: hidden;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-weekly-img { width: 100%; height: auto; display: block; }
      .tf-weekly-meta { padding: 10px; display:flex; flex-direction:column; gap:6px; }
      .tf-weekly-run { font-weight: 650; font-size: 0.92rem; display:flex; justify-content:space-between; gap:10px; }
      .tf-weekly-pill {
        font-size: 0.78rem;
        opacity: 0.9;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.04);
        white-space: nowrap;
      }
      .tf-weekly-kpi {
        border-radius: 10px;
        padding: 8px;
        background: rgba(0,0,0,0.18);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-weekly-k { font-size: 0.74rem; opacity: 0.85; }
      .tf-weekly-v { font-size: 0.98rem; font-weight: 750; margin-top: 2px; }
      .tf-weekly-note { font-size: 0.82rem; opacity: 0.80; }
      .tf-weekly-missing { padding: 18px; font-size: 0.9rem; opacity: 0.75; }
    </style>
    """

    html: List[str] = [css, '<div class="tf-weekly">']
    html.append('<div class="tf-weekly-head">')
    html.append(f'<div class="tf-weekly-title">Weekly Top Bots (Top {top_n})</div>')
    html.append(f'<div class="tf-weekly-range">{week_start.isoformat()} → {week_end.isoformat()}</div>')
    html.append("</div>")

    html.append('<div class="tf-weekly-grid">')
    for r in rows_sorted:
        run_id = r["run_id"]
        card_uri = r.get("card_uri")
        market = r.get("market") or fallback_market
        session = r.get("session") or fallback_session

        html.append('<div class="tf-weekly-item">')
        if card_uri:
            html.append(f'<img class="tf-weekly-img" src="{card_uri}" alt="{run_id} Card" />')
        else:
            html.append('<div class="tf-weekly-missing">Missing _Card.png</div>')

        html.append('<div class="tf-weekly-meta">')
        html.append('<div class="tf-weekly-run">')
        html.append(f"<div>{run_id}</div>")
        html.append(f'<div class="tf-weekly-pill">{market}</div>')
        html.append("</div>")
        html.append('<div class="tf-weekly-kpi"><div class="tf-weekly-k">Week PnL</div><div class="tf-weekly-v">' + _fmt_money(r.get("week_profit")) + "</div></div>")
        html.append(f'<div class="tf-weekly-note">{session}</div>')
        html.append("</div></div>")

    html.append("</div></div>")
    return "\n".join(html)
