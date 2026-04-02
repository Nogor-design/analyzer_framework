# src/ta_foundation/reports/html/sections/strategy_discovery_cohort.py
"""
Strategy Discovery — Cohort Analysis Section
=============================================
Renders Phase-10 cohort analysis results: strategy drift / performance decay
detected by splitting the trade history into rolling windows and tracking PF,
WR, and net profit trends.

CSS prefix: .sdco-
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdco-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdco-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

.sdco-summary-strip { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }
.sdco-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 10px 16px; min-width: 110px; text-align: center; }
.sdco-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 3px; }
.sdco-kpi-value { font-size: 18px; font-weight: 700; color: #e8e8e8; }
.sdco-kpi-value.good    { color: #22c55e; }
.sdco-kpi-value.warn    { color: #f59e0b; }
.sdco-kpi-value.bad     { color: #ef4444; }
.sdco-kpi-value.neutral { color: #94a3b8; }

.sdco-trend-badge { display: inline-flex; align-items: center; gap: 6px;
                    padding: 4px 12px; border-radius: 12px; font-size: 12px;
                    font-weight: 700; letter-spacing: .04em; margin: 0 0 12px; }
.sdco-trend-badge.improving { background: #14532d; color: #86efac; }
.sdco-trend-badge.stable    { background: #1e3a5f; color: #93c5fd; }
.sdco-trend-badge.degrading { background: #7f1d1d; color: #fca5a5; }
.sdco-trend-badge.volatile  { background: #451a03; color: #fcd34d; }
.sdco-trend-badge.unknown   { background: #2a2a3a; color: #888; }

.sdco-table-wrap { overflow-x: auto; margin: 8px 0 16px; }
.sdco-table { border-collapse: collapse; min-width: 500px; width: 100%; }
.sdco-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdco-table td { padding: 5px 10px; border-bottom: 1px solid #222; color: #ccc;
                 font-size: 12px; }
.sdco-table tr:hover td { background: #1a1a2a; }

.sdco-pf-bar-wrap { display: flex; align-items: center; gap: 6px; }
.sdco-pf-bar-bg   { flex: 1; height: 8px; background: #2a2a3a; border-radius: 3px; overflow: hidden; }
.sdco-pf-bar-fill { height: 100%; border-radius: 3px; }

.sdco-evl-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 14px; }
.sdco-evl-card { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
                 padding: 10px 14px; min-width: 160px; }
.sdco-evl-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 5px; }
.sdco-evl-val  { font-size: 17px; font-weight: 700; }
.sdco-evl-sub  { font-size: 11px; color: #666; margin-top: 2px; }

.sdco-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 14px 0 6px; }
.sdco-diag { font-size: 11px; color: #666; margin-top: 4px; }
.sdco-diag-issue { color: #f87171; }
.sdco-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
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
        sign = "+" if f > 0 else ""
        return f"{sign}${f:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _trend_badge(classification: str) -> str:
    icons = {
        "improving": "▲",
        "stable":    "→",
        "degrading": "▼",
        "volatile":  "⚡",
        "unknown":   "?",
    }
    icon = icons.get(classification, "?")
    return f'<span class="sdco-trend-badge {classification}">{icon} {classification.upper()}</span>'


def _decay_color(score: Any) -> str:
    if score is None:
        return "#94a3b8"
    try:
        s = int(score)
        if s < 30:
            return "#22c55e"
        if s < 60:
            return "#f59e0b"
        return "#ef4444"
    except (TypeError, ValueError):
        return "#94a3b8"


def _delta_class(v: Any) -> str:
    if v is None:
        return "neutral"
    try:
        f = float(v)
        return "good" if f > 0 else "bad" if f < 0 else "neutral"
    except (TypeError, ValueError):
        return "neutral"


# ---------------------------------------------------------------------------
# Cross-run comparison table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    # best: lower decay score, better classification
    classifications_rank = {"improving": 0, "stable": 1, "volatile": 2, "degrading": 3, "unknown": 4}

    rows_html = ""
    for run_id, ca in run_items:
        if not ca or ca.get("skipped") or ca.get("error"):
            msg = (ca or {}).get("error") or "skipped"
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="4" style="color:#666;font-style:italic">{msg}</td></tr>"""
            continue

        trend  = ca.get("trend") or {}
        cls    = trend.get("classification") or "unknown"
        slope  = trend.get("slope")
        score  = ca.get("decay_score")
        evl    = ca.get("early_vs_late") or {}
        dpf    = evl.get("delta_pf")
        dwr    = evl.get("delta_wr")

        dpf_cls = _delta_class(dpf)
        dwr_cls = _delta_class(dwr)
        dc_col  = _decay_color(score)

        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{_trend_badge(cls)}</td>
          <td style="color:{dc_col};font-weight:700">{score if score is not None else "—"}</td>
          <td class="{dpf_cls}">{_fmt(dpf, 3, "")}</td>
          <td class="{dwr_cls}">{_pct(dwr) if dwr is not None else "—"}</td>
        </tr>"""

    return f"""
