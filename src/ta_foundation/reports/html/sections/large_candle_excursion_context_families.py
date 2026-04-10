from __future__ import annotations

"""
Context-Conditioned Setup Families Section
==========================================
Renders the strongest signal-context conditions per base setup family.

For each (direction / TF / candle-size) family, shows which context
features — prior-candle direction, MA/VWAP location, key-level proximity,
trend alignment — most improve or degrade continuation odds.

This is the "decision intelligence" layer: it tells you not just THAT a
large reversal candle works, but WHEN and under WHAT CONDITIONS it works best.
"""

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell, fmt, hdr, info_box, section_title,
)


def _get_findings(ctx: dict) -> dict:
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion_findings" in d:
            return d["large_candle_excursion_findings"]
    return ctx.get("large_candle_excursion_findings") or {}


def _lift_span(lift_pp: float) -> str:
    if lift_pp >= 5:
        color = "#1b5e20"
    elif lift_pp >= 3:
        color = "#2e7d32"
    elif lift_pp <= -5:
        color = "#b71c1c"
    elif lift_pp <= -3:
        color = "#c62828"
    else:
        color = "#555"
    sign = "+" if lift_pp >= 0 else ""
    return f'<span style="color:{color};font-weight:bold">{sign}{lift_pp:.1f} pp</span>'


def _better_badge(better: str) -> str:
    if better == "continuation":
        return '<span style="background:#1b5e20;color:#fff;border-radius:3px;padding:1px 5px;font-size:10px">CONT</span>'
    if better == "reversal":
        return '<span style="background:#1a237e;color:#fff;border-radius:3px;padding:1px 5px;font-size:10px">REV</span>'
    return '<span style="background:#888;color:#fff;border-radius:3px;padding:1px 5px;font-size:10px">NEUTRAL</span>'


def render_large_candle_excursion_context_families(ctx: dict) -> str:
    findings = _get_findings(ctx)
    if not findings or not findings.get("has_source"):
        return info_box("Context-conditioned families: no findings data available.")

    setups = findings.get("context_conditioned_setups") or []

    html = '<div style="font-family:Arial,sans-serif">'

    if not setups:
        # Explain why
        diag = findings.get("diagnostics") or {}
        n_ctx = diag.get("n_context_conditioned", 0)
        context_msg = ""
        if n_ctx == 0:
            context_msg = (
                "No context-conditioned setups found. This can happen when: "
                "(1) context modules are disabled in the YAML, "
                "(2) the events sample has insufficient N per context bucket (&lt;30 events), "
                "or (3) no context bucket showed &ge;3 pp lift vs population baseline. "
                "Check the <b>Context Coverage Diagnostics</b> section for per-module details."
            )
        return info_box(context_msg or "No context-conditioned setups available.")

    html += section_title(f"Top Context-Conditioned Setup Families ({len(setups)} found)")

    # Intro
    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:10px">'
        "For each base setup family (direction / timeframe / candle-size), these are the signal-candle "
        "context conditions that most change continuation vs reversal odds. "
        "Lift = WR of this context condition vs population baseline. "
        "Edge = |Cont WR &minus; Rev WR|. "
        "Focus on rows with Lift &ge; +5 pp and N &ge; 50."
        "</p>"
    )

    # Group by family for cleaner display
    from collections import OrderedDict
    by_family: Dict[str, List[Dict]] = OrderedDict()
    for s in setups:
        fl = s.get("family_label", "Unknown")
        by_family.setdefault(fl, []).append(s)

    headers = ["Context Feature", "Condition", "N", "Cont WR", "Rev WR",
               "Lift vs Pop", "Edge pp", "Fam WR", "Better"]
    hdr_row = "".join(hdr(h) for h in headers)

    for fam_label, fam_setups in by_family.items():
        html += (
            f'<div style="margin-top:14px;margin-bottom:2px;font-size:13px;font-weight:bold;'
            f'color:#1565c0;border-bottom:1px solid #90caf9;padding-bottom:2px">'
            f'{fam_label}'
            f'</div>'
        )
        # Show family baseline
        if fam_setups:
            fam_wr  = fam_setups[0].get("family_cont_wr")
            pop_wr  = fam_setups[0].get("baseline_cont_wr")
            if fam_wr is not None and pop_wr is not None:
                html += (
                    f'<p style="font-size:11px;color:#777;margin:2px 0 4px 0">'
                    f'Family Cont WR: <b>{fam_wr:.1f}%</b> &nbsp;|&nbsp; '
                    f'Population baseline: {pop_wr:.1f}%'
                    f'</p>'
                )

        body_rows = []
        for s in fam_setups:
            lift_pp = s.get("lift_pp", 0.0)
            row_bg  = "#f1f8e9" if lift_pp >= 3 else ("#fce4ec" if lift_pp <= -3 else "")
            row_style = f' style="background:{row_bg}"' if row_bg else ""
            body_rows.append(
                f"<tr{row_style}>"
                + cell(s.get("context_feature", "—"))
                + cell(f"<code>{s.get('context_bucket', '—')}</code>", bold=True)
                + cell(str(s.get("n", 0)))
                + cell(fmt(s.get("cont_wr"), 1, "%"))
                + cell(fmt(s.get("rev_wr"), 1, "%"))
                + cell(_lift_span(float(lift_pp)))
                + cell(
                    f'<span style="background:{"#e8f5e9" if (s.get("edge_pp") or 0) >= 4 else ""}'
                    f';padding:1px 3px">{fmt(s.get("edge_pp"), 1, " pp")}</span>'
                )
                + cell(fmt(s.get("family_cont_wr"), 1, "%"))
                + cell(_better_badge(s.get("better", "neutral")))
                + "</tr>"
            )

        html += (
            '<div style="overflow-x:auto">'
            '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f"<thead><tr>{hdr_row}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table></div>"
        )

    # Intelligence layer: narrative insights
    html += _render_narrative_insights(setups)

    html += (
        '<p style="font-size:10px;color:#888;margin-top:8px">'
        "Lift = context bucket WR &minus; population continuation WR. "
        "Family WR = continuation WR for all events in this base family (all contexts). "
        "Min N = 30 per cell. Min |Lift| = 3 pp. Based on enriched events sample."
        "</p>"
    )
    html += "</div>"
    return html


