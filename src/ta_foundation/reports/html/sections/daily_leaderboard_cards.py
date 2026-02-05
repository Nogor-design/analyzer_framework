from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap, BoundaryNorm

from ta_foundation.analysis.leaderboards import (
    build_daily_leaderboard_rows,
    parse_session_windows,
    pick_default_target_date,
)
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{(v * 100.0):.0f}%"


def _parse_target_date(options: Dict[str, Any], packages: Dict[str, AnalysisPackage]) -> Optional[date]:
    td = (options.get("target_date") or "").strip()
    if td:
        try:
            return date.fromisoformat(td)
        except Exception:
            pass
    return pick_default_target_date(packages)


def _make_heatmap_figure(
    bot_names: List[str],
    day_labels: List[str],
    states: np.ndarray,
    title: str,
) -> plt.Figure:
    fig = plt.figure(figsize=(max(9, 0.6 * len(day_labels)), max(5, 0.28 * len(bot_names))))
    ax = fig.add_subplot(111)

    cmap = ListedColormap(["#d04a4a", "#7a7a7a", "#39b36a"])
    norm = BoundaryNorm([-1.5, -0.5, 0.5, 1.5], cmap.N)

    ax.imshow(states, aspect="auto", cmap=cmap, norm=norm)

    ax.set_title(title)
    ax.set_xticks(range(len(day_labels)))
    ax.set_xticklabels(day_labels, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(len(bot_names)))
    ax.set_yticklabels(bot_names, fontsize=8)

    ax.set_xlabel("Trading day")
    ax.set_ylabel("Bot")

    ax.set_xticks(np.arange(-.5, len(day_labels), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(bot_names), 1), minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=0.3, alpha=0.12)
    ax.tick_params(which="minor", bottom=False, left=False)

    fig.tight_layout()
    return fig


