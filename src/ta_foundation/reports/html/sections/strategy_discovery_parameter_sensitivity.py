# src/ta_foundation/reports/html/sections/strategy_discovery_parameter_sensitivity.py
"""
Strategy Discovery — Parameter Sensitivity Section
====================================================
Renders threshold-robustness analysis for the top entry rules.

For each numeric threshold condition in the top discovered entry rules,
shows a sweep of PF/WR across ±N threshold values, classifying each as
ROBUST / MODERATE / FRAGILE.

CSS prefix: .sdps2-
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdps2-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdps2-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

/* summary KPI row */
.sdps2-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 14px; }
.sdps2-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
             padding: 9px 14px; min-width: 100px; text-align: center; }
.sdps2-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                   letter-spacing: .05em; margin-bottom: 2px; }
.sdps2-kpi-value { font-size: 16px; font-weight: 700; color: #e8e8e8; }

/* classification badges */
.sdps2-badge { display: inline-block; padding: 2px 9px; border-radius: 8px;
               font-size: 11px; font-weight: 700; letter-spacing: .04em; }
.sdps2-robust   { background: #14532d; color: #86efac; }
.sdps2-moderate { background: #3b2700; color: #fcd34d; }
.sdps2-fragile  { background: #7f1d1d; color: #fca5a5; }
.sdps2-unknown  { background: #2a2a3a; color: #888; }

/* cross-run table */
.sdps2-table-wrap { overflow-x: auto; margin: 8px 0 16px; }
.sdps2-table { border-collapse: collapse; min-width: 400px; width: 100%; }
.sdps2-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                  text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                  text-align: left; border-bottom: 2px solid #444; }
.sdps2-table td { padding: 5px 10px; border-bottom: 1px solid #222; color: #ccc;
                  font-size: 12px; }
.sdps2-table tr:hover td { background: #1a1a2a; }
.sdps2-best { color: #4ade80; font-weight: 700; }

/* rule cards */
.sdps2-rule-card { background: #1a1a2a; border: 1px solid #333; border-radius: 6px;
                   padding: 12px 14px; margin: 10px 0; }
.sdps2-rule-title { font-size: 12px; font-weight: 700; color: #e8e8e8; margin-bottom: 8px; }
.sdps2-condition-label { font-size: 11px; color: #888; margin: 8px 0 4px; }

/* sweep bar chart */
.sdps2-sweep-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.sdps2-sweep-thr { font-size: 10px; color: #888; width: 70px; flex-shrink: 0;
                   text-align: right; }
.sdps2-sweep-bg  { flex: 1; height: 12px; background: #252530; border-radius: 3px;
                   overflow: hidden; max-width: 260px; }
.sdps2-sweep-fill { height: 100%; border-radius: 3px; }
.sdps2-sweep-pf  { font-size: 10px; color: #888; min-width: 48px; text-align: right; }
.sdps2-sweep-orig { border: 2px solid #a78bfa !important; }

/* sensitivity score bar */
.sdps2-score-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.sdps2-score-label { font-size: 11px; color: #888; width: 120px; flex-shrink: 0; }
.sdps2-score-bg  { flex: 1; height: 10px; background: #252530; border-radius: 3px;
                   overflow: hidden; max-width: 200px; }
.sdps2-score-fill { height: 100%; border-radius: 3px; }
.sdps2-score-val { font-size: 11px; color: #888; min-width: 36px; text-align: right; }

.sdps2-section-label { font-size: 11px; font-weight: 600; color: #888;
                       text-transform: uppercase; letter-spacing: .06em; margin: 14px 0 6px; }
.sdps2-issue { color: #f87171; font-size: 11px; margin: 4px 0; }
.sdps2-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
.sdps2-skipped { color: #888; font-style: italic; font-size: 12px; padding: 8px 0; }
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


def _badge(cls: str) -> str:
    label = cls.replace("_", " ").title() if cls else "Unknown"
    css = {
        "robust":   "sdps2-robust",
        "moderate": "sdps2-moderate",
        "fragile":  "sdps2-fragile",
    }.get(cls, "sdps2-unknown")
    return f'<span class="sdps2-badge {css}">{label}</span>'


def _score_color(score: Optional[int]) -> str:
    if score is None:
        return "#555"
    if score < 30:
        return "#22c55e"
    if score < 60:
        return "#f59e0b"
    return "#ef4444"


# ---------------------------------------------------------------------------
# Threshold sweep bar chart for one condition
# ---------------------------------------------------------------------------

def _render_sweep(sweep_info: dict) -> str:
    """Render PF vs threshold bar chart for a single condition sweep."""
    col           = sweep_info.get("column") or sweep_info.get("col") or "?"
    op            = sweep_info.get("op") or ""
    original      = sweep_info.get("original_value")
    step          = sweep_info.get("step")
    thresh_sweep  = sweep_info.get("threshold_sweep") or []
    classification = sweep_info.get("classification") or "unknown"
    sens_score    = sweep_info.get("sensitivity_score")
    pf_at_orig    = sweep_info.get("pf_at_original")
    pf_mean       = sweep_info.get("pf_mean")
    peak_ratio    = sweep_info.get("peak_ratio")

    op_display = {"gte": "≥", "lte": "≤", "gt": ">", "lt": "<"}.get(op, op)
    cond_label = f"{col} {op_display} {_fmt(original, 3)}"
    step_str   = f"step={_fmt(step, 3)}" if step is not None else ""

    # Compute max PF for scaling bars
    valid_pfs = [s["pf"] for s in thresh_sweep if s.get("pf") is not None]
    max_pf = max(valid_pfs) if valid_pfs else 1.0
    if max_pf <= 0:
        max_pf = 1.0

    rows_html = ""
    for s in thresh_sweep:
        thr  = s.get("threshold")
        pf   = s.get("pf")
        n    = s.get("n_trades", 0)
        is_orig = (thr is not None and original is not None
                   and abs(float(thr) - float(original)) < 1e-9)

        if pf is None:
            bar_html = f"""
            <div class="sdps2-sweep-row">
              <div class="sdps2-sweep-thr{' sdps2-sweep-orig' if is_orig else ''}"
                   style="{'color:#a78bfa;font-weight:700' if is_orig else ''}">{_fmt(thr,3)}</div>
              <div class="sdps2-sweep-bg">
                <div class="sdps2-sweep-fill" style="width:0%;background:#333"></div>
              </div>
              <div class="sdps2-sweep-pf" style="color:#555">n={n} (skip)</div>
            </div>"""
        else:
            w      = int(min(pf / max_pf * 100, 100))
            color  = "#4ade80" if pf >= 1.5 else ("#facc15" if pf >= 1.0 else "#f87171")
            style  = "color:#a78bfa;font-weight:700" if is_orig else ""
            bar_html = f"""
            <div class="sdps2-sweep-row">
              <div class="sdps2-sweep-thr" style="{style}">{_fmt(thr,3)}</div>
              <div class="sdps2-sweep-bg">
                <div class="sdps2-sweep-fill" style="width:{w}%;background:{color}"></div>
              </div>
              <div class="sdps2-sweep-pf">PF {_fmt(pf)}</div>
            </div>"""
        rows_html += bar_html

    # Sensitivity score bar
    score_w    = int(min(sens_score or 0, 100))
    score_col  = _score_color(sens_score)
    score_html = f"""
    <div class="sdps2-score-row">
      <div class="sdps2-score-label">Sensitivity Score</div>
      <div class="sdps2-score-bg">
        <div class="sdps2-score-fill" style="width:{score_w}%;background:{score_col}"></div>
      </div>
      <div class="sdps2-score-val">{sens_score if sens_score is not None else '—'}/100</div>
    </div>"""

    meta_parts = []
    if pf_at_orig is not None:
        meta_parts.append(f"PF@original={_fmt(pf_at_orig)}")
    if pf_mean is not None:
        meta_parts.append(f"PF_mean={_fmt(pf_mean)}")
    if peak_ratio is not None:
        meta_parts.append(f"peak/mean={_fmt(peak_ratio,3)}")
    meta_str = "  |  ".join(meta_parts)

    return f"""
