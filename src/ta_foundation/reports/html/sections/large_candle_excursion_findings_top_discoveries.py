from __future__ import annotations

from typing import List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def _render_neighbor_tables(rows: List[dict]) -> str:
    if not rows:
        return ""
    html = section_title("Neighbor Analysis (Plateau vs Fragility)")
    for setup in rows:
        html += f"<p style='margin:8px 0 4px 0;font-size:12px'><strong>{setup.get('setup_definition', '—')}</strong></p>"
        headers = ["Target%", "Events", "Win%", "Score", "Δ vs Main"]
        hdr_row = "".join(hdr(h) for h in headers)
        body: List[str] = []
        for n in setup.get("neighbors") or []:
            body.append(
                "<tr>"
                + cell(str(n.get("target_percent", "—")))
                + cell(str(n.get("event_count", 0)))
                + cell(fmt(n.get("win_rate"), 1, "%"))
                + cell(fmt(n.get("score"), 3))
                + cell(fmt(n.get("delta_vs_main"), 3), bg="#e8f5e9" if float(n.get("delta_vs_main") or 0) >= -0.02 else "#fff3e0")
                + "</tr>"
            )
        html += '<div style="overflow-x:auto;margin-bottom:10px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    return html


def render_large_candle_excursion_findings_top_discoveries(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings data missing.")
    if not data.get("has_source"):
        return info_box(f"Findings unavailable: {data.get('message', 'source analytics missing')}.")

    rows = data.get("top_discoveries") or []
    top_n = int(options.get("top_n", 25))
    rows = rows[:top_n]
    if not rows:
        return info_box("No findings candidates met thresholds.", color="#f8f9fa", border="#dee2e6")

    headers = [
        "#",
        "Setup",
        "Mode",
        "TF",
        "Bucket",
        "Target%",
        "Events",
        "Win%",
        "AvgTarget",
        "AvgFav",
        "AvgAdv",
        "Expectancy(t)",
        "Score",
        "Stability",
    ]
    hdr_row = "".join(hdr(h) for h in headers)
    body: List[str] = []

    for i, r in enumerate(rows, 1):
        trad = r.get("tradability") or {}
        cols = [
            cell(str(i), bold=True),
            cell(str(r.get("setup_definition", "—"))),
            cell(str(r.get("trade_mode", "—"))),
            cell(f"{r.get('tf_minutes', '—')}m"),
            cell(str(r.get("candle_bucket", "—"))),
            cell(fmt(r.get("target_percent"), 0, "%")),
            cell(str(r.get("n_events", 0))),
            cell(fmt(r.get("win_rate"), 1, "%")),
            cell(fmt(trad.get("avg_target_ticks"), 2)),
            cell(fmt(trad.get("avg_favorable_excursion"), 2)),
            cell(fmt(trad.get("avg_adverse_excursion"), 2)),
            cell(fmt(r.get("expectancy_ticks"), 2)),
            cell(fmt(r.get("composite_score"), 3), bg="#e8f5e9", bold=True),
            cell(fmt(r.get("stability_score"), 3)),
        ]
        body.append(f"<tr>{''.join(cols)}</tr>")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Top Discoveries — Composite Ranking")
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    html += _render_neighbor_tables(data.get("neighbor_analysis") or [])
    html += "</div>"
    return html
