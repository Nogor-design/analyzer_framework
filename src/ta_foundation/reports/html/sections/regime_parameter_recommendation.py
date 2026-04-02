from __future__ import annotations

from typing import Any, Dict
import html


def _h(v: Any) -> str:
    return html.escape(str(v))


def _fmt(v: Any, digits: int = 4) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return _h(v)


def render_regime_parameter_recommendation(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    _ = options

    blocks: list[str] = []
    for run_id, pkg in packages.items():
        rr = (((getattr(pkg, "metadata", None) or {}).get("derived") or {}).get("regime_recommender") or {})
        if not rr:
            blocks.append(f"<div class='card'><div class='muted'>[{_h(run_id)}] regime recommender not available.</div></div>")
            continue

        regime = rr.get("regime") or {}
        rec = rr.get("recommendation") or {}
        template_bundle = rr.get("template_bundle") or {}

        rows = []
        for pr in rec.get("parameter_reasons", []) or []:
            rows.append(
                "<tr>"
                f"<td>{_h(pr.get('name'))}</td>"
                f"<td>{_h(pr.get('baseline'))}</td>"
                f"<td>{_h(pr.get('recommended'))}</td>"
                f"<td>{_h(', '.join(pr.get('because') or []))}</td>"
                "</tr>"
            )

        template_rows = []
        for t in template_bundle.get("templates", []) or []:
            template_rows.append(
                "<tr>"
                f"<td>{_h(t.get('session'))}</td>"
                f"<td class='mono'>{_h(t.get('start_time'))}</td>"
                f"<td class='mono'>{_h(t.get('duration'))}</td>"
                f"<td class='mono'>{_h(t.get('path'))}</td>"
                "</tr>"
            )

        param_table_body = "".join(rows) if rows else "<tr><td colspan='4' class='muted'>No parameter changes.</td></tr>"
        tmpl_table_body = "".join(template_rows) if template_rows else "<tr><td colspan='4' class='muted'>No templates exported.</td></tr>"

        blocks.append(
            "<div class='card' style='grid-column: span 12;'>"
            f"<div class='mono' style='font-size:14px;font-weight:650;'>{_h(run_id)} — Regime Recommendation</div>"
            f"<div class='muted' style='margin-top:6px;'>Regime: <b>{_h(regime.get('regime_id'))}</b> • "
            f"Primary: <b>{_h(regime.get('primary'))}</b> • Confidence: <b>{_fmt(rec.get('confidence', 0.0), 3)}</b> • "
            f"Decision: <b>{_h(rec.get('decision'))}</b></div>"
            "<div style='margin-top:10px;'>"
            "<table class='table'><tr><th>Parameter</th><th>Baseline</th><th>Recommended</th><th>Because</th></tr>"
            f"{param_table_body}"
            "</table></div>"
            "<div style='margin-top:10px;'>"
            "<div class='muted' style='margin-bottom:6px;'>Generated session templates</div>"
            "<table class='table'><tr><th>Session</th><th>Start</th><th>Duration</th><th>Path</th></tr>"
            f"{tmpl_table_body}"
            "</table></div>"
            "</div>"
        )

    if not blocks:
        return "<div class='muted'>No runs found.</div>"
    return "\n".join(blocks)
