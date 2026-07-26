from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def _render_time_splits(rows: list[dict]) -> str:
    if not rows:
        return ""
    html = section_title("Time-Split Robustness")
    for setup in rows:
        html += f"<p style='margin:8px 0 4px 0;font-size:12px'><strong>{setup.get('setup_definition', '—')}</strong></p>"
        if not setup.get("available"):
            html += f"<p style='margin:0 0 8px 0;color:#a55;font-size:12px'>Unavailable: {setup.get('reason', 'missing event-level data')}.</p>"
            continue
        headers = ["Segment", "Events", "Win%", "Score"]
        hdr_row = "".join(hdr(h) for h in headers)
        body = []
        for r in setup.get("splits") or []:
            body.append(
                "<tr>"
                + cell(str(r.get("segment", "—")), bold=True)
                + cell(str(r.get("event_count", 0)))
                + cell(fmt(r.get("win_rate"), 2, "%"))
                + cell(fmt(r.get("score"), 3), bg="#e8f5e9")
                + "</tr>"
            )
        html += '<div style="overflow-x:auto;margin-bottom:10px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    return html


def _render_neighbor(rows: list[dict]) -> str:
    if not rows:
        return ""
    html = section_title("Neighbor Stability (Top Final Discoveries)")
    for setup in rows:
        html += f"<p style='margin:8px 0 4px 0;font-size:12px'><strong>{setup.get('setup_definition', '—')}</strong></p>"
        headers = ["Target%", "Events", "Win%", "Score", "Δ vs Main"]
        hdr_row = "".join(hdr(h) for h in headers)
        body = []
        for r in setup.get("neighbors") or []:
            body.append(
                "<tr>"
                + cell(str(r.get("target_percent", "—")))
                + cell(str(r.get("event_count", 0)))
                + cell(fmt(r.get("win_rate"), 1, "%"))
                + cell(fmt(r.get("score"), 3))
                + cell(fmt(r.get("delta_vs_main"), 3))
                + "</tr>"
            )
        html += '<div style="overflow-x:auto;margin-bottom:10px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    return html


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
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    html += _render_time_splits((data.get("robustness_validation") or {}).get("time_splits") or [])
    html += _render_neighbor(data.get("neighbor_analysis") or [])
    html += "</div>"
    return html
