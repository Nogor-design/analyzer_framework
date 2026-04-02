from __future__ import annotations

"""
Strategy Discovery — Decision Ledger
======================================
Pure HTML renderer for the unified candidate scorecard and validation evidence.

For each run that has a ``candidate_scorecard`` in
``pkg.assets["strategy_discovery"]``, renders one row per candidate showing:

  - Source: pattern_id, family, structure, horizon
  - Base metrics: n_signals, win_rate, avg_ticks
  - Robustness evidence: OOS stability, MC survival rate, audit confirmation rate
  - Session context: session_edge_score, session_concentration
  - Validation gates: gate-by-gate pass/fail with reason (if ValidationResult available)
  - Sensitivity class
  - Confidence decomposition (if regime recommender ran)
  - Final recommendation decision

Section options (ctx["options"]):
  max_candidates   int   max rows per run (default 50)
  show_gates       bool  show per-gate details (default true)
  show_confidence  bool  show confidence decomposition (default true)
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 3, pct: bool = False, default: str = "—") -> str:
    if v is None:
        return default
    try:
        f = float(v)
        if pct:
            return f"{f * 100:.1f}%"
        return f"{f:.{decimals}f}"
    except Exception:
        return str(v) if v != "" else default


def _pass_badge(passed: bool) -> str:
    color = "#22c55e" if passed else "#ef4444"
    label = "PASS" if passed else "FAIL"
    return f'<span style="background:{color};color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600">{label}</span>'


def _gate_rows(gates: List[Dict[str, Any]]) -> str:
    if not gates:
        return ""
    rows = []
    for g in gates:
        name = html.escape(str(g.get("name", "")))
        passed = bool(g.get("passed", False))
        reason = html.escape(str(g.get("reason", "")))
        rows.append(
            f"<tr>"
            f"<td style='padding:2px 6px;font-size:11px;color:#94a3b8'>{name}</td>"
            f"<td style='padding:2px 6px'>{_pass_badge(passed)}</td>"
            f"<td style='padding:2px 6px;font-size:11px;color:#64748b'>{reason}</td>"
            f"</tr>"
        )
    return (
        "<table style='border-collapse:collapse;margin:4px 0'>"
        "<thead><tr>"
        "<th style='padding:2px 6px;font-size:11px;color:#94a3b8;text-align:left'>Gate</th>"
        "<th style='padding:2px 6px;font-size:11px;color:#94a3b8;text-align:left'>Result</th>"
        "<th style='padding:2px 6px;font-size:11px;color:#94a3b8;text-align:left'>Reason</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _confidence_mini(cd: Dict[str, Any]) -> str:
    items = [
        ("Regime clf", cd.get("regime_classifier")),
        ("CV stability", cd.get("cv_stability")),
        ("MC survival", cd.get("mc_survival_rate")),
        ("Sensitivity", cd.get("sensitivity_class")),
        ("Audit rate", cd.get("audit_confirmation_rate")),
        ("Composite", cd.get("composite")),
    ]
    parts = []
    for label, val in items:
        if val is None:
            continue
        if isinstance(val, float):
            fmtval = f"{val:.3f}"
        else:
            fmtval = html.escape(str(val))
        parts.append(f"<span style='margin-right:8px;font-size:11px'><b>{label}:</b> {fmtval}</span>")
    return "".join(parts) if parts else "—"


def _scorecard_table(scorecard_rows: List[Dict[str, Any]], validation_gates: List[Dict[str, Any]],
                     confidence_decomp: Optional[Dict[str, Any]],
                     opts: Dict[str, Any]) -> str:
    max_candidates = int(opts.get("max_candidates", 50))
    show_gates = bool(opts.get("show_gates", True))
    show_confidence = bool(opts.get("show_confidence", True))

    rows_html = []
    for i, row in enumerate(scorecard_rows[:max_candidates]):
        pattern_id = html.escape(str(row.get("pattern_id", "—")))
        family = html.escape(str(row.get("family", "—")))
        structure = html.escape(str(row.get("structure", "—")))
        horizon = row.get("horizon")
        n_signals = row.get("n_signals") or row.get("corpus_n")
        win_rate = row.get("win_rate") or row.get("corpus_win_rate")
        avg_ticks = row.get("avg_ticks") or row.get("corpus_avg_ticks")
        oos_stab = row.get("oos_stability_score") or row.get("stability_score")
        mc_surv = row.get("mc_survival_rate") or row.get("mc_pass_rate")
        audit_rate = row.get("audit_confirmation_rate")
        session_edge = row.get("session_edge_score")
        session_conc = row.get("session_concentration")
        sensitivity = row.get("sensitivity_class") or row.get("overall_class")

        bg = "#1e293b" if i % 2 == 0 else "#172033"

        gate_html = _gate_rows(validation_gates) if show_gates and validation_gates else ""
        conf_html = _confidence_mini(confidence_decomp) if show_confidence and confidence_decomp else ""

        rows_html.append(f"""
        <tr style="background:{bg}">
          <td style="padding:8px 10px;border-bottom:1px solid #334155;vertical-align:top">
            <div style="font-size:12px;font-weight:600;color:#e2e8f0">{pattern_id}</div>
            <div style="font-size:11px;color:#94a3b8">{family} / {structure}</div>
            <div style="font-size:11px;color:#64748b">horizon={_fmt(horizon, 0)}</div>
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#cbd5e1;vertical-align:top">
            n={_fmt(n_signals, 0)}<br>
            wr={_fmt(win_rate, pct=True)}<br>
            avg={_fmt(avg_ticks)} ticks
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#cbd5e1;vertical-align:top">
            OOS={_fmt(oos_stab)}<br>
            MC={_fmt(mc_surv, pct=True)}<br>
            Audit={_fmt(audit_rate, pct=True)}
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#cbd5e1;vertical-align:top">
            Edge={_fmt(session_edge, pct=True)}<br>
            Conc={_fmt(session_conc, pct=True)}<br>
            Sens={html.escape(str(sensitivity)) if sensitivity else "—"}
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;vertical-align:top">
            {gate_html if gate_html else "<span style='color:#64748b;font-size:11px'>no gates</span>"}
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;vertical-align:top">
            {conf_html if conf_html else "<span style='color:#64748b;font-size:11px'>—</span>"}
          </td>
        </tr>""")

    if not rows_html:
        return "<p style='color:#64748b;padding:12px'>No candidates in scorecard.</p>"

    return f"""
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-family:monospace">
      <thead>
        <tr style="background:#0f172a">
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Pattern</th>
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Base Metrics</th>
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Robustness</th>
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Session</th>
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Validation Gates</th>
          <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Confidence</th>
        </tr>
      </thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
    </div>"""


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_decision_ledger(ctx: dict) -> str:
    packages = ctx.get("packages") or {}
    options = ctx.get("options") or {}
    diag = ctx.get("pipeline_diagnostics") or {}

    if not packages:
        return "<p style='color:#64748b'>No packages available.</p>"

    sections_html = []

    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue

        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets = pkg_assets.get("strategy_discovery") or {}
        scorecard_df = sd_assets.get("candidate_scorecard") if isinstance(sd_assets, dict) else None

        if scorecard_df is None or not hasattr(scorecard_df, "to_dict"):
            missing_keys = diag.get("asset_keys", {}).get(str(run_id), [])
            sections_html.append(
                f"<div style='padding:8px;color:#64748b;font-size:12px'>"
                f"{html.escape(str(run_id))}: no candidate_scorecard "
                f"(available assets: {html.escape(str(missing_keys))})</div>"
            )
            continue

        scorecard_rows = scorecard_df.where(scorecard_df.notna(), other=None).to_dict(orient="records")

        # Validation gates from metadata
        derived = getattr(pkg, "metadata", {}).get("derived", {}) or {}
        sd_derived = derived.get("strategy_discovery") or {}
        validation_data = sd_derived.get("validation") or {}
        gates = validation_data.get("gates") or []
        val_passed = bool(validation_data.get("passed", False))

        # Confidence decomposition from regime recommender
        rr_data = derived.get("regime_recommender") or {}
        confidence_decomp = rr_data.get("confidence_decomposition")

        n_total = len(scorecard_df)
        run_label = html.escape(str(run_id))

        val_badge = _pass_badge(val_passed) if validation_data else ""

        sections_html.append(f"""
        <div style="margin-bottom:24px;background:#1e293b;border-radius:8px;overflow:hidden">
          <div style="padding:12px 16px;background:#0f172a;display:flex;align-items:center;gap:12px">
            <span style="font-size:14px;font-weight:700;color:#e2e8f0">{run_label}</span>
            <span style="font-size:12px;color:#64748b">{n_total} candidates</span>
            {val_badge}
          </div>
          {_scorecard_table(scorecard_rows, gates, confidence_decomp, options)}
        </div>""")

    if not sections_html:
        return "<p style='color:#64748b;padding:12px'>No candidate scorecards found. Run pattern engine + strategy discovery first.</p>"

    return f"""
    <div style="padding:16px;background:#0f172a;min-height:200px">
      <p style="color:#94a3b8;font-size:12px;margin:0 0 16px">
        Engines run: {html.escape(", ".join(diag.get("engines_run", [])))}
        &nbsp;|&nbsp; Warnings: {len(diag.get("warnings", []))}
      </p>
      {"".join(sections_html)}
    </div>"""
