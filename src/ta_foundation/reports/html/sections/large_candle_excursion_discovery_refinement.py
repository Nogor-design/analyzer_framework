from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def render_large_candle_excursion_discovery_refinement(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery refinement data missing.")
    if not data.get("has_source"):
        return info_box(f"Refinement unavailable: {data.get('message', 'source analytics missing')}.")

    rows = (data.get("refinement") or {}).get("candidates") or []
    if not rows:
        return info_box("No refinement candidates met criteria.", color="#f8f9fa", border="#dee2e6")

    headers = ["Parent", "Refined Target%", "Child", "Child Score", "Δ Score", "Child Win%", "Child N"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows[:40]:
        body.append(
            "<tr>"
            + cell(str(r.get("parent_setup", "—")))
            + cell(fmt(r.get("refined_target_percent"), 0, "%"), bold=True)
            + cell(str(r.get("child_setup", "—")))
            + cell(fmt(r.get("child_score"), 3), bg="#e8f5e9")
            + cell(fmt(r.get("score_delta_vs_parent"), 3))
            + cell(fmt(r.get("child_win_rate"), 1, "%"))
            + cell(str(r.get("child_n_events", 0)))
            + "</tr>"
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Refinement Results")
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"
    return html
