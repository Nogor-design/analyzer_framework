# src/ta_foundation/reports/html/sections/strategy_discovery_drawdown.py
"""
Strategy Discovery — Drawdown Analysis Section
===============================================
Renders deterministic drawdown metrics from the historical equity curve:
max DD, streaks, Ulcer Index, recovery factor, rolling worst-case windows,
and regime breakdown.

CSS prefix: .sdda-
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdda-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdda-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

.sdda-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }
.sdda-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 10px 16px; min-width: 110px; text-align: center; }
.sdda-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 3px; }
.sdda-kpi-value { font-size: 18px; font-weight: 700; color: #e8e8e8; }
.sdda-kpi-value.good { color: #22c55e; }
.sdda-kpi-value.warn { color: #f59e0b; }
.sdda-kpi-value.bad  { color: #ef4444; }
.sdda-kpi-value.neutral { color: #94a3b8; }

.sdda-table-wrap { overflow-x: auto; margin: 8px 0 16px; }
.sdda-table { border-collapse: collapse; min-width: 500px; width: 100%; }
.sdda-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdda-table td { padding: 6px 10px; border-bottom: 1px solid #2a2a2a; color: #ccc;
                 font-size: 12px; }
.sdda-table tr:hover td { background: #1a1a2a; }
.sdda-table td.best { color: #86efac; font-weight: 700; }

.sdda-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 14px 0 6px; }

.sdda-streak-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 14px; }
.sdda-streak { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
               padding: 10px 14px; min-width: 140px; }
.sdda-streak-label { font-size: 10px; color: #888; text-transform: uppercase;
                     letter-spacing: .05em; margin-bottom: 3px; }
.sdda-streak-value { font-size: 17px; font-weight: 700; }

.sdda-rolling-bar { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.sdda-rolling-label { font-size: 10px; color: #888; width: 80px; flex-shrink: 0; }
.sdda-rolling-bg { flex: 1; height: 10px; background: #2a2a3a; border-radius: 3px; overflow: hidden; }
.sdda-rolling-fill { height: 100%; border-radius: 3px; background: #ef4444; }
.sdda-rolling-val { font-size: 11px; color: #aaa; width: 45px; text-align: right; }

.sdda-regime-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 14px; }
.sdda-regime-card { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
                    padding: 10px 14px; min-width: 160px; }
.sdda-regime-name { font-size: 12px; font-weight: 700; color: #a78bfa; margin-bottom: 5px; }
.sdda-regime-row { display: flex; justify-content: space-between; gap: 16px;
                   font-size: 11px; color: #aaa; margin-bottom: 2px; }
.sdda-regime-row span:last-child { color: #e8e8e8; font-weight: 600; }

.sdda-diag { font-size: 11px; color: #666; margin-top: 4px; }
.sdda-diag-issue { color: #f87171; }
.sdda-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _pct(v: Any, decimals: int = 1) -> str:
    return _fmt(v, decimals, "%")


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "-" if f < 0 else ""
        return f"{sign}${abs(f):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _dd_class(v: Any) -> str:
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f < 10:
            return "good"
        if f < 20:
            return "warn"
        return "bad"
    except (TypeError, ValueError):
        return "neutral"


def _rf_class(v: Any) -> str:
    """Recovery factor — higher is better."""
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f >= 3.0:
            return "good"
        if f >= 1.0:
            return "warn"
        return "bad"
    except (TypeError, ValueError):
        return "neutral"


def _streak_class(v: Any) -> str:
    """Fewer consecutive losses = better."""
    if v is None:
        return "neutral"
    try:
        n = int(v)
        if n <= 3:
            return "good"
        if n <= 6:
            return "warn"
        return "bad"
    except (TypeError, ValueError):
        return "neutral"


# ---------------------------------------------------------------------------
# Cross-run comparison table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    # best value per column: lower is better for DD/streaks, higher for rf/ulcer
    col_def = [
        ("max_drawdown_pct",      "overall",  False),
        ("recovery_factor",       "overall",  True),
        ("ulcer_index",           "overall",  False),
        ("max_consecutive_losses","streaks",  False),
    ]
    best: Dict[str, Optional[float]] = {}
    for key, src, higher in col_def:
        vals = []
        for _, da in run_items:
            if not da or da.get("skipped") or da.get("error"):
                continue
            block = da.get(src) or {}
            v = block.get(key)
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        if not vals:
            best[key] = None
        elif higher:
            best[key] = max(vals)
        else:
            best[key] = min(vals)

    def _cell(key: str, v: Any) -> str:
        if v is None:
            return "<td>—</td>"
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return f"<td>{v}</td>"
        is_best = best.get(key) is not None and abs(fv - best[key]) < 1e-9
        cls = ' class="best"' if is_best else ""
        disp = _pct(v) if "pct" in key else _fmt(v, 2)
        return f"<td{cls}>{disp}</td>"

    rows_html = ""
    for run_id, da in run_items:
        if not da or da.get("skipped") or da.get("error"):
            msg = (da or {}).get("error") or "skipped"
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="5" style="color:#666;font-style:italic">{msg}</td></tr>"""
            continue
        ov = da.get("overall") or {}
        st = da.get("streaks") or {}
        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          {_cell("max_drawdown_pct",       ov.get("max_drawdown_pct"))}
          {_cell("recovery_factor",        ov.get("recovery_factor"))}
          {_cell("ulcer_index",            ov.get("ulcer_index"))}
          <td>{ov.get("longest_drawdown_bars") or "—"}</td>
          {_cell("max_consecutive_losses", st.get("max_consecutive_losses"))}
        </tr>"""

    return f"""
