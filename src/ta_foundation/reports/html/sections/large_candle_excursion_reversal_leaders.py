from __future__ import annotations

"""
Large Candle Excursion — Large Reversal Move Leaders
=====================================================
Ranks reverse setups by how far they go, not just whether they "won."

Sections rendered
-----------------
1. Summary metrics (population baseline for the forward window)
2. Setup rankings — top setups by 50%+ / 75%+ reversal probability
3. Runner potential leaderboard — which setups keep going after first target
4. Context-conditioned rankings — which context produces the biggest reversals
5. Large-move strategy cards — detailed narratives for top candidates
"""

from typing import Any, Dict, List, Optional

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell, fmt, hdr, info_box, section_title,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_reversal_data(ctx: dict) -> dict:
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion_findings" in d:
            return d["large_candle_excursion_findings"].get("reversal_size_analysis") or {}
    return ctx.get("large_candle_excursion_findings", {}).get("reversal_size_analysis") or {}


def _pct(val: Any, digits: int = 1) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _badge(val: float, threshold_lo: float, threshold_hi: float, fmt_str: str = ".1f") -> str:
    """Colour-coded number badge."""
    color = "#1b5e20" if val >= threshold_hi else ("#e65100" if val >= threshold_lo else "#888")
    return f'<span style="color:{color};font-weight:bold">{val:{fmt_str}}%</span>'


def _runner_badge(score: float) -> str:
    if score >= 0.55:
        bg, label = "#1b5e20", "RUNNER"
    elif score >= 0.45:
        bg, label = "#2e7d32", "STRONG"
    elif score >= 0.35:
        bg, label = "#f57f17", "MODERATE"
    else:
        bg, label = "#888", "SCALP"
    return (
        f'<span style="background:{bg};color:#fff;border-radius:3px;'
        f'padding:1px 6px;font-size:10px">{label}</span>'
    )


def _lmc_badge() -> str:
    return (
        '<span style="background:#0d47a1;color:#fff;border-radius:3px;'
        'padding:1px 6px;font-size:10px">★ LM CANDIDATE</span>'
    )


def _tier_bar(tier_dist: Dict[str, float]) -> str:
    """Stacked mini bar of move-size tiers."""
    color_map = {
        "failed":   "#ef9a9a",
        "micro":    "#ffcc80",
        "standard": "#fff176",
        "large":    "#a5d6a7",
        "extended": "#1b5e20",
        "unknown":  "#ddd",
    }
    order = ["extended", "large", "standard", "micro", "failed", "unknown"]
    segments = []
    for lbl in order:
        pct = tier_dist.get(lbl, 0)
        if pct < 2:
            continue
        color = color_map.get(lbl, "#bbb")
        segments.append(
            f'<span title="{lbl}: {pct:.1f}%" '
            f'style="display:inline-block;width:{pct:.0f}px;max-width:{pct:.0f}px;'
            f'height:10px;background:{color};margin:0 0.5px"></span>'
        )
    return f'<span style="display:inline-flex;align-items:center">{"".join(segments)}</span>'


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_population_summary(pop: Dict[str, Any], win_min: int) -> str:
    if not pop:
        return ""
    r  = pop.get("reach_pct", {})
    html = (
        f'<div style="background:#e3f2fd;border-left:4px solid #1565c0;'
        f'padding:10px 14px;border-radius:4px;font-size:12px;margin-bottom:14px">'
        f'<b>Population baseline</b> &mdash; {win_min}-minute forward window &nbsp;|&nbsp; '
        f'N={pop.get("n", 0):,} valid events<br>'
        f'<span style="color:#555">'
        f'Reach 25%: <b>{r.get(25, 0):.1f}%</b> &nbsp;| '
        f'Reach 50%: <b>{r.get(50, 0):.1f}%</b> &nbsp;| '
        f'Reach 75%: <b>{r.get(75, 0):.1f}%</b> &nbsp;| '
        f'Reach 100%: <b>{r.get(100, 0):.1f}%</b><br>'
        f'Avg MFE: <b>{pop.get("mfe_mean_pct", 0):.1f}%</b> of candle &nbsp;| '
        f'Avg heat: <b>{pop.get("mae_mean_pct", 0):.1f}%</b> &nbsp;| '
        f'MFE/MAE: <b>{pop.get("mfe_mae_ratio") or "—"}</b> &nbsp;| '
        f'Runner score: <b>{pop.get("runner_score", 0):.3f}</b>'
        f'</span></div>'
    )
    return html


