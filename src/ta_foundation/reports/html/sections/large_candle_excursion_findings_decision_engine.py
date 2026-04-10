from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    hdr,
    cell,
    info_box,
    section_title,
)


def _table(rows: List[Dict[str, Any]], title: str) -> str:
    if not rows:
        return ""
    head = (
        "<tr>"
        + hdr("Conditions")
        + hdr("N")
        + hdr("Fail%")
        + hdr("Scalp%")
        + hdr("Expand%")
        + hdr("Runner%")
        + "</tr>"
    )
    body = ""
    for r in rows[:20]:
        cond = ", ".join(f"{k}={v}" for k, v in (r.get("group_key") or {}).items())
        body += (
            "<tr>"
            + cell(cond)
            + cell(str(r.get("n", "—")))
            + cell(f"{r.get('failure_rate', 0):.1f}")
            + cell(f"{r.get('scalp_rate', 0):.1f}")
            + cell(f"{r.get('expansion_rate', 0):.1f}")
            + cell(f"{r.get('runner_rate', 0):.1f}")
            + "</tr>"
        )
    return (
        f'<h4 style="margin:10px 0 6px;color:#2c3e50">{title}</h4>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table>"
    )


def _rule_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    head = (
        "<tr>"
        + hdr("Rule") + hdr("Action") + hdr("N") + hdr("Fail%")
        + hdr("Expand%") + hdr("Runner%") + hdr("Lift vs Baseline Runner (pp)")
        + hdr("MFE/MAE")
        + "</tr>"
    )
    body = ""
    for r in rows:
        cond = " AND ".join(f"{k}={v}" for k, v in (r.get("conditions") or {}).items())
        body += (
            "<tr>"
            + cell(cond)
            + cell(str(r.get("recommended_action", "—")))
            + cell(str(r.get("n", "—")))
            + cell(f"{r.get('failure_rate', 0):.1f}")
            + cell(f"{r.get('expansion_rate', 0):.1f}")
            + cell(f"{r.get('runner_rate', 0):.1f}")
            + cell(f"{r.get('lift_vs_baseline_runner_pp', 0):.1f}")
            + cell(f"{r.get('mfe_mae', 0) if r.get('mfe_mae') is not None else '—'}")
            + "</tr>"
        )
    return (
        '<h4 style="margin:10px 0 6px;color:#2c3e50">Extracted Mechanical Decision Rules</h4>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:12px">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table>"
    )


def render_large_candle_excursion_findings_decision_engine(ctx: dict) -> str:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Decision engine unavailable: findings data missing.")

    de = (data.get("reversal_decision_engine") or {})
    if not de.get("enabled"):
        return info_box("Decision engine disabled or unavailable.", color="#f8f9fa", border="#dee2e6")

    if de.get("message"):
        return info_box(f"Decision engine unavailable: {de.get('message')}", color="#f8f9fa", border="#dee2e6")

    tables = de.get("tables") or {}
    base = de.get("baseline") or {}
    rq = de.get("research_questions") or {}

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Reversal Decision Engine Findings")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        'This section converts reversal analytics into operational actions: scratch, scalp-only, hold-for-expansion, '
        'hold-for-runner, and (if evidence supports it) press-runner. '
        f"Strong runner is defined as: <b>{de.get('strong_runner_definition', 'n/a')}</b>."
        '</p>'
    )

    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:12px;font-size:12px">'
        f"<b>Baseline (N={base.get('n', 0)}):</b> "
        f"Fail {base.get('failure_rate', 0):.1f}% | "
        f"Scalp {base.get('scalp_rate', 0):.1f}% | "
        f"Expansion {base.get('expansion_rate', 0):.1f}% | "
        f"Runner {base.get('runner_rate', 0):.1f}% | "
        f"MFE/MAE {base.get('mfe_mae', '—')}"
        "</div>"
    )

    html += _table(tables.get("outcome_by_early_path_class") or [], "A. Outcome Distribution by Early-Path Class")
    html += _table(tables.get("outcome_by_early_path_and_session") or [], "B. Outcome Distribution by Early-Path Class + Session")
    html += _table(tables.get("outcome_by_early_path_and_vwap_stretch") or [], "C. Outcome Distribution by Early-Path Class + VWAP Stretch")
    html += _table(tables.get("outcome_by_early_path_and_trend_state") or [], "D. Outcome Distribution by Early-Path Class + Trend State")

    thresholds = de.get("threshold_discovery") or []
    if thresholds:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Threshold Discovery</h4><ul style="font-size:12px;color:#333">'
        for t in thresholds:
            html += (
                f"<li><b>{t.get('threshold')}</b> | N={t.get('n')} | "
                f"runner lift {t.get('runner_lift_pp', 0):+.1f}pp | "
                f"failure Δ {t.get('failure_delta_pp', 0):+.1f}pp</li>"
            )
        html += "</ul>"

    html += _rule_table(de.get("decision_rules") or [])

    if rq:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Explicit Research Question Checks</h4><ul style="font-size:12px;color:#333">'
        for k, v in rq.items():
            holds = v if isinstance(v, bool) else v.get("holds")
            mark = "✅" if holds else "❌"
            html += f"<li>{mark} <b>{k}</b>: {v}</li>"
        html += "</ul>"

    html += '</div>'
    return html
