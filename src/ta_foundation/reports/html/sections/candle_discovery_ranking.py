from __future__ import annotations

"""
Candle Discovery Ranking Section
==================================
Renders the 5-tier ranked discovery output:
  1. Best Raw Entries
  2. Best Filtered Entries
  3. Most Robust Entries
  4. Likely Overfit
  5. Most Useful Filters

Also renders a payoff matrix (TP × SL grid) for tick-mode results.

Consumes the same candle_discovery context as candle_discovery_overview.
"""

from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies.ranking import rank_sweep_results


# ---------------------------------------------------------------------------
# Shared style helpers
# ---------------------------------------------------------------------------

def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#f5f5f5"
    if pf >= 1.5:  return "#c6efce"
    if pf >= 1.2:  return "#e2efda"
    if pf >= 1.0:  return "#ffeb9c"
    return "#ffc7ce"


def _wr_color(wr: Optional[float]) -> str:
    if wr is None:  return "#f5f5f5"
    if wr >= 0.60:  return "#c6efce"
    if wr >= 0.50:  return "#ffeb9c"
    return "#ffc7ce"


def _th(text: str) -> str:
    return (f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
            f'text-align:center;border:1px solid #555;font-size:12px">{text}</th>')


def _td(text: str, bg: str = "#fff", bold: bool = False) -> str:
    fw = "font-weight:700;" if bold else ""
    return (f'<td style="background:{bg};padding:6px 9px;border:1px solid #ddd;'
            f'text-align:center;font-size:12px;{fw}">{text}</td>')


def _td_left(text: str, bg: str = "#fff") -> str:
    return (f'<td style="background:{bg};padding:6px 9px;border:1px solid #ddd;'
            f'text-align:left;font-size:12px">{text}</td>')


def _section_title(title: str, color: str = "#2c3e50") -> str:
    return (f'<h3 style="color:{color};border-bottom:2px solid {color};'
            f'padding-bottom:5px;margin-top:28px;margin-bottom:10px">{title}</h3>')


def _safe(v: Any, fmt: str = ".2f", default: str = "—") -> str:
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return format(f, fmt)
    except Exception:
        return default


def _pct(v: Any) -> str:
    s = _safe(v, ".1%")
    return s


# ---------------------------------------------------------------------------
# Tier tables
# ---------------------------------------------------------------------------

_RAW_HEADERS = [
    "Pattern", "TF", "Direction", "Timing", "Outcome", "MTF",
    "Trades", "Fill%", "PF", "WR", "Expectancy", "Max DD", "Score",
]


