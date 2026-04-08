from __future__ import annotations

"""
Large Candle Excursion — Multi-Context Interaction Tables
==========================================================
Cross-tabulation tables combining two context dimensions.

Available tables:
  vol_x_close_pos  — RVOL bucket × close-position bucket
  vol_x_wick       — RVOL bucket × wick-structure bucket
  vol_x_size       — RVOL bucket × candle-size bucket  (also in volume section)
  size_x_atr       — candle-size bucket × ATR bucket   (also in volatility section)

Answers: "Does high RVOL + strong close predict continuation?
           Does low RVOL + rejection wick predict reversal?"
"""

from typing import Any, Dict, List, Optional


def _get_data(ctx: dict) -> Optional[Dict]:
    if ctx.get("large_candle_excursion"):
        return ctx["large_candle_excursion"]
    for pkg in (ctx.get("packages") or {}).values():
        derived = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in derived:
            return derived["large_candle_excursion"]
    return None


def _fmt(v: Any, d: int = 1, sfx: str = "") -> str:
    if v is None: return "—"
    try:    return f"{float(v):.{d}f}{sfx}"
    except: return str(v)


def _wr_color(wr: Optional[float]) -> str:
    if wr is None: return "#f5f5f5"
    if wr >= 70:   return "#c6efce"
    if wr >= 55:   return "#e2efda"
    if wr >= 45:   return "#ffeb9c"
    return "#ffc7ce"


def _better_color(b: Optional[str]) -> str:
    if b == "continuation": return "#dff0d8"
    if b == "reverse":      return "#fcf8e3"
    return "#f5f5f5"


def _hdr(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
        f'text-align:center;border:1px solid #ddd;font-size:12px;white-space:nowrap">'
        f'{text}</th>'
    )


def _cell(v: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:5px 8px;'
        f'border:1px solid #ddd;font-size:12px;{fw}">{v}</td>'
    )


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:24px;margin-bottom:12px">{text}</h3>'
    )


def _render_interaction_table(
    rows: List[Dict],
    col_a: str,
    col_b: str,
    show_trade: bool,
) -> str:
    if not rows:
        return '<p style="color:#888">No data for this interaction (need ≥5 events per cell).</p>'

    a_label = col_a.replace("_bucket", "").replace("_", " ").title()
    b_label = col_b.replace("_bucket", "").replace("_", " ").title()
    headers = [a_label, b_label, "Obs", "Mean Fav t", "Mean Adv t"]
    if show_trade:
        headers += ["Cont Win%", "Rev Win%", "Better"]

    hdr_row   = "".join(_hdr(h) for h in headers)
    rows_html = []

    for r in rows:
        better  = r.get("better_mode")
        cont_wr = r.get("cont_win_rate")
        rev_wr  = r.get("rev_win_rate")
        cols = [
            _cell(str(r.get(col_a, "?")), bold=True),
            _cell(str(r.get(col_b, "?"))),
            _cell(str(r.get("n_observations", 0))),
            _cell(_fmt(r.get("mean_fav_ticks"), 1, "t")),
            _cell(_fmt(r.get("mean_adv_ticks"), 1, "t")),
        ]
        if show_trade:
            cols += [
                _cell(_fmt(cont_wr, 1, "%"), _wr_color(cont_wr)),
                _cell(_fmt(rev_wr,  1, "%"), _wr_color(rev_wr)),
                _cell(str(better or "—").replace("_", " ").title(),
                      _better_color(better), bold=True),
            ]
        rows_html.append(f'<tr>{"".join(cols)}</tr>')

    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div>'
    )


def render_large_candle_excursion_interactions(ctx: dict) -> str:
    data = _get_data(ctx)
    if not data:
        return (
            '<div style="padding:16px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px">'
            'Large candle excursion data not found.'
            '</div>'
        )

    ca = data.get("context_analysis")
    if not ca or not ca.get("enabled"):
        return (
            '<div style="padding:16px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px">'
            '<strong>Context Analysis not enabled.</strong> '
            'Enable at least two context dimensions in your YAML to see interaction tables.'
            '</div>'
        )

    ix = ca.get("interactions", {})
    if not ix:
        return (
            '<div style="padding:16px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px">'
            'No interaction tables available. Enable multiple context dimensions '
            '(<code>volume_context</code>, <code>candle_structure_context</code>, '
            '<code>volatility_context</code>) in your YAML.'
            '</div>'
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += (
        '<p style="color:#555;font-size:13px">'
        'Cross-context interaction tables. Cells with fewer than 5 observations are omitted. '
        'Trade win rates computed at the primary target percent.'
        '</p>'
    )

    vol_x_close = ix.get("vol_x_close_pos", [])
    if vol_x_close:
        has_t = any("cont_win_rate" in r for r in vol_x_close)
        html += _section_title("RVOL Bucket × Close-Position Bucket")
        html += _render_interaction_table(vol_x_close, "vol_bucket", "close_pos_bucket", has_t)

    vol_x_wick = ix.get("vol_x_wick", [])
    if vol_x_wick:
        has_t = any("cont_win_rate" in r for r in vol_x_wick)
        html += _section_title("RVOL Bucket × Wick-Structure Bucket")
        html += _render_interaction_table(vol_x_wick, "vol_bucket", "wick_bucket", has_t)

    vol_x_size = ix.get("vol_x_size", [])
    if vol_x_size:
        has_t = any("cont_win_rate" in r for r in vol_x_size)
        html += _section_title("RVOL Bucket × Candle-Size Bucket")
        html += _render_interaction_table(vol_x_size, "vol_bucket", "candle_bucket", has_t)

    size_x_atr = ix.get("size_x_atr", [])
    if size_x_atr:
        has_t = any("cont_win_rate" in r for r in size_x_atr)
        html += _section_title("Candle-Size Bucket × ATR Bucket")
        html += _render_interaction_table(size_x_atr, "candle_bucket", "atr_bucket", has_t)

    if not any([vol_x_close, vol_x_wick, vol_x_size, size_x_atr]):
        html += '<p style="color:#888">No interaction data available with current configuration.</p>'

    html += '</div>'
    return html
