from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)


def render_large_candle_excursion_findings_next_tests(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings next-test data missing.")
    if not data.get("has_source"):
        return info_box(f"Next tests unavailable: {data.get('message', 'source analytics missing')}.")

    tests = data.get("next_tests") or []
    if not tests:
        return info_box("No next-test suggestions generated.", color="#f8f9fa", border="#dee2e6")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Suggested Next Tests")
    html += '<ol style="margin:8px 0 8px 20px">'
    for t in tests:
        html += f"<li style='margin-bottom:6px'>{t}</li>"
    html += "</ol></div>"
    return html
