from __future__ import annotations

"""
Large Candle Excursion — Trade Comparison Tables
=================================================
Side-by-side comparison of continuation vs reverse across:
  - signal candle direction (bullish / bearish)
  - candle size bucket
  - target percent

For each (direction × bucket × target_pct) cell, shows:
  Continuation win %  |  Reverse win %  |  Winner

Answers questions like:
  "On bullish 75–100t candles at 50% target: cont 42% vs rev 78% → Reverse wins"
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


def _hdr(text: str, colspan: int = 1) -> str:
    cs = f' colspan="{colspan}"' if colspan > 1 else ""
    return (
        f'<th{cs} style="background:#2c3e50;color:#fff;padding:7px 10px;'
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


# ---------------------------------------------------------------------------
# Build lookup: (direction, bucket, trade_mode, target_pct) -> avg win_rate
# Aggregates across tf/lookback/basis/threshold/window_minutes to give
# a clean picture — the user can filter further via the event table.
# ---------------------------------------------------------------------------

def _build_lookup(combos: List[Dict]) -> Dict:
    """
    Returns nested dict:
      [direction][bucket][trade_mode][target_pct] -> {win_rate, n_events, n_wins}
    Aggregated (weighted average) across all tf/lookback/basis/threshold/window combos.
    """
    accum: Dict = {}  # key -> {wins, events}

    for c in combos:
        direction   = c.get("direction")
        bucket      = c.get("candle_bucket", "?")
        trade_mode  = c.get("trade_mode", "?")
        target_pct  = c.get("target_percent")
        n           = c.get("n_events", 0)
        wins        = c.get("n_wins", 0)

        if direction is None or target_pct is None or n == 0:
            continue

        key = (direction, bucket, trade_mode, target_pct)
        if key not in accum:
            accum[key] = {"wins": 0, "events": 0}
        accum[key]["wins"]   += wins
        accum[key]["events"] += n

    result: Dict = {}
    for (direction, bucket, trade_mode, target_pct), agg in accum.items():
        n = agg["events"]
        w = agg["wins"]
        wr = round(w / n * 100, 1) if n > 0 else None
        result.setdefault(direction, {}).setdefault(bucket, {}).setdefault(trade_mode, {})[target_pct] = {
            "win_rate": wr, "n_events": n, "n_wins": w,
        }
    return result


def _render_direction_table(direction: int, lookup: Dict, target_pcts: List[float]) -> str:
    dir_label = "Bullish" if direction == 1 else "Bearish"
    buckets   = sorted(lookup.keys(), key=_bucket_sort_key)
    if not buckets:
        return f'<p style="color:#888">No data for {dir_label} candles.</p>'

    # Header: Bucket | Target% | Cont WR | Rev WR | Better
    header_cells = (
        _hdr("Candle Bucket") +
        _hdr("Target %") +
        _hdr("Cont Win%") +
        _hdr("Rev Win%") +
        _hdr("Better") +
        _hdr("Cont N") +
        _hdr("Rev N")
    )

    rows_html: List[str] = []
    for bucket in buckets:
        bkt_data = lookup[bucket]
        for i, tgt_pct in enumerate(target_pcts):
            cont_cell = bkt_data.get("continuation", {}).get(tgt_pct, {})
            rev_cell  = bkt_data.get("reverse",      {}).get(tgt_pct, {})
            cont_wr   = cont_cell.get("win_rate")
            rev_wr    = rev_cell.get("win_rate")
            cont_n    = cont_cell.get("n_events", 0)
            rev_n     = rev_cell.get("n_events", 0)

            if cont_wr is None and rev_wr is None:
                continue

            if cont_wr is not None and rev_wr is not None:
                if cont_wr > rev_wr + 2:
                    better = "Cont ▲"
                    b_bg   = "#dff0d8"
                elif rev_wr > cont_wr + 2:
                    better = "Rev ▲"
                    b_bg   = "#fcf8e3"
                else:
                    better = "≈ Tie"
                    b_bg   = "#f5f5f5"
            elif cont_wr is not None:
                better = "Cont only"
                b_bg   = "#f0f0f0"
            else:
                better = "Rev only"
                b_bg   = "#f0f0f0"

            bucket_cell = _cell(bucket, bold=(i == 0)) if i == 0 else _cell("")
            cols = [
                bucket_cell,
                _cell(f"{tgt_pct:.0f}%"),
                _cell(_fmt(cont_wr, 1, "%"), _wr_color(cont_wr)),
                _cell(_fmt(rev_wr,  1, "%"), _wr_color(rev_wr)),
                _cell(better, b_bg, bold=True),
                _cell(str(cont_n) if cont_n else "—"),
                _cell(str(rev_n)  if rev_n  else "—"),
            ]
            rows_html.append(f'<tr>{"".join(cols)}</tr>')

    if not rows_html:
        return f'<p style="color:#888">No data for {dir_label} candles at configured targets.</p>'

    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div>'
    )


def _bucket_sort_key(label: str) -> float:
    """Sort buckets numerically by lower bound."""
    try:
        return float(label.replace("+", "").split("-")[0])
    except Exception:
        return 9999.0


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_trade_comparison(ctx: dict) -> str:
    data = _get_data(ctx)

    if not data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px">'
            '<strong>Trade Comparison:</strong> No large candle excursion data found.'
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
    if not combos:
        return '<p style="color:#888">No trade combo results to compare.</p>'

    lookup     = _build_lookup(combos)
    target_pcts: List[float] = sorted({
        c["target_percent"] for c in combos if c.get("target_percent") is not None
    })

    html = '<div style="font-family:Arial,sans-serif">'
    html += (
        '<p style="color:#555;font-size:13px">'
        'Continuation vs Reverse win rates aggregated across all '
        'tf/lookback/threshold/window combinations. '
        '"Better" highlights which trade mode had a &gt;2% win-rate edge. '
        '"≈ Tie" means difference was ≤2%.'
        '</p>'
    )

    for direction in (1, -1):
        dir_label = "Bullish" if direction == 1 else "Bearish"
        if direction in lookup:
            html += _section_title(f"{dir_label} Signal Candles — Continuation vs Reverse")
            html += _render_direction_table(direction, lookup[direction], target_pcts)

    html += '</div>'
    return html
