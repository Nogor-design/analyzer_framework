from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)


def render_large_candle_excursion_findings_fragility(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings fragility data missing.")
    if not data.get("has_source"):
        return info_box(f"Fragility unavailable: {data.get('message', 'source analytics missing')}.")

    warnings = data.get("fragility_warnings") or []
    if not warnings:
        return info_box("No fragility warnings generated for retained findings.", color="#e8f5e9", border="#81c784")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Fragility Warnings")
    html += '<ul style="margin:8px 0 8px 20px">'
    for w in warnings:
        html += (
            f"<li style='margin-bottom:6px'>"
            f"<strong>{w.get('type','warning')}</strong> ({w.get('severity','n/a')}): "
            f"{w.get('setup','—')} — {w.get('details','')}</li>"
        )
    html += "</ul></div>"
    return html
