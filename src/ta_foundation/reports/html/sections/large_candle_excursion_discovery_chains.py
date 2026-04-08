from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def render_large_candle_excursion_discovery_chains(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery chaining data missing.")
    if not data.get("has_source"):
        return info_box(f"Chaining unavailable: {data.get('message', 'source analytics missing')}.")

    rows = (data.get("interaction_chaining") or {}).get("candidates") or []
    if not rows:
        return info_box("No chained candidates passed guardrails.", color="#f8f9fa", border="#dee2e6")

    headers = ["Base Setup", "Chain Conditions", "Depth", "N", "Win%", "Base Score", "Improvement", "Complexity Pen", "Robustness", "Final Score"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows[:30]:
        body.append(
            "<tr>"
            + cell(str(r.get("base_setup", "—")))
            + cell("; ".join(r.get("chain_conditions") or []))
            + cell(str(r.get("chain_depth", 0)))
            + cell(str(r.get("n_events", 0)))
            + cell(fmt(r.get("win_rate"), 1, "%"))
            + cell(fmt(r.get("base_score"), 3))
            + cell(fmt(r.get("incremental_improvement"), 3), bg="#e8f5e9")
            + cell(fmt(r.get("complexity_penalty"), 3), bg="#fff3e0")
            + cell(fmt(r.get("robustness_score"), 3))
            + cell(fmt(r.get("composite_score"), 3), bg="#e8f5e9", bold=True)
            + "</tr>"
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Chained Discoveries")
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"
    return html
