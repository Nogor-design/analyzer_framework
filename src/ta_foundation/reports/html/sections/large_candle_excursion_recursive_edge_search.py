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


def _filters(d: Dict[str, Any]) -> str:
    return " AND ".join(f"{k}={v}" for k, v in (d or {}).items()) or "baseline"


def _tree(root: Dict[str, Any]) -> str:
    rows = root.get("children_tested") or []
    if not rows:
        return "<p style='font-size:12px;color:#666'>No children tested for this root.</p>"
    head = (
        "<tr>"
        + hdr("Depth")
        + hdr("Child refinement")
        + hdr("Status")
        + hdr("Reason")
        + hdr("N")
        + hdr("Fail%")
        + hdr("Expand%")
        + hdr("Runner%")
        + hdr("MFE/MAE")
        + hdr("Score")
        + "</tr>"
    )
    body = ""
    for r in rows:
        m = r.get("metrics") or {}
        status = str(r.get("status", "pruned"))
        bg = "#e8f5e9" if status == "promoted" else "#ffebee"
        body += (
            "<tr>"
            + cell(str(r.get("depth", "—")))
            + cell(_filters(r.get("filters") or {}))
            + cell(status, bg=bg)
            + cell(str(r.get("reason", "—")))
            + cell(str(m.get("n", "—")))
            + cell(_fmt(m.get("fail_rate")))
            + cell(_fmt(m.get("expansion_rate")))
            + cell(_fmt(m.get("runner_rate")))
            + cell(_fmt(m.get("mfe_mae"), 2))
            + cell(_fmt(m.get("branch_score"), 3))
            + "</tr>"
        )
    return '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;margin-bottom:10px">' + f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"


def _best(rows: List[Dict[str, Any]], title: str) -> str:
    if not rows:
        return ""
    head = (
        "<tr>"
        + hdr("Depth")
        + hdr("Branch")
        + hdr("Parent")
        + hdr("N")
        + hdr("Fail%")
        + hdr("Expand%")
        + hdr("Runner%")
        + hdr("Avg MFE")
        + hdr("Avg MAE")
        + hdr("MFE/MAE")
        + hdr("Stability")
        + hdr("Branch Score")
        + hdr("Why promoted/pruned")
        + "</tr>"
    )
    body = ""
    for r in rows:
        m = r.get("metrics") or {}
        body += (
            "<tr>"
            + cell(str(r.get("depth", "—")))
            + cell(_filters(r.get("filters") or {}))
            + cell(_filters(r.get("parent_filters") or {}))
            + cell(str(m.get("n", "—")))
            + cell(_fmt(m.get("fail_rate")))
            + cell(_fmt(m.get("expansion_rate")))
            + cell(_fmt(m.get("runner_rate")))
            + cell(_fmt(m.get("avg_mfe"), 2))
            + cell(_fmt(m.get("avg_mae"), 2))
            + cell(_fmt(m.get("mfe_mae"), 2))
            + cell(_fmt(m.get("stability_score"), 3))
            + cell(_fmt(m.get("branch_score"), 3))
            + cell(str(r.get("reason", "—")))
            + "</tr>"
        )
    return f'<h4 style="margin:10px 0 6px;color:#2c3e50">{title}</h4><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">{head}{body}</table></div>'


def render_large_candle_excursion_recursive_edge_search(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")
    _ = packages, options, market, report_config

    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Recursive edge search unavailable: findings data missing.")
    search = data.get("recursive_edge_search") or {}
    if not search.get("enabled"):
        return info_box("Recursive edge search disabled or unavailable.", color="#f8f9fa", border="#dee2e6")
    if search.get("message"):
        return info_box(f"Recursive edge search unavailable: {search.get('message')}", color="#f8f9fa", border="#dee2e6")

    cfg = search.get("search_configuration") or {}
    roots = search.get("roots") or []
    best = search.get("best_promoted_branches") or []
    dead = search.get("dead_end_branches") or []
    finals = search.get("final_promoted_candidates") or []
    rq = search.get("research_questions") or {}
    handoff = search.get("strategy_handoff") or []

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Recursive Edge Search")
    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:12px;font-size:12px">'
        f"<b>Seed types:</b> {', '.join(cfg.get('seed_types_used') or [])}<br/>"
        f"<b>Depth:</b> max_depth={cfg.get('max_depth')} | max_children_per_node={cfg.get('max_children_per_node')} | max_total_nodes={cfg.get('max_total_nodes')}<br/>"
        f"<b>Promotion rules:</b> {cfg.get('promotion_rules')}<br/>"
        f"<b>Pruning rules:</b> {cfg.get('pruning_rules')}<br/>"
        f"<b>Scoring formula:</b> {cfg.get('scoring_formula')}"
        "</div>"
    )

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Seed Summary</h4><ul style="font-size:12px;color:#333">'
    for s in (search.get("seed_summary") or []):
        html += f"<li><b>{s.get('seed_type')}</b> | {_filters(s.get('filters') or {})} | N={s.get('n')}</li>"
    html += "</ul>"

    html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Promoted Edge Tree</h4>'
    for root in roots[:8]:
        html += (
            '<div style="border:1px solid #dee2e6;border-radius:4px;padding:8px;margin:8px 0">'
            f"<div style='font-size:12px;color:#333'><b>Root:</b> {_filters(root.get('filters') or {})} "
            f"| seed={root.get('seed_type')} | N={(root.get('metrics') or {}).get('n')} | score={_fmt((root.get('metrics') or {}).get('branch_score'), 3)}</div>"
            f"{_tree(root)}"
            "</div>"
        )

    html += _best(best, "Best Promoted Branches")
    html += _best(dead, "Dead-end / Misleading Branches")
    html += _best(finals, "Final Promoted Candidates")

    if rq:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Research Questions</h4><ul style="font-size:12px;color:#333">'
        for k, v in rq.items():
            html += f"<li><b>{k}</b>: {v}</li>"
        html += "</ul>"

    if handoff:
        html += '<h4 style="margin:10px 0 6px;color:#2c3e50">Strategy Handoff</h4>'
        for h in handoff:
            html += (
                '<div style="font-size:12px;color:#333;border:1px solid #dee2e6;border-radius:4px;padding:8px;margin:6px 0">'
                f"<b>Entry:</b> {_filters(h.get('entry') or {})}<br/>"
                f"<b>Early validation:</b> {' | '.join(h.get('early_validation') or [])}<br/>"
                f"<b>Decision:</b> {h.get('decision')}<br/>"
                f"<b>Why survived recursion:</b> {h.get('why_survived_recursion')}"
                "</div>"
            )

    html += "</div>"
    return html
