from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def render_large_candle_excursion_discovery_robustness(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery robustness data missing.")
    if not data.get("has_source"):
        return info_box(f"Robustness unavailable: {data.get('message', 'source analytics missing')}.")

    rows = (data.get("robustness_validation") or {}).get("candidates") or []
    if not rows:
        return info_box("No robustness rows generated.", color="#f8f9fa", border="#dee2e6")

    headers = ["Setup", "Neighbor Stability", "Split Instability Pen", "OOS Required", "OOS Pen", "Robustness Score"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows[:30]:
        body.append(
            "<tr>"
            + cell(str(r.get("setup_definition", "—")))
            + cell(fmt(r.get("neighbor_stability"), 3))
            + cell(fmt(r.get("split_instability_penalty"), 3), bg="#fff3e0")
            + cell("Yes" if r.get("oos_check_required") else "No")
            + cell(fmt(r.get("oos_penalty"), 3), bg="#fff3e0")
            + cell(fmt(r.get("robustness_score"), 3), bg="#e8f5e9", bold=True)
            + "</tr>"
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Robustness Validation")
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"
    return html
