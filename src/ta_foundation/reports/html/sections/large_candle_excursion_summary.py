from __future__ import annotations

"""
Large Candle Excursion — Summary Section
==========================================
Renders a top-level summary banner + per-combination breakdown table.

Shows for each (tf × lookback × basis × threshold × window) combo:
  n_events, n_bull/bear, mean/median fav/adv ticks, pct_fav_dominant.

Consumes ctx["all_options"]["large_candle_excursion"] data stored under
pkg.metadata["derived"]["large_candle_excursion"].
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_data(ctx: dict) -> Optional[Dict]:
    """Extract large_candle_excursion result dict from ctx."""
    if ctx.get("large_candle_excursion"):
        return ctx["large_candle_excursion"]
    for pkg in (ctx.get("packages") or {}).values():
        derived = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in derived:
            return derived["large_candle_excursion"]
    return None


def _v(d: Dict, *keys, default=None):
    """Safe nested dict access."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


def _fmt(v: Any, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except Exception:
        return str(v)


def _stat_box(label: str, value: str, accent: str = "#3498db") -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {accent};'
        f'padding:10px 16px;border-radius:4px;min-width:110px">'
        f'<div style="font-size:11px;color:#888;margin-bottom:2px">{label}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{accent}">{value}</div>'
        f'</div>'
    )


def _hdr(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
        f'text-align:center;border:1px solid #ddd;font-size:12px;white-space:nowrap">'
        f'{text}</th>'
    )


def _cell(value: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:5px 8px;'
        f'border:1px solid #ddd;font-size:12px;{fw}">{value}</td>'
    )


def _fav_color(ticks: Optional[float]) -> str:
    if ticks is None:
        return "#f5f5f5"
    if ticks >= 20:
        return "#c6efce"
    if ticks >= 10:
        return "#e2efda"
    if ticks >= 5:
        return "#ffeb9c"
    return "#fff"


def _adv_color(ticks: Optional[float]) -> str:
    if ticks is None:
        return "#f5f5f5"
    if ticks >= 20:
        return "#ffc7ce"
    if ticks >= 10:
        return "#ffe0e6"
    return "#fff"


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:24px;margin-bottom:12px">{text}</h3>'
    )


# ---------------------------------------------------------------------------
# Sub-renders
# ---------------------------------------------------------------------------

def _render_banner(results: List[Dict], n_combos: int, n_results: int) -> str:
    if not results:
        return '<p style="color:#888">No results available.</p>'

    all_fav = [_v(r, "fav_ticks", "mean") for r in results if _v(r, "fav_ticks", "mean") is not None]
    all_adv = [_v(r, "adv_ticks", "mean") for r in results if _v(r, "adv_ticks", "mean") is not None]
    all_n   = [r.get("n_events", 0) for r in results]
    total_events = sum(all_n)
    best_fav = max(all_fav, default=0.0)
    avg_fav  = round(sum(all_fav) / len(all_fav), 1) if all_fav else 0.0
    avg_adv  = round(sum(all_adv) / len(all_adv), 1) if all_adv else 0.0
    fav_dom  = [r.get("pct_fav_dominant", 0) for r in results]
    avg_dom  = round(sum(fav_dom) / len(fav_dom), 1) if fav_dom else 0.0

    html = '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px">'
    html += _stat_box("Combos Run",    str(n_combos))
    html += _stat_box("With Results",  str(n_results))
    html += _stat_box("Total Events",  f"{total_events:,}")
    html += _stat_box("Best Fav Avg",  f"{best_fav:.1f}t", "#27ae60")
    html += _stat_box("Avg Fav",       f"{avg_fav:.1f}t",  "#27ae60")
    html += _stat_box("Avg Adv",       f"{avg_adv:.1f}t",  "#e74c3c")
    html += _stat_box("Fav Dominant%", f"{avg_dom:.1f}%",  "#8e44ad")
    html += '</div>'
    return html


def _render_combo_table(results: List[Dict]) -> str:
    if not results:
        return ""

    headers = [
        "TF", "Lookback", "Basis", "Threshold", "Window",
        "Events", "Bull", "Bear",
        "Mean Fav", "Mean Adv",
        "Med Fav", "Med Adv",
        "Max Fav", "Fav Dom%",
        "Fav First%",
    ]
    hdr_row = "".join(_hdr(h) for h in headers)

    rows_html: List[str] = []
    for r in sorted(results, key=lambda x: (-x.get("fav_ticks", {}).get("mean", 0) or 0,
                                             x.get("threshold_value", 0))):
        mean_fav = _v(r, "fav_ticks", "mean")
        mean_adv = _v(r, "adv_ticks", "mean")
        med_fav  = _v(r, "fav_ticks", "median")
        med_adv  = _v(r, "adv_ticks", "median")
        max_fav  = _v(r, "fav_ticks", "max")
        dom_pct  = r.get("pct_fav_dominant")
        fst_pct  = r.get("pct_fav_first")

        threshold_display = (
            f"{r.get('threshold_value', '')}×"
            if r.get("threshold_mode") == "multiplier"
            else f"{round(r.get('threshold_value', 0) * 100)}%"
        )

        cols = [
            _cell(f"{r.get('tf', '?')}m"),
            _cell(str(r.get("lookback", "?"))),
            _cell(str(r.get("basis", "?")).capitalize()),
            _cell(threshold_display),
            _cell(f"{r.get('window_minutes', '?')}m"),
            _cell(str(r.get("n_events", 0)), bold=True),
            _cell(str(r.get("n_bullish", 0))),
            _cell(str(r.get("n_bearish", 0))),
            _cell(_fmt(mean_fav, 1, "t"), _fav_color(mean_fav)),
            _cell(_fmt(mean_adv, 1, "t"), _adv_color(mean_adv)),
            _cell(_fmt(med_fav, 1, "t")),
            _cell(_fmt(med_adv, 1, "t")),
            _cell(_fmt(max_fav, 1, "t"), "#e8f5e9" if max_fav and max_fav >= 20 else "#fff"),
            _cell(_fmt(dom_pct, 1, "%"), "#e8f5e9" if dom_pct and dom_pct >= 55 else "#fff"),
            _cell(_fmt(fst_pct, 1, "%")),
        ]
        rows_html.append(f'<tr>{"".join(cols)}</tr>')

    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div>'
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_summary(ctx: dict) -> str:
    data = _get_data(ctx)

    if not data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px">'
            '<strong>Large Candle Excursion:</strong> No results found. '
            'Enable <code>large_candle_excursion.enabled: true</code> in YAML and provide minute bar data.'
            '</div>'
        )

    results      = data.get("combo_results", [])
    n_combos     = data.get("n_combinations_run", 0)
    n_results    = data.get("n_results", len(results))
    tick_used    = data.get("tick_data_used", False)

    html = '<div style="font-family:Arial,sans-serif">'

    tick_badge = (
        '<span style="background:#27ae60;color:#fff;padding:2px 8px;border-radius:10px;'
        'font-size:11px;margin-left:8px">Tick Mode ON</span>'
        if tick_used else ""
    )
    html += (
        f'<p style="color:#555;font-size:13px">'
        f'Large candle forward excursion analysis — '
        f'{n_combos:,} parameter combinations across timeframes, thresholds, and forward windows.{tick_badge}'
        f'</p>'
    )

    html += _render_banner(results, n_combos, n_results)

    if results:
        html += _section_title("Combination Breakdown — Sorted by Mean Favorable Excursion")
        html += _render_combo_table(results)
    else:
        html += '<p style="color:#888">No combinations met the minimum event threshold.</p>'

    html += '</div>'
    return html
