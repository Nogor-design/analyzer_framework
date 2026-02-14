from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import pandas as pd

from ta_foundation.analysis.prop_trailing import compute_prop_trailing_states, PropDaySnapshot
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png


WEEKDAY_LABELS_6 = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]

def _compute_streaks(snaps_week: List[Optional[PropDaySnapshot]]) -> Dict[str, Any]:
    """
    Streaks are computed on the weekday sequence (Sun..Fri):
      - W: pnl > 0
      - L: pnl < 0
      - N: no trade or pnl == 0 (breaks streaks)

    Returns:
      {
        "max_w": int,
        "max_l": int,
        "current": str  # e.g. "W2", "L1", "N"
      }
    """
    def cls(s: Optional[PropDaySnapshot]) -> str:
        if s is None:
            return "N"
        pnl = s.equity_close - s.equity_open
        if pnl > 0:
            return "W"
        if pnl < 0:
            return "L"
        return "N"

    seq = [cls(s) for s in snaps_week]

    max_w = 0
    max_l = 0
    cur_type = "N"
    cur_len = 0

    for x in seq:
        if x not in ("W", "L"):
            cur_type = "N"
            cur_len = 0
            continue

        if x == cur_type:
            cur_len += 1
        else:
            cur_type = x
            cur_len = 1

        if cur_type == "W":
            max_w = max(max_w, cur_len)
        elif cur_type == "L":
            max_l = max(max_l, cur_len)

    # current streak ending on last day (Fri)
    last = seq[-1] if seq else "N"
    if last in ("W", "L"):
        i = len(seq) - 1
        n = 0
        while i >= 0 and seq[i] == last:
            n += 1
            i -= 1
        current = f"{last}{n}"
    else:
        current = "N"
    # print(max_w)
    return {"max_w": int(max_w), "max_l": int(max_l), "current": current}

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

    best = None
    for pkg in packages.values():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue
        tcol = None
        for c in ("Exit time", "Exit Time", "exit_time"):
            if c in trades.columns:
                tcol = c
                break
        if not tcol:
            for c in ("Entry time", "Entry Time", "entry_time"):
                if c in trades.columns:
                    tcol = c
                    break
        if not tcol:
            continue

        s = pd.to_datetime(trades[tcol], errors="coerce").dropna()
        if len(s):
            cand = s.dt.date.max()
            if best is None or cand > best:
                best = cand
    return best


def _week_sunday_start(d: date) -> date:
    delta = (d.weekday() + 1) % 7
    return d - timedelta(days=delta)


def _prop_cfg(options: Dict[str, Any]) -> Tuple[float, float]:
    start_balance = float(options.get("starting_balance", 50000))
    trailing_dd = float(options.get("trailing_dd", 2500))
    return start_balance, trailing_dd


def _exit_time_col(trades: pd.DataFrame) -> Optional[str]:
    for c in ("Exit time", "Exit Time", "exit_time"):
        if c in trades.columns:
            return c
    for c in ("Entry time", "Entry Time", "entry_time"):
        if c in trades.columns:
            return c
    return None


def _filter_trades_by_dates(trades: pd.DataFrame, days: List[date]) -> pd.DataFrame:
    if trades is None or len(trades) == 0:
        return trades
    tcol = _exit_time_col(trades)
    if not tcol:
        return trades.iloc[0:0].copy()
    ts = pd.to_datetime(trades[tcol], errors="coerce")
    dd = ts.dt.date
    dayset = set(days)
    mask = dd.isin(dayset)
    return trades.loc[mask].copy()


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
    alpha = 0.16 + 0.56 * intensity
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


