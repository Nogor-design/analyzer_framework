from __future__ import annotations

"""
Large Candle Excursion — Trade Analysis Summary Section
========================================================
Renders per-(candle-direction × candle-bucket × trade-mode × target-percent)
win-rate tables so the analyst can quickly answer:

  "After a large bullish candle (50–75 ticks), does continuation or reverse
   work better at a 50 % target?"

Consumes ctx["large_candle_excursion"]["trade_analysis"]["trade_combo_results"].
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared helpers (mirrors large_candle_excursion_summary.py style)
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


def _wr_color(win_rate: Optional[float]) -> str:
    if win_rate is None:
        return "#f5f5f5"
    if win_rate >= 70:
        return "#c6efce"
    if win_rate >= 55:
        return "#e2efda"
    if win_rate >= 45:
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
        f'padding-bottom:6px;margin-top:24px;margin-bottom:12px">{text}</h3>'
    )


def _stat_box(label: str, value: str, accent: str = "#3498db") -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {accent};'
        f'padding:10px 16px;border-radius:4px;min-width:110px">'
        f'<div style="font-size:11px;color:#888;margin-bottom:2px">{label}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{accent}">{value}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Summary banner
# ---------------------------------------------------------------------------

def _render_banner(combos: List[Dict]) -> str:
    if not combos:
        return '<p style="color:#888">No trade analysis results available.</p>'

    n_total  = sum(c.get("n_events", 0) for c in combos)
    win_rates = [c["win_rate"] for c in combos if c.get("win_rate") is not None]
    best_wr   = max(win_rates, default=0.0)
    avg_wr    = round(sum(win_rates) / len(win_rates), 1) if win_rates else 0.0

    cont  = [c for c in combos if c.get("trade_mode") == "continuation"]
    rev   = [c for c in combos if c.get("trade_mode") == "reverse"]
    best_cont = max((c["win_rate"] for c in cont if c.get("win_rate") is not None), default=0.0)
    best_rev  = max((c["win_rate"] for c in rev  if c.get("win_rate") is not None), default=0.0)

    html  = '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px">'
    html += _stat_box("Trade Combos",        str(len(combos)))
    html += _stat_box("Total Events",        f"{n_total:,}")
    html += _stat_box("Best Win Rate",       f"{best_wr:.1f}%",  "#27ae60")
    html += _stat_box("Avg Win Rate",        f"{avg_wr:.1f}%",   "#3498db")
    html += _stat_box("Best Continuation",   f"{best_cont:.1f}%","#8e44ad")
    html += _stat_box("Best Reverse",        f"{best_rev:.1f}%", "#e67e22")
    html += '</div>'
    return html


# ---------------------------------------------------------------------------
# Best-of tables
# ---------------------------------------------------------------------------

def _best_of_table(combos: List[Dict], trade_mode: str, top_n: int = 15) -> str:
    rows = [c for c in combos if c.get("trade_mode") == trade_mode and c.get("n_events", 0) >= 5]
    if not rows:
        return f'<p style="color:#888">No {trade_mode} results with ≥5 events.</p>'

    rows = sorted(rows, key=lambda r: -(r.get("win_rate") or 0))[:top_n]

    headers = ["Direction", "Bucket", "Target%", "Window",
               "Events", "Wins", "Win Rate",
               "Avg Signal t", "Avg Target t", "Avg Trade Fav t"]
    hdr_row = "".join(_hdr(h) for h in headers)

    rows_html: List[str] = []
    for r in rows:
        dir_label = "Bull" if r.get("direction") == 1 else ("Bear" if r.get("direction") == -1 else str(r.get("direction", "?")))
        wr = r.get("win_rate")
        cols = [
            _cell(dir_label),
            _cell(str(r.get("candle_bucket", "?"))),
            _cell(f"{r.get('target_percent', '?')}%"),
            _cell(f"{r.get('window_minutes', '?')}m"),
            _cell(str(r.get("n_events", 0)), bold=True),
            _cell(str(r.get("n_wins", 0))),
            _cell(_fmt(wr, 1, "%"), _wr_color(wr), bold=True),
            _cell(_fmt(r.get("avg_signal_ticks"), 1, "t")),
            _cell(_fmt(r.get("avg_target_ticks"), 1, "t")),
            _cell(_fmt(r.get("avg_trade_fav_ticks"), 1, "t")),
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

def render_large_candle_excursion_trade_summary(ctx: dict) -> str:
    data = _get_data(ctx)

    if not data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px">'
            '<strong>Trade Analysis:</strong> No large candle excursion data found.'
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

    combos: List[Dict] = ta.get("trade_combo_results", [])

    html = '<div style="font-family:Arial,sans-serif">'
    html += (
        '<p style="color:#555;font-size:13px">'
        'Simulated trade outcomes for each signal candle. Entry at signal close. '
        'Continuation = follow candle direction. Reverse = fade candle direction. '
        'Target expressed as % of signal candle size in ticks.'
        '</p>'
    )
    html += _render_banner(combos)

    html += _section_title("Top Continuation Setups — Sorted by Win Rate")
    html += _best_of_table(combos, "continuation")

    html += _section_title("Top Reverse Setups — Sorted by Win Rate")
    html += _best_of_table(combos, "reverse")

    html += '</div>'
    return html