<div class="sdda-section-label">Cross-Run Drawdown Comparison</div>
<div class="sdda-table-wrap">
  <table class="sdda-table">
    <thead>
      <tr>
        <th>Run</th>
        <th>Max DD</th>
        <th>Recovery Factor</th>
        <th>Ulcer Index</th>
        <th>Longest DD (trades)</th>
        <th>Max Cons. Losses</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Per-run renderers
# ---------------------------------------------------------------------------

def _render_overall(overall: dict) -> str:
    max_dd_pct   = overall.get("max_drawdown_pct")
    max_dd_usd   = overall.get("max_drawdown_usd")
    rf           = overall.get("recovery_factor")
    ulcer        = overall.get("ulcer_index")
    avg_dd       = overall.get("avg_drawdown_pct")
    longest      = overall.get("longest_drawdown_bars")
    rec_bars     = overall.get("recovery_bars")
    start_dt     = overall.get("max_dd_start") or "—"
    trough_dt    = overall.get("max_dd_trough") or "—"
    rec_dt       = overall.get("max_dd_recovery") or "unrecovered"

    return f"""
<div class="sdda-kpi-grid">
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Max DD</div>
    <div class="sdda-kpi-value {_dd_class(max_dd_pct)}">{_pct(max_dd_pct)}</div>
  </div>
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Max DD ($)</div>
    <div class="sdda-kpi-value bad">{_usd(max_dd_usd)}</div>
  </div>
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Recovery Factor</div>
    <div class="sdda-kpi-value {_rf_class(rf)}">{_fmt(rf, 2)}</div>
  </div>
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Ulcer Index</div>
    <div class="sdda-kpi-value {_dd_class(ulcer)}">{_fmt(ulcer, 3)}</div>
  </div>
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Avg DD</div>
    <div class="sdda-kpi-value neutral">{_pct(avg_dd)}</div>
  </div>
  <div class="sdda-kpi">
    <div class="sdda-kpi-label">Longest DD</div>
    <div class="sdda-kpi-value neutral">{longest or "—"} <span style="font-size:10px;color:#888">trades</span></div>
  </div>
</div>
<div style="font-size:11px;color:#777;margin:-6px 0 12px;">
  Max DD: {start_dt} → {trough_dt} | Recovery: {rec_dt}
  {f"({rec_bars} trades)" if rec_bars else ""}
</div>"""


def _render_streaks(streaks: dict) -> str:
    max_l  = streaks.get("max_consecutive_losses")
    max_w  = streaks.get("max_consecutive_wins")
    avg_l  = streaks.get("avg_loss_streak_len")
    avg_w  = streaks.get("avg_win_streak_len")
    usd_l  = streaks.get("max_loss_streak_usd")
    n_l    = streaks.get("n_loss_streaks", 0)
    n_w    = streaks.get("n_win_streaks", 0)

    return f"""
<div class="sdda-section-label">Streak Analysis</div>
<div class="sdda-streak-grid">
  <div class="sdda-streak">
    <div class="sdda-streak-label">Max Cons. Losses</div>
    <div class="sdda-streak-value" style="color:{
        '#22c55e' if max_l and max_l <= 3 else '#f59e0b' if max_l and max_l <= 6 else '#ef4444'
    }">{max_l or "—"}</div>
  </div>
  <div class="sdda-streak">
    <div class="sdda-streak-label">Worst Losing Streak</div>
    <div class="sdda-streak-value" style="color:#ef4444">{_usd(usd_l)}</div>
  </div>
  <div class="sdda-streak">
    <div class="sdda-streak-label">Avg Loss Streak</div>
    <div class="sdda-streak-value" style="color:#94a3b8">{_fmt(avg_l, 1)}</div>
  </div>
  <div class="sdda-streak">
    <div class="sdda-streak-label">Max Cons. Wins</div>
    <div class="sdda-streak-value" style="color:#22c55e">{max_w or "—"}</div>
  </div>
  <div class="sdda-streak">
    <div class="sdda-streak-label">Avg Win Streak</div>
    <div class="sdda-streak-value" style="color:#86efac">{_fmt(avg_w, 1)}</div>
  </div>
</div>
<div style="font-size:11px;color:#666;margin:-6px 0 12px;">
  {n_l} losing streak(s) | {n_w} winning streak(s)
</div>"""


