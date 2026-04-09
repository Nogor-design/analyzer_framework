from __future__ import annotations

from typing import List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    fmt,
    get_derived_payload,
    hdr,
    info_box,
    section_title,
)


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
        "#", "Setup", "Mode", "TF", "Bucket", "Target%", "Events", "Win%", "Expectancy(t)", "Score", "Stability",
        "Avg Target t", "Med Target t", "Avg Fav t", "Avg Adv t", "Med Fav t", "Med Adv t"
    ]
    hdr_row = "".join(hdr(h) for h in headers)
    body: List[str] = []

    for i, r in enumerate(rows, 1):
        cols = [
            cell(str(i), bold=True),
            cell(str(r.get("setup_definition", "—"))),
            cell(str(r.get("trade_mode", "—"))),
            cell(f"{r.get('tf_minutes', '—')}m"),
            cell(str(r.get("candle_bucket", "—"))),
            cell(fmt(r.get("target_percent"), 0, "%")),
            cell(str(r.get("n_events", 0))),
            cell(fmt(r.get("win_rate"), 1, "%")),
            cell(fmt(r.get("expectancy_ticks"), 2)),
            cell(fmt(r.get("composite_score"), 3), bg="#e8f5e9", bold=True),
            cell(fmt(r.get("stability_score"), 3)),
            cell(fmt(r.get("avg_target_ticks"), 2)),
            cell(fmt(r.get("median_target_ticks"), 2)),
            cell(fmt(r.get("avg_favorable_ticks"), 2)),
            cell(fmt(r.get("avg_adverse_ticks"), 2)),
            cell(fmt(r.get("median_favorable_ticks"), 2)),
            cell(fmt(r.get("median_adverse_ticks"), 2)),
        ]
        body.append(f"<tr>{''.join(cols)}</tr>")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Top Discoveries — Composite Ranking")
    html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"

    plateau = data.get("plateau_analysis") or {}
    time_splits = data.get("time_split_stability") or {}
    html += section_title("Plateau / Neighbor Analysis (Best Continuation & Reverse)")
    for label, key in (("Best Continuation", "best_continuation_neighbors"), ("Best Reverse", "best_reverse_neighbors")):
        nrows = plateau.get(key) or []
        html += f"<p style='font-size:12px;color:#444'><strong>{label}</strong></p>"
        if not nrows:
            html += "<p style='color:#888'>No nearby target rows available.</p>"
            continue
        ph = ["Target%", "Win%", "N", "Score", "Stability", "Δ vs Anchor"]
        ph_row = "".join(hdr(h) for h in ph)
        pb = []
        for n in nrows:
            pb.append(
                "<tr>"
                + cell(fmt(n.get("target_percent"), 0, "%"), bold=True)
                + cell(fmt(n.get("win_rate"), 1, "%"))
                + cell(str(n.get("n_events", 0)))
                + cell(fmt(n.get("score"), 3))
                + cell(fmt(n.get("stability_score"), 3))
                + cell(fmt(n.get("delta_from_anchor"), 3), bg="#e8f5e9" if float(n.get("delta_from_anchor") or 0) >= 0 else "#fff3e0")
                + "</tr>"
            )
        html += '<div style="overflow-x:auto;margin-bottom:12px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{ph_row}</tr></thead><tbody>{''.join(pb)}</tbody></table></div>"

    html += section_title("Time-Split Stability (Best Continuation & Reverse)")
    for label, key in (("Best Continuation", "best_continuation_splits"), ("Best Reverse", "best_reverse_splits")):
        srows = time_splits.get(key) or []
        html += f"<p style='font-size:12px;color:#444'><strong>{label}</strong></p>"
        if not srows:
            html += "<p style='color:#888'>No split rows available.</p>"
            continue
        sh = ["Split", "N", "WR%", "Score"]
        sh_row = "".join(hdr(h) for h in sh)
        sb = []
        for s in srows:
            sb.append(
                "<tr>"
                + cell(str(s.get("split_id", "—")))
                + cell(str(s.get("n_events", 0)))
                + cell(fmt(s.get("win_rate"), 2, "%"))
                + cell(fmt(s.get("score"), 3))
                + "</tr>"
            )
        html += '<div style="overflow-x:auto;margin-bottom:12px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{sh_row}</tr></thead><tbody>{''.join(sb)}</tbody></table></div>"
    return html
