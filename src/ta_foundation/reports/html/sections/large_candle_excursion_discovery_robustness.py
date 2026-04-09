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
    split_rows = data.get("time_split_validation") or []
    tradable = data.get("final_discoveries") or []
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

    if split_rows:
        html += section_title("Time-Split Robustness")
        for sr in split_rows[:10]:
            html += f"<p style='font-size:12px;color:#444'><strong>{sr.get('setup_definition')}</strong></p>"
            sh = ["Split", "N", "Win%", "Score", "Expectancy(t)"]
            sh_row = "".join(hdr(h) for h in sh)
            sb = []
            for s in sr.get("splits") or []:
                sb.append(
                    "<tr>"
                    + cell(str(s.get("split_id", "—")))
                    + cell(str(s.get("n_events", 0)))
                    + cell(fmt(s.get("win_rate"), 2, "%"))
                    + cell(fmt(s.get("score"), 3))
                    + cell(fmt(s.get("expectancy_ticks"), 3))
                    + "</tr>"
                )
            html += '<div style="overflow-x:auto;margin-bottom:14px"><table style="border-collapse:collapse;width:100%;font-size:12px">'
            html += f"<thead><tr>{sh_row}</tr></thead><tbody>{''.join(sb)}</tbody></table></div>"

    if tradable:
        html += section_title("Tradability Metrics (Top Final Discoveries)")
        th = [
            "Setup", "Avg Target t", "Med Target t", "Avg Fav t", "Med Fav t",
            "Avg Adv t", "Med Adv t", "Stop-Hit %", "Expectancy t"
        ]
        th_row = "".join(hdr(h) for h in th)
        tb = []
        for t in tradable[:20]:
            tb.append(
                "<tr>"
                + cell(str(t.get("setup_definition", "—")))
                + cell(fmt(t.get("avg_target_ticks"), 2))
                + cell(fmt(t.get("median_target_ticks"), 2))
                + cell(fmt(t.get("avg_favorable_ticks"), 2))
                + cell(fmt(t.get("median_favorable_ticks"), 2))
                + cell(fmt(t.get("avg_adverse_ticks"), 2))
                + cell(fmt(t.get("median_adverse_ticks"), 2))
                + cell(fmt(t.get("stop_hit_rate"), 2, "%"))
                + cell(fmt(t.get("expectancy_ticks"), 3), bg="#e8f5e9")
                + "</tr>"
            )
        html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        html += f"<thead><tr>{th_row}</tr></thead><tbody>{''.join(tb)}</tbody></table></div>"
    return html
