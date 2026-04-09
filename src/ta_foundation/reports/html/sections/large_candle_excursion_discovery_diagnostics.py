from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import get_derived_payload, info_box, section_title


def render_large_candle_excursion_discovery_diagnostics(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery diagnostics data missing.")
    if not data.get("has_source"):
        return info_box(f"Diagnostics unavailable: {data.get('message', 'source analytics missing')}.")

    d = data.get("diagnostics") or {}
    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Discovery Diagnostics")
    html += "<ul>"
    for k, v in d.items():
        html += f"<li><strong>{k}</strong>: {v}</li>"
    html += "</ul></div>"
    return html
