from __future__ import annotations

"""
Large Candle Excursion — Volume Context Section
================================================
Shows continuation vs reverse win rates and excursion stats grouped by
relative-volume bucket.

Answers: "Does high-RVOL favour continuation? Does low-RVOL favour reversals?"
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
    if v is None:
        return "—"
    try:
        return f"{float(v):.{d}f}{sfx}"
    except Exception:
        return str(v)


def _wr_color(wr: Optional[float]) -> str:
    if wr is None:
        return "#f5f5f5"
    if wr >= 70:  return "#c6efce"
    if wr >= 55:  return "#e2efda"
    if wr >= 45:  return "#ffeb9c"
    return "#ffc7ce"


def _better_color(better: Optional[str]) -> str:
    if better == "continuation": return "#dff0d8"
    if better == "reverse":      return "#fcf8e3"
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


def _render_vol_table(rows: List[Dict], show_trade: bool) -> str:
    if not rows:
        return '<p style="color:#888">No volume context data.</p>'

    headers = ["RVOL Bucket", "Observations", "Mean Fav t", "Mean Adv t",
               "Med Fav t", "Med Adv t"]
    if show_trade:
        headers += ["Cont Win%", "Rev Win%", "Better", "Target%"]

    hdr_row = "".join(_hdr(h) for h in headers)
    rows_html: List[str] = []

    for r in rows:
        better = r.get("better_mode")
        cont_wr = r.get("cont_win_rate")
        rev_wr  = r.get("rev_win_rate")
        cols = [
            _cell(str(r.get("vol_bucket", "?")), bold=True),
            _cell(str(r.get("n_observations", 0))),
            _cell(_fmt(r.get("mean_fav_ticks"), 1, "t")),
            _cell(_fmt(r.get("mean_adv_ticks"), 1, "t")),
            _cell(_fmt(r.get("median_fav_ticks"), 1, "t")),
            _cell(_fmt(r.get("median_adv_ticks"), 1, "t")),
        ]
        if show_trade:
            cols += [
                _cell(_fmt(cont_wr, 1, "%"), _wr_color(cont_wr)),
                _cell(_fmt(rev_wr,  1, "%"), _wr_color(rev_wr)),
                _cell(str(better or "—").replace("_", " ").title(),
                      _better_color(better), bold=True),
                _cell(_fmt(r.get("target_pct"), 0, "%")),
            ]
        rows_html.append(f'<tr>{"".join(cols)}</tr>')

    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div>'
    )


def render_large_candle_excursion_volume_context(ctx: dict) -> str:
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
            '<strong>Volume Context not enabled.</strong> '
            'Set <code>volume_context.enabled: true</code> in your YAML.'
            '</div>'
        )

    vc = ca.get("volume_context", {})
    if not vc.get("enabled"):
        return (
            '<div style="padding:16px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px">'
            '<strong>Volume Context not enabled.</strong> '
            'Set <code>volume_context.enabled: true</code> in your YAML.'
            '</div>'
        )

    rows      = vc.get("by_vol_bucket", [])
    has_trade = any("cont_win_rate" in r for r in rows)

    html = '<div style="font-family:Arial,sans-serif">'
    html += (
        '<p style="color:#555;font-size:13px">'
        'Relative volume (RVOL) = signal candle volume / rolling average volume. '
        'Each bucket shows excursion stats and — when trade analysis is enabled — '
        'continuation vs reverse win rates at the primary target percent.'
        '</p>'
    )
    html += _section_title("Excursion & Trade Stats by Relative-Volume Bucket")
    html += _render_vol_table(rows, has_trade)

    # Interaction: vol × candle size
    ix = ca.get("interactions", {})
    vol_x_size = ix.get("vol_x_size", [])
    if vol_x_size:
        html += _section_title("Candle-Size Bucket × Relative-Volume Interaction")
        headers = ["Candle Bucket", "RVOL Bucket", "Observations",
                   "Mean Fav t", "Mean Adv t"]
        if has_trade:
            headers += ["Cont Win%", "Rev Win%", "Better"]
        hdr_row = "".join(_hdr(h) for h in headers)
        rows_html = []
        for r in vol_x_size:
            better   = r.get("better_mode")
            cont_wr  = r.get("cont_win_rate")
            rev_wr   = r.get("rev_win_rate")
            cols = [
                _cell(str(r.get("candle_bucket", "?"))),
                _cell(str(r.get("vol_bucket",    "?")), bold=True),
                _cell(str(r.get("n_observations", 0))),
                _cell(_fmt(r.get("mean_fav_ticks"), 1, "t")),
                _cell(_fmt(r.get("mean_adv_ticks"), 1, "t")),
            ]
            if has_trade:
                cols += [
                    _cell(_fmt(cont_wr, 1, "%"), _wr_color(cont_wr)),
                    _cell(_fmt(rev_wr,  1, "%"), _wr_color(rev_wr)),
                    _cell(str(better or "—").replace("_", " ").title(),
                          _better_color(better), bold=True),
                ]
            rows_html.append(f'<tr>{"".join(cols)}</tr>')
        html += (
            '<div style="overflow-x:auto;margin-bottom:24px">'
            '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f'<thead><tr>{hdr_row}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody>'
            '</table></div>'
        )

    html += '</div>'
    return html
