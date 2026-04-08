from __future__ import annotations

"""
Large Candle Excursion — Event Table Section
=============================================
Renders an event-level detail table from the events_sample stored in the
analysis results dict.

Columns displayed (in order):
  Timestamp, TF, Window, Dir, Open, High, Low, Close,
  Body Ticks, Range Ticks, Basis, Lookback, Threshold, Ratio,
  Fav Ticks, Adv Ticks, TTF (min), TTA (min),
  [tick columns if tick_data_used]

Table is paginated / capped at the top N rows (configurable via options.top_n).
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        return str(v)


def _fmt_dt(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v)
    # Show date + time without microseconds
    if "T" in s:
        parts = s.split("T")
        t_part = parts[1][:8] if len(parts) > 1 else ""
        return f"{parts[0]} {t_part}"
    return s[:16]


def _hdr(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 8px;'
        f'text-align:center;border:1px solid #ddd;font-size:11px;white-space:nowrap">'
        f'{text}</th>'
    )


def _cell(value: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:4px 7px;'
        f'border:1px solid #ddd;font-size:11px;{fw}">{value}</td>'
    )


def _fav_color(ticks: Any) -> str:
    try:
        t = float(ticks)
        if t >= 20:
            return "#c6efce"
        if t >= 10:
            return "#e2efda"
        if t >= 5:
            return "#ffeb9c"
        return "#fff"
    except Exception:
        return "#f5f5f5"


def _adv_color(ticks: Any) -> str:
    try:
        t = float(ticks)
        if t >= 20:
            return "#ffc7ce"
        if t >= 10:
            return "#ffe0e6"
        return "#fff"
    except Exception:
        return "#f5f5f5"


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:28px;margin-bottom:12px">{text}</h3>'
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_event_table(ctx: dict) -> str:
    data = _get_data(ctx)
    if not data:
        return '<p style="color:#888">No large_candle_excursion data available.</p>'

    events      = data.get("events_sample", [])
    tick_used   = data.get("tick_data_used", False)
    options     = ctx.get("options") or {}
    top_n       = int(options.get("top_n", 500))

    if not events:
        return (
            '<p style="color:#888">No event records in sample. '
            'Set <code>output.include_event_table: true</code> in YAML.</p>'
        )

    # Sort events by fav_ticks descending, then by dt
    def _sort_key(e):
        fav = e.get("fav_ticks")
        try:
            return -(float(fav) if fav is not None else 0.0)
        except Exception:
            return 0.0

    events_sorted = sorted(events, key=_sort_key)[:top_n]
    total = len(data.get("events_sample", []))

    html = '<div style="font-family:Arial,sans-serif">'
    html += _section_title(f"Event Detail Table (Top {len(events_sorted)} of {total:,} sampled events)")
    html += (
        '<p style="color:#666;font-size:12px;margin-bottom:12px">'
        f'Sorted by favorable excursion (largest first). Showing top {top_n} events. '
        'Each row is one qualifying large candle event.'
        '</p>'
    )

    # Build headers
    headers = [
        "Timestamp", "TF", "Win", "Dir",
        "Open", "High", "Low", "Close",
        "Body T", "Rng T",
        "Basis", "LB", "Thr", "Ratio",
        "Fav T", "Adv T",
        "TTF", "TTA",
    ]
    if tick_used:
        headers += ["Tick Fav T", "Tick Adv T", "Rev?"]

    hdr_row = "".join(_hdr(h) for h in headers)
    rows: List[str] = []

    for ev in events_sorted:
        direction_label = ev.get("direction_label", "?")
        dir_bg = "#e8f5e9" if direction_label == "Bull" else "#fce4ec"
        threshold_display = (
            f"{ev.get('threshold_value', '')}×"
            if ev.get("threshold_mode") == "multiplier"
            else f"{round(float(ev.get('threshold_value', 0)) * 100)}%"
        )

        fav_t = ev.get("fav_ticks")
        adv_t = ev.get("adv_ticks")

        cols = [
            _cell(_fmt_dt(ev.get("dt"))),
            _cell(f"{ev.get('tf_minutes', '?')}m"),
            _cell(f"{ev.get('window_minutes', '?')}m"),
            _cell(direction_label, dir_bg, bold=True),
            _cell(_fmt(ev.get("open"), 2)),
            _cell(_fmt(ev.get("high"), 2)),
            _cell(_fmt(ev.get("low"), 2)),
            _cell(_fmt(ev.get("close"), 2)),
            _cell(_fmt(ev.get("body_ticks"), 1)),
            _cell(_fmt(ev.get("size_ticks"), 1)),
            _cell(str(ev.get("basis", "?")).capitalize()),
            _cell(str(ev.get("lookback", "?"))),
            _cell(threshold_display),
            _cell(_fmt(ev.get("ratio"), 2)),
            _cell(_fmt(fav_t, 1, "t"), _fav_color(fav_t), bold=True),
            _cell(_fmt(adv_t, 1, "t"), _adv_color(adv_t)),
            _cell(_fmt(ev.get("time_to_max_fav_min"), 1, "m")),
            _cell(_fmt(ev.get("time_to_max_adv_min"), 1, "m")),
        ]

        if tick_used:
            cols.append(_cell(_fmt(ev.get("tick_pre_reversal_fav_ticks"), 1, "t"), "#e8f5e9"))
            cols.append(_cell(_fmt(ev.get("tick_pre_reversal_adv_ticks"), 1, "t")))
            rev = ev.get("tick_reversal_occurred")
            rev_str = "Yes" if rev else "No"
            rev_bg  = "#ffeb9c" if rev else "#fff"
            cols.append(_cell(rev_str, rev_bg))

        rows.append(f'<tr>{"".join(cols)}</tr>')

    html += (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:11px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )

    html += '</div>'
    return html