<div class="sdps2-condition-label">
  Condition: <strong>{cond_label}</strong>&nbsp;&nbsp;
  {step_str}&nbsp;&nbsp;{_badge(classification)}&nbsp;&nbsp;
  <span style="color:#888;font-size:10px">{meta_str}</span>
</div>
{score_html}
{rows_html}"""


# ---------------------------------------------------------------------------
# Per-rule card
# ---------------------------------------------------------------------------

def _render_rule_card(rule: dict, rule_num: int) -> str:
    rule_id   = rule.get("rule_id") or f"rule_{rule_num}"
    rule_desc = rule.get("rule_desc") or rule_id
    sweeps    = rule.get("sweeps") or []

    if not sweeps:
        return f"""
<div class="sdps2-rule-card">
  <div class="sdps2-rule-title">Rule {rule_num}: {rule_desc}</div>
  <div class="sdps2-no-data">No numeric threshold conditions to sweep.</div>
</div>"""

    sweeps_html = "".join(_render_sweep(sw) for sw in sweeps)

    # Overall classification for this rule (worst of all sweeps)
    cls_rank = {"fragile": 2, "moderate": 1, "robust": 0, "unknown": -1}
    worst_cls = max(
        (sw.get("classification") or "unknown" for sw in sweeps),
        key=lambda c: cls_rank.get(c, -1),
    )

    return f"""