def render_daily_leaderboard_cards(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    top_n = int(options.get("top_n", 8))
    hide_missing_cards = bool(options.get("hide_missing_cards", True))
    fallback_session = str(options.get("fallback_session_label", "Unclassified"))
    fallback_market = str(options.get("fallback_market_label", "Unknown"))

    lookback_days = int(options.get("lookback_days", 10))
    heatmap_top_bots = int(options.get("heatmap_top_bots", 30))

    windows = parse_session_windows(options)
    target = _parse_target_date(options, packages)

    if not target:
        return "<div><em>No target date could be inferred (no daily/trades timestamps found).</em></div>"

    rows = build_daily_leaderboard_rows(
        packages,
        target=target,
        windows=windows,
        fallback_session=fallback_session,
        fallback_market=fallback_market,
        lookback_days=lookback_days,
    )

    if hide_missing_cards:
        rows = [r for r in rows if r.get("card_uri")]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["session"], []).append(r)

    session_order = [w.label for w in windows]
    sessions_sorted = sorted(grouped.keys(), key=lambda s: (session_order.index(s) if s in session_order else 999, s))

    # Bar chart: sum of top-N day profits per session
    chart_vals = []
    for sess in sessions_sorted:
        items = grouped[sess]
        items_sorted = sorted(items, key=lambda x: (x.get("day_profit") is None, x.get("day_profit") or 0.0), reverse=True)
        items_sorted = items_sorted[:top_n] if top_n > 0 else items_sorted
        chart_vals.append(sum([(i.get("day_profit") or 0.0) for i in items_sorted]))

    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.bar(sessions_sorted, chart_vals)
    ax.set_title(f"Top-{top_n} Session PnL Totals — {target.isoformat()}")
    ax.set_ylabel("PnL")
    ax.tick_params(axis="x", labelrotation=15)
    chart_uri = fig_to_base64_png(fig)
    plt.close(fig)

    # Heatmap (bots x recent days)
    rows_for_heat = sorted(
        rows,
        key=lambda r: (
            r.get("wtd_profit") is None,
            (r.get("wtd_profit") or 0.0),
            (r.get("day_profit") or 0.0),
        ),
        reverse=True,
    )
    if heatmap_top_bots > 0:
        rows_for_heat = rows_for_heat[:heatmap_top_bots]

    day_axis: List[date] = []
    for r in rows_for_heat:
        if r.get("recent_days"):
            day_axis = list(r["recent_days"])
            break

    heatmap_uri = None
    if day_axis and rows_for_heat:
        day_labels = [d.strftime("%m/%d") for d in day_axis]
        bot_names = [r["run_id"] for r in rows_for_heat]

        states = np.zeros((len(bot_names), len(day_axis)), dtype=int)
        for i, r in enumerate(rows_for_heat):
            pnls: List[Optional[float]] = r.get("recent_pnls") or [None] * len(day_axis)
            pnls = (pnls[-len(day_axis):] if len(pnls) >= len(day_axis) else ([None] * (len(day_axis) - len(pnls)) + pnls))
            for j, v in enumerate(pnls):
                if v is None or v == 0:
                    states[i, j] = 0
                elif v > 0:
                    states[i, j] = 1
                else:
                    states[i, j] = -1

        hfig = _make_heatmap_figure(
            bot_names=bot_names,
            day_labels=day_labels,
            states=states,
            title=f"Bot Daily Outcomes (last {lookback_days} trading days, through {target.isoformat()})",
        )
        heatmap_uri = fig_to_base64_png(hfig)
        plt.close(hfig)

    css = """
    <style>
      .tf-daily { display: flex; flex-direction: column; gap: 16px; }
      .tf-panel {
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(255,255,255,0.03);
      }
      .tf-panel img { width: 100%; height: auto; display: block; }

      .tf-daily-session { padding: 12px; border-radius: 14px; background: rgba(255,255,255,0.03); }
      .tf-daily-session h3 { margin: 0 0 10px 0; font-size: 1.05rem; }
      .tf-daily-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 12px;
        align-items: start;
      }
      .tf-daily-item {
        border-radius: 14px;
        overflow: hidden;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-daily-img { width: 100%; height: auto; display: block; }
      .tf-daily-meta { padding: 10px; display: flex; flex-direction: column; gap: 6px; }
      .tf-daily-run { font-weight: 650; font-size: 0.92rem; display:flex; justify-content:space-between; gap:10px; }
      .tf-daily-pill {
        font-size: 0.78rem;
        opacity: 0.9;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.04);
        white-space: nowrap;
      }

      .tf-daily-kpis {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 8px;
      }
      .tf-daily-kpi {
        border-radius: 10px;
        padding: 8px;
        background: rgba(0,0,0,0.18);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-daily-k { font-size: 0.74rem; opacity: 0.85; }
      .tf-daily-v { font-size: 0.92rem; font-weight: 650; margin-top: 2px; }

      .tf-daily-subkpis { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

      .tf-daily-badge { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top: 2px; }

      .tf-daily-note { font-size: 0.82rem; opacity: 0.80; margin-top: 4px; }
      .tf-daily-missing { padding: 18px; font-size: 0.9rem; opacity: 0.75; }
    </style>
    """

    html: List[str] = [css, '<div class="tf-daily">']

    html.append('<div class="tf-panel">')
    html.append(f'<img src="{chart_uri}" alt="Daily session bar chart" />')
    html.append("</div>")

    if heatmap_uri:
        html.append('<div class="tf-panel">')
        html.append(f'<img src="{heatmap_uri}" alt="Bot daily outcome heatmap" />')
        html.append("</div>")

    for sess in sessions_sorted:
        items = grouped[sess]
        items_sorted = sorted(items, key=lambda x: (x.get("day_profit") is None, x.get("day_profit") or 0.0), reverse=True)
        if top_n > 0:
            items_sorted = items_sorted[:top_n]

        html.append('<section class="tf-daily-session">')
        html.append(f"<h3>{sess}</h3>")
        html.append('<div class="tf-daily-grid">')

        for r in items_sorted:
            run_id = r["run_id"]
            card_uri = r.get("card_uri")
            market = r.get("market") or fallback_market

            win_rate = r.get("win_rate")
            worst_day = r.get("worst_day")
            max_red_streak = r.get("max_red_streak")
            trade_days = r.get("trade_days", 0)

            trail_move_day = r.get("trail_move_day")
            max_dd_day = r.get("max_dd_day")

            html.append('<div class="tf-daily-item">')
            if card_uri:
                html.append(f'<img class="tf-daily-img" src="{card_uri}" alt="{run_id} Card" />')
            else:
                html.append('<div class="tf-daily-missing">Missing _Card.png</div>')

            html.append('<div class="tf-daily-meta">')
            html.append('<div class="tf-daily-run">')
            html.append(f'<div>{run_id}</div>')
            html.append(f'<div class="tf-daily-pill">{market}</div>')
            html.append("</div>")

            html.append('<div class="tf-daily-badge">')
            html.append(f'<div class="tf-daily-pill">Win% (last {trade_days}d): {_fmt_pct(win_rate)}</div>')
            html.append(f'<div class="tf-daily-pill">Worst: {_fmt_money(worst_day)}</div>')
            html.append(f'<div class="tf-daily-pill">Max red streak: {int(max_red_streak or 0)}</div>')
            html.append("</div>")

            # Main KPI grid (include trail move + max dd)
            html.append('<div class="tf-daily-kpis">')
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Day</div><div class="tf-daily-v">' + _fmt_money(r.get("day_profit")) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">WTD</div><div class="tf-daily-v">' + _fmt_money(r.get("wtd_profit")) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Last Week</div><div class="tf-daily-v">' + _fmt_money(r.get("last_week_profit")) + "</div></div>")
            html.append("</div>")

            # Secondary KPIs: MAE/MFE/ETD
            html.append('<div class="tf-daily-subkpis">')
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Avg MAE</div><div class="tf-daily-v">' + _fmt_num(r.get("avg_mae")) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Avg MFE</div><div class="tf-daily-v">' + _fmt_num(r.get("avg_mfe")) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Avg ETD</div><div class="tf-daily-v">' + _fmt_num(r.get("avg_etd")) + "</div></div>")
            html.append("</div>")

            # Prop trailing drawdown movement row
            html.append('<div class="tf-daily-subkpis">')
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Trail Move (Day)</div><div class="tf-daily-v">' + _fmt_money(trail_move_day) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Max DD (Day)</div><div class="tf-daily-v">' + _fmt_money(max_dd_day) + "</div></div>")
            html.append('<div class="tf-daily-kpi"><div class="tf-daily-k">Peak Runup (Day)</div><div class="tf-daily-v">' + _fmt_money(r.get("peak_runup_day")) + "</div></div>")
            html.append("</div>")

            html.append(f'<div class="tf-daily-note">Date: {target.isoformat()}</div>')
            html.append("</div></div>")

        html.append("</div></section>")

    html.append("</div>")
    return "\n".join(html)
