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
    rej = (data.get("chain_rejection_diagnostics") or {}).get("attempted") or []
    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Discovery Diagnostics")
    html += "<ul>"
    for k, v in d.items():
        html += f"<li><strong>{k}</strong>: {v}</li>"
    html += "</ul>"

    if rej:
        html += section_title("Chaining Rejection Diagnostics")
        html += "<p style='font-size:12px;color:#444'>Strongest attempted chains that were rejected and why.</p>"
        html += "<table style='border-collapse:collapse;width:100%;font-size:12px'>"
        html += "<thead><tr>"
        html += "<th style='background:#2c3e50;color:#fff;padding:7px 10px;border:1px solid #ddd'>Base Setup</th>"
        html += "<th style='background:#2c3e50;color:#fff;padding:7px 10px;border:1px solid #ddd'>Reason</th>"
        html += "<th style='background:#2c3e50;color:#fff;padding:7px 10px;border:1px solid #ddd'>Details</th>"
        html += "</tr></thead><tbody>"
        for r in rej[:60]:
            html += "<tr>"
            html += f"<td style='padding:5px 8px;border:1px solid #ddd'>{r.get('base_setup','—')}</td>"
            html += f"<td style='padding:5px 8px;border:1px solid #ddd'>{r.get('reason','—')}</td>"
            html += f"<td style='padding:5px 8px;border:1px solid #ddd'>{r.get('details','')}</td>"
            html += "</tr>"
        html += "</tbody></table>"

    html += "</div>"
    return html
