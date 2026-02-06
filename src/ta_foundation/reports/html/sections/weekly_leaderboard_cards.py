from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt

from ta_foundation.analysis.prop_trailing import compute_prop_trailing_states, PropDaySnapshot
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png


# Weekly strip is Sun->Fri (drop Saturday)
WEEKDAY_LABELS_6 = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _parse_week_ending(options: Dict[str, Any], packages: Dict[str, AnalysisPackage]) -> Optional[date]:
    we = (options.get("week_ending") or "").strip()
    if we:
        try:
            return date.fromisoformat(we)
        except Exception:
            pass

    # fallback: infer from packages (most recent trades exit date)
    best = None
    for pkg in packages.values():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue
        col = None
        for c in ("Exit time", "Exit Time", "exit_time"):
            if c in trades.columns:
                col = c
                break
        if not col:
            continue
        try:
            import pandas as pd

            s = pd.to_datetime(trades[col], errors="coerce").dropna()
            if len(s):
                cand = s.dt.date.max()
                if best is None or cand > best:
                    best = cand
        except Exception:
            continue
    return best


def _week_sunday_start(d: date) -> date:
    # Python weekday: Mon=0..Sun=6. We want Sunday start.
    delta = (d.weekday() + 1) % 7
    return d - timedelta(days=delta)


def _prop_cfg(options: Dict[str, Any]) -> Tuple[float, float]:
    start_balance = float(options.get("starting_balance", 50000))
    trailing_dd = float(options.get("trailing_dd", 2500))
    return start_balance, trailing_dd


def _pnl_for_snapshot(snap: Optional[PropDaySnapshot]) -> Optional[float]:
    if snap is None:
        return None
    return float(snap.equity_close - snap.equity_open)


def _tile_bg(pnl: Optional[float], max_abs: float) -> str:
    if pnl is None:
        return "background: rgba(140,140,140,0.14);"
    if max_abs <= 0:
        max_abs = 1.0
    intensity = min(1.0, abs(float(pnl)) / max_abs)
    alpha = 0.16 + 0.56 * intensity  # 0.16..0.72
    if pnl >= 0:
        return f"background: rgba(57,179,106,{alpha:.3f});"
    return f"background: rgba(208,74,74,{alpha:.3f});"


