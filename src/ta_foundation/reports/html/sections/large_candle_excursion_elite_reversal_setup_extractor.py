from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    hdr,
    cell,
    info_box,
    section_title,
)


def _fmt(v: Any, nd: int = 1) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def _elite_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    head = (
        "<tr>"
        + hdr("Rank") + hdr("Setup") + hdr("N") + hdr("Fail%") + hdr("Scalp%")
        + hdr("Expand%") + hdr("Runner%") + hdr("Avg MFE") + hdr("Avg MAE") + hdr("MFE/MAE")
        + hdr("Elite Score") + hdr("Context") + hdr("Early Validation") + hdr("Action")
        + "</tr>"
    )
    body = ""
    for i, r in enumerate(rows, 1):
        c = r.get("conditions") or {}
        s = r.get("strategy") or {}
        context = ", ".join(
            f"{k}={v}" for k, v in c.items() if v not in (None, "", "any") and k != "early_path_class"
        )
        early = " | ".join(s.get("early_validation") or [])
        body += (
            "<tr>"
            + cell(str(i))
            + cell(" / ".join([str(c.get("early_path_class", "mixed")), str(c.get("session", "any")), str(c.get("timeframe", "any"))]))
            + cell(str(r.get("n", "—")))
            + cell(_fmt(r.get("failure_rate")))
            + cell(_fmt(r.get("scalp_rate")))
            + cell(_fmt(r.get("expansion_rate")))
            + cell(_fmt(r.get("runner_rate")))
            + cell(_fmt(r.get("avg_mfe")))
            + cell(_fmt(r.get("avg_mae")))
            + cell(_fmt(r.get("mfe_mae"), 2))
            + cell(_fmt(r.get("elite_score"), 3))
            + cell(context)
            + cell(early)
            + cell(str(r.get("recommended_action", "scalp_only")))
            + "</tr>"
        )
    return '<table style="width:100%;border-collapse:collapse">' + f"<thead>{head}</thead><tbody>{body}</tbody></table>"


def _near_miss_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    body = ""
    for r in rows[:6]:
        c = r.get("conditions") or {}
        body += (
            f"<li><b>{c}</b> | N={r.get('n')} | fail={_fmt(r.get('failure_rate'))}% | "
            f"runner={_fmt(r.get('runner_rate'))}% | rejected: {', '.join(r.get('rejection_reasons') or [])}</li>"
        )
    return '<h4 style="margin:10px 0 6px;color:#2c3e50">Rejected Near-Miss Setups</h4><ul style="font-size:12px;color:#333">' + body + "</ul>"


def _comparison(cards: List[Dict[str, Any]]) -> str:
    if not cards:
        return ""
    html = '<h4 style="margin:10px 0 6px;color:#2c3e50">Comparison vs Baseline / Family</h4>'
    for r in cards:
        cmp = r.get("comparison") or {}
        b = cmp.get("baseline_reversal") or {}
        f = cmp.get("family_baseline") or {}
        e = cmp.get("elite_subset") or {}
        html += (
            '<div style="font-size:12px;color:#333;border:1px solid #dee2e6;border-radius:4px;padding:8px;margin:6px 0">'
            f"<b>{r.get('conditions')}</b><br/>"
            f"Baseline: fail { _fmt(b.get('failure_rate')) }%, runner { _fmt(b.get('runner_rate')) }%, MFE/MAE { _fmt(b.get('mfe_mae'), 2) } | "
            f"Family: fail { _fmt(f.get('failure_rate')) }%, runner { _fmt(f.get('runner_rate')) }%, MFE/MAE { _fmt(f.get('mfe_mae'), 2) } | "
            f"Elite: fail { _fmt(e.get('failure_rate')) }%, runner { _fmt(e.get('runner_rate')) }%, MFE/MAE { _fmt(e.get('mfe_mae'), 2) }"
            "</div>"
        )
    return html


def render_large_candle_excursion_elite_reversal_setup_extractor(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = (packages, options, market, report_config)

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Elite reversal extractor unavailable: findings data missing.")

    elite = data.get("elite_reversal_setup_extractor") or {}
    if not elite.get("enabled"):
        return info_box("Elite reversal extractor disabled or unavailable.", color="#f8f9fa", border="#dee2e6")
    if elite.get("message") and not elite.get("elite_setups"):
        return info_box(f"Elite reversal extractor unavailable: {elite.get('message')}", color="#f8f9fa", border="#dee2e6")

    rs = elite.get("repair_summary") or {}
    rq = elite.get("research_answers") or {}
    picks = elite.get("elite_setups") or []
    near = elite.get("near_miss_setups") or []

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Elite Reversal Setup Extractor")
    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:12px;font-size:12px">'
        f"<b>Repair summary:</b> decision engine broken={rs.get('decision_engine_broken')} | "
        f"probabilities valid={rs.get('probabilities_valid')}<br/>"
        f"Broken: {rs.get('what_was_broken', '—')}<br/>"
        f"Repair: {rs.get('what_changed', '—')}"
        "</div>"
    )

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Elite Setup Ranking</h4>'
    html += _elite_rows(picks)
    html += _comparison(picks)
    html += _near_miss_rows(near)

    if rq:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Research Question Results</h4><ul style="font-size:12px;color:#333">'
        for k, v in rq.items():
            html += f"<li><b>{k}</b>: {v}</li>"
        html += "</ul>"

    html += "</div>"
    return html
