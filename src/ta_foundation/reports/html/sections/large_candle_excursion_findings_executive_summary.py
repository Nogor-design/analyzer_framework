from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)


def render_large_candle_excursion_findings_executive_summary(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings report data missing. Enable <code>large_candle_excursion_findings.enabled: true</code>.")
    if not data.get("has_source"):
        return info_box(f"Findings unavailable: {data.get('message', 'source analytics missing')}.")

    bullets = data.get("executive_summary") or []
    if not bullets:
        return info_box("Findings source exists, but no executive summary bullets were produced.", color="#f8f9fa", border="#dee2e6")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Large Candle Excursion Findings — Executive Summary")
    html += '<ul style="margin:8px 0 8px 22px">'
    for b in bullets:
        html += f'<li style="margin-bottom:6px;font-size:13px;color:#333">{b}</li>'
    html += '</ul></div>'
    return html
