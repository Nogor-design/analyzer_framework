from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    get_derived_payload,
    hdr,
    info_box,
    section_title,
)


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def _list_items(values: List[str]) -> str:
    if not values:
        return "<span style='color:#666'>None</span>"
    return "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{v}</li>" for v in values) + "</ul>"


def _input_summary(inp: Dict[str, Any]) -> str:
    considered = inp.get("considered") or []
    rejected = inp.get("rejected_before_construction") or []
    html = "<h4 style='margin:10px 0 6px;color:#2c3e50'>Input Candidate Summary</h4>"
    html += f"<p style='font-size:12px;color:#333'>Considered: <b>{len(considered)}</b> | Rejected before construction: <b>{len(rejected)}</b></p>"
    if considered:
        html += "<ul style='font-size:12px;color:#333'>"
        for c in considered[:12]:
            html += f"<li><b>{c.get('candidate_name')}</b> | type={c.get('source_type')} | label={c.get('validation_label')} | OOS N={c.get('oos_n')}</li>"
        html += "</ul>"
    if rejected:
        html += "<h5 style='margin:8px 0 4px;color:#2c3e50'>Rejected before construction</h5><ul style='font-size:12px;color:#333'>"
        for r in rejected[:12]:
            html += f"<li><b>{r.get('candidate_name')}</b> | label={r.get('validation_label')} | reason={r.get('reason')}</li>"
        html += "</ul>"
    return html


def _strategy_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p style='font-size:12px;color:#666'>No constructed strategies.</p>"
    head = (
        "<tr>"
        + hdr("Strategy")
        + hdr("Source Candidate")
        + hdr("Archetype")
        + hdr("Deployment Score")
        + hdr("Net Exp(t)")
        + hdr("Friction")
        + hdr("Complexity")
        + hdr("Paper-Test Priority")
        + hdr("Automation")
        + "</tr>"
    )
    body = ""
    for r in rows:
        body += (
            "<tr>"
            + cell(str(r.get("strategy_name", "—")))
            + cell(str(r.get("source_candidate", "—")))
            + cell(str(r.get("archetype", "—")))
            + cell(_fmt(r.get("deployment_score"), 3))
            + cell(_fmt((r.get("live_friction") or {}).get("net_expectancy_after_friction_ticks"), 2))
            + cell(str((r.get("live_friction") or {}).get("friction_viability", "---")))
            + cell(str(r.get("complexity_level", "—")))
            + cell(str(r.get("paper_test_priority", "—")))
            + cell(str(r.get("automation_readiness", "—")))
            + "</tr>"
        )
    return (
        "<h4 style='margin:10px 0 6px;color:#2c3e50'>Constructed Strategy List</h4>"
        + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        + f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"
    )


def _card(s: Dict[str, Any]) -> str:
    context = s.get("eligible_market_context") or {}
    entry = s.get("entry_trigger") or {}
    stop = s.get("initial_stop_logic") or {}
    early = s.get("early_management_logic") or {}
    pt = s.get("profit_taking_logic") or {}
    nt8 = s.get("ninjatrader_translation_notes") or {}
    friction = s.get("live_friction") or {}
    warnings = s.get("failure_mode_warnings") or []
    return (
        '<div style="border:1px solid #dee2e6;border-radius:4px;padding:10px;margin:8px 0;font-size:12px;color:#333">'
        f"<h5 style='margin:0 0 8px;color:#2c3e50'>{s.get('strategy_name')}</h5>"
        f"<b>Thesis:</b> {s.get('thesis')}<br/>"
        f"<b>Context:</b> {context}<br/>"
        f"<b>Entry:</b> {entry}<br/>"
        f"<b>Initial invalidation:</b> {s.get('initial_invalidation')}<br/>"
        f"<b>Stop:</b> {stop}<br/>"
        f"<b>Early management:</b> {early}<br/>"
        f"<b>Targets / exit:</b> {pt}<br/>"
        f"<b>Live friction:</b> {friction}<br/>"
        f"<b>Session rules:</b> {s.get('session_rules')}<br/>"
        f"<b>Risk / position:</b> {s.get('risk_position_logic')}<br/>"
        f"<b>Warnings:</b> {warnings}<br/>"
        f"<b>NT8 notes:</b> {nt8}"
        "</div>"
    )


def render_large_candle_excursion_strategy_construction_engine(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Strategy construction engine unavailable: findings data missing.")

    sce = data.get("strategy_construction_engine") or {}
    if not sce.get("enabled"):
        return info_box("Strategy construction engine disabled or unavailable.", color="#f8f9fa", border="#dee2e6")
    if sce.get("message"):
        return info_box(f"Strategy construction engine unavailable: {sce.get('message')}", color="#f8f9fa", border="#dee2e6")

    strategies = sce.get("constructed_strategies") or []
    summary = sce.get("summary") or {}
    rankings = sce.get("deployment_rankings") or {}
    rq = sce.get("research_questions") or {}

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Strategy Construction Engine")
    html += _input_summary(sce.get("input_candidate_summary") or {})
    html += _strategy_table(strategies)

    html += "<h4 style='margin:10px 0 6px;color:#2c3e50'>Strategy Detail Cards</h4>"
    for s in strategies:
        html += _card(s)

    rejected = sce.get("rejected_construction_candidates") or []
    html += "<h4 style='margin:10px 0 6px;color:#2c3e50'>Rejected Construction Candidates</h4>"
    if rejected:
        html += "<ul style='font-size:12px;color:#333'>"
        for r in rejected:
            html += f"<li><b>{r.get('candidate_name')}</b> | reason={r.get('reason')} | details={r.get('details')}</li>"
        html += "</ul>"
    else:
        html += "<p style='font-size:12px;color:#666'>None.</p>"

    html += "<h4 style='margin:10px 0 6px;color:#2c3e50'>Practical Next-Step Recommendations</h4>"
    html += (
        "<div style='font-size:12px;color:#333'>"
        f"<b>code_next</b>{_list_items(rankings.get('code_next') or [])}"
        f"<b>paper_test_ready</b>{_list_items(rankings.get('paper_test_ready') or [])}"
        f"<b>monitor_only</b>{_list_items(rankings.get('monitor_only') or [])}"
        f"<b>discard</b>{_list_items(rankings.get('discard') or [])}"
        "</div>"
    )

    html += "<h4 style='margin:10px 0 6px;color:#2c3e50'>Research Questions Answered</h4>"
    html += "<ul style='font-size:12px;color:#333'>"
    for k, v in rq.items():
        html += f"<li><b>{k}</b>: {v}</li>"
    html += "</ul>"

    html += "<h4 style='margin:10px 0 6px;color:#2c3e50'>Best Candidate Summary</h4>"
    html += (
        "<ul style='font-size:12px;color:#333'>"
        f"<li><b>Best strategy candidates:</b> {summary.get('best_strategy_candidates')}</li>"
        f"<li><b>Best scalp candidate:</b> {summary.get('best_scalp_candidate')}</li>"
        f"<li><b>Best expansion candidate:</b> {summary.get('best_expansion_candidate')}</li>"
        f"<li><b>Best runner candidate:</b> {summary.get('best_runner_candidate')}</li>"
        f"<li><b>Best hybrid candidate:</b> {summary.get('best_hybrid_candidate')}</li>"
        f"<li><b>Paper-test first:</b> {summary.get('paper_test_first')}</li>"
        "</ul>"
    )

    html += "</div>"
    return html
