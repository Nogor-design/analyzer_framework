from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)

_BEHAVIOR_BADGE = {
    "scalp":               '<span style="background:#28a745;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">SCALP</span>',
    "runner":              '<span style="background:#007bff;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">RUNNER</span>',
    "mixed":               '<span style="background:#ffc107;color:#333;padding:2px 6px;border-radius:3px;font-size:11px">MIXED</span>',
    "mixed (limited data)":'<span style="background:#adb5bd;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">MIXED*</span>',
}

_STABILITY_BADGE = {
    "stable":              '<span style="background:#28a745;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">STABLE</span>',
    "moderate":            '<span style="background:#ffc107;color:#333;padding:2px 6px;border-radius:3px;font-size:11px">MODERATE</span>',
    "fragile":             '<span style="background:#dc3545;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">FRAGILE</span>',
    "fragile (no plateau)":'<span style="background:#dc3545;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">FRAGILE ⚠</span>',
}

_PROMO_BADGE = {
    "strategy-test-ready": '<span style="background:#155724;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">STRATEGY-TEST-READY</span>',
    "candidate":           '<span style="background:#004085;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">CANDIDATE</span>',
    "observation":         '<span style="background:#6c757d;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">OBSERVATION</span>',
}

_FRAGILITY_BADGE = {
    "low_sample":           '<span style="background:#dc3545;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">LOW N</span>',
    "suspicious_strength":  '<span style="background:#fd7e14;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">SUSPECT WR</span>',
    "no_plateau":           '<span style="background:#dc3545;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">NO PLATEAU</span>',
    "rapid_edge_decay":     '<span style="background:#e83e8c;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">EDGE DECAY</span>',
    "micro_scalp_artifact": '<span style="background:#111;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">MICRO SCALP</span>',
    "time_instability":     '<span style="background:#795548;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">TIME SPLIT</span>',
    "many_variants":        '<span style="background:#6f42c1;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">MANY VARIANTS</span>',
    "friction-risky":       '<span style="background:#fd7e14;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">FRICTION RISKY</span>',
    "friction-invalid":     '<span style="background:#111;color:#fff;padding:2px 4px;border-radius:3px;font-size:10px;margin-right:3px">FRICTION INVALID</span>',
}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        import math
        f = float(v)
        return default if math.isnan(f) else f
    except Exception:
        return default


def _render_family_row(rank: int, fam: Dict[str, Any]) -> str:
    fk      = fam.get("family_key") or ("?", "?", "?", "?")
    mode    = fam.get("trade_mode", str(fk[0]))
    direc   = fam.get("direction", str(fk[1]))
    tf      = fam.get("tf_minutes", fk[2])
    bucket  = fam.get("candle_bucket", str(fk[3]))
    best    = fam.get("best_variant") or {}
    promo   = best.get("_promotion_level", "observation")

    bt      = fam.get("behavior_type", "mixed")
    cs      = fam.get("curve_stability", "fragile")
    flags   = fam.get("fragility_flags") or []

    best_score  = _safe_float(fam.get("best_score"))
    avg_score   = _safe_float(fam.get("avg_score"))
    variants    = int(fam.get("variant_count") or 0)
    plateau     = int(fam.get("plateau_width") or 0)
    edp         = _safe_float(fam.get("edge_decay_penalty"), 0.0)
    nts         = _safe_float(fam.get("neighbor_target_stability"), 0.0)
    tts         = _safe_float(fam.get("target_time_stability"), 0.0)
    target_lbl  = fam.get("target_stability_label") or ""

    best_wr     = _safe_float(best.get("win_rate"), 0.0)
    best_n      = int(best.get("n_events") or 0)
    best_tgt    = int(_safe_float(best.get("target_percent"), 0))
    best_window = best.get("window_minutes", "?")
    best_def    = best.get("setup_definition", "")
    gross_tgt   = _safe_float(fam.get("gross_target_ticks"), _safe_float(best.get("gross_target_ticks"), 0.0))
    cost_ticks  = _safe_float(fam.get("estimated_round_trip_cost_ticks"), _safe_float(best.get("estimated_round_trip_cost_ticks"), 0.0))
    net_tgt     = _safe_float(fam.get("net_target_after_friction_ticks"), _safe_float(best.get("net_target_after_friction_ticks"), 0.0))
    net_exp     = _safe_float(fam.get("net_expectancy_after_friction_ticks"), _safe_float(best.get("net_expectancy_after_friction_ticks"), 0.0))
    friction    = fam.get("friction_viability") or best.get("friction_viability") or ""

    behavior_badge  = _BEHAVIOR_BADGE.get(bt, _BEHAVIOR_BADGE.get("mixed", ""))
    stability_badge = _STABILITY_BADGE.get(cs, "")
    promo_badge     = _PROMO_BADGE.get(promo, _PROMO_BADGE["observation"])

    flag_html = "".join(_FRAGILITY_BADGE.get(f, "") for f in flags)

    row_bg = "#f8f9fa" if rank % 2 == 0 else "#ffffff"

    return (
        f'<tr style="background:{row_bg};vertical-align:top">'
        f'<td style="padding:8px 6px;font-size:12px;font-weight:bold;color:#2c3e50">{rank}</td>'
        f'<td style="padding:8px 6px;font-size:12px">'
        f'  <b>{mode}</b> / {direc} / {tf}m / {bucket}'
        f'  <div style="font-size:10px;color:#777;margin-top:2px">{best_def}</div>'
        f'</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:center">{promo_badge}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:center">{variants}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{best_score:.4f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{avg_score:.4f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{best_wr:.1f}%</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{best_n}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{gross_tgt:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{cost_ticks:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{net_tgt:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{net_exp:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:11px;text-align:center">{friction}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:center">{behavior_badge}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:center">{stability_badge}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{plateau}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{nts:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{tts:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:12px;text-align:right">{edp:.2f}</td>'
        f'<td style="padding:8px 6px;font-size:11px">{target_lbl}<div>{flag_html}</div></td>'
        f'</tr>'
    )


