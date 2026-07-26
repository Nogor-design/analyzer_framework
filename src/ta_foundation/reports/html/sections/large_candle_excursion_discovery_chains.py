from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def _render_attempted(rows: list[dict]) -> str:
    if not rows:
        return ""
    headers = ["Base Setup", "Chain Conditions", "Incremental Improvement", "Final Score", "Rejection Reason"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        reason = r.get("rejection_reason") or "passed"
        bg = "#fff3e0" if reason != "passed" else "#e8f5e9"
        body.append(
            "<tr>"
            + cell(str(r.get("base_setup", "—")))
            + cell("; ".join(r.get("chain_conditions") or []))
            + cell(fmt(r.get("incremental_improvement"), 3))
            + cell(fmt(r.get("composite_score"), 3))
            + cell(str(reason), bg=bg)
            + "</tr>"
        )
    return section_title("Attempted Chains (Diagnostics)") + '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">' + f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


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

    chain_data = data.get("interaction_chaining") or {}
    rows = chain_data.get("candidates") or []
    attempted = chain_data.get("attempted") or []

    html = '<div style="font-family:Arial,sans-serif">'
    if rows:
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

        html += section_title("Chained Discoveries")
        html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    else:
        html += info_box("No chained candidates passed guardrails.", color="#f8f9fa", border="#dee2e6")

    html += _render_attempted(attempted)
    html += "</div>"
    return html
