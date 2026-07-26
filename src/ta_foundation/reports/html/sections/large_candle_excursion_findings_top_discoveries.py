from __future__ import annotations

from typing import List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def _render_neighbor_tables(rows: List[dict]) -> str:
    if not rows:
        return ""
    html = section_title("Fine Target Curve (Plateau vs Fragility)")
    for setup in rows:
        label = setup.get("target_stability_label") or "unknown"
        plateau = setup.get("plateau_width")
        ns = setup.get("neighbor_target_stability")
        artifact = " | MICRO-SCALP ARTIFACT" if setup.get("micro_scalp_artifact") else ""
        time_label = setup.get("time_stability_label") or "time_unknown"
        html += (
            f"<p style='margin:8px 0 4px 0;font-size:12px'><strong>{setup.get('setup_definition', '---')}</strong> "
            f"<span style='color:#777'>plateau={plateau}, neighbor stability={fmt(ns, 3)}, "
            f"time stability={fmt(setup.get('target_time_stability'), 3)}, {label}, {time_label}{artifact}</span></p>"
        )
        headers = ["Target%", "Main", "Events", "Win%", "WR Delta", "Score", "Score Delta", "Target Stability", "Time Stability"]
        hdr_row = "".join(hdr(h) for h in headers)
        body: List[str] = []
        main_target = int(setup.get("main_target_percent") or -1)
        for n in setup.get("neighbors") or []:
            t = int(n.get("target_percent", -2))
            is_main = t == main_target
            wr_delta = float(n.get("win_rate_delta_vs_main") or 0)
            wr_bg = "#e8f5e9" if wr_delta >= -3.0 else "#fff3e0"
            body.append(
                "<tr>"
                + cell(str(n.get("target_percent", "---")))
                + cell("yes" if is_main else "")
                + cell(str(n.get("event_count", 0)))
                + cell(fmt(n.get("win_rate"), 1, "%"))
                + cell(fmt(n.get("win_rate_delta_vs_main"), 1, " pp"), bg=wr_bg)
                + cell(fmt(n.get("score"), 3))
                + cell(fmt(n.get("delta_vs_main"), 3), bg="#e8f5e9" if float(n.get("delta_vs_main") or 0) >= -0.02 else "#fff3e0")
                + cell(str(n.get("target_stability_label") or ""))
                + cell(str(n.get("time_stability_label") or ""))
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
        "Target(t)",
        "Cost(t)",
        "NetTarget",
        "AvgFav",
        "AvgAdv",
        "NetExp(t)",
        "Friction",
        "Score",
        "Stability",
        "Plateau",
        "Target Stable",
        "Time Stable",
        "Curve Penalty",
    ]
    hdr_row = "".join(hdr(h) for h in headers)
    body: List[str] = []

    for i, r in enumerate(rows, 1):
        trad = r.get("tradability") or {}
        target_label = str(r.get("target_stability_label") or "---")
        if r.get("micro_scalp_artifact"):
            target_label += " / micro-scalp"
        time_label = str(r.get("time_stability_label") or "---")
        cols = [
            cell(str(i), bold=True),
            cell(str(r.get("setup_definition", "---"))),
            cell(str(r.get("trade_mode", "---"))),
            cell(f"{r.get('tf_minutes', '---')}m"),
            cell(str(r.get("candle_bucket", "---"))),
            cell(fmt(r.get("target_percent"), 0, "%")),
            cell(str(r.get("n_events", 0))),
            cell(fmt(r.get("win_rate"), 1, "%")),
            cell(fmt(r.get("gross_target_ticks", trad.get("avg_target_ticks")), 2)),
            cell(fmt(r.get("estimated_round_trip_cost_ticks"), 2)),
            cell(fmt(r.get("net_target_after_friction_ticks"), 2)),
            cell(fmt(trad.get("avg_favorable_excursion"), 2)),
            cell(fmt(trad.get("avg_adverse_excursion"), 2)),
            cell(fmt(r.get("net_expectancy_after_friction_ticks"), 2)),
            cell(str(r.get("friction_viability") or "---")),
            cell(fmt(r.get("composite_score"), 3), bg="#e8f5e9", bold=True),
            cell(fmt(r.get("stability_score"), 3)),
            cell(str(r.get("curve_plateau_width", "---"))),
            cell(target_label),
            cell(time_label),
            cell(fmt(r.get("curve_penalty"), 3)),
        ]
        body.append(f"<tr>{''.join(cols)}</tr>")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Top Discoveries - Composite Ranking")
    fs = data.get("friction_viability_summary") or {}
    if fs:
        html += (
            f"<p style='font-size:12px;color:#444;margin:0 0 8px'>"
            f"Friction viability in shown top set: {fs.get('friction_viable', 0)} viable, "
            f"{fs.get('friction_risky', 0)} risky, {fs.get('friction_invalid', 0)} invalid "
            f"({fs.get('viable_pct', 0)}% viable).</p>"
        )
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    html += _render_neighbor_tables(data.get("neighbor_analysis") or [])
    html += "</div>"
    return html
