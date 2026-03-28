# src/ta_foundation/reports/html/sections/strategy_discovery_regime.py
"""
Strategy Discovery — Market Regime Section
===========================================
Renders per-run regime_summary: the daily market regime distribution table
(dominant_regime, pct_trending, pct_ranging, pct_high_vol, avg_adx, avg_atr)
plus an aggregate distribution summary and a recent-days spotlight table.

CSS prefix: .sdre-
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdre-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdre-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

/* distribution bar chart */
.sdre-dist-row { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.sdre-dist-label { font-size: 11px; color: #aaa; width: 190px; flex-shrink: 0; }
.sdre-dist-bg    { flex: 1; height: 14px; background: #2a2a3a; border-radius: 4px;
                   overflow: hidden; max-width: 320px; }
.sdre-dist-fill  { height: 100%; border-radius: 4px; }
.sdre-dist-val   { font-size: 11px; color: #888; min-width: 55px; text-align: right; }

/* summary pills */
.sdre-pill-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 16px; }
.sdre-pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px;
             border-radius: 12px; font-size: 12px; font-weight: 600; }

/* data table */
.sdre-table-wrap { overflow-x: auto; margin: 8px 0 16px; }
.sdre-table { border-collapse: collapse; min-width: 520px; width: 100%; }
.sdre-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdre-table td { padding: 5px 10px; border-bottom: 1px solid #222; color: #ccc;
                 font-size: 12px; }
.sdre-table tr:hover td { background: #1a1a2a; }

/* regime label colour chips */
.sdre-regime-chip { display: inline-block; padding: 1px 8px; border-radius: 8px;
                    font-size: 10px; font-weight: 700; letter-spacing: .04em; }

.sdre-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 14px; }
.sdre-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 9px 14px; min-width: 100px; text-align: center; }
.sdre-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 2px; }
.sdre-kpi-value { font-size: 16px; font-weight: 700; color: #e8e8e8; }

.sdre-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 14px 0 6px; }
.sdre-issue { color: #f87171; font-size: 11px; margin: 4px 0; }
.sdre-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
</style>
"""

# Palette for regime labels
_REGIME_COLORS: Dict[str, tuple] = {
    "trending_up":         ("#14532d", "#86efac"),
    "trending_down":       ("#7f1d1d", "#fca5a5"),
    "ranging_wide":        ("#1e3a5f", "#93c5fd"),
    "ranging_tight":       ("#1e3a5f", "#bfdbfe"),
    "low_vol_compression": ("#1e3a5f", "#e0f2fe"),
    "high_vol_expansion":  ("#451a03", "#fcd34d"),
}

