"""
export_weekly_leaderboard_text.py
──────────────────────────────────
Writes a condensed, LLM-friendly plain-text summary of the Weekly Prop
Dashboard — the same data that appears in the weekly_leaderboard_cards HTML
section, but with no markup, no images, and a compact structure that loads
cheaply into any AI chat context.

The computation mirrors weekly_leaderboard_cards.py exactly so the text
output always agrees with what the card shows.

Usage (internal):
    from ta_foundation.reports.text.export_weekly_leaderboard_text import (
        export_weekly_leaderboard_text,
    )
    export_weekly_leaderboard_text(packages, output_path, options={...})
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.analysis.prop_trailing import (
    compute_prop_trailing_states,
    PropDaySnapshot,
)
from ta_foundation.reports.html.sections.weekly_leaderboard_cards import (
    _daily_df,
    _find_col,
    _resolve_dashboard_window,
    _snapshot_lists_for_pkg,
)

WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]


# ─────────────────────────────────────────────────────────────
# Helpers (mirrors the HTML renderer's private helpers)
# ─────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], sign: bool = False) -> str:
    if v is None:
        return "—"
    prefix = ("+" if v >= 0 else "") if sign else ""
    return f"{prefix}{v:,.0f}"


def _exit_time_col(trades: pd.DataFrame) -> Optional[str]:
    for c in ("Exit time", "Exit Time", "exit_time", "Entry time", "Entry Time", "entry_time"):
        if c in trades.columns:
            return c
    return None


def _filter_week(trades: pd.DataFrame, days: List[date]) -> pd.DataFrame:
    if trades is None or len(trades) == 0:
        return trades
    col = _exit_time_col(trades)
    if not col:
        return trades.iloc[0:0].copy()
    ts = pd.to_datetime(trades[col], errors="coerce").dt.date
    return trades.loc[ts.isin(set(days))].copy()


def _week_sunday(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _infer_week_ending(packages: Dict[str, AnalysisPackage]) -> Optional[date]:
    best: Optional[date] = None
    for pkg in packages.values():
        trades = getattr(pkg, "trades", None)
        if trades is not None and len(trades) > 0:
            col = _exit_time_col(trades)
            if col:
                s = pd.to_datetime(trades[col], errors="coerce").dropna()
                if len(s):
                    cand = s.dt.date.max()
                    if best is None or cand > best:
                        best = cand
                    continue

        daily = _daily_df(pkg)
        if isinstance(daily, pd.DataFrame) and not daily.empty:
            col = _find_col(daily, ["date", "Period", "Date", "Day"])
            if col:
                s = pd.to_datetime(daily[col], errors="coerce").dropna()
                if len(s):
                    cand = s.dt.date.max()
                    if best is None or cand > best:
                        best = cand
    return best


def _week_profit(snaps: List[Optional[PropDaySnapshot]]) -> float:
    return sum(
        (s.equity_close - s.equity_open) for s in snaps if s is not None
    )


def _last_snap(snaps: List[Optional[PropDaySnapshot]]) -> Optional[PropDaySnapshot]:
    for s in reversed(snaps):
        if s is not None:
            return s
    return None


def _compute_streaks(snaps: List[Optional[PropDaySnapshot]]) -> Dict[str, Any]:
    def cls(s: Optional[PropDaySnapshot]) -> str:
        if s is None:
            return "N"
        pnl = s.equity_close - s.equity_open
        return "W" if pnl > 0 else ("L" if pnl < 0 else "N")

    seq = [cls(s) for s in snaps]
    max_w = max_l = cur_len = 0
    cur_type = "N"

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
        else:
            max_l = max(max_l, cur_len)

    last = seq[-1] if seq else "N"
    if last in ("W", "L"):
        i, n = len(seq) - 1, 0
        while i >= 0 and seq[i] == last:
            n += 1
            i -= 1
        current = f"{last}{n}"
    else:
        current = "N"

    return {"max_w": max_w, "max_l": max_l, "current": current}


# ─────────────────────────────────────────────────────────────
# Per-run text block
# ─────────────────────────────────────────────────────────────

def _run_block(
    rank: int,
    run_id: str,
    row: Dict[str, Any],
    week_days: List[date],
    labels: List[str],
    warn_buffer: float,
) -> str:
    lines: list[str] = []

    def ln(text: str = "") -> None:
        lines.append(text)

    def kv(label: str, value: str, width: int = 22) -> None:
        lines.append(f"  {label:<{width}}{value}")

    SEP = "-" * 72

    ln(SEP)
    ln(f"#{rank}  {run_id}")
    ln(SEP)

    # ── Weekly summary ────────────────────────────────────────
    week_profit = row["week_profit"]
    prev_week_profit = row.get("prev_week_profit")
    win_days = row.get("win_days", 0)
    loss_days = row.get("loss_days", 0)
    no_trade_days = row.get("no_trade_days", 0)
    max_w = row.get("max_w_streak", 0)
    max_l = row.get("max_l_streak", 0)
    current_streak = row.get("current_streak", "N")
    had_tight = bool(row.get("had_tight_day", False))
    had_breach = bool(row.get("had_breach_day", False))

    kv("Week P&L:", _fmt(week_profit, sign=True))
    kv("Prev Week P&L:", _fmt(prev_week_profit, sign=True) if prev_week_profit is not None else "—")
    kv("Days (W/L/No-Trade):", f"W{win_days}  L{loss_days}  N{no_trade_days}")
    kv("Max Streak:", f"W{max_w}  L{max_l}")
    kv("Current Streak:", current_streak)

    risk_label = "BREACH" if had_breach else ("TIGHT" if had_tight else "—")
    kv("Risk Flag:", risk_label)

    # ── Account state EOW ─────────────────────────────────────
    ln()
    kv("Equity (EOW):", _fmt(row.get("equity_eow")))
    kv("Trail (EOW):", _fmt(row.get("trail_eow")))
    kv("Buffer (EOW):", _fmt(row.get("buffer_eow")))
    kv("Prev Buffer (EOW):", _fmt(row.get("prev_buffer_eow")))
    kv("Trail Move (Week):", _fmt(row.get("trail_move_week"), sign=True))
    kv("Worst Trough (Week):", _fmt(row.get("worst_trough_week")))
    kv("Min Buffer (Week):", _fmt(row.get("min_buffer_week")))

    # ── Day-by-day strip ──────────────────────────────────────
    ln()
    ln("  DAY-BY-DAY")

    snaps: List[Optional[PropDaySnapshot]] = row["snaps_week"]
    for i, s in enumerate(snaps):
        d = week_days[i]
        day_label = f"{labels[i]}  {d.isoformat()}"

        if s is None:
            ln(f"    {day_label:<22}  (no trade)")
            continue

        pnl = s.equity_close - s.equity_open
        pnl_str = _fmt(pnl, sign=True)
        worst_buf = s.min_buffer
        trough_buf = s.worst_trough_buffer
        close_buf = s.buffer_close
        tm = s.trail_move_today or 0.0

        risk_tag = ""
        if worst_buf <= 0:
            risk_tag = "  ⚠ BREACH"
        elif worst_buf <= warn_buffer:
            risk_tag = "  ⚠ TIGHT"

        ratchet_tag = f"  ↑ Ratchet +{tm:,.0f}" if tm > 0 else ""

        ln(
            f"    {day_label:<22}  {pnl_str:<12}"
            f"  Worst Buf: {_fmt(worst_buf):<10}"
            f"  Trough Buf: {_fmt(trough_buf):<10}"
            f"  Close Buf: {_fmt(close_buf)}"
            f"{ratchet_tag}{risk_tag}"
        )

    ln()
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────

def export_weekly_leaderboard_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Weekly Prop Dashboard",
) -> Path:
    """
    Write a condensed plain-text weekly leaderboard summary to *output_path*.

    *options* mirrors the YAML section options for weekly_leaderboard_cards:
        starting_balance  (default 50000)
        trailing_dd       (default 2500)
        top_n             (default 12)
        warn_buffer       (default 500)
        week_ending       (ISO date string, default = auto-inferred)
        baseline_mode     ("fresh_week" | "continuous", default "fresh_week")

    Returns the resolved output path.
    """
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Config ────────────────────────────────────────────────
    start_balance = float(options.get("starting_balance", 50_000))
    trailing_dd = float(options.get("trailing_dd", 2_500))
    top_n = int(options.get("top_n", 12))
    warn_buffer = float(options.get("warn_buffer", 500))
    baseline_mode = (options.get("baseline_mode") or "fresh_week").strip().lower()
    if baseline_mode not in ("fresh_week", "continuous"):
        baseline_mode = "fresh_week"

    # ── Week window ───────────────────────────────────────────
    week_days, prev_week_days, labels, window_descriptor = _resolve_dashboard_window(options, packages)

    if not week_days:
        output_path.write_text(
            "No week could be inferred (no trade timestamps found).\n",
            encoding="utf-8",
        )
        return output_path

    mode_label = (
        f"fresh_week (reset ${start_balance:,.0f} each week)"
        if baseline_mode == "fresh_week"
        else "continuous (full account history)"
    )

    # ── Build rows (same logic as HTML renderer) ──────────────
    bot_rows: List[Dict[str, Any]] = []

    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        trades_all = getattr(pkg, "trades", None)
        daily_all = _daily_df(pkg)
        if (trades_all is None or len(trades_all) == 0) and (daily_all is None or len(daily_all) == 0):
            continue

        win_days = loss_days = no_trade_days = 0

        snaps_week, snaps_prev = _snapshot_lists_for_pkg(
            pkg,
            week_days=week_days,
            prev_week_days=prev_week_days,
            start_balance=start_balance,
            trailing_dd=trailing_dd,
            baseline_mode=baseline_mode,
        )

        if not any(s is not None for s in snaps_week):
            continue

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
        week_pnl = _week_profit(snaps_week)
        prev_pnl = _week_profit(snaps_prev) if any(s is not None for s in snaps_prev) else None

        min_buffer_week = worst_trough_week = None
        trail_move_week = 0.0
        had_tight = had_breach = False

        for s in snaps_week:
            if s is None:
                continue
            min_buffer_week = (
                s.min_buffer if min_buffer_week is None else min(min_buffer_week, s.min_buffer)
            )
            worst_trough_week = (
                s.worst_trough_buffer
                if worst_trough_week is None
                else min(worst_trough_week, s.worst_trough_buffer)
            )
            trail_move_week += s.trail_move_today or 0.0
            if s.min_buffer <= 0:
                had_breach = had_tight = True
            elif s.min_buffer <= warn_buffer:
                had_tight = True

        last_s = _last_snap(snaps_week)
        prev_last = _last_snap(snaps_prev)

        bot_rows.append(
            dict(
                run_id=run_id,
                snaps_week=snaps_week,
                week_profit=week_pnl,
                prev_week_profit=prev_pnl,
                win_days=win_days,
                loss_days=loss_days,
                no_trade_days=no_trade_days,
                max_w_streak=streaks["max_w"],
                max_l_streak=streaks["max_l"],
                current_streak=streaks["current"],
                trail_move_week=trail_move_week,
                min_buffer_week=min_buffer_week,
                worst_trough_week=worst_trough_week,
                buffer_eow=last_s.buffer_close if last_s else None,
                trail_eow=last_s.trail_close if last_s else None,
                equity_eow=last_s.equity_close if last_s else None,
                prev_buffer_eow=prev_last.buffer_close if prev_last else None,
                had_tight_day=had_tight,
                had_breach_day=had_breach,
            )
        )

    # Sort: safest first (highest worst trough), then by week profit
    bot_rows.sort(
        key=lambda r: (
            r.get("worst_trough_week") is None,
            -(r.get("worst_trough_week") or 0.0),
            -(r.get("week_profit") or 0.0),
        )
    )
    if top_n > 0:
        bot_rows = bot_rows[:top_n]

    # ── Write text ────────────────────────────────────────────
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    fri = week_days[-1]  # Friday

    header_lines = [
        "=" * 72,
        f"WEEKLY PROP DASHBOARD SUMMARY",
        f"Report:       {title}",
        f"Generated:    {now}",
        f"Window:       {window_descriptor}",
        f"Mode:         {mode_label}",
        f"Trailing DD:  ${trailing_dd:,.0f}  |  Warn Buffer: ≤ ${warn_buffer:,.0f}",
        f"Runs shown:   {len(bot_rows)}",
        "=" * 72,
        "",
        "Ranked: safest first (highest worst-trough buffer), then week P&L.",
        "Worst Buf = modeled minimum buffer (intraday trough).",
        "Trough Buf = MAE-based liquidation-risk metric.",
        "Close Buf  = end-of-day buffer comfort.",
        "↑ Ratchet  = trailing drawdown moved up (locked in profits).",
        "",
    ]

    run_blocks = [
        _run_block(i + 1, r["run_id"], r, week_days, labels, warn_buffer)
        for i, r in enumerate(bot_rows)
    ]

    if not run_blocks:
        run_blocks = ["  (No runs had trades in this week.)\n"]

    footer_lines = [
        "=" * 72,
        f"END OF WEEKLY SUMMARY  ({len(bot_rows)} runs)",
        "=" * 72,
    ]

    output_path.write_text(
        "\n".join(header_lines) + "\n".join(run_blocks) + "\n" + "\n".join(footer_lines),
        encoding="utf-8",
    )
    return output_path