def _render_setup_rankings(rankings: List[Dict], targets: List[int]) -> str:
    if not rankings:
        return info_box("No setup families met minimum-N threshold for large-move analysis.")

    html = section_title("Top Setups by Large-Reversal Move Probability")
    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:8px">'
        'Ranked by probability of reaching 50% of signal candle size in the reversal direction. '
        'Reversal MFE = adv_ticks (the move against the signal candle direction). '
        'Heat = fav_ticks (continuation move = adverse for a fade trade). '
        'Focus on setups with Reach-50% &ge; 25% and Runner score &ge; 0.45.'
        '</p>'
    )

    # Target columns for header
    t_cols = [f"R{t}%" for t in targets]
    headers = ["Setup Family", "N"] + t_cols + ["P50|25", "P75|50", "Runner", "Avg MFE", "MFE/MAE", "Top Session", "Tiers", ""]
    hdr_row = "".join(hdr(h) for h in headers)

    rows = []
    for s in rankings:
        r = s.get("reach_pct", {})
        row_bg = "#e8f5e9" if s.get("is_large_move_candidate") else ""
        row_style = f' style="background:{row_bg}"' if row_bg else ""
        runner_sc = s.get("runner_score", 0)

        t_cells = "".join(
            cell(_badge(r.get(t, 0), 20, 35)) for t in targets
        )
        rows.append(
            f"<tr{row_style}>"
            + cell(f'<b>{s.get("family_label", "—")}</b>')
            + cell(str(s.get("n", 0)))
            + t_cells
            + cell(f'{s.get("p50_given_25", 0):.1f}%')
            + cell(f'{s.get("p75_given_50", 0):.1f}%')
            + cell(_runner_badge(runner_sc))
            + cell(f'{s.get("mfe_mean_pct", 0):.1f}%')
            + cell(fmt(s.get("mfe_mae_ratio"), 2, "x") if s.get("mfe_mae_ratio") is not None else "—")
            + cell(str(s.get("dominant_session") or "—"))
            + cell(_tier_bar(s.get("tier_distribution") or {}))
            + cell(_lmc_badge() if s.get("is_large_move_candidate") else "")
            + "</tr>"
        )

    html += (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f"<thead><tr>{hdr_row}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    html += (
        '<p style="font-size:10px;color:#888;margin-top:4px">'
        "R10%/25%/50%/75%/100% = % of events reaching that % of signal candle size in reversal direction. "
        "P50|25 = conditional: P(reach 50% | reached 25%). "
        "Tiers: green=extended, light-green=large, yellow=standard, orange=micro, red=failed. "
        "★ LM Candidate = meets min_n + min_p50% + min_runner_score thresholds."
        "</p>"
    )
    return html