<div class="sdco-section-label">Cross-Run Drift Summary</div>
<div class="sdco-table-wrap">
  <table class="sdco-table">
    <thead>
      <tr>
        <th>Run</th>
        <th>Trend</th>
        <th>Decay Score (0–100)</th>
        <th>ΔPF (early→late)</th>
        <th>ΔWR (early→late)</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Per-cohort table (PF bar chart style)
# ---------------------------------------------------------------------------

def _render_cohort_table(cohorts: List[dict]) -> str:
    if not cohorts:
        return '<div class="sdco-no-data">No cohort data.</div>'

    # Max PF for bar scaling (cap at 3.0 to avoid tiny bars for outlier-free runs)
    pf_vals = [float(c.get("pf") or 0) for c in cohorts if c.get("pf") is not None]
    max_pf  = max(max(pf_vals, default=1.0), 1.0)
    max_pf  = min(max_pf, 4.0)

    rows_html = ""
    for c in cohorts:
        idx   = c.get("cohort_index", "")
        start = str(c.get("period_start") or "")[:10]
        end   = str(c.get("period_end")   or "")[:10]
        n     = c.get("n_trades", 0)
        pf    = c.get("pf")
        wr    = c.get("win_rate")
        avg_p = c.get("avg_profit")
        net_p = c.get("net_profit")

        # PF bar
        if pf is not None:
            pf_f    = float(pf)
            width   = int(min(pf_f / max_pf, 1.0) * 100)
            fill_c  = "#22c55e" if pf_f >= 1.5 else "#f59e0b" if pf_f >= 1.0 else "#ef4444"
            pf_cell = f"""
            <td>
              <div class="sdco-pf-bar-wrap">
                <div class="sdco-pf-bar-bg">
                  <div class="sdco-pf-bar-fill" style="width:{width}%;background:{fill_c}"></div>
                </div>
                <span style="font-size:11px;color:{fill_c};font-weight:600;min-width:32px">{_fmt(pf, 2)}</span>
              </div>
            </td>"""
        else:
            pf_cell = "<td>—</td>"

        wr_pct  = _pct(wr) if wr is not None else "—"
        net_cls = "good" if (net_p or 0) > 0 else "bad"

        rows_html += f"""
        <tr>
          <td style="color:#888">{idx}</td>
          <td style="font-size:11px;color:#666">{start}</td>
          <td style="font-size:11px;color:#666">{end}</td>
          <td>{n}</td>
          {pf_cell}
          <td>{wr_pct}</td>
          <td>{_usd(avg_p)}</td>
          <td class="{net_cls}">{_usd(net_p)}</td>
        </tr>"""

    return f"""
<div class="sdco-section-label">Cohort Breakdown ({len(cohorts)} cohorts)</div>
<div class="sdco-table-wrap">
  <table class="sdco-table">
    <thead>
      <tr>
        <th>#</th><th>From</th><th>To</th><th>Trades</th>
        <th>PF</th><th>WR</th><th>Avg $</th><th>Net $</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


def _render_early_vs_late(evl: dict) -> str:
    if not evl:
        return ""

    pf_e  = evl.get("pf_early")
    pf_l  = evl.get("pf_late")
    wr_e  = evl.get("wr_early")
    wr_l  = evl.get("wr_late")
    dpf   = evl.get("delta_pf")
    dwr   = evl.get("delta_wr")

    dpf_c = "#22c55e" if (dpf or 0) > 0 else "#ef4444"
    dwr_c = "#22c55e" if (dwr or 0) > 0 else "#ef4444"

    return f"""