def render_large_candle_excursion_findings_families(ctx: dict) -> str:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Setup families unavailable: findings data missing.")
    if not data.get("has_source"):
        return info_box(f"Setup families unavailable: {data.get('message', 'source analytics missing')}.")

    families: List[Dict[str, Any]] = data.get("setup_families") or []
    if not families:
        return info_box("No setup families generated.", color="#f8f9fa", border="#dee2e6")

    options = ctx.get("options") or {}
    top_n = int(options.get("top_n", 20))
    families = families[:top_n]

    # Promotion summary counts
    n_str  = sum(1 for f in families if (f.get("best_variant") or {}).get("_promotion_level") == "strategy-test-ready")
    n_cand = sum(1 for f in families if (f.get("best_variant") or {}).get("_promotion_level") == "candidate")
    n_obs  = len(families) - n_str - n_cand
    n_fv   = sum(1 for f in families if f.get("friction_viability") == "friction-viable")
    n_fr   = sum(1 for f in families if f.get("friction_viability") == "friction-risky")
    n_fi   = sum(1 for f in families if f.get("friction_viability") == "friction-invalid")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Top Setup Families")

    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:8px">'
        "Setups are grouped into <b>families</b> by (trade mode, direction, timeframe, candle bucket). "
        "Minor parameter variations (lookback, threshold, basis, target) within a family are collapsed — "
        "only the best variant is shown. This eliminates duplication and surfaces the clearest picture of "
        "which market conditions produce durable edge.</p>"
    )

    # Promotion ladder summary
    html += (
        '<div style="display:flex;gap:16px;margin-bottom:14px;flex-wrap:wrap">'
        f'<div style="background:#d4edda;border:1px solid #c3e6cb;border-radius:5px;padding:8px 14px">'
        f'  <div style="font-size:11px;color:#155724;font-weight:bold">STRATEGY-TEST-READY</div>'
        f'  <div style="font-size:22px;font-weight:bold;color:#155724">{n_str}</div>'
        f'</div>'
        f'<div style="background:#cce5ff;border:1px solid #b8daff;border-radius:5px;padding:8px 14px">'
        f'  <div style="font-size:11px;color:#004085;font-weight:bold">CANDIDATE</div>'
        f'  <div style="font-size:22px;font-weight:bold;color:#004085">{n_cand}</div>'
        f'</div>'
        f'<div style="background:#e2e3e5;border:1px solid #d6d8db;border-radius:5px;padding:8px 14px">'
        f'  <div style="font-size:11px;color:#383d41;font-weight:bold">OBSERVATION</div>'
        f'  <div style="font-size:22px;font-weight:bold;color:#383d41">{n_obs}</div>'
        f'</div>'
        '</div>'
    )

    html += (
        '<p style="font-size:12px;color:#444;margin:0 0 10px">'
        f"Friction viability among shown families: {n_fv} viable, {n_fr} risky, {n_fi} invalid.</p>"
    )

    # Table
    th_style = "padding:6px 6px;background:#343a40;color:#fff;font-size:11px;text-align:left;white-space:nowrap"
    html += (
        '<div style="overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif">'
        '<thead><tr>'
        f'<th style="{th_style}">#</th>'
        f'<th style="{th_style}">Family</th>'
        f'<th style="{th_style};text-align:center">Status</th>'
        f'<th style="{th_style};text-align:center">Variants</th>'
        f'<th style="{th_style};text-align:right">Best Score</th>'
        f'<th style="{th_style};text-align:right">Avg Score</th>'
        f'<th style="{th_style};text-align:right">Best WR</th>'
        f'<th style="{th_style};text-align:right">Best N</th>'
        f'<th style="{th_style};text-align:right">Target(t)</th>'
        f'<th style="{th_style};text-align:right">Cost(t)</th>'
        f'<th style="{th_style};text-align:right">NetTarget</th>'
        f'<th style="{th_style};text-align:right">NetExp</th>'
        f'<th style="{th_style};text-align:center">Friction</th>'
        f'<th style="{th_style};text-align:center">Behavior</th>'
        f'<th style="{th_style};text-align:center">Curve</th>'
        f'<th style="{th_style};text-align:right">Plateau</th>'
        f'<th style="{th_style};text-align:right">Nbr Stable</th>'
        f'<th style="{th_style};text-align:right">Time Stable</th>'
        f'<th style="{th_style};text-align:right">Edge Decay</th>'
        f'<th style="{th_style}">Flags</th>'
        '</tr></thead>'
        '<tbody>'
    )

    for rank, fam in enumerate(families, 1):
        html += _render_family_row(rank, fam)

    html += '</tbody></table></div>'

    # Legend
    html += (
        '<div style="margin-top:10px;font-size:10px;color:#777">'
        '<b>Promotion:</b> Strategy-test-ready = meets all quality thresholds; '
        'Candidate = strong but needs more data or lower edge decay; '
        'Observation = interesting but not yet actionable. '
        '<b>Curve:</b> Stable = plateau ≥3 steps &amp; low edge decay; '
        'Moderate = plateau ≥2 or edge decay ≤0.60; '
        'Fragile = no clear plateau or high edge decay. '
        '<b>Variants</b> = number of (lookback/threshold/basis/target) combos within the family.'
        '</div>'
    )

    html += '</div>'
    return html
