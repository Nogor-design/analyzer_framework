from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)


def render_large_candle_excursion_findings_methodology(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings methodology unavailable: no data.")

    method = data.get("scoring_methodology") or {}
    formula = method.get("formula", "not available")
    components = method.get("components") or []

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Findings Methodology")
    html += f"<p style='font-size:13px;color:#444'><strong>Source:</strong> large_candle_excursion analytics output.</p>"
    html += f"<p style='font-size:13px;color:#444'><strong>Composite formula:</strong> <code>{formula}</code></p>"
    if components:
        html += "<p style='font-size:13px;color:#444'><strong>Components:</strong></p><ul>"
        for c in components:
            html += f"<li style='font-size:13px;color:#444'>{c}</li>"
        html += "</ul>"
    html += "</div>"
    return html