<div class="sdco-section-label">Early vs Late Cohort Comparison</div>
<div class="sdco-evl-grid">
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">PF — Early</div>
    <div class="sdco-evl-val" style="color:#94a3b8">{_fmt(pf_e, 2)}</div>
  </div>
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">PF — Late</div>
    <div class="sdco-evl-val" style="color:#94a3b8">{_fmt(pf_l, 2)}</div>
  </div>
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">ΔPF</div>
    <div class="sdco-evl-val" style="color:{dpf_c}">{_fmt(dpf, 3)}</div>
    <div class="sdco-evl-sub">late − early</div>
  </div>
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">WR — Early</div>
    <div class="sdco-evl-val" style="color:#94a3b8">{_pct(wr_e)}</div>
  </div>
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">WR — Late</div>
    <div class="sdco-evl-val" style="color:#94a3b8">{_pct(wr_l)}</div>
  </div>
  <div class="sdco-evl-card">
    <div class="sdco-evl-label">ΔWR</div>
    <div class="sdco-evl-val" style="color:{dwr_c}">{_pct(dwr)}</div>
    <div class="sdco-evl-sub">late − early</div>
  </div>
</div>"""


def _render_run_card(run_id: str, ca: dict, options: dict) -> str:
    if not ca:
        return f'<div class="sdco-run-header">{run_id}</div><div class="sdco-no-data">No cohort data.</div>'
    if ca.get("skipped"):
        return f'<div class="sdco-run-header">{run_id}</div><div class="sdco-no-data">Cohort analysis disabled.</div>'
    if ca.get("error"):
        return f'<div class="sdco-run-header">{run_id}</div><div class="sdco-no-data">Error: {ca["error"]}</div>'

    cohorts   = ca.get("cohorts") or []
    trend     = ca.get("trend") or {}
    score     = ca.get("decay_score")
    evl       = ca.get("early_vs_late") or {}
    overall   = ca.get("overall") or {}
    diag      = ca.get("diagnostics") or {}

    cls       = trend.get("classification") or "unknown"
    slope     = trend.get("slope")
    r2        = trend.get("r_squared")

    show_evl  = bool(options.get("show_early_vs_late", True))

    issues    = diag.get("issues") or []
    issue_html = "".join(f'<span class="sdco-diag-issue"> ⚠ {i}</span>' for i in issues)

    dc_col = _decay_color(score)

    # Trend slope context
    slope_desc = ""
    if slope is not None:
        slope_desc = f"slope={_fmt(slope, 4)}/cohort"
    if r2 is not None:
        slope_desc += f", R²={_fmt(r2, 3)}"

    return f"""
<div class="sdco-run-header">{run_id}</div>

{_trend_badge(cls)}

<div class="sdco-summary-strip">
  <div class="sdco-kpi">
    <div class="sdco-kpi-label">Decay Score</div>
    <div class="sdco-kpi-value" style="color:{dc_col}">{score if score is not None else "—"}</div>
  </div>
  <div class="sdco-kpi">
    <div class="sdco-kpi-label">Cohorts</div>
    <div class="sdco-kpi-value neutral">{len(cohorts)}</div>
  </div>
  <div class="sdco-kpi">
    <div class="sdco-kpi-label">Overall PF</div>
    <div class="sdco-kpi-value {'good' if (overall.get('pf') or 0) >= 1.5 else 'warn' if (overall.get('pf') or 0) >= 1.0 else 'bad'}">{_fmt(overall.get("pf"), 2)}</div>
  </div>
  <div class="sdco-kpi">
    <div class="sdco-kpi-label">Overall WR</div>
    <div class="sdco-kpi-value neutral">{_pct(overall.get("win_rate"))}</div>
  </div>
</div>
{f'<div style="font-size:11px;color:#666;margin:-8px 0 12px">{slope_desc}</div>' if slope_desc else ""}

{_render_cohort_table(cohorts)}

{_render_early_vs_late(evl) if show_evl else ""}

<div class="sdco-diag">
  {len(cohorts)} cohorts &nbsp;|&nbsp; by: {diag.get("cohort_by","?")} &nbsp;|&nbsp;
  size: {diag.get("cohort_size","?")} &nbsp;|&nbsp;
  profit col: <code>{diag.get("profit_col_used","?")}</code>
  {issue_html}
</div>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_cohort(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        ca = sd.get("cohort_analysis") or {}
        run_items.append((run_id, ca))

    if not run_items:
        return f"{_CSS}<div class='sdco-wrap'><div class='sdco-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdco-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, ca in run_items:
            parts.append(_render_run_card(run_id, ca, options))

    parts.append("</div>")
    return "\n".join(parts)
