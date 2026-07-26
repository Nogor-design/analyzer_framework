from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    get_derived_payload,
    hdr,
    info_box,
    section_title,
)


def _fmt(v: Any, nd: int = 1) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def _table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p style='font-size:12px;color:#666'>No candidates validated.</p>"
    head = (
        "<tr>"
        + hdr("Candidate")
        + hdr("Type")
        + hdr("IS N")
        + hdr("OOS N")
        + hdr("IS Fail%")
        + hdr("OOS Fail%")
        + hdr("IS Runner%")
        + hdr("OOS Runner%")
        + hdr("Target Hit%")
        + hdr("Net Exp(t)")
        + hdr("Friction")
        + hdr("IS MFE/MAE")
        + hdr("OOS MFE/MAE")
        + hdr("Runner Δpp")
        + hdr("Fail Δpp")
        + hdr("Stability")
        + hdr("Classification")
        + "</tr>"
    )
    body = ""
    for r in rows:
        is_m = r.get("in_sample") or {}
        oos_m = r.get("out_of_sample") or {}
        d = r.get("deltas") or {}
        label = str(r.get("validation_label", "—"))
        bg = "#e8f5e9" if label == "stable_edge" else ("#fff3e0" if label == "acceptable_degradation" else ("#ffebee" if label == "likely_overfit" else "#fafafa"))
        body += (
            "<tr>"
            + cell(str(r.get("candidate_name", "—")))
            + cell(str(r.get("source_type", "—")))
            + cell(str(is_m.get("n", 0)))
            + cell(str(oos_m.get("n", 0)))
            + cell(_fmt(is_m.get("fail_rate")))
            + cell(_fmt(oos_m.get("fail_rate")))
            + cell(_fmt(is_m.get("runner_rate")))
            + cell(_fmt(oos_m.get("runner_rate")))
            + cell(_fmt(oos_m.get("target_hit_rate")))
            + cell(_fmt(oos_m.get("net_expectancy_after_friction_ticks"), 2))
            + cell(str(oos_m.get("friction_viability", "â€”")))
            + cell(_fmt(is_m.get("mfe_mae"), 2))
            + cell(_fmt(oos_m.get("mfe_mae"), 2))
            + cell(_fmt(d.get("runner_rate_delta_pp"), 2))
            + cell(_fmt(d.get("fail_rate_delta_pp"), 2))
            + cell(_fmt(r.get("stability_score"), 3))
            + cell(label, bg=bg, bold=True)
            + "</tr>"
        )
    return f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse"><thead>{head}</thead><tbody>{body}</tbody></table></div>'


def _list_block(title: str, rows: List[Dict[str, Any]]) -> str:
    html = f'<h4 style="margin:10px 0 6px;color:#2c3e50">{title}</h4>'
    if not rows:
        return html + "<p style='font-size:12px;color:#666'>None.</p>"
    html += "<ul style='font-size:12px;color:#333'>"
    for r in rows[:12]:
        oos = r.get("out_of_sample") or {}
        html += (
            f"<li><b>{r.get('candidate_name')}</b> | label={r.get('validation_label')} | "
            f"OOS fail={_fmt(oos.get('fail_rate'))}% | OOS runner={_fmt(oos.get('runner_rate'))}% | "
            f"net_exp={_fmt(oos.get('net_expectancy_after_friction_ticks'), 2)}t | friction={oos.get('friction_viability')} | "
            f"stability={_fmt(r.get('stability_score'), 3)}</li>"
        )
    html += "</ul>"
    return html


def render_large_candle_excursion_edge_validation_engine(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Edge validation unavailable: findings data missing.")

    ev = data.get("edge_validation_engine") or {}
    if not ev.get("enabled"):
        return info_box("Edge validation engine disabled or unavailable.", color="#f8f9fa", border="#dee2e6")
    if ev.get("message"):
        return info_box(f"Edge validation engine unavailable: {ev.get('message')}", color="#f8f9fa", border="#dee2e6")

    cfg = ev.get("validation_configuration") or {}
    baseline = ev.get("overall_baseline") or {}
    validated = ev.get("validated_candidates") or []
    stable = ev.get("stable_candidates") or []
    degrading = ev.get("degrading_candidates") or []
    overfit = ev.get("likely_overfit_candidates") or []
    leaderboard = ev.get("validation_leaderboard") or []
    handoff = ev.get("strategy_handoff") or []
    questions = ev.get("validation_questions") or {}

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Edge Validation Engine")
    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:12px;font-size:12px">'
        f"<b>Validation split:</b> {cfg.get('split')}<br/>"
        f"<b>Minimum samples:</b> {cfg.get('minimum_samples')}<br/>"
        f"<b>Classification rules:</b> {cfg.get('classification_thresholds')}<br/>"
        f"<b>Rolling validation:</b> {cfg.get('rolling_validation')}"
        "</div>"
    )
    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:12px;font-size:12px">'
        f"<b>Overall baseline IS:</b> {baseline.get('in_sample')}<br/>"
        f"<b>Overall baseline OOS:</b> {baseline.get('out_of_sample')}"
        "</div>"
    )

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Candidate Summary</h4>'
    html += _table(validated)
    html += _list_block("Stable Candidates", stable)
    html += _list_block("Degrading but Interesting Candidates", degrading)
    html += _list_block("Likely Overfit Candidates", overfit)

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Validation Leaderboard</h4>'
    html += _table(leaderboard)

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Research Conclusions</h4><ul style="font-size:12px;color:#333">'
    rc = ev.get("research_conclusions") or {}
    html += f"<li><b>Survived:</b> {rc.get('survived', [])}</li>"
    html += f"<li><b>Degraded:</b> {rc.get('degraded', [])}</li>"
    html += f"<li><b>Likely overfit:</b> {rc.get('likely_overfit', [])}</li>"
    html += f"<li><b>Paper test next:</b> {rc.get('paper_test_next', [])}</li>"
    html += f"<li><b>Discard:</b> {rc.get('discard', [])}</li>"
    html += "</ul>"

    if handoff:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Practical Strategy Handoff</h4>'
        for h in handoff:
            html += (
                '<div style="font-size:12px;color:#333;border:1px solid #dee2e6;border-radius:4px;padding:8px;margin:6px 0">'
                f"<b>Candidate:</b> {h.get('candidate')}<br/>"
                f"<b>Branch definition:</b> {h.get('branch_definition')}<br/>"
                f"<b>Why it survived:</b> {h.get('why_it_survived')}<br/>"
                f"<b>IS vs OOS:</b> {h.get('is_vs_oos')}<br/>"
                f"<b>Practical recommendation:</b> {h.get('practical_recommendation')}"
                "</div>"
            )

    if questions:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Validation Questions</h4><ul style="font-size:12px;color:#333">'
        for k, v in questions.items():
            html += f"<li><b>{k}</b>: {v}</li>"
        html += "</ul>"

    html += "</div>"
    return html

