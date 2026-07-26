from __future__ import annotations

"""
Large Candle Excursion — Threshold Hit Rates Section
======================================================
Shows the percentage of events where favorable (or adverse) excursion
reached each of several tick thresholds.

Columns: 3t, 5t, 8t, 10t, 15t, 20t, 30t (configurable via HIT_THRESHOLDS).
Rows: one per (tf × lookback × basis × threshold × window) combo.

Also renders a compact heatmap-style table grouped by forward window duration.
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HIT_THRESHOLDS = [3, 5, 8, 10, 15, 20, 30]


def _get_data(ctx: dict) -> Optional[Dict]:
    if ctx.get("large_candle_excursion"):
        return ctx["large_candle_excursion"]
    for pkg in (ctx.get("packages") or {}).values():
        derived = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in derived:
            return derived["large_candle_excursion"]
    return None


def _fmt(v: Any, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}{suffix}"
    except Exception:
        return "—"


def _pct_color(pct: Optional[float]) -> str:
    if pct is None:
        return "#f5f5f5"
    if pct >= 70:
        return "#c6efce"
    if pct >= 50:
        return "#e2efda"
    if pct >= 30:
        return "#ffeb9c"
    return "#ffc7ce"


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


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:28px;margin-bottom:12px">{text}</h3>'
    )


def _table_wrap(hdr_row: str, body_rows: List[str]) -> str:
    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


# ---------------------------------------------------------------------------
# Hit-rate table (full detail)
# ---------------------------------------------------------------------------

def _render_hit_rate_table(results: List[Dict], direction: str = "fav") -> str:
    """
    Render hit-rate table for favorable or adverse excursion.
    direction: "fav" | "adv"
    """
    label     = "Favorable" if direction == "fav" else "Adverse"
    hr_key    = f"hit_rates_{direction}"

    headers = [
        "TF", "Lookback", "Basis", "Threshold", "Window", "N",
    ] + [f"≥{t}t" for t in HIT_THRESHOLDS]
    hdr_row = "".join(_hdr(h) for h in headers)

    rows: List[str] = []
    for r in sorted(results, key=lambda x: (
        -(x.get(hr_key, {}).get("pct_ge_10t", 0) or 0),
        x.get("threshold_value", 0),
    )):
        hr = r.get(hr_key, {})
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
            _cell(str(r.get("n_events", 0))),
        ]
        for t in HIT_THRESHOLDS:
            pct = hr.get(f"pct_ge_{t}t")
            cols.append(_cell(_fmt(pct, 1, "%"), _pct_color(pct)))
        rows.append(f'<tr>{"".join(cols)}</tr>')

    title = f"Hit Rates — {label} Excursion (% events reaching each tick threshold)"
    return f"<h4 style='color:#555;margin:16px 0 8px'>{title}</h4>" + _table_wrap(hdr_row, rows)


# ---------------------------------------------------------------------------
# Heatmap by window — averaged across combos
# ---------------------------------------------------------------------------

def _render_window_heatmap(results: List[Dict]) -> str:
    """
    Compact table: rows = threshold values, columns = window durations.
    Cell = avg pct_ge_10t (favorable) across matching combos.
    """
    # Collect unique thresholds and windows
    thresholds = sorted({r.get("threshold_value") for r in results if r.get("threshold_value") is not None})
    windows    = sorted({r.get("window_minutes") for r in results if r.get("window_minutes") is not None})

    if not thresholds or not windows:
        return ""

    hdr_row = _hdr("Threshold") + "".join(_hdr(f"{w}m") for w in windows)
    rows: List[str] = []

    for thr in thresholds:
        thr_mode = next((r.get("threshold_mode", "multiplier") for r in results if r.get("threshold_value") == thr), "multiplier")
        thr_label = f"{thr}×" if thr_mode == "multiplier" else f"{round(thr * 100)}%"
        row_cols = [_cell(thr_label, bold=True)]

        for w in windows:
            matching = [
                r for r in results
                if r.get("threshold_value") == thr and r.get("window_minutes") == w
            ]
            vals = [
                r.get("hit_rates_fav", {}).get("pct_ge_10t")
                for r in matching
                if r.get("hit_rates_fav", {}).get("pct_ge_10t") is not None
            ]
            avg_val = round(sum(vals) / len(vals), 1) if vals else None
            row_cols.append(_cell(_fmt(avg_val, 1, "%"), _pct_color(avg_val)))

        rows.append(f'<tr>{"".join(row_cols)}</tr>')

    return (
        '<h4 style="color:#555;margin:16px 0 8px">'
        'Heatmap — % Events with Fav Excursion ≥ 10 ticks (Threshold × Window)</h4>'
        + _table_wrap(hdr_row, rows)
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_threshold_hits(ctx: dict) -> str:
    data = _get_data(ctx)
    if not data:
        return '<p style="color:#888">No large_candle_excursion data available.</p>'

    results = data.get("combo_results", [])
    if not results:
        return '<p style="color:#888">No results to display.</p>'

    html = '<div style="font-family:Arial,sans-serif">'

    html += _section_title("Threshold Hit Rates — Excursion Probability Analysis")
    html += (
        '<p style="color:#666;font-size:13px;margin-bottom:16px">'
        'Each cell shows the percentage of qualifying candle events where price '
        'reached that tick level in the favorable direction within the forward window. '
        'Higher percentages indicate more reliable continuation after large candles.'
        '</p>'
    )

    html += _render_hit_rate_table(results, direction="fav")
    html += _render_hit_rate_table(results, direction="adv")
    html += _render_window_heatmap(results)

    html += '</div>'
    return html
