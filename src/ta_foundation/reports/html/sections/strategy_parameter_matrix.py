from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, Optional

from ta_foundation.reports.executive_parameter_matrix import (
    build_executive_parameter_matrix,
)


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
        if abs(numeric - round(numeric)) < 1e-9:
            return str(int(round(numeric)))
        return f"{numeric:.{decimals}f}"
    except Exception:
        return str(value)


def _fmt_money(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
        sign = "-" if numeric < 0 else ""
        return f"{sign}${abs(numeric):,.0f}"
    except Exception:
        return str(value)


def _fmt_pct(value: Optional[float], decimals: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:.{decimals}f}%"


def _metric_class(value: Any) -> str:
    try:
        numeric = float(value)
    except Exception:
        return "tf-spm-cell"
    if numeric > 0:
        return "tf-spm-cell tf-spm-cell--pos"
    if numeric < 0:
        return "tf-spm-cell tf-spm-cell--neg"
    return "tf-spm-cell tf-spm-cell--flat"


def _td(value: str, cls: str = "") -> str:
    class_attr = f' class="{cls}"' if cls else ""
    return f"<td{class_attr}>{escape(value)}</td>"


def _render_row(row: Dict[str, Any]) -> str:
    return (
        "<tr>"
        + _td(str(row.get("run_id") or "-"), "tf-spm-run")
        + _td(str(row.get("period") or "-"))
        + _td(str(row.get("instrument") or "-"))
        + _td(_fmt_num(row.get("tick_value"), 2))
        + _td(str(row.get("direction") or "-"))
        + _td(str(row.get("contracts") or "-"))
        + _td(str(row.get("active_window") or "-"), "tf-spm-nowrap")
        + _td(str(row.get("chart_label") or "-"))
        + _td(str(row.get("label") or "-"))
        + _td(str(row.get("fast_ma") or "-"))
        + _td(str(row.get("slow_ma") or "-"))
        + _td(str(row.get("trend_ma") or "-"))
        + _td(str(row.get("max_trades") or "-"))
        + _td(_fmt_num(row.get("max_stop"), 0))
        + _td(_fmt_num(row.get("tp_ratio"), 2))
        + _td(_fmt_num(row.get("max_take_profit"), 0))
        + _td(_fmt_money(row.get("total_net_profit")), _metric_class(row.get("total_net_profit")))
        + _td(_fmt_money(row.get("max_drawdown")))
        + _td(_fmt_pct(row.get("win_rate_pct"), 2))
        + _td(_fmt_num(row.get("profit_factor"), 2))
        + _td(_fmt_num(row.get("total_trades"), 0))
        + _td(_fmt_money(row.get("avg_win")))
        + _td(_fmt_money(row.get("avg_loss")))
        + _td(_fmt_money(row.get("avg_mae")))
        + _td(_fmt_money(row.get("avg_mfe")))
        + _td(_fmt_money(row.get("avg_etd")))
        + _td(_fmt_num(row.get("mae_mfe_ratio"), 2))
        + _td(str(row.get("mae_mfe_rating") or "-"))
        + _td(_fmt_num(row.get("mfe_etd_ratio"), 2))
        + _td(str(row.get("mfe_etd_rating") or "-"))
        + _td(_fmt_money(row.get("best_day")), _metric_class(row.get("best_day")))
        + _td(_fmt_money(row.get("worst_day")), _metric_class(row.get("worst_day")))
        + _td(_fmt_money(row.get("max_potential_profit")))
        + _td(_fmt_money(row.get("max_potential_loss")))
        + _td(_fmt_money(row.get("long_profit")), _metric_class(row.get("long_profit")))
        + _td(_fmt_pct(row.get("long_win_rate_pct"), 1))
        + _td(_fmt_money(row.get("short_profit")), _metric_class(row.get("short_profit")))
        + _td(_fmt_pct(row.get("short_win_rate_pct"), 1))
        + "</tr>"
    )


def _render_group_headers(groups: Iterable[tuple[str, int]]) -> str:
    parts = []
    for label, span in groups:
        parts.append(f'<th class="tf-spm-group" colspan="{span}">{escape(label)}</th>')
    return "<tr>" + "".join(parts) + "</tr>"


def render_strategy_parameter_matrix(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options", {}) or {}

    rows = build_executive_parameter_matrix(
        packages,
        sort_by=str(options.get("sort_by") or "run_id"),
    )
    if not rows:
        return "<div><em>No runs were available for the executive parameter matrix.</em></div>"

    css = """
    <style>
      .tf-spm {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .tf-spm-hero {
        border-radius: 20px;
        padding: 20px 22px;
        background:
          radial-gradient(circle at top right, rgba(56,189,248,0.12), transparent 28%),
          radial-gradient(circle at top left, rgba(34,197,94,0.10), transparent 24%),
          linear-gradient(180deg, rgba(15,23,42,0.98), rgba(17,24,39,0.98));
        border: 1px solid rgba(148,163,184,0.24);
        box-shadow: 0 18px 40px rgba(15,23,42,0.14);
      }
      .tf-spm-title {
        font-size: 28px;
        font-weight: 950;
        color: #f8fafc;
      }
      .tf-spm-subtitle {
        margin-top: 8px;
        font-size: 14px;
        line-height: 1.5;
        color: rgba(226,232,240,0.82);
        max-width: 1100px;
      }
      .tf-spm-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 14px;
      }
      .tf-spm-pill {
        border-radius: 999px;
        padding: 6px 10px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.06);
        color: rgba(241,245,249,0.9);
        font-size: 12px;
        font-weight: 800;
      }
      .tf-spm-wrap {
        overflow-x: auto;
        border-radius: 18px;
        border: 1px solid rgba(148,163,184,0.22);
        background: linear-gradient(180deg, rgba(15,23,42,0.98), rgba(11,18,32,0.98));
        box-shadow: 0 16px 36px rgba(15,23,42,0.12);
      }
      .tf-spm-table {
        width: max-content;
        min-width: 100%;
        border-collapse: separate;
        border-spacing: 0;
      }
      .tf-spm-table th,
      .tf-spm-table td {
        border-right: 1px solid rgba(148,163,184,0.16);
        border-bottom: 1px solid rgba(148,163,184,0.12);
        padding: 7px 8px;
        vertical-align: top;
        font-size: 11px;
        line-height: 1.35;
      }
      .tf-spm-group {
        position: sticky;
        top: 0;
        z-index: 3;
        background: rgba(30,41,59,0.98);
        color: #f8fafc;
        font-size: 11px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        text-align: center;
        font-weight: 900;
      }
      .tf-spm-table thead tr:nth-child(2) th {
        position: sticky;
        top: 34px;
        z-index: 3;
        background: rgba(15,23,42,0.99);
        color: #dbe7f5;
        font-size: 10px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }
      .tf-spm-table tbody td {
        color: #dbe7f5;
      }
      .tf-spm-table tbody tr:nth-child(even) td {
        background: rgba(255,255,255,0.025);
      }
      .tf-spm-table tbody tr:hover td {
        background: rgba(56,189,248,0.10);
      }
      .tf-spm-run,
      .tf-spm-table thead tr:nth-child(2) th:first-child {
        position: sticky;
        left: 0;
        z-index: 2;
        background: rgba(15,23,42,0.99);
      }
      .tf-spm-table tbody tr:nth-child(even) .tf-spm-run {
        background: rgba(18,28,44,0.99);
      }
      .tf-spm-run {
        min-width: 230px;
        max-width: 230px;
        font-weight: 900;
        color: #f8fafc;
        overflow-wrap: anywhere;
      }
      .tf-spm-table tbody td:not(.tf-spm-run) {
        white-space: nowrap;
      }
      .tf-spm-table tbody td:nth-child(2),
      .tf-spm-table tbody td:nth-child(7),
      .tf-spm-table tbody td:nth-child(9) {
        white-space: normal;
      }
      .tf-spm-nowrap {
        white-space: nowrap;
      }
      .tf-spm-cell--pos { color: #4ade80; font-weight: 800; }
      .tf-spm-cell--neg { color: #f87171; font-weight: 800; }
      .tf-spm-cell--flat { color: #cbd5e1; }
    </style>
    """

    header_groups = _render_group_headers(
        [
            ("Identity", 7),
            ("Logic", 9),
            ("Performance", 5),
            ("Trade Quality", 8),
            ("Risk / Split", 8),
        ]
    )

    column_headers = """
    <tr>
      <th>Run</th>
      <th>Period</th>
      <th>Instrument</th>
      <th>Tick</th>
      <th>Direction</th>
      <th>Contracts</th>
      <th>Active Window</th>
      <th>Chart</th>
      <th>Label</th>
      <th>Fast MA</th>
      <th>Slow MA</th>
      <th>Trend MA</th>
      <th>Max Trades</th>
      <th>Max Stop</th>
      <th>TP Ratio</th>
      <th>Max TP</th>
      <th>Total Net</th>
      <th>Max DD</th>
      <th>Win Rate</th>
      <th>PF</th>
      <th>Total Trades</th>
      <th>Avg Win</th>
      <th>Avg Loss</th>
      <th>Avg MAE</th>
      <th>Avg MFE</th>
      <th>Avg ETD</th>
      <th>MAE/MFE</th>
      <th>MAE/MFE Rating</th>
      <th>MFE/ETD</th>
      <th>MFE/ETD Rating</th>
      <th>Best Day</th>
      <th>Worst Day</th>
      <th>Max Pot Profit</th>
      <th>Max Pot Loss</th>
      <th>Long Profit</th>
      <th>Long WR</th>
      <th>Short Profit</th>
      <th>Short WR</th>
    </tr>
    """

    body = "".join(_render_row(row) for row in rows)

    return f"""
    {css}
    <section class="tf-spm">
      <div class="tf-spm-hero">
        <div class="tf-spm-title">Executive Parameter Matrix</div>
        <div class="tf-spm-subtitle">
          One-row-per-bot reference sheet built from the same executive-profile inputs used by the card report.
          This version is optimized for scanning and comparing settings, risk controls, performance, and trade-quality metrics across every run at once.
        </div>
        <div class="tf-spm-pills">
          <span class="tf-spm-pill">Runs: {len(rows)}</span>
          <span class="tf-spm-pill">Sort: {escape(str(options.get("sort_by") or "run_id"))}</span>
          <span class="tf-spm-pill">Source: run_executive_profile_cards fields</span>
        </div>
      </div>
      <div class="tf-spm-wrap">
        <table class="tf-spm-table">
          <thead>
            {header_groups}
            {column_headers}
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>
    """
