from __future__ import annotations

"""
Large Candle Excursion — Trade Event Detail Table
==================================================
Per-event trade outcome records: signal candle properties, trade setup,
and win/loss result for each combination of trade_mode × target_percent.

Capped at options["top_n"] rows (default 500).
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


def _hdr(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
        f'text-align:center;border:1px solid #ddd;font-size:11px;white-space:nowrap">'
        f'{text}</th>'
    )


def _cell(value: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:4px 7px;'
        f'border:1px solid #ddd;font-size:11px;{fw}">{value}</td>'
    )


def _win_bg(win: Any) -> str:
    if win is True:
        return "#c6efce"
    if win is False:
        return "#ffc7ce"
    return "#f5f5f5"


def _first_hit_bg(fh: Optional[str]) -> str:
    if fh == "target":
        return "#c6efce"
    if fh == "stop":
        return "#ffc7ce"
    return "#fff"


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_trade_event_table(ctx: dict) -> str:
    data    = _get_data(ctx)
    options = ctx.get("options") or {}
    top_n   = int(options.get("top_n", 500))

    if not data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px">'
            '<strong>Trade Event Table:</strong> No large candle excursion data found.'
            '</div>'
        )

    ta = data.get("trade_analysis")
    if not ta or not ta.get("enabled"):
        return (
            '<div style="padding:16px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px">'
            '<strong>Trade Analysis not enabled.</strong> '
            'Add <code>trade_analysis.enabled: true</code> under <code>large_candle_excursion:</code> in your YAML.'
            '</div>'
        )

    events: List[Dict] = ta.get("trade_events_sample", [])
    if not events:
        return '<p style="color:#888">No trade event records available.</p>'

    capped   = events[:top_n]
    has_stop = any(e.get("stop_ticks") is not None for e in capped)

    headers = [
        "DateTime", "TF", "Win", "Direction", "Trade Dir",
        "Mode", "Bucket", "Signal t", "Target%", "Target t",
        "Trade Fav t", "Trade Adv t",
        "Window",
    ]
    if has_stop:
        headers += ["Stop%", "Stop t", "Stop Hit", "First Hit"]

    hdr_row = "".join(_hdr(h) for h in headers)

    rows_html: List[str] = []
    for ev in capped:
        win       = ev.get("win")
        direction = ev.get("direction", 0)
        dir_label = "Bull" if direction == 1 else "Bear"
        dt_str    = str(ev.get("dt") or "—")[:19]
        fh        = ev.get("first_hit")

        cols = [
            _cell(dt_str),
            _cell(f"{ev.get('tf_minutes', '?')}m"),
            _cell("WIN" if win is True else ("LOSS" if win is False else "—"),
                  _win_bg(win), bold=True),
            _cell(dir_label),
            _cell(str(ev.get("trade_direction", "?")).capitalize()),
            _cell(str(ev.get("trade_mode", "?")).capitalize()),
            _cell(str(ev.get("candle_bucket", "?"))),
            _cell(_fmt(ev.get("signal_candle_ticks"), 1, "t")),
            _cell(f"{ev.get('target_percent', '?')}%"),
            _cell(_fmt(ev.get("target_ticks"), 1, "t")),
            _cell(_fmt(ev.get("trade_fav_ticks"), 1, "t")),
            _cell(_fmt(ev.get("trade_adv_ticks"), 1, "t")),
            _cell(f"{ev.get('window_minutes', '?')}m"),
        ]
        if has_stop:
            cols += [
                _cell(f"{ev.get('stop_percent', '—')}%"),
                _cell(_fmt(ev.get("stop_ticks"), 1, "t")),
                _cell("Y" if ev.get("stop_hit") is True else ("N" if ev.get("stop_hit") is False else "—")),
                _cell(str(fh or "—").capitalize(), _first_hit_bg(fh)),
            ]

        rows_html.append(f'<tr>{"".join(cols)}</tr>')

    note = ""
    if len(events) > top_n:
        note = (
            f'<p style="color:#888;font-size:11px">'
            f'Showing {top_n:,} of {len(events):,} trade event records. '
            f'Increase <code>options.top_n</code> to show more.</p>'
        )

    return (
        '<div style="font-family:Arial,sans-serif">'
        + note +
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:11px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div></div>'
    )
