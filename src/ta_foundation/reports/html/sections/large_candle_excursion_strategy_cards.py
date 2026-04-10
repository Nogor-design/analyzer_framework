from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)

_BEHAVIOR_BADGE = {
    "scalp":               ('<span style="background:#28a745;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">SCALP</span>',   "#d4edda"),
    "runner":              ('<span style="background:#007bff;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">RUNNER</span>',  "#cce5ff"),
    "mixed":               ('<span style="background:#ffc107;color:#333;padding:2px 6px;border-radius:3px;font-size:11px">MIXED</span>',   "#fff3cd"),
    "mixed (limited data)":(
        '<span style="background:#adb5bd;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">MIXED*</span>', "#f8f9fa"),
    "unknown":             ('<span style="background:#6c757d;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px">?</span>',       "#f8f9fa"),
}

_PROMOTION_BADGE = {
    "strategy-test-ready": '<span style="background:#155724;color:#fff;padding:2px 6px;border-radius:3px;font-size:10px">STRATEGY-TEST-READY</span>',
    "candidate":           '<span style="background:#004085;color:#fff;padding:2px 6px;border-radius:3px;font-size:10px">CANDIDATE</span>',
    "observation":         '<span style="background:#6c757d;color:#fff;padding:2px 6px;border-radius:3px;font-size:10px">OBSERVATION</span>',
}

_CURVE_STABILITY_BADGE = {
    "stable":               '<span style="background:#28a745;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px">STABLE CURVE</span>',
    "moderate":             '<span style="background:#ffc107;color:#333;padding:2px 5px;border-radius:3px;font-size:10px">MODERATE CURVE</span>',
    "fragile":              '<span style="background:#dc3545;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px">FRAGILE CURVE</span>',
    "fragile (no plateau)": '<span style="background:#dc3545;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px">NO PLATEAU</span>',
}

_FRAGILITY_BADGE = {
    "low_sample":          ('<span style="background:#dc3545;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px;margin-right:3px">LOW N</span>'),
    "suspicious_strength": ('<span style="background:#fd7e14;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px;margin-right:3px">SUSPECT WR</span>'),
    "neighbor_instability":('<span style="background:#6f42c1;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px;margin-right:3px">UNSTABLE</span>'),
    "rapid_edge_decay":    ('<span style="background:#e83e8c;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px;margin-right:3px">EDGE DECAY</span>'),
    "no_plateau":          ('<span style="background:#dc3545;color:#fff;padding:2px 5px;border-radius:3px;font-size:10px;margin-right:3px">NO PLATEAU</span>'),
}

_KV_STYLE    = "font-size:12px;margin:3px 0;color:#333"
_LABEL_STYLE = "font-weight:bold;color:#555;min-width:130px;display:inline-block"


