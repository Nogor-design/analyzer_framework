from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def render_large_candle_excursion_discovery_broad_scan(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_discovery")
    if not data:
        return info_box("Discovery broad scan missing.")
    if not data.get("has_source"):
        return info_box(f"Broad scan unavailable: {data.get('message', 'source analytics missing')}.")

    broad = data.get("broad_scan") or {}
    rows = broad.get("candidates") or []
    top_n = int(options.get("top_n", 30))
    rows = rows[:top_n]
    if not rows:
        return info_box("Broad scan ran, but no candidates passed filters.", color="#f8f9fa", border="#dee2e6")

    headers = ["Setup", "Mode", "TF", "Bucket", "Target%", "Target(t)", "Cost(t)", "NetTarget", "NetExp", "Friction", "Events", "Win%", "Score"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        body.append(
            "<tr>"
            + cell(str(r.get("setup_definition", "—")))
            + cell(str(r.get("trade_mode", "—")))
            + cell(f"{r.get('tf_minutes', '—')}m")
            + cell(str(r.get("candle_bucket", "—")))
            + cell(fmt(r.get("target_percent"), 0, "%"))
            + cell(fmt(r.get("gross_target_ticks"), 2))
            + cell(fmt(r.get("estimated_round_trip_cost_ticks"), 2))
            + cell(fmt(r.get("net_target_after_friction_ticks"), 2))
            + cell(fmt(r.get("net_expectancy_after_friction_ticks"), 2))
            + cell(str(r.get("friction_viability") or "---"))
            + cell(str(r.get("n_events", 0)))
            + cell(fmt(r.get("win_rate"), 1, "%"))
            + cell(fmt(r.get("composite_score"), 3), bg="#e8f5e9", bold=True)
            + "</tr>"
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Broad Scan Results")
    html += f"<p style='font-size:13px;color:#444'>Evaluated {broad.get('n_evaluated', 0)} candidates, retained {broad.get('n_retained', 0)}.</p>"
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"
    return html