def _render_rolling(rolling: list, top_n: int = 10) -> str:
    if not rolling:
        return ""
    # Show last top_n windows
    items = rolling[-top_n:]
    if not items:
        return ""
    max_dd = max((r.get("max_dd_pct") or 0.0) for r in items)
    if max_dd == 0:
        max_dd = 1.0

    bars_html = ""
    for r in items:
        end    = str(r.get("period_end") or "")[:10]
        dd     = r.get("max_dd_pct") or 0.0
        width  = int(dd / max_dd * 100)
        fill_col = "#22c55e" if dd < 10 else "#f59e0b" if dd < 20 else "#ef4444"
        bars_html += f"""
        <div class="sdda-rolling-bar">
          <div class="sdda-rolling-label">{end}</div>
          <div class="sdda-rolling-bg">
            <div class="sdda-rolling-fill" style="width:{width}%;background:{fill_col}"></div>
          </div>
          <div class="sdda-rolling-val">{_pct(dd)}</div>
        </div>"""

    return f"""
<div class="sdda-section-label">Rolling Max DD (last {len(items)} windows)</div>
{bars_html}"""


def _render_regime_breakdown(by_regime: dict) -> str:
    if not by_regime:
        return ""
    cards = ""
    for regime, rd in sorted(by_regime.items()):
        cards += f"""
        <div class="sdda-regime-card">
          <div class="sdda-regime-name">{regime}</div>
          <div class="sdda-regime-row">
            <span>Max DD</span>
            <span style="color:{
                '#22c55e' if (rd.get('max_drawdown_pct') or 100) < 10
                else '#f59e0b' if (rd.get('max_drawdown_pct') or 100) < 20
                else '#ef4444'
            }">{_pct(rd.get("max_drawdown_pct"))}</span>
          </div>
          <div class="sdda-regime-row">
            <span>Max Cons. Losses</span>
            <span>{rd.get("max_consecutive_losses") or "—"}</span>
          </div>
        </div>"""
    return f"""
<div class="sdda-section-label">Regime Breakdown ({len(by_regime)} regimes)</div>
<div class="sdda-regime-grid">{cards}</div>"""


def _render_run_card(run_id: str, da: dict, options: dict) -> str:
    if not da:
        return f'<div class="sdda-run-header">{run_id}</div><div class="sdda-no-data">No drawdown data.</div>'
    if da.get("skipped"):
        return f'<div class="sdda-run-header">{run_id}</div><div class="sdda-no-data">Drawdown analysis disabled.</div>'
    if da.get("error"):
        return f'<div class="sdda-run-header">{run_id}</div><div class="sdda-no-data">Error: {da["error"]}</div>'

    overall   = da.get("overall") or {}
    streaks   = da.get("streaks") or {}
    rolling   = da.get("rolling_max_dd") or []
    by_regime = da.get("by_regime") or {}
    diag      = da.get("diagnostics") or {}

    show_rolling = bool(options.get("show_rolling", True))
    show_regime  = bool(options.get("show_regime", True))
    top_n_rolling = int(options.get("top_n_rolling", 10))

    issues = diag.get("issues") or []
    issue_html = "".join(f'<span class="sdda-diag-issue"> ⚠ {i}</span>' for i in issues)

    parts = [f'<div class="sdda-run-header">{run_id}</div>']

    if overall:
        parts.append(_render_overall(overall))

    if streaks:
        parts.append(_render_streaks(streaks))

    if show_rolling and rolling:
        parts.append(_render_rolling(rolling, top_n=top_n_rolling))

    if show_regime and by_regime:
        parts.append(_render_regime_breakdown(by_regime))

    parts.append(f"""
<div class="sdda-diag">
  n={diag.get("n_trades","?")} &nbsp;|&nbsp;
  equity base: ${float(diag.get("initial_equity", 10000)):,.0f} &nbsp;|&nbsp;
  profit col: <code>{diag.get("profit_col_used","?")}</code>
  {issue_html}
</div>""")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_drawdown(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        da = sd.get("drawdown_analysis") or {}
        run_items.append((run_id, da))

    if not run_items:
        return f"{_CSS}<div class='sdda-wrap'><div class='sdda-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdda-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, da in run_items:
            parts.append(_render_run_card(run_id, da, options))

    parts.append("</div>")
    return "\n".join(parts)
