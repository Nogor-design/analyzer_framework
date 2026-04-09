from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import get_derived_payload, info_box, section_title


def render_large_candle_excursion_discovery_summary(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery report data missing. Enable <code>large_candle_excursion_discovery.enabled: true</code>.")
    if not data.get("has_source"):
        return info_box(f"Discovery unavailable: {data.get('message', 'source analytics missing')}.")

    s = data.get("summary") or {}
    cautions = s.get("major_cautions") or []

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Discovery Executive Summary")
    html += "<ul>"
    if s.get("strongest_broad_scan"):
        b = s["strongest_broad_scan"]
        html += f"<li>Strongest broad-scan: {b.get('setup_definition')} (score={b.get('composite_score')}, N={b.get('n_events')}, WR={b.get('win_rate')}%).</li>"
    if s.get("strongest_refined"):
        r = s["strongest_refined"]
        html += f"<li>Strongest refinement: {r.get('child_setup')} (Δscore={r.get('score_delta_vs_parent')}).</li>"
    if s.get("strongest_chain"):
        c = s["strongest_chain"]
        html += f"<li>Strongest chain: {c.get('base_setup')} + {', '.join(c.get('chain_conditions') or [])} (score={c.get('composite_score')}).</li>"
    if cautions:
        for c in cautions:
            html += f"<li><strong>Caution:</strong> {c}</li>"
    html += "</ul></div>"
    return html
