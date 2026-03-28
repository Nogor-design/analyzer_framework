# src/ta_foundation/reports/html/sections/strategy_discovery_evaluation.py
"""
Strategy Discovery — Evaluation Section
=========================================
Renders Phase-1 evaluation results: core P&L KPIs, direction breakdown
(Long vs Short), session breakdown, and a mini equity curve sparkline.

CSS prefix: .sdev-
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdev-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdev-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

.sdev-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }
.sdev-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 10px 16px; min-width: 110px; text-align: center; }
.sdev-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 3px; }
.sdev-kpi-value { font-size: 18px; font-weight: 700; color: #e8e8e8; }
.sdev-kpi-value.pos  { color: #22c55e; }
.sdev-kpi-value.neg  { color: #ef4444; }
.sdev-kpi-value.warn { color: #f59e0b; }
.sdev-kpi-value.neutral { color: #94a3b8; }

/* breakdown table */
.sdev-table-wrap { overflow-x: auto; margin: 8px 0 16px; }
.sdev-table { border-collapse: collapse; min-width: 480px; width: 100%; }
.sdev-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdev-table td { padding: 6px 10px; border-bottom: 1px solid #2a2a2a; color: #ccc;
                 font-size: 12px; }
.sdev-table tr:hover td { background: #1a1a2a; }

/* cross-run comparison */
.sdev-cross-table-wrap { overflow-x: auto; margin: 8px 0 20px; }

/* equity sparkline */
.sdev-spark-wrap { margin: 4px 0 14px; }
.sdev-spark-label { font-size: 10px; color: #888; text-transform: uppercase;
                    letter-spacing: .05em; margin-bottom: 4px; }

.sdev-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 14px 0 6px; }
.sdev-diag { font-size: 11px; color: #666; margin-top: 4px; }
.sdev-diag-issue { color: #f87171; }
.sdev-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        # handle raw rate (0-1) vs percentage (>1) gracefully
        if abs(f) <= 1.5:
            f *= 100.0
        return f"{f:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f > 0 else ""
        return f"{sign}${f:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _pf_class(v: Any) -> str:
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f >= 1.5:
            return "pos"
        if f >= 1.0:
            return "warn"
        return "neg"
    except (TypeError, ValueError):
        return "neutral"


def _val_class(v: Any, pos_good: bool = True) -> str:
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f == 0:
            return "neutral"
        return ("pos" if f > 0 else "neg") if pos_good else ("neg" if f > 0 else "pos")
    except (TypeError, ValueError):
        return "neutral"


# ---------------------------------------------------------------------------
# SVG equity sparkline
# ---------------------------------------------------------------------------

def _sparkline(equity_curve: list, width: int = 400, height: int = 60) -> str:
    """Minimal inline SVG sparkline from equity curve values."""
    vals = []
    for v in equity_curve:
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            pass
    if len(vals) < 2:
        return ""

    mn  = min(vals)
    mx  = max(vals)
    rng = mx - mn or 1.0

    def _x(i: int) -> float:
        return i / (len(vals) - 1) * width

    def _y(v: float) -> float:
        return height - (v - mn) / rng * height

    points = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(vals))
    last_color = "#22c55e" if vals[-1] >= vals[0] else "#ef4444"

    # Zero line
    zero_y = _y(0.0) if mn <= 0 <= mx else None
    zero_line = ""
    if zero_y is not None:
        zero_line = f'<line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" stroke="#444" stroke-width="1" stroke-dasharray="4,4"/>'

    return f"""
<div class="sdev-spark-wrap">
  <div class="sdev-spark-label">Equity Curve ({len(vals)} points)</div>
  <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
       style="display:block;background:#111;border-radius:4px">
    {zero_line}
    <polyline points="{points}" fill="none" stroke="{last_color}" stroke-width="1.5"/>
  </svg>
</div>"""


# ---------------------------------------------------------------------------
# Breakdown table (direction / session)
# ---------------------------------------------------------------------------

def _breakdown_row(label: str, d: dict) -> str:
    n   = d.get("n_trades", 0)
    pf  = d.get("profit_factor")
    wr  = d.get("win_rate")
    net = d.get("net_profit")
    avg = d.get("avg_trade")

    return f"""
    <tr>
      <td style="font-weight:600">{label}</td>
      <td>{n}</td>
      <td class="{_pf_class(pf)}">{_fmt(pf)}</td>
      <td>{_pct(wr)}</td>
      <td class="{_val_class(net)}">{_usd(net)}</td>
      <td class="{_val_class(avg)}">{_usd(avg)}</td>
    </tr>"""


def _breakdown_table(title: str, rows_data: List[tuple]) -> str:
    """rows_data: list of (label, metrics_dict)"""
    rows_html = "".join(_breakdown_row(lbl, d) for lbl, d in rows_data)
    return f"""
