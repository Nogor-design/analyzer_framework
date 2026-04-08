from __future__ import annotations

"""
Large Candle Excursion — Distributions Section
================================================
Renders percentile-based excursion distribution tables and grouped breakdowns:
  - Percentile table (P10/P25/P50/P75/P90 for fav and adv ticks)
  - Direction breakdown (bullish vs bearish)
  - Basis breakdown (body-based vs range-based)
  - Timeframe breakdown (1m vs 5m vs ...)
  - Forward window breakdown (5m vs 10m vs 15m vs 30m)
  - Time-to-max tables (when was peak favorable/adverse reached)
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers shared across sub-renders
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
        return "—"


def _hdr(text: str, width: str = "") -> str:
    w = f"min-width:{width};" if width else ""
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
        f'text-align:center;border:1px solid #ddd;font-size:12px;{w}">'
        f'{text}</th>'
    )


def _cell(value: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (
        f'<td style="background:{bg};text-align:center;padding:5px 8px;'
        f'border:1px solid #ddd;font-size:12px;{fw}">{value}</td>'
    )


def _fav_shade(v: Optional[float]) -> str:
    if v is None:
        return "#f5f5f5"
    if v >= 20:
        return "#c6efce"
    if v >= 10:
        return "#e2efda"
    if v >= 5:
        return "#ffeb9c"
    return "#fff"


def _section_title(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:28px;margin-bottom:12px">{text}</h3>'
    )


def _sub_title(text: str) -> str:
    return (
        f'<h4 style="color:#555;margin-top:18px;margin-bottom:8px;'
        f'font-size:13px;border-bottom:1px solid #eee;padding-bottom:4px">{text}</h4>'
    )


def _table_wrap(hdr_row: str, body_rows: List[str]) -> str:
    return (
        '<div style="overflow-x:auto;margin-bottom:20px">'
        '<table style="border-collapse:collapse;width:100%;font-size:12px">'
        f'<thead><tr>{hdr_row}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


# ---------------------------------------------------------------------------
# Percentile table
# ---------------------------------------------------------------------------

def _render_percentile_table(results: List[Dict]) -> str:
    """One row per combo; columns are P10/P25/P50/P75/P90 for fav and adv."""
    if not results:
        return ""

    pcts = [10, 25, 50, 75, 90]
    hdr_row = "".join([
        _hdr("TF"), _hdr("Lookback"), _hdr("Basis"), _hdr("Threshold"),
        _hdr("Window"), _hdr("N"),
    ] + [_hdr(f"Fav P{p}") for p in pcts] + [_hdr(f"Adv P{p}") for p in pcts])

    rows: List[str] = []
    for r in sorted(results, key=lambda x: (-(_v(x, "percentiles_fav", "p50") or 0),
                                             x.get("threshold_value", 0))):
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
        for p in pcts:
            v = _v(r, "percentiles_fav", f"p{p}")
            cols.append(_cell(_fmt(v, 1, "t"), _fav_shade(v)))
        for p in pcts:
            v = _v(r, "percentiles_adv", f"p{p}")
            cols.append(_cell(_fmt(v, 1, "t")))
        rows.append(f'<tr>{"".join(cols)}</tr>')

    return _table_wrap(hdr_row, rows)


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _group_and_average(
    results: List[Dict],
    group_key: str,
    group_label_fn=None,
) -> Dict[str, Dict]:
    """Average fav/adv stats by a dimension key."""
    buckets: Dict[str, List[Dict]] = {}
    for r in results:
        k = str(r.get(group_key, "?"))
        if group_label_fn:
            k = group_label_fn(r)
        buckets.setdefault(k, []).append(r)

    out: Dict[str, Dict] = {}
    for k, group in sorted(buckets.items()):
        fav_means = [_v(g, "fav_ticks", "mean") for g in group if _v(g, "fav_ticks", "mean") is not None]
        adv_means = [_v(g, "adv_ticks", "mean") for g in group if _v(g, "adv_ticks", "mean") is not None]
        fav_meds  = [_v(g, "fav_ticks", "median") for g in group if _v(g, "fav_ticks", "median") is not None]
        adv_meds  = [_v(g, "adv_ticks", "median") for g in group if _v(g, "adv_ticks", "median") is not None]
        p50_favs  = [_v(g, "percentiles_fav", "p50") for g in group if _v(g, "percentiles_fav", "p50") is not None]
        p50_advs  = [_v(g, "percentiles_adv", "p50") for g in group if _v(g, "percentiles_adv", "p50") is not None]
        ttf       = [_v(g, "time_to_max_fav", "median") for g in group if _v(g, "time_to_max_fav", "median") is not None]
        tta       = [_v(g, "time_to_max_adv", "median") for g in group if _v(g, "time_to_max_adv", "median") is not None]
        dom_pcts  = [g.get("pct_fav_dominant", 0) for g in group]
        n_total   = sum(g.get("n_events", 0) for g in group)

        def _avg(lst):
            return round(sum(lst) / len(lst), 1) if lst else None

        out[k] = {
            "n_combos":   len(group),
            "n_events":   n_total,
            "mean_fav":   _avg(fav_means),
            "mean_adv":   _avg(adv_means),
            "med_fav":    _avg(fav_meds),
            "med_adv":    _avg(adv_meds),
            "p50_fav":    _avg(p50_favs),
            "p50_adv":    _avg(p50_advs),
            "med_ttf":    _avg(ttf),
            "med_tta":    _avg(tta),
            "dom_pct":    _avg(dom_pcts),
        }
    return out


def _render_group_table(
    grouped: Dict[str, Dict],
    group_col_label: str,
) -> str:
    headers = [group_col_label, "Combos", "Events", "Mean Fav", "Mean Adv", "Med Fav",
               "Med Adv", "P50 Fav", "P50 Adv", "Fav Dom%",
               "Med TTF", "Med TTA"]
    hdr_row = "".join(_hdr(h) for h in headers)

    rows: List[str] = []
    for label, s in grouped.items():
        cols = [
            _cell(label, bold=True),
            _cell(str(s["n_combos"])),
            _cell(f"{s['n_events']:,}"),
            _cell(_fmt(s["mean_fav"], 1, "t"), _fav_shade(s["mean_fav"])),
            _cell(_fmt(s["mean_adv"], 1, "t")),
            _cell(_fmt(s["med_fav"], 1, "t"), _fav_shade(s["med_fav"])),
            _cell(_fmt(s["med_adv"], 1, "t")),
            _cell(_fmt(s["p50_fav"], 1, "t"), _fav_shade(s["p50_fav"])),
            _cell(_fmt(s["p50_adv"], 1, "t")),
            _cell(_fmt(s["dom_pct"], 1, "%"), "#e8f5e9" if s.get("dom_pct") and s["dom_pct"] >= 55 else "#fff"),
            _cell(_fmt(s["med_ttf"], 1, "m")),
            _cell(_fmt(s["med_tta"], 1, "m")),
        ]
        rows.append(f'<tr>{"".join(cols)}</tr>')

    return _table_wrap(hdr_row, rows)


def _v(d: Dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_distributions(ctx: dict) -> str:
    data = _get_data(ctx)
    if not data:
        return '<p style="color:#888">No large_candle_excursion data available.</p>'

    results = data.get("combo_results", [])
    if not results:
        return '<p style="color:#888">No combination results to display.</p>'

    options   = ctx.get("options") or {}
    show_dir  = options.get("include_direction_breakdown", True)
    show_tf   = options.get("include_timeframe_breakdown", True)
    show_basis= options.get("include_basis_breakdown", True)
    show_pct  = options.get("include_distribution_tables", True)

    html = '<div style="font-family:Arial,sans-serif">'

    if show_pct:
        html += _section_title("Excursion Percentile Distribution (Fav vs Adv Ticks)")
        html += _render_percentile_table(results)

    if show_dir:
        html += _section_title("Direction Breakdown — Bullish vs Bearish Events")
        grouped_dir = _group_and_average(
            results,
            "direction",
            group_label_fn=lambda r: "Both" if r.get("direction") == "both" else str(r.get("direction", "?")),
        )
        if grouped_dir:
            html += _render_group_table(grouped_dir, "Direction Mode")

    if show_basis:
        html += _section_title("Basis Breakdown — Body vs Range Threshold")
        grouped_basis = _group_and_average(results, "basis")
        if grouped_basis:
            html += _render_group_table(grouped_basis, "Basis")

    if show_tf:
        html += _section_title("Timeframe Breakdown")
        grouped_tf = _group_and_average(
            results, "tf",
            group_label_fn=lambda r: f"{r.get('tf', '?')}m",
        )
        if grouped_tf:
            html += _render_group_table(grouped_tf, "Timeframe")

    html += _section_title("Forward Window Breakdown")
    grouped_win = _group_and_average(
        results, "window_minutes",
        group_label_fn=lambda r: f"{r.get('window_minutes', '?')}m",
    )
    if grouped_win:
        html += _render_group_table(grouped_win, "Window")

    html += _section_title("Threshold Breakdown")
    grouped_thr = _group_and_average(
        results, "threshold_value",
        group_label_fn=lambda r: (
            f"{r.get('threshold_value', '')}×"
            if r.get("threshold_mode") == "multiplier"
            else f"{round(r.get('threshold_value', 0) * 100)}%"
        ),
    )
    if grouped_thr:
        html += _render_group_table(grouped_thr, "Threshold")

    html += '</div>'
    return html