_DIST_COLORS = {
    "trending":  "#22c55e",
    "ranging":   "#3b82f6",
    "high_vol":  "#f59e0b",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _regime_chip(label: str) -> str:
    bg, fg = _REGIME_COLORS.get(label, ("#2a2a3a", "#aaa"))
    display = label.replace("_", " ").title() if label else "—"
    return f'<span class="sdre-regime-chip" style="background:{bg};color:{fg}">{display}</span>'


# ---------------------------------------------------------------------------
# Aggregate statistics from a list of day records
# ---------------------------------------------------------------------------

def _aggregate(records: List[dict]) -> Dict[str, Any]:
    if not records:
        return {}

    n = len(records)
    regime_counts: Counter = Counter()
    sum_trending = sum_ranging = sum_high_vol = 0.0
    sum_adx = sum_atr = 0.0
    n_adx = n_atr = 0

    for r in records:
        dom = r.get("dominant_regime")
        if dom:
            regime_counts[dom] += 1
        sum_trending += float(r.get("pct_trending") or 0)
        sum_ranging  += float(r.get("pct_ranging")  or 0)
        sum_high_vol += float(r.get("pct_high_vol") or 0)
        adx = r.get("avg_adx")
        if adx is not None:
            sum_adx += float(adx)
            n_adx += 1
        atr = r.get("avg_atr")
        if atr is not None:
            sum_atr += float(atr)
            n_atr += 1

    # Sort by count descending
    top_regime = regime_counts.most_common(1)[0][0] if regime_counts else None

    return {
        "n_days":         n,
        "regime_counts":  dict(regime_counts),
        "top_regime":     top_regime,
        "avg_pct_trending": sum_trending / n,
        "avg_pct_ranging":  sum_ranging  / n,
        "avg_pct_high_vol": sum_high_vol / n,
        "avg_adx": sum_adx / n_adx if n_adx > 0 else None,
        "avg_atr": sum_atr / n_atr if n_atr > 0 else None,
    }


# ---------------------------------------------------------------------------
# Distribution bar chart
# ---------------------------------------------------------------------------

def _render_distribution(agg: dict) -> str:
    avg_t  = agg.get("avg_pct_trending", 0.0)
    avg_r  = agg.get("avg_pct_ranging",  0.0)
    avg_hv = agg.get("avg_pct_high_vol", 0.0)

    def _bar(label: str, pct: float, color: str) -> str:
        w = int(min(pct, 100))
        return f"""
        <div class="sdre-dist-row">
          <div class="sdre-dist-label">Avg % {label}</div>
          <div class="sdre-dist-bg">
            <div class="sdre-dist-fill" style="width:{w}%;background:{color}"></div>
          </div>
          <div class="sdre-dist-val">{_fmt(pct)}%</div>
        </div>"""

    return (
        _bar("Trending",  avg_t,  _DIST_COLORS["trending"]) +
        _bar("Ranging",   avg_r,  _DIST_COLORS["ranging"])  +
        _bar("High Vol",  avg_hv, _DIST_COLORS["high_vol"])
    )


# ---------------------------------------------------------------------------
# Regime count pills
# ---------------------------------------------------------------------------

def _render_regime_pills(regime_counts: dict, n_days: int) -> str:
    pills = ""
    for regime, count in sorted(regime_counts.items(), key=lambda x: -x[1]):
        bg, fg = _REGIME_COLORS.get(regime, ("#2a2a3a", "#aaa"))
        pct    = count / n_days * 100 if n_days > 0 else 0
        pills += f"""
        <span class="sdre-pill" style="background:{bg};color:{fg}">
          {regime.replace("_"," ").title()} &nbsp;
          <span style="font-size:10px;opacity:.8">{count}d ({pct:.0f}%)</span>
        </span>"""
    return f'<div class="sdre-pill-row">{pills}</div>'


# ---------------------------------------------------------------------------
# Recent-days table (last N records)
# ---------------------------------------------------------------------------

def _render_recent_table(records: List[dict], max_rows: int = 20) -> str:
    recent = records[-max_rows:]
    rows_html = ""
    for r in recent:
        day   = r.get("day_id", "—")
        dom   = r.get("dominant_regime") or ""
        pct_t = r.get("pct_trending")
        pct_r = r.get("pct_ranging")
        pct_h = r.get("pct_high_vol")
        adx   = r.get("avg_adx")
        atr   = r.get("avg_atr")

        rows_html += f"""
        <tr>
          <td style="color:#888;font-size:11px">{day}</td>
          <td>{_regime_chip(dom)}</td>
          <td>{_fmt(pct_t)}%</td>
          <td>{_fmt(pct_r)}%</td>
          <td>{_fmt(pct_h)}%</td>
          <td style="color:#888">{_fmt(adx)}</td>
          <td style="color:#888">{_fmt(atr)}</td>
        </tr>"""

    return f"""
<div class="sdre-section-label">Recent Days (last {len(recent)} of {len(records)})</div>
<div class="sdre-table-wrap">
  <table class="sdre-table">
    <thead>
      <tr>
        <th>Day</th><th>Dominant Regime</th>
        <th>% Trending</th><th>% Ranging</th><th>% High Vol</th>
        <th>Avg ADX</th><th>Avg ATR</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Per-run card
# ---------------------------------------------------------------------------

def _render_run_card(run_id: str, records: list, issues: list, options: dict) -> str:
    show_recent  = bool(options.get("show_recent_days", True))
    max_rows     = int(options.get("max_recent_days", 20))

    if not records:
        issues_html = ""
        for iss in issues:
            issues_html += f'<div class="sdre-issue">⚠ {iss}</div>'
        return f"""
<div class="sdre-run-header">{run_id}</div>
<div class="sdre-no-data">No regime data.{issues_html}</div>"""

    agg = _aggregate(records)
    n   = agg.get("n_days", 0)

    issues_html = "".join(f'<div class="sdre-issue">⚠ {i}</div>' for i in issues)

    parts = [f'<div class="sdre-run-header">{run_id}</div>']

    # Summary KPIs
    parts.append(f"""
<div class="sdre-kpi-grid">
  <div class="sdre-kpi">
    <div class="sdre-kpi-label">Days</div>
    <div class="sdre-kpi-value">{n}</div>
  </div>
  <div class="sdre-kpi">
    <div class="sdre-kpi-label">Dominant Regime</div>
    <div class="sdre-kpi-value" style="font-size:13px">{_regime_chip(agg.get("top_regime",""))}</div>
  </div>
  <div class="sdre-kpi">
    <div class="sdre-kpi-label">Avg ADX</div>
    <div class="sdre-kpi-value">{_fmt(agg.get("avg_adx"))}</div>
  </div>
  <div class="sdre-kpi">
    <div class="sdre-kpi-label">Avg ATR</div>
    <div class="sdre-kpi-value">{_fmt(agg.get("avg_atr"))}</div>
  </div>
</div>""")

    # Distribution bars
    parts.append('<div class="sdre-section-label">Avg Intraday Composition</div>')
    parts.append(_render_distribution(agg))

    # Regime frequency pills
    parts.append('<div class="sdre-section-label">Dominant Regime Frequency</div>')
    parts.append(_render_regime_pills(agg.get("regime_counts", {}), n))

    if show_recent:
        parts.append(_render_recent_table(records, max_rows))

    if issues_html:
        parts.append(issues_html)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Cross-run summary: only shown if runs differ (different instruments)
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    rows_html = ""
    for run_id, (records, _issues) in run_items:
        if not records:
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="5" style="color:#666;font-style:italic">no regime data</td></tr>"""
            continue
        agg    = _aggregate(records)
        n      = agg.get("n_days", 0)
        top    = agg.get("top_regime") or "—"
        avg_t  = agg.get("avg_pct_trending", 0)
        avg_hv = agg.get("avg_pct_high_vol", 0)
        adx    = agg.get("avg_adx")
        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{n}</td>
          <td>{_regime_chip(top)}</td>
          <td>{_fmt(avg_t)}%</td>
          <td>{_fmt(avg_hv)}%</td>
          <td style="color:#888">{_fmt(adx)}</td>
        </tr>"""

    return f"""
<div class="sdre-section-label">Cross-Run Regime Comparison</div>
<div class="sdre-table-wrap">
  <table class="sdre-table">
    <thead>
      <tr>
        <th>Run</th><th>Days</th><th>Dominant Regime</th>
        <th>Avg % Trending</th><th>Avg % High Vol</th><th>Avg ADX</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_regime(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd      = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        records = sd.get("regime_summary") or []
        issues  = sd.get("regime_issues") or []
        run_items.append((run_id, (records, issues)))

    if not run_items:
        return f"{_CSS}<div class='sdre-wrap'><div class='sdre-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdre-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, (records, issues) in run_items:
            parts.append(_render_run_card(run_id, records, issues, options))

    parts.append("</div>")
    return "\n".join(parts)