def _render_narrative_insights(setups: List[Dict]) -> str:
    """Generate human-readable insights from the strongest context-conditioned setups."""
    if not setups:
        return ""

    strong_positive = [s for s in setups if s.get("lift_pp", 0) >= 5 and s.get("n", 0) >= 40]
    strong_negative = [s for s in setups if s.get("lift_pp", 0) <= -5 and s.get("n", 0) >= 40]

    if not strong_positive and not strong_negative:
        return ""

    html = section_title("Context Intelligence — Key Findings")
    html += '<ul style="font-size:12px;color:#333;line-height:1.8">'

    # Group by context feature type for narrative
    cont_patterns = [s for s in strong_positive if s.get("better") == "continuation"]
    rev_patterns  = [s for s in strong_positive if s.get("better") == "reversal"]

    for s in cont_patterns[:3]:
        fam   = s.get("family_label", "")
        feat  = s.get("context_feature", "")
        bkt   = s.get("context_bucket", "")
        lift  = s.get("lift_pp", 0)
        wr    = s.get("cont_wr", 0)
        html += (
            f'<li><b>Continuation performs best</b> for {fam} when '
            f'{feat.lower()} is <code>{bkt}</code> — '
            f'Win rate {wr:.1f}% (+{lift:.1f} pp above baseline)</li>'
        )

    for s in rev_patterns[:3]:
        fam   = s.get("family_label", "")
        feat  = s.get("context_feature", "")
        bkt   = s.get("context_bucket", "")
        lift  = s.get("lift_pp", 0)
        wr    = s.get("rev_wr", 0) or 0
        html += (
            f'<li><b>Reversal odds improve</b> for {fam} when '
            f'{feat.lower()} is <code>{bkt}</code> — '
            f'Continuation WR drops to {s.get("cont_wr", 0):.1f}% '
            f'({lift:.1f} pp vs baseline); reversal WR: {wr:.1f}%</li>'
        )

    for s in strong_negative[:2]:
        fam   = s.get("family_label", "")
        feat  = s.get("context_feature", "")
        bkt   = s.get("context_bucket", "")
        lift  = s.get("lift_pp", 0)
        html += (
            f'<li><b>Avoid</b> {fam} when '
            f'{feat.lower()} is <code>{bkt}</code> — '
            f'WR drops {lift:.1f} pp below baseline</li>'
        )

    html += "</ul>"
    return html