def _mini_prop_chart(
    labels: List[str],
    snaps_by_day: List[Optional[PropDaySnapshot]],
    *,
    baseline_equity: float,
    title: str,
) -> Optional[str]:
    x = list(range(len(labels)))
    cum_profit: List[float] = []
    trail_rel: List[float] = []
    have_any = False

    for s in snaps_by_day:
        if s is None:
            cum_profit.append(float("nan"))
            trail_rel.append(float("nan"))
            continue
        have_any = True
        cum_profit.append(s.equity_close - baseline_equity)
        trail_rel.append(s.trail_close - baseline_equity)

    if not have_any:
        return None

    fig = plt.figure(figsize=(6.6, 2.4))
    ax = fig.add_subplot(111)

    ax.plot(x, cum_profit, label="Cum Profit")
    ax.plot(x, trail_rel, label="Trail Line")

    ax.set_title(title, fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()

    uri = fig_to_base64_png(fig)
    plt.close(fig)
    return uri


def _last_non_none(snaps: List[Optional[PropDaySnapshot]]) -> Optional[PropDaySnapshot]:
    for s in reversed(snaps):
        if s is not None:
            return s
    return None


def _week_profit(snaps: List[Optional[PropDaySnapshot]]) -> float:
    tot = 0.0
    for s in snaps:
        if s is None:
            continue
        tot += (s.equity_close - s.equity_open)
    return float(tot)


def render_weekly_leaderboard_cards(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    top_n = int(options.get("top_n", 12))
    hide_missing_cards = bool(options.get("hide_missing_cards", True))
    show_chart = bool(options.get("show_chart", True))

    week_ending = _parse_week_ending(options, packages)
    if not week_ending:
        return "<div><em>No week could be inferred (no trades timestamps found).</em></div>"

    week_start = _week_sunday_start(week_ending)

    # Current week (Sun->Fri)
    week_days = [week_start + timedelta(days=i) for i in range(6)]
    labels = WEEKDAY_LABELS_6

    # Previous week (Sun->Fri)
    prev_week_start = week_start - timedelta(days=7)
    prev_week_days = [prev_week_start + timedelta(days=i) for i in range(6)]

    start_balance, trailing_dd = _prop_cfg(options)

    bot_rows: List[Dict[str, Any]] = []
    all_pnls: List[float] = []

    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        if not pkg:
            continue

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg else {}
        card_uri = derived.get("card_image_uri")

        if hide_missing_cards and not card_uri:
            continue

        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue

        states = compute_prop_trailing_states(
            trades,
            start_balance=start_balance,
            trailing_dd=trailing_dd,
        )
        cont = states.get("continuous", {}) or {}

        snaps_week: List[Optional[PropDaySnapshot]] = [cont.get(d) for d in week_days]
        snaps_prev: List[Optional[PropDaySnapshot]] = [cont.get(d) for d in prev_week_days]

        # current week aggregates
        have_any = any(s is not None for s in snaps_week)
        if not have_any:
            continue

        week_profit = _week_profit(snaps_week)
        min_buffer_week = None
        trail_move_week = 0.0
        for s in snaps_week:
            if s is None:
                continue
            if min_buffer_week is None or s.min_buffer < min_buffer_week:
                min_buffer_week = s.min_buffer
            trail_move_week += s.trail_move_today

            pnl_d = _pnl_for_snapshot(s)
            if pnl_d is not None:
                all_pnls.append(float(pnl_d))

        last_s = _last_non_none(snaps_week)
        buffer_eow = (last_s.buffer_close if last_s is not None else None)

        # previous week metrics (may be absent)
        prev_have_any = any(s is not None for s in snaps_prev)
        prev_week_profit = _week_profit(snaps_prev) if prev_have_any else None
        prev_last = _last_non_none(snaps_prev)
        prev_buffer_eow = (prev_last.buffer_close if prev_last is not None else None)

        bot_rows.append(
            {
                "run_id": run_id,
                "card_uri": card_uri,
                "snaps_week": snaps_week,
                "week_profit": week_profit,
                "min_buffer_week": min_buffer_week,
                "trail_move_week": trail_move_week,
                "buffer_eow": buffer_eow,
                "prev_week_profit": prev_week_profit,
                "prev_buffer_eow": prev_buffer_eow,
            }
        )

    # Rank: safest first (higher min buffer), then profit
    bot_rows.sort(
        key=lambda r: (
            r.get("min_buffer_week") is None,
            -(r.get("min_buffer_week") or 0.0),
            -(r.get("week_profit") or 0.0),
        )
    )
    if top_n > 0:
        bot_rows = bot_rows[:top_n]

    max_abs = max([abs(x) for x in all_pnls], default=1.0)

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
      .tf-weekly-title { font-weight: 750; font-size: 1.02rem; }
      .tf-weekly-range { opacity: 0.85; font-size: 0.9rem; }

      .tf-weekly-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 12px;
        align-items: start;
      }

      .tf-weekly-card {
        border-radius: 16px;
        overflow: hidden;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-weekly-img { width: 100%; height: auto; display: block; }
      .tf-weekly-meta { padding: 10px 12px; display:flex; flex-direction:column; gap:10px; }

      .tf-weekly-top {
        display:flex; justify-content:space-between; gap:10px; align-items:flex-start;
      }
      .tf-weekly-name { font-weight: 750; font-size: 0.96rem; }
      .tf-weekly-pill {
        font-size: 0.78rem;
        opacity: 0.92;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.04);
        white-space: nowrap;
      }

      .tf-weekly-kpis {
        display:grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 8px;
      }
      .tf-weekly-kpi {
        border-radius: 12px;
        padding: 8px;
        background: rgba(0,0,0,0.18);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-weekly-k { font-size: 0.74rem; opacity: 0.85; }
      .tf-weekly-v { font-size: 0.95rem; font-weight: 750; margin-top: 2px; }

      .tf-week-strip {
        display:grid;
        grid-template-columns: repeat(6, 1fr);
        gap: 6px;
      }

      .tf-day-tile {
        position: relative;
        border-radius: 12px;
        padding: 8px 8px;
        border: 1px solid rgba(255,255,255,0.08);
        min-height: 54px;
      }

      .tf-day-tile.tf-ratchet {
        border: 2px solid rgba(255,255,255,0.28);
        box-shadow: 0 0 0 2px rgba(255,255,255,0.06) inset;
      }

      .tf-ratchet-badge {
        position: absolute;
        top: 6px;
        right: 6px;
        font-size: 0.72rem;
        font-weight: 800;
        opacity: 0.92;
        padding: 2px 7px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.18);
        background: rgba(0,0,0,0.22);
        max-width: 80px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .tf-day-dow { font-size: 0.74rem; opacity: 0.9; margin-bottom: 3px; }
      .tf-day-pnl { font-size: 0.92rem; font-weight: 800; }
      .tf-day-sub { font-size: 0.72rem; opacity: 0.85; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

      .tf-mini-chart {
        border-radius: 14px;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(255,255,255,0.02);
      }
      .tf-mini-chart img { width: 100%; height: auto; display: block; }
    </style>
    """

    html: List[str] = [css, '<div class="tf-weekly">']
    html.append('<div class="tf-weekly-head">')
    html.append(f'<div class="tf-weekly-title">Weekly Prop Dashboard (Top {len(bot_rows)})</div>')
    html.append(
        f'<div class="tf-weekly-range">'
        f'{week_days[0].isoformat()} → {week_days[-1].isoformat()} (Sun→Fri) '
        f'(Start ${start_balance:,.0f}, Trail ${trailing_dd:,.0f})'
        f"</div>"
    )
    html.append("</div>")

    html.append('<div class="tf-weekly-grid">')

    for r in bot_rows:
        run_id = r["run_id"]
        card_uri = r.get("card_uri")
        snaps_week: List[Optional[PropDaySnapshot]] = r["snaps_week"]

        week_profit = r.get("week_profit")
        min_buffer_week = r.get("min_buffer_week")
        trail_move_week = r.get("trail_move_week")
        buffer_eow = r.get("buffer_eow")
        prev_week_profit = r.get("prev_week_profit")
        prev_buffer_eow = r.get("prev_buffer_eow")

        html.append('<div class="tf-weekly-card">')
        if card_uri:
            html.append(f'<img class="tf-weekly-img" src="{card_uri}" alt="{run_id} Card" />')

        html.append('<div class="tf-weekly-meta">')

        html.append('<div class="tf-weekly-top">')
        html.append(f'<div class="tf-weekly-name">{run_id}</div>')
        html.append(f'<div class="tf-weekly-pill">Min Buffer: {_fmt_money(min_buffer_week)}</div>')
        html.append("</div>")

        # KPIs: current week (top row)
        html.append('<div class="tf-weekly-kpis">')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Week PnL</div><div class="tf-weekly-v">{_fmt_money(week_profit)}</div></div>')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Trail Move (Week)</div><div class="tf-weekly-v">{_fmt_money(trail_move_week)}</div></div>')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Buffer (EOW)</div><div class="tf-weekly-v">{_fmt_money(buffer_eow)}</div></div>')
        html.append("</div>")

        # KPIs: previous week (second row)
        html.append('<div class="tf-weekly-kpis">')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Prev Week PnL</div><div class="tf-weekly-v">{_fmt_money(prev_week_profit)}</div></div>')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Prev Buffer (EOW)</div><div class="tf-weekly-v">{_fmt_money(prev_buffer_eow)}</div></div>')
        html.append(f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Trail Distance</div><div class="tf-weekly-v">{_fmt_money(trailing_dd)}</div></div>')
        html.append("</div>")

        # Weekday strip (Sun->Fri), with ratchet indicator when trail_move_today > 0
        html.append('<div class="tf-week-strip">')
        for i, s in enumerate(snaps_week):
            pnl = _pnl_for_snapshot(s)
            min_buf = (s.min_buffer if s is not None else None)

            tm = (s.trail_move_today if s is not None else 0.0) or 0.0
            ratchet = (tm > 0.0)

            bg = _tile_bg(pnl, max_abs)
            pnl_txt = ("—" if pnl is None else (f"+{_fmt_money(pnl)}" if pnl >= 0 else f"{_fmt_money(pnl)}"))
            cls = "tf-day-tile tf-ratchet" if ratchet else "tf-day-tile"

            html.append(f'<div class="{cls}" style="{bg}">')
            if ratchet:
                html.append(f'<div class="tf-ratchet-badge">↑ {_fmt_money(tm)}</div>')
            html.append(f'<div class="tf-day-dow">{labels[i]}</div>')
            html.append(f'<div class="tf-day-pnl">{pnl_txt}</div>')
            html.append(f'<div class="tf-day-sub">Min Buf: {_fmt_money(min_buf)}</div>')
            html.append("</div>")
        html.append("</div>")

        # Mini prop chart (optional)
        if show_chart:
            baseline = start_balance
            for s in snaps_week:
                if s is not None:
                    baseline = s.equity_open
                    break

            chart_uri = _mini_prop_chart(
                labels=labels,
                snaps_by_day=snaps_week,
                baseline_equity=baseline,
                title="Cum Profit vs Trail Line",
            )
            if chart_uri:
                html.append('<div class="tf-mini-chart">')
                html.append(f'<img src="{chart_uri}" alt="{run_id} weekly prop chart" />')
                html.append("</div>")

        html.append("</div></div>")  # meta + card

    html.append("</div></div>")  # grid + weekly
    return "\n".join(html)