def _render_debug_table(
    week_days: List[date], labels: List[str], snaps: List[Optional[PropDaySnapshot]], baseline_mode: str
) -> str:
    rows = []
    for i, d in enumerate(week_days):
        s = snaps[i]
        if s is None:
            rows.append(
                f"<tr><td>{labels[i]} ({d.isoformat()})</td>"
                f"<td colspan='11' style='opacity:0.7;'>—</td></tr>"
            )
            continue

        rows.append(
            "<tr>"
            f"<td>{labels[i]} ({d.isoformat()})</td>"
            f"<td>{_fmt_money(s.equity_open)}</td>"
            f"<td>{_fmt_money(s.equity_close)}</td>"
            f"<td>{_fmt_money(s.hwm_open)}</td>"
            f"<td>{_fmt_money(s.hwm_close)}</td>"
            f"<td>{_fmt_money(s.trail_open)}</td>"
            f"<td>{_fmt_money(s.trail_close)}</td>"
            f"<td>{_fmt_money(s.buffer_open)}</td>"
            f"<td>{_fmt_money(s.buffer_close)}</td>"
            f"<td>{_fmt_money(s.worst_trough_buffer)}</td>"
            f"<td>{_fmt_money(s.min_buffer)}</td>"
            f"<td>{_fmt_money(s.trail_move_today)}</td>"
            "</tr>"
        )

    mode_label = "WEEK-SCOPED (fresh start)" if baseline_mode == "fresh_week" else "CONTINUOUS (full history)"
    return (
        '<div class="tf-debug-wrap">'
        f'<div class="tf-debug-title">Debug: Daily Prop Trail States ({mode_label})</div>'
        '<table class="tf-debug">'
        "<thead><tr>"
        "<th>Day</th>"
        "<th>Eq Open</th><th>Eq Close</th>"
        "<th>HWM Open</th><th>HWM Close</th>"
        "<th>Trail Open</th><th>Trail Close</th>"
        "<th>Close Buf Open</th><th>Close Buf</th>"
        "<th>Trough Buf (MAE)</th>"
        "<th>Worst Buf (modeled)</th>"
        "<th>Trail Δ</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )


def render_weekly_leaderboard_cards(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}

    top_n = int(options.get("top_n", 12))
    hide_missing_cards = bool(options.get("hide_missing_cards", True))
    show_chart = bool(options.get("show_chart", True))
    show_debug_table = bool(options.get("show_debug_table", False))

    # NEW: baseline mode: "fresh_week" (default) or "continuous"
    baseline_mode = (options.get("baseline_mode") or "fresh_week").strip().lower()
    if baseline_mode not in ("fresh_week", "continuous"):
        baseline_mode = "fresh_week"

    # NEW: show card image toggle
    show_card_image = bool(options.get("show_card_image", True))

    # If worst buffer <= warn_buffer, show a "TIGHT" badge.
    warn_buffer = float(options.get("warn_buffer", 500))

    week_ending = _parse_week_ending(options, packages)
    if not week_ending:
        return "<div><em>No week could be inferred (no trades timestamps found).</em></div>"

    compact_noimg = bool(options.get("compact_noimg", True))

    bot_columns = int(options.get("bot_columns", 1))
    if bot_columns not in (1, 2, 3):
        bot_columns = 1

    week_start = _week_sunday_start(week_ending)
    week_days = [week_start + timedelta(days=i) for i in range(6)]
    labels = WEEKDAY_LABELS_6

    prev_week_start = week_start - timedelta(days=7)
    prev_week_days = [prev_week_start + timedelta(days=i) for i in range(6)]

    start_balance, trailing_dd = _prop_cfg(options)

    bot_rows: List[Dict[str, Any]] = []
    all_pnls: List[float] = []

    # assert "market" in ctx
    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        if not pkg:
            continue

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg else {}
        card_uri = derived.get("run_image_uri")

        # Only enforce card existence if we plan to show it
        if show_card_image and hide_missing_cards and not card_uri:
            continue

        trades_all = getattr(pkg, "trades", None)
        if trades_all is None or len(trades_all) == 0:
            continue

        # ✅ ALWAYS initialize outcome counters
        win_days = 0
        loss_days = 0
        no_trade_days = 0

        # --- Compute snapshots depending on baseline_mode ---
        if baseline_mode == "fresh_week":
            # Fresh $50k baseline each week: compute states from week-only trades
            trades_week = _filter_trades_by_dates(trades_all, week_days)
            trades_prev = _filter_trades_by_dates(trades_all, prev_week_days)

            states_week = compute_prop_trailing_states(trades_week, start_balance=start_balance, trailing_dd=trailing_dd)
            cont_week = states_week.get("continuous", {}) or {}
            snaps_week: List[Optional[PropDaySnapshot]] = [cont_week.get(d) for d in week_days]

            states_prev = compute_prop_trailing_states(trades_prev, start_balance=start_balance, trailing_dd=trailing_dd)
            cont_prev = states_prev.get("continuous", {}) or {}
            snaps_prev: List[Optional[PropDaySnapshot]] = [cont_prev.get(d) for d in prev_week_days]

            chart_baseline = start_balance  # fresh-week baseline
        else:
            # Continuous account: compute once from all trades, then slice out the week
            states_all = compute_prop_trailing_states(trades_all, start_balance=start_balance, trailing_dd=trailing_dd)
            cont_all = states_all.get("continuous", {}) or {}

            snaps_week = [cont_all.get(d) for d in week_days]
            snaps_prev = [cont_all.get(d) for d in prev_week_days]

            # For continuous view, chart baseline should be the equity_open of the first available day,
            # so the chart reads as “what happened this week relative to where I started this week”.
            first = next((s for s in snaps_week if s is not None), None)
            chart_baseline = float(first.equity_open) if first is not None else start_balance

        have_any = any(s is not None for s in snaps_week)
        if not have_any:
            continue

        # ✅ Weekly outcome summary (computed once, regardless of baseline mode)
        for s in snaps_week:
            if s is None:
                no_trade_days += 1
                continue

            pnl = s.equity_close - s.equity_open
            if pnl > 0:
                win_days += 1
            elif pnl < 0:
                loss_days += 1
            else:
                no_trade_days += 1

        streaks = _compute_streaks(snaps_week)

        prev_have_any = any(s is not None for s in snaps_prev)

        week_profit = _week_profit(snaps_week)
        prev_week_profit = _week_profit(snaps_prev) if prev_have_any else None

        min_buffer_week = None
        worst_trough_week = None
        trail_move_week = 0.0

        for s in snaps_week:
            if s is None:
                continue
            min_buffer_week = s.min_buffer if (min_buffer_week is None or s.min_buffer < min_buffer_week) else min_buffer_week
            worst_trough_week = (
                s.worst_trough_buffer
                if (worst_trough_week is None or s.worst_trough_buffer < worst_trough_week)
                else worst_trough_week
            )
            trail_move_week += s.trail_move_today

            pnl_d = _pnl_for_snapshot(s)
            if pnl_d is not None:
                all_pnls.append(float(pnl_d))

        last_s = _last_non_none(snaps_week)
        buffer_eow = (last_s.buffer_close if last_s is not None else None)
        trail_eow = (last_s.trail_close if last_s is not None else None)
        equity_eow = (last_s.equity_close if last_s is not None else None)

        prev_last = _last_non_none(snaps_prev)
        prev_buffer_eow = (prev_last.buffer_close if prev_last is not None else None)
        # ✅ Track if any day was prop-risk (Worst Buf <= warn_buffer)
        had_tight_day = False
        had_breach_day = False

        for s in snaps_week:
            if s is None:
                continue
            worst_buf = getattr(s, "min_buffer", None)
            if worst_buf is None:
                continue
            if worst_buf <= 0:
                had_breach_day = True
                had_tight_day = True
            elif worst_buf <= warn_buffer:
                had_tight_day = True
        bot_rows.append(
            dict(
                run_id=run_id,
                card_uri=card_uri,
                snaps_week=snaps_week,
                snaps_prev=snaps_prev,
                week_profit=week_profit,
                min_buffer_week=min_buffer_week,
                worst_trough_week=worst_trough_week,
                trail_move_week=trail_move_week,
                buffer_eow=buffer_eow,
                trail_eow=trail_eow,
                equity_eow=equity_eow,
                prev_week_profit=prev_week_profit,
                prev_buffer_eow=prev_buffer_eow,
                chart_baseline=chart_baseline,
                win_days=win_days,
                loss_days=loss_days,
                no_trade_days=no_trade_days,
                max_w_streak=streaks["max_w"],
                max_l_streak=streaks["max_l"],
                current_streak=streaks["current"],
                had_tight_day=had_tight_day,
                had_breach_day=had_breach_day,
            )
        )



    # Rank: safest first (highest worst-trough), then profit
    bot_rows.sort(
        key=lambda r: (
            r.get("worst_trough_week") is None,
            -(r.get("worst_trough_week") or 0.0),
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
        background: rgba(25,25,28,0.55);
        border: 1px solid rgba(255,255,255,0.08);
      }

      .tf-weekly-grid {
        display: grid;
        grid-template-columns: repeat(var(--tf-bot-cols), minmax(0, 1fr));
        gap: 14px;
        align-items: start;
      }

      /* For 2–3 columns, tighten a bit so cards don't feel cramped */
      .tf-weekly[data-cols="2"] .tf-weekly-v { font-size: 32px; }
      .tf-weekly[data-cols="3"] .tf-weekly-v { font-size: 30px; }
      .tf-weekly[data-cols="2"] .tf-weekly-kpi { padding: 10px 12px; }
      .tf-weekly[data-cols="3"] .tf-weekly-kpi { padding: 10px 12px; }

      /* In 3-column mode, force no-image layout inside cards (prevents squeeze) */
      .tf-weekly[data-cols="3"] .tf-week-row {
        grid-template-columns: 1fr;
      }


      .tf-week-card {
        border-radius: 18px;
        padding: 12px 12px;
        background: rgba(18,18,22,0.64);
        border: 1px solid rgba(255,255,255,0.10);
        box-shadow: 0 10px 26px rgba(0,0,0,0.30);
      }
      .tf-week-card { width: 100%; }


      /* Layout adapts if card image is hidden */
      .tf-week-row { display: grid; grid-template-columns: 220px 1fr; gap: 12px; align-items: start; }
      .tf-week-row--noimg { grid-template-columns: 1fr; }
      .tf-week-meta { display: flex; flex-direction: column; gap: 10px; }
      .tf-week-title { font-size: 22px; font-weight: 800; letter-spacing: 0.2px; }
      .tf-week-title--noimg { font-size: 26px; }
      .tf-week-badge {
        align-self: flex-end;
        padding: 8px 12px;
        border-radius: 999px;
        background: rgba(255,255,255,0.10);
        border: 1px solid rgba(255,255,255,0.12);
        font-weight: 900;
      }
      .tf-week-row--compact .tf-week-meta { gap: 6px; }
      .tf-week-row--compact .tf-week-badge { padding: 6px 10px; font-size: 13px; }
      .tf-week-row--compact .tf-weekly-kpis { gap: 8px; }
      .tf-week-row--compact .tf-weekly-kpi { padding: 10px 12px; }
      .tf-week-row--compact .tf-weekly-v { font-size: 32px; }


      .tf-weekly-kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
      .tf-weekly-kpi {
        border-radius: 18px;
        padding: 12px 14px;
        background: rgba(12,12,16,0.52);
        border: 1px solid rgba(255,255,255,0.10);
      }
      .tf-weekly-k { font-size: 15px; opacity: 0.85; }
      .tf-weekly-v { font-size: 36px; font-weight: 900; margin-top: 4px; }
      .tf-weekly-sub { font-size: 13px; opacity: 0.75; margin-top: 2px; }

      .tf-week-strip { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-top: 10px; }
      .tf-day-tile {
        border-radius: 18px;
        padding: 12px 12px;
        border: 1px solid rgba(255,255,255,0.10);
        position: relative;
        overflow: hidden;
      }
      .tf-day-dow { font-size: 22px; font-weight: 800; opacity: 0.9; }
      .tf-day-pnl { font-size: 34px; font-weight: 950; margin-top: 6px; }
      .tf-day-sub { font-size: 16px; opacity: 0.85; margin-top: 6px; line-height: 1.15; }

      .tf-ratchet { outline: 2px solid rgba(255,255,255,0.22); }
      .tf-day-tile { padding-top: 44px; } /* ensures room for top badges */

      .tf-ratchet-badge {
        position: absolute;
        top: 10px;
        left: 10px;              /* ✅ ratchet top-left */
        font-size: 12px;
        padding: 5px 8px;
        border-radius: 999px;
        background: rgba(255,255,255,0.18);
        border: 1px solid rgba(255,255,255,0.18);
        font-weight: 900;
        z-index: 5;
      }

      .tf-risk-badge {
        position: absolute;
        top: 10px;
        right: 10px;             /* ✅ risk top-right */
        font-size: 12px;
        padding: 5px 8px;
        border-radius: 999px;
        font-weight: 950;
        border: 1px solid rgba(255,255,255,0.16);
        background: rgba(0,0,0,0.28);
        z-index: 6;              /* above day label */
      }
      .tf-risk-breach { color: rgba(255,120,120,1); }
      .tf-risk-tight { color: rgba(255,210,120,1); }
      .tf-risk-breach { color: rgba(255,120,120,1); }
      .tf-risk-tight { color: rgba(255,210,120,1); }

      .tf-mini-chart { margin-top: 10px; }
      .tf-mini-chart img { width: 100%; border-radius: 14px; border: 1px solid rgba(255,255,255,0.10); }

      .tf-debug-wrap { margin-top: 10px; }
      .tf-debug-title { font-weight: 800; margin: 6px 0 8px 0; opacity: 0.9; }
      table.tf-debug {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
        background: rgba(10,10,14,0.40);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 12px;
        overflow: hidden;
      }
      table.tf-debug th, table.tf-debug td {
        padding: 8px 8px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        text-align: right;
        white-space: nowrap;
      }
      table.tf-debug th:first-child, table.tf-debug td:first-child { text-align: left; }
      table.tf-debug thead th {
        background: rgba(255,255,255,0.06);
        font-weight: 900;
        opacity: 0.92;
      }
      table.tf-debug tbody tr:hover { background: rgba(255,255,255,0.04); }
            .tf-week-outcomes {
        display: inline-flex;
        gap: 10px;
        align-items: center;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        font-weight: 900;
        font-size: 13px;
        letter-spacing: 0.3px;
      }
      .tf-out-win { color: rgba(90,210,140,1); }
      .tf-out-loss { color: rgba(230,110,110,1); }
      .tf-out-none { color: rgba(180,180,190,1); }
      
      .tf-week-card--badwl {
        border: 2px solid rgba(230,110,110,0.65) !important;
        box-shadow: 0 10px 26px rgba(0,0,0,0.30), 0 0 0 4px rgba(230,110,110,0.10);
      }
            .tf-week-card--risk {
        border: 2px solid rgba(255,210,120,0.70) !important;
        box-shadow: 0 10px 26px rgba(0,0,0,0.30), 0 0 0 4px rgba(255,210,120,0.10);
      }

      .tf-week-card--breach {
        border: 2px solid rgba(255,120,120,0.75) !important;
        box-shadow: 0 10px 26px rgba(0,0,0,0.30), 0 0 0 4px rgba(255,120,120,0.12);
      }



    </style>
    """

    mode_label = "fresh_week (reset $50k)" if baseline_mode == "fresh_week" else "continuous (full-history)"
    html: List[str] = []
    html.append(css)
    html.append(f'<div class="tf-weekly" data-cols="{bot_columns}" style="--tf-bot-cols:{bot_columns};">')

    html.append(
        f'<div class="tf-weekly-head">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<div><b>Weekly Prop Dashboard</b> (Sun–Fri). Week ending: {week_ending.isoformat()} • Mode: {mode_label}</div>'
        f'<div style="opacity:0.8;font-weight:800;">Worst Buf uses MAE trough • Warn ≤ {warn_buffer:,.0f}</div>'
        f"</div>"
        f"</div>"
    )
    html.append('<div class="tf-weekly-grid">')

    for row in bot_rows:
        run_id = row["run_id"]
        card_uri = row["card_uri"]
        snaps_week: List[Optional[PropDaySnapshot]] = row["snaps_week"]

        week_profit = row["week_profit"]
        min_buffer_week = row.get("min_buffer_week")
        worst_trough_week = row.get("worst_trough_week")
        trail_move_week = row["trail_move_week"]
        buffer_eow = row["buffer_eow"]
        trail_eow = row.get("trail_eow")
        equity_eow = row.get("equity_eow")

        prev_week_profit = row.get("prev_week_profit")
        prev_buffer_eow = row.get("prev_buffer_eow")

        chart_baseline = float(row.get("chart_baseline", start_balance))

        noimg = (not show_card_image) or (not card_uri)

        row_cls = "tf-week-row tf-week-row--noimg" if noimg else "tf-week-row"

        title_cls = "tf-week-title tf-week-title--noimg" if noimg else "tf-week-title"
        if noimg and compact_noimg:
            row_cls += " tf-week-row--compact"


        win_days = row.get("win_days", 0)
        loss_days = row.get("loss_days", 0)
        no_trade_days = row.get("no_trade_days", 0)
        max_w_streak = row.get("max_w_streak", 0)
        max_l_streak = row.get("max_l_streak", 0)
        current_streak = row.get("current_streak", "N")
        had_tight_day = bool(row.get("had_tight_day", False))
        had_breach_day = bool(row.get("had_breach_day", False))

        # html.append('<div class="tf-week-card">')
        # badwl = (loss_days > win_days)
        # card_cls = "tf-week-card tf-week-card--badwl" if badwl else "tf-week-card"
        # html.append(f'<div class="{card_cls}">')
        badwl = (loss_days > win_days)

        card_cls = "tf-week-card"
        if badwl:
            card_cls += " tf-week-card--badwl"
        elif had_breach_day:
            card_cls += " tf-week-card--breach"
        elif had_tight_day:
            card_cls += " tf-week-card--risk"

        html.append(f'<div class="{card_cls}">')

        html.append(f'<div class="{row_cls}">')

        # Left meta (optional image)
        html.append('<div class="tf-week-meta">')
        html.append(f'<div class="{title_cls}">{run_id}</div>')


        # html.append(
        #     f'<div class="tf-week-outcomes">'
        #     f'<span class="tf-out-win">W {win_days}</span>'
        #     f'<span class="tf-out-loss">L {loss_days}</span>'
        #     f'<span class="tf-out-none">N {no_trade_days}</span>'
        #     f'</div>'
        # )
        # Badge: include Equity (EOW) + Worst + Min


        html.append(
            f'<div class="tf-week-badge">'
            
            f'Equity: {_fmt_money(equity_eow)} • '
            # f'Worst Buf: {_fmt_money(min_buffer_week)} • '
            # f'Trough Buf: {_fmt_money(worst_trough_week)}'
            f'<div class="tf-week-outcomes">'
            f'<span class="tf-out-win">W {win_days}</span>'
            f'<span class="tf-out-loss">L {loss_days}</span>'
            f'<span class="tf-out-none">N {no_trade_days}</span>'
            f'<span style="opacity:0.55;">|</span>'
            f'<span class="tf-out-win">Wst {max_w_streak}</span>'
            f'<span class="tf-out-loss">Lst {max_l_streak}</span>'
            f'<span class="tf-out-none">Now {current_streak}</span>'
            f'</div>'
            f"</div>"
        )

        if show_card_image and card_uri:
            html.append(
                f'<img src="{card_uri}" '
                f'style="width:100%;border-radius:14px;border:1px solid rgba(255,255,255,0.10);" />'
            )
        html.append("</div>")

        # Right content (always present; if no image, it's the only column)
        html.append("<div>")
        html.append('<div class="tf-weekly-kpis">')
        html.append(
            f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Week PnL</div>'
            f'<div class="tf-weekly-v">{_fmt_money(week_profit)}</div></div>'
        )
        html.append(
            f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Trail Move (Week)</div>'
            f'<div class="tf-weekly-v">{_fmt_money(trail_move_week)}</div></div>'
        )
        html.append(
            f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Buffer (EOW)</div>'
            f'<div class="tf-weekly-v">{_fmt_money(buffer_eow)}</div></div>'
        )

        html.append(
            f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Prev Week PnL</div>'
            f'<div class="tf-weekly-v">{_fmt_money(prev_week_profit)}</div></div>'
        )
        html.append(
            f'<div class="tf-weekly-kpi"><div class="tf-weekly-k">Prev Buffer (EOW)</div>'
            f'<div class="tf-weekly-v">{_fmt_money(prev_buffer_eow)}</div></div>'
        )
        html.append(
            f'<div class="tf-weekly-kpi">'
            f'  <div class="tf-weekly-k">Trail (EOW)</div>'
            f'  <div class="tf-weekly-v">{_fmt_money(trail_eow)}</div>'
            f'  <div class="tf-weekly-sub">Equity (EOW): {_fmt_money(equity_eow)} • DD: {_fmt_money(trailing_dd)}</div>'
            f'</div>'
        )
        html.append("</div>")

        # Weekday strip (Sun->Fri)
        html.append('<div class="tf-week-strip">')
        for i, s in enumerate(snaps_week):
            pnl = _pnl_for_snapshot(s)
            trough_buf = (s.worst_trough_buffer if s is not None else None)  # MAE trough diagnostic
            close_buf = (s.buffer_close if s is not None else None)  # end-of-day comfort
            worst_buf = (s.min_buffer if s is not None else None)  # true modeled minimum

            tm = (s.trail_move_today if s is not None else 0.0) or 0.0
            ratchet = (tm > 0.0)

            bg = _tile_bg(pnl, max_abs)
            pnl_txt = ("—" if pnl is None else (f"+{_fmt_money(pnl)}" if pnl >= 0 else f"{_fmt_money(pnl)}"))
            cls = "tf-day-tile tf-ratchet" if ratchet else "tf-day-tile"

            html.append(f'<div class="{cls}" style="{bg}">')

            # ✅ Risk badge should use "Worst Buf" (min_buffer), not trough_buf
            if worst_buf is not None:
                if worst_buf <= 0:
                    html.append('<div class="tf-risk-badge tf-risk-breach">BREACH</div>')
                elif worst_buf <= warn_buffer:
                    html.append('<div class="tf-risk-badge tf-risk-tight">TIGHT</div>')

            if ratchet:
                html.append(f'<div class="tf-ratchet-badge">↑ {_fmt_money(tm)}</div>')

            html.append(f'<div class="tf-day-dow">{labels[i]}</div>')
            html.append(f'<div class="tf-day-pnl">{pnl_txt}</div>')
            html.append(
                f'<div class="tf-day-sub">'
                f'Worst Buf: {_fmt_money(worst_buf)}<br/>'
                f'Trough Buf: {_fmt_money(trough_buf)}<br/>'
                f'Close Buf: {_fmt_money(close_buf)}'
                f'</div>'
            )
            html.append("</div>")
        html.append("</div>")


        # Debug table (optional)
        if show_debug_table:
            html.append(_render_debug_table(week_days, labels, snaps_week, baseline_mode=baseline_mode))

        # Mini chart (optional)
        if show_chart:
            chart_uri = _mini_prop_chart(
                labels=labels,
                snaps_by_day=snaps_week,
                baseline_equity=chart_baseline,
                title=f"Cum Profit vs Trail Line (baseline = {_fmt_money(chart_baseline)})",
            )
            if chart_uri:
                html.append('<div class="tf-mini-chart">')
                html.append(f'<img src="{chart_uri}" alt="{run_id} weekly prop chart" />')
                html.append("</div>")

        html.append("</div>")  # right content
        html.append("</div>")  # row
        html.append("</div>")  # card

    html.append("</div></div>")
    return "\n".join(html)