<div class="sdev-section-label">{title}</div>
<div class="sdev-table-wrap">
  <table class="sdev-table">
    <thead>
      <tr>
        <th>Segment</th><th>Trades</th><th>PF</th><th>WR</th><th>Net $</th><th>Avg $</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Cross-run comparison table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    # Best: highest PF, highest WR, highest net profit, lowest max DD
    keys = ["profit_factor", "win_rate", "net_profit", "max_drawdown"]
    higher_better = {"profit_factor": True, "win_rate": True,
                     "net_profit": True, "max_drawdown": False}
    best: Dict[str, Optional[float]] = {}
    for k in keys:
        vals = []
        for _, ev in run_items:
            if not ev or ev.get("error"):
                continue
            v = ev.get(k)
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        if not vals:
            best[k] = None
        elif higher_better[k]:
            best[k] = max(vals)
        else:
            best[k] = min(vals)

    def _cell(k: str, v: Any) -> str:
        if v is None:
            return "<td>—</td>"
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return f"<td>{v}</td>"
        is_best = best.get(k) is not None and abs(fv - best[k]) < 1e-9
        cls = ' class="best"' if is_best else ""
        if k == "win_rate":
            disp = _pct(v)
        elif k in ("net_profit", "max_drawdown"):
            disp = _usd(v)
        else:
            disp = _fmt(v)
        return f"<td{cls} style=\"{'color:#86efac;font-weight:700' if is_best else ''}\">{disp}</td>"

    rows_html = ""
    for run_id, ev in run_items:
        if not ev or ev.get("error"):
            msg = (ev or {}).get("error") or "no data"
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="6" style="color:#666;font-style:italic">{msg}</td></tr>"""
            continue
        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{ev.get("n_trades","—")}</td>
          {_cell("profit_factor", ev.get("profit_factor"))}
          {_cell("win_rate",      ev.get("win_rate"))}
          {_cell("net_profit",    ev.get("net_profit"))}
          {_cell("max_drawdown",  ev.get("max_drawdown"))}
          <td style="color:#888">{_fmt(ev.get("avg_duration_min"), 0)} min</td>
        </tr>"""

    return f"""
<div class="sdev-section-label">Cross-Run Evaluation Summary</div>
<div class="sdev-cross-table-wrap">
  <div class="sdev-table-wrap">
    <table class="sdev-table">
      <thead>
        <tr>
          <th>Run</th><th>Trades</th><th>PF</th><th>WR</th>
          <th>Net $</th><th>Max DD $</th><th>Avg Hold</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Per-run card
# ---------------------------------------------------------------------------

def _render_run_card(run_id: str, ev: dict, options: dict) -> str:
    if not ev:
        return f'<div class="sdev-run-header">{run_id}</div><div class="sdev-no-data">No evaluation data.</div>'
    if ev.get("error"):
        return f'<div class="sdev-run-header">{run_id}</div><div class="sdev-no-data">Error: {ev["error"]}</div>'

    n           = ev.get("n_trades", 0)
    n_w         = ev.get("n_winners", 0)
    n_l         = ev.get("n_losers", 0)
    pf          = ev.get("profit_factor")
    wr          = ev.get("win_rate")
    net         = ev.get("net_profit")
    gross_p     = ev.get("gross_profit")
    gross_l     = ev.get("gross_loss")
    avg_t       = ev.get("avg_trade")
    avg_w       = ev.get("avg_winner")
    avg_l_val   = ev.get("avg_loser")
    exp         = ev.get("expectancy")
    max_dd      = ev.get("max_drawdown")
    avg_dur     = ev.get("avg_duration_min")
    equity      = ev.get("equity_curve") or []
    by_dir      = ev.get("by_direction") or {}
    by_sess     = ev.get("by_session") or {}
    diag        = ev.get("diagnostics") or {}

    show_spark   = bool(options.get("show_equity_curve", True))
    show_dir     = bool(options.get("show_direction", True))
    show_session = bool(options.get("show_session", True))

    issues     = diag.get("issues") or []
    issue_html = "".join(f'<span class="sdev-diag-issue"> ⚠ {i}</span>' for i in issues)

    parts = [f'<div class="sdev-run-header">{run_id}</div>']

    # Core KPI strip
    parts.append(f"""
<div class="sdev-kpi-grid">
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Trades</div>
    <div class="sdev-kpi-value neutral">{n} <span style="font-size:11px;color:#555">({n_w}W/{n_l}L)</span></div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Profit Factor</div>
    <div class="sdev-kpi-value {_pf_class(pf)}">{_fmt(pf)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Win Rate</div>
    <div class="sdev-kpi-value neutral">{_pct(wr)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Net Profit</div>
    <div class="sdev-kpi-value {_val_class(net)}">{_usd(net)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Expectancy</div>
    <div class="sdev-kpi-value {_val_class(exp)}">{_usd(exp)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Max DD</div>
    <div class="sdev-kpi-value neg">{_usd(max_dd)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Avg Win</div>
    <div class="sdev-kpi-value pos">{_usd(avg_w)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Avg Loss</div>
    <div class="sdev-kpi-value neg">{_usd(avg_l_val)}</div>
  </div>
  <div class="sdev-kpi">
    <div class="sdev-kpi-label">Avg Hold</div>
    <div class="sdev-kpi-value neutral">{_fmt(avg_dur, 0)} min</div>
  </div>
</div>""")

    if show_spark and equity:
        parts.append(_sparkline(equity))

    if show_dir and by_dir:
        dir_rows = sorted(by_dir.items())
        parts.append(_breakdown_table("Direction Breakdown", dir_rows))

    if show_session and by_sess:
        sess_rows = sorted(by_sess.items())
        parts.append(_breakdown_table("Session Breakdown", sess_rows))

    parts.append(f"""
<div class="sdev-diag">
  profit col: <code>{diag.get("profit_col_used","?")}</code>
  {issue_html}
</div>""")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_evaluation(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        ev = sd.get("evaluation") or {}
        run_items.append((run_id, ev))

    if not run_items:
        return f"{_CSS}<div class='sdev-wrap'><div class='sdev-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdev-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, ev in run_items:
            parts.append(_render_run_card(run_id, ev, options))

    parts.append("</div>")
    return "\n".join(parts)
