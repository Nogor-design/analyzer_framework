from __future__ import annotations

from typing import List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import cell, fmt, get_derived_payload, hdr, info_box, section_title


def _render_table(title: str, rows: List[dict]) -> str:
    if not rows:
        return f"<p style='color:#888'>{title}: no rows.</p>"
    headers = ["Bucket", "N", "Cont Win%", "Rev Win%", "Edge", "Better"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        bucket = r.get("bucket") or r.get("vol_bucket") or r.get("close_pos_bucket") or r.get("atr_bucket") or "—"
        body.append(
            "<tr>"
            + cell(str(bucket), bold=True)
            + cell(str(r.get("n_observations", 0)))
            + cell(fmt(r.get("cont_win_rate"), 1, "%"))
            + cell(fmt(r.get("rev_win_rate"), 1, "%"))
            + cell(fmt(r.get("edge_abs"), 2), bg="#e8f5e9")
            + cell(str(r.get("better_mode", "—")))
            + "</tr>"
        )
    return section_title(title) + '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">' + f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _render_attempted(rows: List[dict]) -> str:
    if not rows:
        return ""
    headers = ["Condition Combination", "Event Count", "Win%", "Score", "Rejection Reason"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        reason = r.get("rejection_reason") or "passed"
        bg = "#fff3e0" if reason != "passed" else "#e8f5e9"
        body.append(
            "<tr>"
            + cell(str(r.get("condition_combination", "—")))
            + cell(str(r.get("event_count", 0)))
            + cell(fmt(r.get("win_rate"), 1, "%"))
            + cell(fmt(r.get("score"), 3))
            + cell(str(reason), bg=bg)
            + "</tr>"
        )
    return section_title("Attempted Interaction Candidates") + '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">' + f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def render_large_candle_excursion_findings_interactions(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Findings interaction data missing.")
    if not data.get("has_source"):
        return info_box(f"Findings unavailable: {data.get('message', 'source analytics missing')}.")

    strong = data.get("strong_context_effects") or {}
    html = '<div style="font-family:Arial,sans-serif">'
    html += _render_table("Strongest Volume Effects", strong.get("volume") or [])
    html += _render_table("Strongest Structure Effects", strong.get("structure") or [])
    html += _render_table("Strongest Volatility Effects", strong.get("volatility") or [])
    html += _render_table("Strongest Interaction Effects", data.get("strongest_interactions") or [])
    html += _render_attempted((data.get("interaction_diagnostics") or {}).get("attempted") or [])
    html += "</div>"
    return html