<div class="sdps2-rule-card">
  <div class="sdps2-rule-title">
    Rule {rule_num}: {rule_desc}&nbsp;&nbsp;{_badge(worst_cls)}
  </div>
  {sweeps_html}
</div>"""


# ---------------------------------------------------------------------------
# Per-run block
# ---------------------------------------------------------------------------

def _render_run_block(run_id: str, ps: dict, options: dict) -> str:
    max_rules = int(options.get("max_rules_shown", 10))

    if ps.get("skipped"):
        return f"""
<div class="sdps2-run-header">{run_id}</div>
<div class="sdps2-skipped">Parameter sensitivity analysis was skipped.</div>"""

    if ps.get("error"):
        return f"""
<div class="sdps2-run-header">{run_id}</div>
<div class="sdps2-issue">Error: {ps['error']}</div>"""

    summary     = ps.get("summary") or {}
    rule_sweeps = ps.get("rule_sweeps") or []
    diagnostics = ps.get("diagnostics") or {}

    n_rules   = summary.get("n_rules_analysed", 0)
    n_sweeps  = summary.get("n_numeric_sweeps", 0)
    n_robust  = summary.get("n_robust", 0)
    n_mod     = summary.get("n_moderate", 0)
    n_fragile = summary.get("n_fragile", 0)
    issues    = diagnostics.get("issues") or []

    if not rule_sweeps and not n_rules:
        issues_html = "".join(f'<div class="sdps2-issue">⚠ {i}</div>' for i in issues)
        return f"""
<div class="sdps2-run-header">{run_id}</div>
<div class="sdps2-no-data">No parameter sensitivity data available.</div>
{issues_html}"""

    kpis_html = f"""