def _kv(label: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f'<p style="{_KV_STYLE}"><span style="{_LABEL_STYLE}">{label}:</span> {value}</p>'


def _robustness_bar(value: float, label: str) -> str:
    """Render a small horizontal bar showing a 0-1 metric."""
    pct = max(0, min(100, int(value * 100)))
    if pct >= 65:
        color = "#28a745"
    elif pct >= 45:
        color = "#ffc107"
    else:
        color = "#dc3545"
    return (
        f'<div style="margin:3px 0">'
        f'<span style="font-size:11px;color:#555;display:inline-block;min-width:100px">{label}</span>'
        f'<div style="display:inline-block;vertical-align:middle;width:80px;background:#e9ecef;border-radius:3px;height:8px">'
        f'<div style="width:{pct}%;background:{color};border-radius:3px;height:8px"></div>'
        f'</div>'
        f'<span style="font-size:11px;color:#555;margin-left:5px">{pct}%</span>'
        f'</div>'
    )


def _render_session_table(session_table: List[Dict[str, Any]]) -> str:
    if not session_table:
        return ""
    th = 'style="background:#495057;color:#fff;font-size:10px;padding:3px 6px;text-align:left"'
    td = 'style="font-size:11px;padding:3px 6px;border-bottom:1px solid #dee2e6"'
    td_r = 'style="font-size:11px;padding:3px 6px;border-bottom:1px solid #dee2e6;text-align:right"'
    rows_html = "".join(
        f'<tr><td {td}>{r["session"]}</td>'
        f'<td {td_r}>{r["n"]}</td>'
        f'<td {td_r}>{r["win_rate"]:.1f}%</td>'
        f'<td {td_r}>{r["expectancy_ticks"]:.1f}</td>'
        f'<td {td_r}>{r["pct_of_total"]:.0f}%</td></tr>'
        for r in session_table
    )
    return (
        f'<table style="width:100%;border-collapse:collapse;margin-top:6px">'
        f'<thead><tr>'
        f'<th {th}>Session</th><th {th} style="text-align:right">N</th>'
        f'<th {th} style="text-align:right">WR</th>'
        f'<th {th} style="text-align:right">Exp(t)</th>'
        f'<th {th} style="text-align:right">% Total</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table>'
    )


def _render_card(card: Dict[str, Any]) -> str:
    bt = card.get("metrics", {}).get("behavior_type", "mixed (limited data)")
    badge, card_bg = _BEHAVIOR_BADGE.get(bt, _BEHAVIOR_BADGE.get("mixed (limited data)", _BEHAVIOR_BADGE["unknown"]))

    cond     = card.get("conditions", {})
    metrics  = card.get("metrics", {})
    targets  = card.get("targets", {})
    sess     = card.get("session_context", {})
    flags    = card.get("fragility_flags") or []
    why      = card.get("why_interesting", "")
    promo    = card.get("promotion") or {}
    tradab   = card.get("tradability") or {}

    wr       = metrics.get("win_rate")
    wr_str   = f"{float(wr):.1f}%" if wr is not None else "?"
    n        = metrics.get("n_events", "?")
    score    = metrics.get("composite_score")
    score_str = f"{float(score):.4f}" if score is not None else "?"
    plateau_w = int(metrics.get("plateau_width") or 0)
    curve_st  = metrics.get("curve_stability", "")

    promo_level = promo.get("level", "observation")
    promo_badge = _PROMOTION_BADGE.get(promo_level, _PROMOTION_BADGE["observation"])
    curve_badge = _CURVE_STABILITY_BADGE.get(curve_st, "")

    # Header border: red if fragile, else green if strategy-test-ready, else default
    if flags:
        border_color = "#dc3545"
    elif promo_level == "strategy-test-ready":
        border_color = "#28a745"
    else:
        border_color = "#ccc"

    html = (
        f'<div style="background:{card_bg};border:1px solid {border_color};border-radius:6px;'
        f'padding:14px 18px;margin-bottom:16px;font-family:Arial,sans-serif">'
    )

    # Header row
    html += (
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
        f'<span style="font-size:14px;font-weight:bold;color:#2c3e50">#{card["rank"]} — {card.get("setup_definition","")}</span>'
        f'<span>{badge}</span>'
        f'</div>'
    )

    # Promotion + curve stability row
    html += f'<div style="margin-bottom:6px">{promo_badge}'
    if curve_badge:
        html += f'&nbsp;{curve_badge}'
    if promo.get("reason"):
        html += f'&nbsp;<span style="font-size:10px;color:#777">{promo["reason"]}</span>'
    html += '</div>'

    # Fragility badges
    if flags:
        html += '<div style="margin-bottom:8px">'
        for f in flags:
            html += _FRAGILITY_BADGE.get(f, "")
        html += '</div>'

    # Why interesting narrative
    if why:
        html += (
            f'<div style="background:rgba(0,0,0,0.04);border-left:3px solid #3498db;'
            f'padding:8px 12px;margin-bottom:10px;border-radius:0 4px 4px 0">'
            f'<p style="font-size:12px;color:#2c3e50;margin:0;line-height:1.6">{why}</p>'
            f'</div>'
        )

    # Metrics row
    html += (
        f'<div style="display:flex;gap:24px;margin-bottom:10px;flex-wrap:wrap">'
        f'<span style="font-size:13px"><b>WR:</b> {wr_str}</span>'
        f'<span style="font-size:13px"><b>N:</b> {n}</span>'
        f'<span style="font-size:13px"><b>Score:</b> {score_str}</span>'
        f'<span style="font-size:13px"><b>Plateau:</b> {plateau_w} steps</span>'
    )
    epd = tradab.get("events_per_day")
    if epd is not None:
        html += f'<span style="font-size:13px"><b>Events/day:</b> {epd:.1f}</span>'
    p90_adv = tradab.get("p90_adverse_excursion_ticks")
    if p90_adv is not None:
        html += f'<span style="font-size:13px"><b>P90 Adv:</b> {p90_adv:.1f}t</span>'
    html += '</div>'

    # Three-column layout: conditions | targets & session | robustness
    html += '<div style="display:flex;gap:20px;flex-wrap:wrap">'

    # Column 1: entry conditions
    html += '<div style="flex:1;min-width:180px">'
    html += '<p style="font-size:11px;font-weight:bold;color:#555;margin:0 0 4px">ENTRY CONDITIONS</p>'
    html += _kv("Trade Mode",    cond.get("trade_mode"))
    html += _kv("Direction",     cond.get("direction"))
    html += _kv("Timeframe",     f"{cond.get('timeframe_min')}m" if cond.get("timeframe_min") else None)
    html += _kv("Fwd Window",    f"{cond.get('window_minutes')}m" if cond.get("window_minutes") else None)
    html += _kv("Candle Bucket", cond.get("candle_bucket"))
    html += _kv("Lookback",      cond.get("lookback"))
    html += _kv("Basis",         cond.get("basis"))
    html += _kv("Threshold",     cond.get("threshold"))
    html += _kv("Target",        f"{cond.get('target_percent')}%" if cond.get("target_percent") is not None else None)
    html += '</div>'

    # Column 2: targets & session
    html += '<div style="flex:1;min-width:180px">'
    html += '<p style="font-size:11px;font-weight:bold;color:#555;margin:0 0 4px">TARGET &amp; SESSION</p>'
    html += _kv("Stable Target Range", targets.get("stable_range"))
    html += _kv("Peak Target %",       f"{targets.get('peak_target_pct'):.0f}%" if targets.get("peak_target_pct") is not None else None)
    html += _kv("Expectancy (ticks)",  metrics.get("expectancy_ticks"))
    html += _kv("Edge Decay Penalty",  f"{float(metrics.get('edge_decay_penalty', 0)):.2f}" if metrics.get("edge_decay_penalty") is not None else None)

    best_sess = sess.get("best_sessions") or []
    if best_sess:
        html += _kv("Best Sessions", ", ".join(best_sess))

    session_table = sess.get("session_table") or []
    if session_table:
        html += '<p style="font-size:10px;font-weight:bold;color:#555;margin:6px 0 0">BY SESSION</p>'
        html += _render_session_table(session_table)
    html += '</div>'

    # Column 3: robustness indicators
    stab  = metrics.get("stability_score")
    samp  = metrics.get("sample_score")
    html += '<div style="flex:1;min-width:180px">'
    html += '<p style="font-size:11px;font-weight:bold;color:#555;margin:0 0 4px">ROBUSTNESS</p>'
    if stab is not None:
        html += _robustness_bar(float(stab), "Neighbor Stab.")
    if samp is not None:
        html += _robustness_bar(float(samp), "Sample Score")
    if plateau_w > 0:
        plateau_proxy = min(1.0, plateau_w / 5.0)
        html += _robustness_bar(plateau_proxy, "Plateau Width")
    edp = float(metrics.get("edge_decay_penalty") or 0.0)
    html += _robustness_bar(1.0 - edp, "Edge Stability")
    html += '</div>'

    html += '</div></div>'
    return html


def render_large_candle_excursion_strategy_cards(ctx: dict) -> str:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Strategy cards unavailable: findings data missing.")
    if not data.get("has_source"):
        return info_box(f"Strategy cards unavailable: {data.get('message', 'source analytics missing')}.")

    options = ctx.get("options") or {}
    top_n = int(options.get("top_n", 10))

    cards: List[Dict[str, Any]] = data.get("strategy_cards") or []
    if not cards:
        return info_box("No strategy cards generated.", color="#f8f9fa", border="#dee2e6")

    cards = cards[:top_n]

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Strategy Cards — Top Setups")
    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:12px">'
        "Each card summarises a tradeable setup with entry conditions, target zone, "
        "behavior classification, session context, and robustness indicators. "
        "Fragility badges (red/orange) indicate setups that need additional validation. "
        "The narrative block explains <em>why</em> each setup is worth investigating.</p>"
    )
    for card in cards:
        html += _render_card(card)

    html += "</div>"
    return html
