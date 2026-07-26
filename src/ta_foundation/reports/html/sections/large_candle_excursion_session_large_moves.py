from __future__ import annotations

"""
Large Candle Excursion — Large Move Probability by Session
===========================================================
For each trading session bucket, shows reverse-move reach rates and runner metrics.

Answers: which sessions produce the biggest reversal moves?
"""

from typing import Any, Dict, List, Optional

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell, fmt, hdr, info_box, section_title,
)

# Sessions in preferred display order
_SESSION_ORDER = [
    "ny_open",
    "power_hour",
    "london_ny_overlap",
    "ny_pre",
    "mid_ny",
    "london",
    "asia",
    "overnight",
    "unknown",
]

_SESSION_LABELS = {
    "ny_open":           "NY Open",
    "power_hour":        "Power Hour",
    "london_ny_overlap": "London/NY Overlap",
    "ny_pre":            "NY Pre-Market",
    "mid_ny":            "Mid-NY",
    "london":            "London",
    "asia":              "Asia",
    "overnight":         "Overnight",
    "unknown":           "Unknown",
}


def _get_reversal_data(ctx: dict) -> dict:
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion_findings" in d:
            return d["large_candle_excursion_findings"].get("reversal_size_analysis") or {}
    return ctx.get("large_candle_excursion_findings", {}).get("reversal_size_analysis") or {}


def _color_reach(pct: float) -> str:
    """Background color for a reach-rate cell based on quality."""
    if pct >= 35:
        return "#c8e6c9"
    if pct >= 25:
        return "#e8f5e9"
    if pct >= 15:
        return "#fff9c4"
    return ""


def _runner_label(score: float) -> str:
    if score >= 0.55:
        return '<span style="color:#1b5e20;font-weight:bold">RUNNER</span>'
    if score >= 0.45:
        return '<span style="color:#2e7d32;font-weight:bold">Strong</span>'
    if score >= 0.35:
        return '<span style="color:#e65100">Moderate</span>'
    return '<span style="color:#888">Scalp</span>'