<div class="sdps2-kpi-grid">
  <div class="sdps2-kpi">
    <div class="sdps2-kpi-label">Rules Analysed</div>
    <div class="sdps2-kpi-value">{n_rules}</div>
  </div>
  <div class="sdps2-kpi">
    <div class="sdps2-kpi-label">Conditions Swept</div>
    <div class="sdps2-kpi-value">{n_sweeps}</div>
  </div>
  <div class="sdps2-kpi">
    <div class="sdps2-kpi-label">Robust</div>
    <div class="sdps2-kpi-value" style="color:#4ade80">{n_robust}</div>
  </div>
  <div class="sdps2-kpi">
    <div class="sdps2-kpi-label">Moderate</div>
    <div class="sdps2-kpi-value" style="color:#facc15">{n_mod}</div>
  </div>
  <div class="sdps2-kpi">
    <div class="sdps2-kpi-label">Fragile</div>
    <div class="sdps2-kpi-value" style="color:#f87171">{n_fragile}</div>
  </div>
</div>"""

    rules_html = ""
    for i, rule in enumerate(rule_sweeps[:max_rules], 1):
        rules_html += _render_rule_card(rule, i)

    if len(rule_sweeps) > max_rules:
        rules_html += f"""
<div class="sdps2-no-data">
  … {len(rule_sweeps) - max_rules} more rules not shown
  (increase <code>max_rules_shown</code> in options).
</div>"""

    issues_html = "".join(f'<div class="sdps2-issue">⚠ {i}</div>' for i in issues)

    return f"""
<div class="sdps2-run-header">{run_id}</div>
{kpis_html}
<div class="sdps2-section-label">Rule Threshold Sweeps</div>
{rules_html}
{issues_html}"""


# ---------------------------------------------------------------------------
# Cross-run summary table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    # Best column: most robust (highest n_robust / n_sweeps ratio)
    best_ratio = -1.0
    best_run   = None
    for run_id, ps in run_items:
        if ps.get("skipped") or ps.get("error"):
            continue
        summary  = ps.get("summary") or {}
        n_sweeps = summary.get("n_numeric_sweeps") or 0
        n_robust = summary.get("n_robust") or 0
        ratio    = n_robust / n_sweeps if n_sweeps > 0 else 0.0
        if ratio > best_ratio:
            best_ratio = ratio
            best_run   = run_id

    rows_html = ""
    for run_id, ps in run_items:
        if ps.get("skipped"):
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="5" style="color:#666;font-style:italic">skipped</td></tr>"""
            continue
        if ps.get("error"):
            rows_html += f"""
            <tr><td style="color:#a78bfa">{run_id}</td>
            <td colspan="5" style="color:#f87171">{ps['error']}</td></tr>"""
            continue

        summary   = ps.get("summary") or {}
        n_rules   = summary.get("n_rules_analysed", 0)
        n_sweeps  = summary.get("n_numeric_sweeps", 0)
        n_robust  = summary.get("n_robust", 0)
        n_mod     = summary.get("n_moderate", 0)
        n_fragile = summary.get("n_fragile", 0)
        is_best   = (run_id == best_run)

        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{n_rules}</td>
          <td>{n_sweeps}</td>
          <td class="{'sdps2-best' if is_best else ''}" style="color:#4ade80">{n_robust}</td>
          <td style="color:#facc15">{n_mod}</td>
          <td style="color:#f87171">{n_fragile}</td>
        </tr>"""

    return f"""
<div class="sdps2-section-label">Cross-Run Sensitivity Summary</div>
<div class="sdps2-table-wrap">
  <table class="sdps2-table">
    <thead>
      <tr>
        <th>Run</th><th>Rules</th><th>Conditions</th>
        <th>Robust</th><th>Moderate</th><th>Fragile</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_parameter_sensitivity(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        ps = sd.get("parameter_sensitivity") or {}
        run_items.append((run_id, ps))

    if not run_items:
        return (
            f"{_CSS}<div class='sdps2-wrap'>"
            "<div class='sdps2-no-data'>No strategy discovery data available.</div>"
            "</div>"
        )

    parts: List[str] = [_CSS, '<div class="sdps2-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, ps in run_items:
            parts.append(_render_run_block(run_id, ps, options))

    parts.append("</div>")
    return "\n".join(parts)