def _render_context_rankings(context_rankings: List[Dict], targets: List[int]) -> str:
    if not context_rankings:
        return ""

    html = section_title("Context-Conditioned Large-Move Rankings")
    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:8px">'
        'Which signal-candle contexts produce the biggest reversal moves? '
        'Lift = percentage-point difference vs population 50% reach rate. '
        'Focus on rows with Lift &ge; +5 pp and Reach-50% &ge; 25%.'
        '</p>'
    )

    t_hdrs = [f"R{t}%" for t in targets if t in (25, 50, 75)]
    headers = ["Context Feature", "Bucket", "N", "Reach 50%", "Lift vs Pop", "Runner", "Avg MFE", "P50|25", "P75|50"]
    hdr_row = "".join(hdr(h) for h in headers)

    rows = []
    for c in context_rankings:
        lift = c.get("lift_50_pp", 0) or 0
        if lift >= 5:
            lift_color = "#1b5e20"
        elif lift >= 3:
            lift_color = "#2e7d32"
        elif lift <= -5:
            lift_color = "#b71c1c"
        elif lift <= -3:
            lift_color = "#c62828"
        else:
            lift_color = "#555"
        sign = "+" if lift >= 0 else ""
        lift_span = f'<span style="color:{lift_color};font-weight:bold">{sign}{lift:.1f} pp</span>'

        r = c.get("reach_pct", {})
        row_bg = "#f1f8e9" if lift >= 5 else ("#fce4ec" if lift <= -5 else "")
        row_style = f' style="background:{row_bg}"' if row_bg else ""

        rows.append(
            f"<tr{row_style}>"
            + cell(c.get("context_feature", "—"))
            + cell(f'<code>{c.get("context_bucket", "—")}</code>', bold=True)
            + cell(str(c.get("n", 0)))
            + cell(_badge(r.get(50, 0), 20, 35))
            + cell(lift_span)
            + cell(_runner_badge(c.get("runner_score", 0)))
            + cell(f'{c.get("mfe_mean_pct", 0):.1f}%')
            + cell(f'{c.get("p50_given_25", 0):.1f}%')
            + cell(f'{c.get("p75_given_50", 0):.1f}%')
            + "</tr>"
        )

    html += (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f"<thead><tr>{hdr_row}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    return html


def _build_card_narrative(s: Dict, pop_reach_50: float) -> str:
    """Generate a human-readable WHY narrative for a large-move strategy card."""
    fam   = s.get("family_label", "")
    r50   = s.get("reach_pct", {}).get(50, 0)
    r75   = s.get("reach_pct", {}).get(75, 0)
    p5025 = s.get("p50_given_25", 0)
    p7550 = s.get("p75_given_50", 0)
    rscore= s.get("runner_score", 0)
    sess  = s.get("dominant_session") or "various sessions"
    exh   = s.get("dominant_exhaustion")
    mfe   = s.get("mfe_mean_pct", 0)
    mae   = s.get("mae_mean_pct", 0)
    ratio = s.get("mfe_mae_ratio")
    ttm   = s.get("ttm_p50_min")

    parts = []

    # Opening: core finding
    if r50 >= 30:
        parts.append(
            f"This reversal setup produces significant follow-through: "
            f"{r50:.1f}% of events reach the 50%-of-candle target."
        )
    elif r50 >= 20:
        parts.append(
            f"This reversal setup produces meaningful moves: "
            f"{r50:.1f}% of events reach the 50%-of-candle target."
        )
    else:
        parts.append(
            f"This setup shows reversal potential ({r50:.1f}% reach 50% of candle)."
        )

    # Runner quality
    if p7550 >= 60:
        parts.append(
            f"Once 50% is reached, {p7550:.1f}% continue to 75%+ — this is a genuine runner setup."
        )
    elif p5025 >= 60:
        parts.append(
            f"High initial momentum: {p5025:.1f}% that reach 25% continue to 50%."
        )

    # Exhaustion context
    if exh and exh not in ("none", "unknown"):
        exh_desc = {
            "streak_up_exhaustion":     "occurs after a bullish streak (bulls may be exhausted)",
            "streak_down_exhaustion":   "occurs after a bearish streak (bears may be exhausted)",
            "extended_above_vwap":      "signals above extended VWAP levels (stretched longs)",
            "extended_below_vwap":      "signals below extended VWAP levels (stretched shorts)",
            "extension_into_resistance":"signals pushing into known resistance",
            "extension_into_support":   "signals pushing into known support",
        }.get(exh, exh)
        parts.append(f"Most commonly the signal {exh_desc} — a classic exhaustion setup.")

    # Session context
    if sess != "various sessions":
        sess_desc = {
            "ny_open":           "NY open (high-velocity moves are common)",
            "power_hour":        "power hour (late session reversions are frequent)",
            "ny_pre":            "NY pre-market (thin liquidity amplifies reversals)",
            "london_ny_overlap": "London/NY overlap (peak institutional activity)",
            "mid_ny":            "mid-NY session (slower, more grinding reversals)",
        }.get(sess, sess)
        parts.append(f"Edge concentrates in {sess_desc}.")

    # Quality (MFE/MAE)
    if ratio and ratio >= 2.0:
        parts.append(
            f"Clean move quality: average reversal MFE is {mfe:.1f}% vs "
            f"heat of {mae:.1f}% (MFE/MAE = {ratio:.2f}x)."
        )
    elif ratio:
        parts.append(f"Average MFE: {mfe:.1f}% of candle. Average heat: {mae:.1f}%.")

    # Time to move
    if ttm is not None:
        if ttm <= 5:
            parts.append(f"Move tends to be immediate (median {ttm:.0f} min to max reversal).")
        elif ttm <= 15:
            parts.append(f"Move develops moderately quickly (median {ttm:.0f} min to peak).")
        else:
            parts.append(f"Move can take time to develop (median {ttm:.0f} min to peak).")

    return " ".join(parts)


def _render_large_move_cards(rankings: List[Dict], pop_reach_50: float, targets: List[int]) -> str:
    """Render large-move strategy cards for top setups."""
    lmc_setups = [s for s in rankings if s.get("is_large_move_candidate")]
    if not lmc_setups:
        lmc_setups = rankings[:3]  # fall back to top 3

    if not lmc_setups:
        return ""

    html = section_title(f"Large-Move Strategy Cards ({len(lmc_setups)} setups)")

    for s in lmc_setups:
        r = s.get("reach_pct", {})
        tier_dist = s.get("tier_distribution") or {}

        # Card frame
        html += (
            '<div style="border:1px solid #90caf9;border-radius:6px;padding:12px 16px;'
            'margin-bottom:16px;background:#fafafa">'
        )

        # Header row
        html += (
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
            f'<span style="font-size:14px;font-weight:bold;color:#1565c0">'
            f'{s.get("family_label", "Unknown")}</span>'
            f'{_lmc_badge() if s.get("is_large_move_candidate") else ""}'
            f'{_runner_badge(s.get("runner_score", 0))}'
            f'<span style="font-size:11px;color:#555">N={s.get("n", 0):,}</span>'
            f'</div>'
        )

        # Reach rate bar
        html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px">'
        for t in targets:
            val = r.get(t, 0)
            color = "#1b5e20" if val >= 35 else ("#e65100" if val >= 20 else "#888")
            html += (
                f'<div style="text-align:center;min-width:50px">'
                f'<div style="font-size:18px;font-weight:bold;color:{color}">{val:.1f}%</div>'
                f'<div style="font-size:9px;color:#888">Reach {t}%</div>'
                f'</div>'
            )
        html += '</div>'

        # Metrics row
        ratio = s.get("mfe_mae_ratio")
        ttm   = s.get("ttm_p50_min")
        html += (
            f'<div style="font-size:11px;color:#444;margin-bottom:8px;display:flex;gap:16px;flex-wrap:wrap">'
            f'<span>P50|25: <b>{s.get("p50_given_25", 0):.1f}%</b></span>'
            f'<span>P75|50: <b>{s.get("p75_given_50", 0):.1f}%</b></span>'
            f'<span>Runner: <b>{s.get("runner_score", 0):.3f}</b></span>'
            f'<span>Avg MFE: <b>{s.get("mfe_mean_pct", 0):.1f}%</b></span>'
            f'<span>Avg heat: <b>{s.get("mae_mean_pct", 0):.1f}%</b></span>'
            f'<span>MFE/MAE: <b>{ratio:.2f}x</b></span>' if ratio else ""
            f'<span>Time to peak: <b>{ttm:.0f} min</b></span>' if ttm else ""
            f'<span>Session: <b>{s.get("dominant_session") or "—"}</b></span>'
            f'</div>'
        )

        # Tier bar
        html += f'<div style="margin-bottom:8px">{_tier_bar(tier_dist)} <span style="font-size:9px;color:#888">move-size tiers (green=extended, red=failed)</span></div>'

        # Narrative
        narrative = _build_card_narrative(s, pop_reach_50)
        html += (
            f'<div style="font-size:12px;color:#333;line-height:1.7;'
            f'background:#e8f5e9;border-radius:3px;padding:8px 10px">'
            f'{narrative}'
            f'</div>'
        )

        html += '</div>'  # end card

    return html


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_reversal_leaders(ctx: dict) -> str:
    rsa = _get_reversal_data(ctx)

    if not rsa:
        return info_box(
            "Reversal size analysis not available. "
            "Enable <code>reversal_size_analysis.enabled: true</code> in the YAML "
            "under <code>large_candle_excursion_findings:</code>."
        )

    if not rsa.get("enabled"):
        return info_box(
            "Reversal size analysis is disabled. "
            "Set <code>reversal_size_analysis.enabled: true</code> in the findings YAML block."
        )

    msg = rsa.get("message")
    if msg or not rsa.get("population"):
        return info_box(f"Reversal size analysis: {msg or 'no data available.'}")

    pop        = rsa.get("population") or {}
    setup_rank = rsa.get("setup_rankings") or []
    ctx_rank   = rsa.get("context_rankings") or []
    win_min    = rsa.get("forward_window_min", 30)
    targets    = rsa.get("targets_pct") or [10, 25, 50, 75, 100, 150]
    n_valid    = rsa.get("n_valid", 0)
    pop_reach_50 = pop.get("reach_pct", {}).get(50, 0)

    html = '<div style="font-family:Arial,sans-serif">'

    html += section_title(
        f"Large Candle Excursion — Large Reversal Move Leaders "
        f'<span style="font-size:12px;color:#888;font-weight:normal">'
        f"({n_valid:,} events, {win_min}-min window)</span>"
    )

    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:10px">'
        "Answers: <b>which reverse setups produce the biggest moves?</b> "
        "Reversal MFE = maximum move in the <em>opposite</em> direction to the signal candle. "
        "All percentages are normalized by signal candle size. "
        "A setup with strong runner score keeps producing move after the initial target — "
        "these are the highest-profit setups when timed correctly."
        "</p>"
    )

    html += _render_population_summary(pop, win_min)
    html += _render_setup_rankings(setup_rank, targets)
    html += _render_context_rankings(ctx_rank, targets)
    html += _render_large_move_cards(setup_rank, pop_reach_50, targets)

    html += (
        '<p style="font-size:10px;color:#888;margin-top:10px">'
        "Reversal MFE = adv_ticks (maximum reversal excursion in forward window). "
        "Heat = fav_ticks (continuation excursion = adverse for a fade trade). "
        "Runner score = weighted conditional probability: "
        "0.35×P(50%|25%) + 0.40×P(75%|50%) + 0.25×P(100%|50%). "
        "Min N per setup: 30 events."
        "</p>"
    )

    html += "</div>"
    return html
