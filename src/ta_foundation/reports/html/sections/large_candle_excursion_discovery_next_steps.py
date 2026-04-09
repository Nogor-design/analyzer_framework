from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import get_derived_payload, info_box, section_title


def render_large_candle_excursion_discovery_next_steps(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery next-step data missing.")
    if not data.get("has_source"):
        return info_box(f"Next steps unavailable: {data.get('message', 'source analytics missing')}.")

    steps = data.get("next_steps") or []
    if not steps:
        return info_box("No next-step suggestions generated.", color="#f8f9fa", border="#dee2e6")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Suggested Next Research Runs")
    html += "<ol>"
    for s in steps:
        html += f"<li style='margin-bottom:6px'>{s}</li>"
    html += "</ol></div>"
    return html
