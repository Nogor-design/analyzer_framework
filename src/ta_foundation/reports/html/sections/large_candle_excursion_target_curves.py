from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    fmt,
    get_derived_payload,
    hdr,
    info_box,
    section_title,
)

_BEHAVIOR_COLOR = {
    "scalp":  "#d4edda",
    "runner": "#cce5ff",
    "mixed":  "#fff3cd",
}


def _sparkline(points: List[Dict[str, Any]]) -> str:
    """Render a tiny inline text sparkline from win_rate curve points."""
    if not points:
        return "—"
    blocks = " ▁▂▃▄▅▆▇█"
    wrs = [float(p.get("win_rate", 0)) for p in points]
    if not wrs:
        return "—"
    mn, mx = min(wrs), max(wrs)
    span = mx - mn if mx > mn else 1.0
    chars = []
    for w in wrs:
        idx = min(int((w - mn) / span * 7) + 1, 8)
        chars.append(blocks[idx])
    return "".join(chars)


def render_large_candle_excursion_target_curves(ctx: dict) -> str:
    options = ctx.get("options") or {}
    top_n = int(options.get("top_n", 20))
    min_n = int(options.get("min_n", 30))

    lce = None
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            lce = d["large_candle_excursion"]
            break

    if not lce:
        return info_box("Target curves unavailable: large_candle_excursion analytics not found.")

    tc = lce.get("target_curves") or {}
    if not tc.get("enabled"):
        return info_box(
            "Target curves not computed. Enable <code>trade_analysis.enabled: true</code> "
            "in large_candle_excursion config."
        )

    curves_dict = tc.get("curves") or {}
    if not curves_dict:
        return info_box("No target curves generated.", color="#f8f9fa", border="#dee2e6")

    # Filter and sort by peak_win_rate
    curves = list(curves_dict.values())
    curves = [c for c in curves if any(p.get("n_events", 0) >= min_n for p in (c.get("points") or []))]
    curves.sort(key=lambda c: -float(c.get("peak_win_rate", 0)))
    curves = curves[:top_n]

    if not curves:
        return info_box(f"No curves with ≥{min_n} events found.", color="#f8f9fa", border="#dee2e6")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Target Curve Analysis")

    html += (
        '<p style="font-size:12px;color:#555;margin-bottom:12px">'
        "Win rate across target percentages for each setup combination. "
        "<b>Behavior:</b> scalp = edge decays early; runner = stable across wide range; mixed = neither. "
        "Plateau = # consecutive targets within 3 pp of peak.</p>"
    )

    html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += "<thead><tr>"
    for h in ["Setup", "Behavior", "Peak WR", "Peak Target %", "Stable Range", "Plateau W", "Edge Decay", "Sparkline"]:
        html += hdr(h)
    html += "</tr></thead><tbody>"

    for c in curves:
        bt = c.get("behavior_type", "unknown")
        bg = _BEHAVIOR_COLOR.get(bt, "#fff")
        peak_wr = float(c.get("peak_win_rate", 0)) * 100.0
        peak_t = c.get("peak_target_pct")
        stable = c.get("stable_target_range") or [None, None]
        stable_str = (
            f"{stable[0]:.0f}%–{stable[1]:.0f}%"
            if stable[0] is not None and stable[1] is not None
            else "—"
        )
        edge_decay = float(c.get("edge_decay", 0)) * 100.0
        pw = c.get("plateau_width", 0)
        spark = _sparkline(c.get("points") or [])

        setup_key = c.get("setup_key", "")
        # Make setup key more readable: replace | with thin spaces
        label = setup_key.replace("|", " · ")

        html += f'<tr style="background:{bg}">'
        html += cell(f'<span title="{setup_key}" style="font-size:11px">{label}</span>')
        html += cell(f"<b>{bt}</b>", bg=bg)
        html += cell(f"{peak_wr:.1f}%", bg=bg, bold=peak_wr >= 55)
        html += cell(fmt(peak_t, digits=0, suffix="%"), bg=bg)
        html += cell(stable_str, bg=bg)
        html += cell(str(pw), bg=bg)
        html += cell(f"{edge_decay:.1f} pp", bg=bg)
        html += cell(f'<span style="letter-spacing:2px;font-size:14px">{spark}</span>', bg=bg)
        html += "</tr>"

    html += "</tbody></table></div>"
    return html
