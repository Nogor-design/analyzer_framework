from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, get_derived_payload, hdr, info_box, section_title


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
    ranked = data.get("next_tests_ranked") or []
    if not tests:
        return info_box("No next-test suggestions generated.", color="#f8f9fa", border="#dee2e6")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Suggested Next Tests")
    html += '<ol style="margin:8px 0 8px 20px">'
    for t in tests:
        html += f"<li style='margin-bottom:6px'>{t}</li>"
    html += "</ol>"

    if ranked:
        headers = ["Recommendation", "Group", "Frequency"]
        hdr_row = "".join(hdr(h) for h in headers)
        body = []
        for row in ranked:
            body.append("<tr>" + cell(str(row.get("recommendation", "—"))) + cell(str(row.get("group", "—"))) + cell(str(row.get("count", 0)), bg="#e8f5e9") + "</tr>")
        html += section_title("Deduplicated Recommendation Ranking")
        html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"

    html += "</div>"
    return html
