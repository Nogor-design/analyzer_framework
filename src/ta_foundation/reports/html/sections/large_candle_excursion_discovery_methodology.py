from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import get_derived_payload, info_box, section_title


def render_large_candle_excursion_discovery_methodology(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery methodology unavailable: no data.")

    method = data.get("scoring_methodology") or {}
    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Discovery Methodology")
    html += "<p style='font-size:13px;color:#444'><strong>Source:</strong> base large_candle_excursion analytics output.</p>"
    html += f"<p style='font-size:13px;color:#444'><strong>Candidate score:</strong> <code>{method.get('candidate_score', 'n/a')}</code></p>"
    html += f"<p style='font-size:13px;color:#444'><strong>Chain score:</strong> <code>{method.get('chain_score', 'n/a')}</code></p>"
    html += f"<p style='font-size:13px;color:#444'><strong>Robustness:</strong> <code>{method.get('robustness', 'n/a')}</code></p>"
    html += "</div>"
    return html