def _render_session_table(session_analysis: Dict[str, Dict], targets: List[int]) -> str:
    if not session_analysis:
        return info_box("No sessions had sufficient events (min 20) for analysis.")

    # Sort sessions by display order, then fall back alphabetical
    ordered = []
    for sess in _SESSION_ORDER:
        if sess in session_analysis:
            ordered.append((sess, session_analysis[sess]))
    for sess, data in session_analysis.items():
        if sess not in _SESSION_ORDER:
            ordered.append((sess, data))

    t_hdrs = [f"Reach {t}%" for t in targets]
    headers = ["Session", "N", "Avg MFE", "Avg Heat", "MFE/MAE"] + t_hdrs + ["P50|25", "P75|50", "Runner", "★"]
    hdr_row = "".join(hdr(h) for h in headers)

    rows = []
    for sess, data in ordered:
        r  = data.get("reach_pct", {})
        rs = data.get("runner_score", 0)
        ratio = data.get("mfe_mae_ratio")
        label = _SESSION_LABELS.get(sess, sess)

        t_cells = ""
        for t in targets:
            val = r.get(t, 0)
            bg  = _color_reach(val)
            bg_style = f'background:{bg};' if bg else ""
            t_cells += f'<td style="padding:4px 8px;border:1px solid #e0e0e0;{bg_style}text-align:right">{val:.1f}%</td>'

        # Crown for best session per metric
        star = ""

        rows.append(
            f"<tr>"
            + cell(f"<b>{label}</b>")
            + cell(str(data.get("n", 0)))
            + cell(f'{data.get("mfe_mean_pct", 0):.1f}%')
            + cell(f'{data.get("mae_mean_pct", 0):.1f}%')
            + cell(f'{ratio:.2f}x' if ratio is not None else "—")
            + t_cells
            + cell(f'{data.get("p50_given_25", 0):.1f}%')
            + cell(f'{data.get("p75_given_50", 0):.1f}%')
            + cell(_runner_label(rs))
            + cell(star)
            + "</tr>"
        )

    html = (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f"<thead><tr>{hdr_row}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    return html


def _render_session_narrative(sessions_ranked: List[Dict]) -> str:
    """Key findings narrative from session rankings."""
    if not sessions_ranked:
        return ""

    best3 = sessions_ranked[:3]
    bottom2 = sessions_ranked[-2:]

    html = section_title("Session Intelligence — Key Findings")
    html += '<ul style="font-size:12px;color:#333;line-height:1.8">'

    for s in best3:
        sess  = _SESSION_LABELS.get(s.get("session", ""), s.get("session", ""))
        r50   = s.get("reach_pct", {}).get(50, 0)
        rscore= s.get("runner_score", 0)
        mfe   = s.get("mfe_mean_pct", 0)
        runner_txt = "strong runner potential" if rscore >= 0.50 else "moderate follow-through"
        html += (
            f'<li><b>{sess}</b> produces the largest reversal moves: '
            f'{r50:.1f}% reach 50%+ of candle, avg reversal MFE {mfe:.1f}% — {runner_txt}.</li>'
        )

    for s in bottom2:
        sess  = _SESSION_LABELS.get(s.get("session", ""), s.get("session", ""))
        r50   = s.get("reach_pct", {}).get(50, 0)
        if r50 < 15:
            html += (
                f'<li><b>{sess}</b> tends to produce only scalp-sized reversals '
                f'({r50:.1f}% reach 50%); large-move setups here are rare.</li>'
            )

    html += "</ul>"
    return html


def _render_best_sessions_bar(sessions_ranked: List[Dict], targets: List[int]) -> str:
    """Simple ranked-bar summary of sessions by 50% reach rate."""
    if not sessions_ranked:
        return ""

    html = '<div style="margin-bottom:14px">'
    html += '<div style="font-size:12px;font-weight:bold;color:#333;margin-bottom:6px">Sessions ranked by 50%-reach rate</div>'

    max_r50 = max((s.get("reach_pct", {}).get(50, 0) for s in sessions_ranked), default=1)
    bar_scale = 180 / max(max_r50, 1)

    for s in sessions_ranked:
        sess   = _SESSION_LABELS.get(s.get("session", ""), s.get("session", ""))
        r50    = s.get("reach_pct", {}).get(50, 0)
        r75    = s.get("reach_pct", {}).get(75, 0)
        rscore = s.get("runner_score", 0)
        bar_w  = int(r50 * bar_scale)
        color  = "#1b5e20" if r50 >= 30 else ("#e65100" if r50 >= 20 else "#bbb")

        html += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
            f'<div style="width:110px;font-size:11px;color:#333;text-align:right">{sess}</div>'
            f'<div style="background:{color};height:14px;width:{bar_w}px;border-radius:2px"></div>'
            f'<div style="font-size:11px;color:#444">'
            f'<b>{r50:.1f}%</b> reach-50 &nbsp; '
            f'{r75:.1f}% reach-75 &nbsp; '
            f'Runner: {rscore:.3f}'
            f'</div>'
            f'</div>'
        )

    html += '</div>'
    return html


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_session_large_moves(ctx: dict) -> str:
    rsa = _get_reversal_data(ctx)

    if not rsa:
        return info_box(
            "Reversal size analysis not available. "
            "Enable <code>reversal_size_analysis.enabled: true</code> in the YAML."
        )

    if not rsa.get("enabled"):
        return info_box("Reversal size analysis disabled. Set <code>reversal_size_analysis.enabled: true</code>.")

    session_analysis = rsa.get("session_analysis") or {}
    sessions_ranked  = rsa.get("sessions_ranked") or []
    win_min          = rsa.get("forward_window_min", 30)
    targets          = rsa.get("targets_pct") or [10, 25, 50, 75, 100, 150]
    n_valid          = rsa.get("n_valid", 0)

    if not session_analysis:
        return info_box(
            "No sessions had sufficient events for large-move analysis. "
            "Check that session tagging is enabled and there are enough events per session."
        )

    html = '<div style="font-family:Arial,sans-serif">'

    html += section_title(
        f"Large Candle Excursion — Large Move Probability by Session "
        f'<span style="font-size:12px;color:#888;font-weight:normal">'
        f"({n_valid:,} events, {win_min}-min window)</span>"
    )

    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:10px">'
        "Answers: <b>which sessions produce the biggest reversal moves?</b> "
        "For each session, shows how far reverse-entry trades can go after a large-candle signal. "
        "Reach N% = probability that the reversal exceeded N% of the signal candle size. "
        "Use this to time large-reversal strategies to the highest-probability windows."
        "</p>"
    )

    html += _render_best_sessions_bar(sessions_ranked, targets)
    html += _render_session_table(session_analysis, targets)
    html += _render_session_narrative(sessions_ranked)

    html += (
        '<p style="font-size:10px;color:#888;margin-top:8px">'
        "Reversal MFE = adv_ticks (max move against signal direction in forward window). "
        "Heat = fav_ticks (continuation = adverse for fade trade). "
        "Minimum 20 events per session. "
        f"Forward window: {win_min} minutes."
        "</p>"
    )

    html += "</div>"
    return html
