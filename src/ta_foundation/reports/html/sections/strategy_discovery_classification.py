# src/ta_foundation/reports/html/sections/strategy_discovery_classification.py
"""
Strategy Discovery — Classification Section
============================================
Renders the strategy automation-level classification: automated / hybrid /
semi-discretionary verdict with signal bar chart and human-readable reasoning.

CSS prefix: .sdcl-
"""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdcl-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdcl-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 10px; }

/* verdict badge */
.sdcl-verdict { display: inline-flex; align-items: center; gap: 10px;
                padding: 10px 18px; border-radius: 10px; margin: 0 0 14px; }
.sdcl-verdict.automated        { background: #14532d; border: 1px solid #22c55e; }
.sdcl-verdict.hybrid           { background: #1e3a5f; border: 1px solid #3b82f6; }
.sdcl-verdict.semi_discretionary { background: #451a03; border: 1px solid #f59e0b; }
.sdcl-verdict-label { font-size: 18px; font-weight: 800; letter-spacing: .03em; }
.sdcl-verdict.automated         .sdcl-verdict-label { color: #86efac; }
.sdcl-verdict.hybrid            .sdcl-verdict-label { color: #93c5fd; }
.sdcl-verdict.semi_discretionary .sdcl-verdict-label { color: #fcd34d; }
.sdcl-verdict-meta { font-size: 11px; color: #aaa; }

/* score gauge */
.sdcl-gauge-wrap { margin: 0 0 16px; }
.sdcl-gauge-track { height: 14px; background: #2a2a3a; border-radius: 7px;
                    overflow: hidden; position: relative; width: 100%; max-width: 400px; }
.sdcl-gauge-fill  { height: 100%; border-radius: 7px; }
.sdcl-gauge-ticks { display: flex; justify-content: space-between; max-width: 400px;
                    font-size: 10px; color: #555; margin-top: 2px; }

/* signal bars */
.sdcl-signals { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 16px; }
.sdcl-signal-row { display: flex; align-items: center; gap: 8px; }
.sdcl-signal-name { font-size: 11px; color: #888; width: 200px; flex-shrink: 0; }
.sdcl-signal-bg   { flex: 1; height: 10px; background: #2a2a3a; border-radius: 3px;
                    overflow: hidden; max-width: 300px; }
.sdcl-signal-fill { height: 100%; border-radius: 3px; }
.sdcl-signal-val  { font-size: 11px; color: #aaa; width: 32px; text-align: right; }

/* reasoning list */
.sdcl-reasons { list-style: none; padding: 0; margin: 8px 0 16px; }
.sdcl-reasons li { font-size: 12px; color: #bbb; padding: 4px 0;
                   border-bottom: 1px solid #222; line-height: 1.5; }
.sdcl-reasons li:last-child { border-bottom: none; }
.sdcl-reasons li::before { content: "→ "; color: #a78bfa; }

/* cross-run table */
.sdcl-table-wrap { overflow-x: auto; margin: 8px 0 20px; }
.sdcl-table { border-collapse: collapse; min-width: 500px; width: 100%; }
.sdcl-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdcl-table td { padding: 6px 10px; border-bottom: 1px solid #2a2a2a; color: #ccc;
                 font-size: 12px; }
.sdcl-table tr:hover td { background: #1a1a2a; }

.sdcl-badge { display: inline-block; padding: 2px 10px; border-radius: 10px;
              font-size: 11px; font-weight: 700; letter-spacing: .03em; }
.sdcl-badge.automated         { background: #14532d; color: #86efac; }
.sdcl-badge.hybrid            { background: #1e3a5f; color: #93c5fd; }
.sdcl-badge.semi_discretionary { background: #451a03; color: #fcd34d; }

.sdcl-conf { font-size: 10px; color: #888; }
.sdcl-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 12px 0 6px; }
.sdcl-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
</style>
"""

# Signal display names
_SIGNAL_LABELS: Dict[str, str] = {
    "has_settings":            "Settings file present",
    "has_regime_data":         "Regime data available",
    "pattern_coverage":        "Pattern engine coverage",
    "pattern_predictive":      "Pattern score predictive",
    "session_spread":          "Session diversity",
    "hold_time_consistency":   "Hold time consistency",
    "session_wr_consistency":  "Session WR consistency",
    "trade_count":             "Trade count (≥100)",
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


def _verdict_display(label: str) -> str:
    return {
        "automated":          "Automated",
        "hybrid":             "Hybrid",
        "semi_discretionary": "Semi-Discretionary",
    }.get(label, label.replace("_", " ").title())


def _gauge_color(score: float) -> str:
    if score >= 70:
        return "#22c55e"
    if score >= 40:
        return "#3b82f6"
    return "#f59e0b"


def _signal_bar_color(v: float) -> str:
    if v >= 0.8:
        return "#22c55e"
    if v >= 0.5:
        return "#86efac"
    if v >= 0.2:
        return "#f59e0b"
    return "#6b7280"


def _conf_badge(confidence: str) -> str:
    colors = {"high": "#22c55e", "moderate": "#f59e0b", "low": "#ef4444"}
    c = colors.get(confidence, "#888")
    return f'<span class="sdcl-conf" style="color:{c}">confidence: {confidence}</span>'


def _label_badge(label: str) -> str:
    return f'<span class="sdcl-badge {label}">{_verdict_display(label)}</span>'


# ---------------------------------------------------------------------------
# Cross-run table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    rows_html = ""
    for run_id, cl in run_items:
        if not cl or cl.get("error"):
            msg = (cl or {}).get("error") or "no data"
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="3" style="color:#666;font-style:italic">{msg}</td></tr>"""
            continue
        label  = cl.get("label") or "unknown"
        score  = cl.get("automation_score")
        conf   = cl.get("confidence") or "low"

        score_bar = ""
        if score is not None:
            width = int(min(float(score), 100))
            gc = _gauge_color(float(score))
            score_bar = f"""
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1;height:8px;background:#2a2a3a;border-radius:4px;overflow:hidden;max-width:150px">
                <div style="width:{width}%;height:100%;background:{gc};border-radius:4px"></div>
              </div>
              <span style="font-size:11px;color:#aaa">{_fmt(score)}</span>
            </div>"""

        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{_label_badge(label)}</td>
          <td>{score_bar}</td>
          <td>{_conf_badge(conf)}</td>
        </tr>"""

    return f"""
<div class="sdcl-section-label">Cross-Run Automation Classification</div>
<div class="sdcl-table-wrap">
  <table class="sdcl-table">
    <thead>
      <tr>
        <th>Run</th>
        <th>Classification</th>
        <th>Automation Score (0–100)</th>
        <th>Confidence</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Per-run card
# ---------------------------------------------------------------------------

def _render_run_card(run_id: str, cl: dict) -> str:
    if not cl:
        return f'<div class="sdcl-run-header">{run_id}</div><div class="sdcl-no-data">No classification data.</div>'
    if cl.get("error"):
        return f'<div class="sdcl-run-header">{run_id}</div><div class="sdcl-no-data">Error: {cl["error"]}</div>'

    label    = cl.get("label") or "unknown"
    score    = cl.get("automation_score")
    conf     = cl.get("confidence") or "low"
    signals  = cl.get("signals") or {}
    reasons  = cl.get("reasoning") or []

    score_f   = float(score) if score is not None else 0.0
    gc        = _gauge_color(score_f)
    width_pct = int(min(score_f, 100))

    # Gauge
    gauge_html = f"""
<div class="sdcl-gauge-wrap">
  <div class="sdcl-gauge-track">
    <div class="sdcl-gauge-fill" style="width:{width_pct}%;background:{gc}"></div>
  </div>
  <div class="sdcl-gauge-ticks">
    <span>0</span><span>Semi-disc (0–40)</span><span>Hybrid (40–70)</span><span>Auto (70–100)</span>
  </div>
</div>"""

    # Signal bars
    sig_rows = ""
    for key, label_text in _SIGNAL_LABELS.items():
        v = signals.get(key)
        if v is None:
            continue
        vf     = float(v)
        width  = int(vf * 100)
        fc     = _signal_bar_color(vf)
        sig_rows += f"""
        <div class="sdcl-signal-row">
          <span class="sdcl-signal-name">{label_text}</span>
          <div class="sdcl-signal-bg">
            <div class="sdcl-signal-fill" style="width:{width}%;background:{fc}"></div>
          </div>
          <span class="sdcl-signal-val">{vf:.2f}</span>
        </div>"""

    # Reasoning
    reason_items = "".join(f"<li>{r}</li>" for r in reasons)

    return f"""
<div class="sdcl-run-header">{run_id}</div>

<div class="sdcl-verdict {label}">
  <div>
    <div class="sdcl-verdict-label">{_verdict_display(label)}</div>
    <div class="sdcl-verdict-meta">
      Score: {_fmt(score)} / 100 &nbsp;|&nbsp; {_conf_badge(conf)}
    </div>
  </div>
</div>

{gauge_html}

<div class="sdcl-section-label">Signal Breakdown</div>
<div class="sdcl-signals">{sig_rows}</div>

<div class="sdcl-section-label">Reasoning</div>
<ul class="sdcl-reasons">{reason_items}</ul>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_classification(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        cl = sd.get("classification") or {}
        run_items.append((run_id, cl))

    if not run_items:
        return f"{_CSS}<div class='sdcl-wrap'><div class='sdcl-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdcl-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, cl in run_items:
            parts.append(_render_run_card(run_id, cl))

    parts.append("</div>")
    return "\n".join(parts)
