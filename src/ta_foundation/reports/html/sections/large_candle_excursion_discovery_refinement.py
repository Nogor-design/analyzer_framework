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
    plateaus = data.get("plateau_analysis") or []
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
    if plateaus:
        html += section_title("Plateau / Neighbor Stability")
        for p in plateaus[:10]:
            html += f"<p style='font-size:12px;color:#444'><strong>{p.get('setup_definition')}</strong> — {p.get('plateau_label')}</p>"
            n_headers = ["Type", "Target%", "Threshold", "Bucket", "Score", "Win%", "N", "Robustness", "Δ from Parent"]
            n_hdr = "".join(hdr(h) for h in n_headers)
            n_rows = []
            for n in p.get("neighbors") or []:
                n_rows.append(
                    "<tr>"
                    + cell(str(n.get("neighbor_type", "—")))
                    + cell(fmt(n.get("target_percent"), 0, "%"))
                    + cell(fmt(n.get("threshold_value"), 2))
                    + cell(str(n.get("candle_bucket", "—")))
                    + cell(fmt(n.get("score"), 3))
                    + cell(fmt(n.get("win_rate"), 1, "%"))
                    + cell(str(n.get("n_events", 0)))
                    + cell(fmt(n.get("robustness_score"), 3))
                    + cell(fmt(n.get("delta_from_parent"), 3), bg="#e8f5e9" if float(n.get("delta_from_parent") or 0) >= 0 else "#fff3e0")
                    + "</tr>"
                )
            html += '<div style="overflow-x:auto;margin-bottom:16px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
            html += f"<thead><tr>{n_hdr}</tr></thead><tbody>{''.join(n_rows)}</tbody></table></div>"
    return html