def _render_best_raw(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888;font-style:italic">No results met the minimum trade threshold.</p>'

    rows = []
    for r in results:
        m   = r.get("metrics", {})
        pf  = _safe(m.get("profit_factor"))
        wr  = _safe(m.get("win_rate"), ".1%")
        exp = _safe(m.get("expectancy"), ".2f")
        dd  = _safe(m.get("max_drawdown"), ".2f")
        sc  = _safe(r.get("_score"), ".3f")
        fr  = _safe(r.get("fill_rate"), ".1%")
        nt  = str(int(m.get("n_trades", 0)))

        pf_bg = _pf_color(m.get("profit_factor"))
        wr_bg = _wr_color(m.get("win_rate"))

        row = (
            _td_left(str(r.get("pattern_id", "?")))
            + _td(f"{r.get('tf','?')}m")
            + _td(str(r.get("direction_mode", "?")))
            + _td(str(r.get("entry_timing", "?")))
            + _td(str(r.get("outcome_mode", "?")))
            + _td(str(r.get("mtf_mode", "independent")).replace("independent_", "").replace("m", "m "))
            + _td(nt)
            + _td(fr)
            + _td(pf, bg=pf_bg, bold=True)
            + _td(wr, bg=wr_bg)
            + _td(exp)
            + _td(dd)
            + _td(sc)
        )
        rows.append(f"<tr>{row}</tr>")

    header = "".join(_th(h) for h in _RAW_HEADERS)
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _render_best_filtered(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888;font-style:italic">No filtered results available.</p>'

    headers = ["Pattern", "TF", "Timing", "Outcome", "Raw PF", "Filtered PF", "PF Lift", "Filter Applied"]
    rows = []
    for r in results:
        raw_pf  = _safe(r.get("_raw_pf"),      ".2f")
        filt_pf = _safe(r.get("_filtered_pf"), ".2f")
        lift    = _safe(r.get("_pf_improvement"), "+.3f")
        filt_bg = _pf_color(r.get("_filtered_pf"))

        row = (
            _td_left(str(r.get("pattern_id", "?")))
            + _td(f"{r.get('tf','?')}m")
            + _td(str(r.get("entry_timing", "?")))
            + _td(str(r.get("outcome_mode", "?")))
            + _td(raw_pf,  bg=_pf_color(r.get("_raw_pf")))
            + _td(filt_pf, bg=filt_bg, bold=True)
            + _td(lift,    bg="#e8f5e9" if r.get("_pf_improvement", 0) > 0 else "#fff")
            + _td_left(str(r.get("_filter_label", ""))[:80])
        )
        rows.append(f"<tr>{row}</tr>")

    header = "".join(_th(h) for h in headers)
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _render_most_robust(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888;font-style:italic">No results met the robustness criteria.</p>'

    headers = ["Pattern", "TF", "Direction", "Timing", "Outcome", "Trades", "PF", "WR", "Regimes", "Sessions", "IS/OOS Deg"]
    rows = []
    for r in results:
        m  = r.get("metrics", {})
        pf = _safe(m.get("profit_factor"))
        wr = _safe(m.get("win_rate"), ".1%")
        nt = str(int(m.get("n_trades", 0)))
        nr = str(r.get("_n_regimes", "?"))
        ns = str(r.get("_n_sessions", "?"))
        dg = _safe(r.get("_is_oos_deg"), ".1%") if r.get("_is_oos_deg") is not None else "n/a"

        row = (
            _td_left(str(r.get("pattern_id", "?")))
            + _td(f"{r.get('tf','?')}m")
            + _td(str(r.get("direction_mode", "?")))
            + _td(str(r.get("entry_timing", "?")))
            + _td(str(r.get("outcome_mode", "?")))
            + _td(nt)
            + _td(pf, bg=_pf_color(m.get("profit_factor")), bold=True)
            + _td(wr, bg=_wr_color(m.get("win_rate")))
            + _td(nr, bg="#e8f5e9" if int(nr) >= 2 else "#fff")
            + _td(ns, bg="#e8f5e9" if int(ns) >= 2 else "#fff")
            + _td(dg)
        )
        rows.append(f"<tr>{row}</tr>")

    header = "".join(_th(h) for h in headers)
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _render_likely_overfit(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888;font-style:italic">No entries flagged as likely overfit.</p>'

    headers = ["Pattern", "TF", "Timing", "Outcome", "Trades", "PF", "Overfit Flags"]
    rows = []
    for r in results:
        m  = r.get("metrics", {})
        pf = _safe(m.get("profit_factor"))
        nt = str(int(m.get("n_trades", 0)))
        flags = str(r.get("_overfit_flags", ""))

        row = (
            _td_left(str(r.get("pattern_id", "?")))
            + _td(f"{r.get('tf','?')}m")
            + _td(str(r.get("entry_timing", "?")))
            + _td(str(r.get("outcome_mode", "?")))
            + _td(nt)
            + _td(pf, bg="#ffc7ce", bold=True)
            + _td_left(f'<span style="color:#c0392b">{flags}</span>')
        )
        rows.append(f"<tr>{row}</tr>")

    header = "".join(_th(h) for h in headers)
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _render_useful_filters(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888;font-style:italic">No filter data available.</p>'

    headers = ["Filter Condition", "Patterns Improved", "Total PF Lift", "Avg PF Lift per Pattern"]
    rows = []
    for r in results:
        ni  = str(r.get("n_patterns_improved", 0))
        tl  = _safe(r.get("total_pf_lift"), ".3f")
        al  = _safe(r.get("avg_pf_lift"),   ".3f")
        cond = str(r.get("condition", ""))[:100]

        row = (
            _td_left(cond)
            + _td(ni, bg="#e8f5e9")
            + _td(tl, bg="#e8f5e9")
            + _td(al, bg=_pf_color(r.get("avg_pf_lift")))
        )
        rows.append(f"<tr>{row}</tr>")

    header = "".join(_th(h) for h in headers)
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _render_payoff_matrix(all_results: List[Dict]) -> str:
    """
    For tick-mode results, render a TP × SL payoff matrix showing average PF per cell.
    Groups across all patterns/TFs for a "universal best R:R" view.
    """
    grid: Dict[str, Dict[str, List[float]]] = {}

    for r in all_results:
        om = str(r.get("outcome_mode", ""))
        if not om.startswith("ticks_"):
            continue
        parts = om.split("_")
        if len(parts) != 3:
            continue
        tp, sl = parts[1], parts[2]
        pf = r.get("metrics", {}).get("profit_factor")
        if pf is None:
            continue
        grid.setdefault(tp, {}).setdefault(sl, []).append(float(pf))

    if not grid:
        return ""

    all_tp = sorted(grid.keys(),      key=lambda x: int(x))
    all_sl = sorted({sl for vals in grid.values() for sl in vals}, key=lambda x: int(x))

    header = _th("TP \\ SL") + "".join(_th(f"SL {sl}") for sl in all_sl)
    body = []
    for tp in all_tp:
        row = _td_left(f"TP {tp}")
        for sl in all_sl:
            vals = grid.get(tp, {}).get(sl)
            if vals:
                avg_pf = round(sum(vals) / len(vals), 2)
                row += _td(str(avg_pf), bg=_pf_color(avg_pf), bold=True)
            else:
                row += _td("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("TP × SL Payoff Matrix (avg PF across all patterns)", color="#1a5276")
    html += '<p style="color:#666;font-size:12px">Average PF for each take-profit / stop-loss tick combination across all candle patterns. Best R:R ratio highlighted in green.</p>'
    html += (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody>'
        '</table></div>'
    )
    return html


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_candle_discovery_ranking(ctx: dict) -> str:
    """
    Render the 5-tier candle discovery ranking section.
    """
    # Locate results (same logic as overview)
    cd_data: Optional[Dict] = None

    if "candle_discovery" in ctx:
        cd_data = ctx["candle_discovery"]
    else:
        ao_cd = ctx.get("all_options", {}).get("candle_discovery")
        if ao_cd and isinstance(ao_cd, dict) and "sweep_results" in ao_cd:
            cd_data = ao_cd
        else:
            packages = ctx.get("packages") or {}
            for pkg in packages.values():
                derived = getattr(pkg, "metadata", {}).get("derived", {})
                if "candle_discovery" in derived:
                    cd_data = derived["candle_discovery"]
                    break

    if not cd_data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px"><strong>Candle Discovery Ranking:</strong> '
            'No results found.</div>'
        )

    sweep        = cd_data.get("sweep_results", [])
    confluence   = cd_data.get("mtf_confluence_results", [])
    hierarchical = cd_data.get("mtf_hierarchical_results", [])
    all_results  = sweep + confluence + hierarchical

    # Run ranking
    try:
        tiers = rank_sweep_results(all_results)
    except Exception as e:
        return f'<div style="color:#c0392b">Ranking error: {e}</div>'

    html = '<div style="font-family:Arial,sans-serif">'

    # Tier 1
    html += _section_title("Best Raw Entries", color="#1a5276")
    html += '<p style="color:#666;font-size:12px">Ranked by composite score: 0.4×PF + 0.3×WR + 0.2×norm_expectancy + 0.1×fill_rate</p>'
    html += _render_best_raw(tiers["best_raw"])

    # Tier 2
    html += _section_title("Best Filtered Entries", color="#145a32")
    html += '<p style="color:#666;font-size:12px">Best improvement when top filter condition is applied. Higher PF Lift = more useful filter.</p>'
    html += _render_best_filtered(tiers["best_filtered"])

    # Tier 3
    html += _section_title("Most Robust Entries", color="#784212")
    html += '<p style="color:#666;font-size:12px">Entries that work across multiple regimes and sessions with low IS/OOS degradation.</p>'
    html += _render_most_robust(tiers["most_robust"])

    # Tier 4
    html += _section_title("Likely Overfit", color="#7b241c")
    html += '<p style="color:#666;font-size:12px">High apparent PF but flagged for low trade count, single-session concentration, or high IS/OOS degradation. Trade with caution.</p>'
    html += _render_likely_overfit(tiers["likely_overfit"])

    # Tier 5
    html += _section_title("Most Useful Filters", color="#1b2631")
    html += '<p style="color:#666;font-size:12px">Filter conditions that consistently improve PF across multiple entry types — candidates for universal pre-filters.</p>'
    html += _render_useful_filters(tiers["most_useful_filters"])

    # Payoff matrix
    html += _render_payoff_matrix(all_results)

    html += '</div>'
    return html
