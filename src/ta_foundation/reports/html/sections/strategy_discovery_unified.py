from __future__ import annotations

"""
Unified Strategy Discovery Section
=====================================
Aggregates results from ALL entry strategy modules (candle, MA, ORB, BB)
into a single cross-strategy ranking table.

Shows:
  1. Strategy type × outcome — best PF per family
  2. Top-20 entries overall (ranked by composite score across all strategy types)
  3. Best by regime — which strategy type dominates each market regime
  4. Best by session — which strategy type dominates each session
  5. Strategy family comparison summary

Consumes from pkg.metadata["derived"]:
  candle_discovery, ma_discovery, orb_discovery, bb_discovery
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _pf_color(pf: Optional[float]) -> str:
    if pf is None:    return "#f5f5f5"
    if pf >= 1.5:     return "#c6efce"
    if pf >= 1.2:     return "#e2efda"
    if pf >= 1.0:     return "#ffeb9c"
    return "#ffc7ce"


def _wr_color(wr: Optional[float]) -> str:
    if wr is None:    return "#f5f5f5"
    if wr >= 0.60:    return "#c6efce"
    if wr >= 0.50:    return "#ffeb9c"
    return "#ffc7ce"


def _safe(v: Any, fmt: str = ".2f", default: str = "—") -> str:
    if v is None: return default
    try:
        f = float(v)
        if f != f: return default
        return format(f, fmt)
    except Exception:
        return default


def _th(text: str) -> str:
    return (f'<th style="background:#1a252f;color:#fff;padding:8px 10px;'
            f'text-align:center;border:1px solid #555;font-size:12px">{text}</th>')


def _td(text: str, bg: str = "#fff", bold: bool = False, left: bool = False) -> str:
    align = "left" if left else "center"
    fw    = "font-weight:700;" if bold else ""
    return (f'<td style="background:{bg};padding:6px 9px;border:1px solid #ddd;'
            f'text-align:{align};font-size:12px;{fw}">{text}</td>')


def _section_title(title: str, color: str = "#1a252f") -> str:
    return (f'<h3 style="color:{color};border-bottom:2px solid {color};'
            f'padding-bottom:5px;margin-top:28px;margin-bottom:10px">{title}</h3>')


def _table(headers: List[str], rows: List[str]) -> str:
    hdr = "".join(_th(h) for h in headers)
    return (
        '<div style="overflow-x:auto;margin-bottom:20px">'
        '<table style="border-collapse:collapse;width:100%">'
        f'<thead><tr>{hdr}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )


def _stat_box(label: str, value: str, color: str) -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {color};'
        f'padding:10px 16px;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.1);'
        f'min-width:110px">'
        f'<div style="font-size:11px;color:#888;text-transform:uppercase">{label}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{color}">{value}</div>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

STRATEGY_COLORS = {
    "candle":   "#3498db",
    "ma":       "#27ae60",
    "orb":      "#e67e22",
    "bb":       "#9b59b6",
    "breakout": "#e74c3c",
    "pullback": "#1abc9c",
    "level":    "#f39c12",
    "other":    "#7f8c8d",
}

STRATEGY_LABELS = {
    "candle":   "Candle Pattern",
    "ma":       "MA Cross/Pullback",
    "orb":      "Opening Range Breakout",
    "bb":       "Bollinger Band",
    "breakout": "Price Breakout",
    "pullback": "Pullback/Continuation",
    "level":    "Level-Based Entry",
}


def _collect_all_results(ctx: dict) -> List[Dict]:
    """Pull sweep_results from every discovery module found in ctx."""
    all_results: List[Dict] = []
    packages = ctx.get("packages") or {}

    def _pull(data: Dict, strategy_type: str) -> None:
        for r in data.get("sweep_results", []):
            r2 = dict(r)
            # Normalise strategy_type tag
            if "strategy_type" not in r2:
                r2["strategy_type"] = strategy_type
            # Normalise pattern_id / signal_id label
            if "pattern_id" not in r2:
                r2["pattern_id"] = r2.get("signal_id", "?")
            all_results.append(r2)

    discovery_keys = {
        "candle_discovery":   "candle",
        "ma_discovery":       "ma",
        "orb_discovery":      "orb",
        "bb_discovery":       "bb",
        "breakout_discovery": "breakout",
        "pullback_discovery": "pullback",
        "level_discovery":    "level",
    }

    for key, stype in discovery_keys.items():
        # Direct ctx key
        if key in ctx:
            _pull(ctx[key], stype)
            continue
        # all_options
        if ctx.get("all_options", {}).get(key):
            _pull(ctx["all_options"][key], stype)
            continue
        # packages metadata
        for pkg in packages.values():
            derived = getattr(pkg, "metadata", {}).get("derived", {})
            if key in derived:
                _pull(derived[key], stype)
                break

    return all_results


def _score(r: Dict) -> float:
    """Composite score matching candle ranking formula."""
    m       = r.get("metrics", {})
    pf      = float(m.get("profit_factor") or 0)
    wr      = float(m.get("win_rate") or 0)
    exp     = float(m.get("expectancy") or 0)
    fill    = float(r.get("fill_rate") or 0)
    # Normalise expectancy to 0-1 range using soft cap at 200 per trade
    norm_exp = min(max(exp, 0), 200) / 200
    return 0.4 * min(pf, 5) / 5 + 0.3 * wr + 0.2 * norm_exp + 0.1 * fill


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_family_summary(all_results: List[Dict]) -> str:
    """One row per strategy family — best PF, avg PF, n_results, n_gt1."""
    by_family: Dict[str, List[float]] = {}
    for r in all_results:
        stype = str(r.get("strategy_type", "other"))
        pf    = r.get("metrics", {}).get("profit_factor")
        if pf is None: continue
        by_family.setdefault(stype, []).append(float(pf))

    if not by_family:
        return '<p style="color:#888;font-style:italic">No results across any strategy type.</p>'

    headers = ["Strategy Family", "Results", "Best PF", "Avg PF", "PF ≥ 1.0", "PF ≥ 1.3"]
    rows    = []
    for stype in sorted(by_family):
        pfs    = by_family[stype]
        color  = STRATEGY_COLORS.get(stype, "#7f8c8d")
        label  = STRATEGY_LABELS.get(stype, stype.upper())
        badge  = (f'<span style="display:inline-block;width:8px;height:8px;'
                  f'border-radius:50%;background:{color};margin-right:6px"></span>')
        best   = round(max(pfs), 2)
        avg    = round(sum(pfs) / len(pfs), 2)
        n_gt1  = sum(1 for p in pfs if p >= 1.0)
        n_gt13 = sum(1 for p in pfs if p >= 1.3)
        row = (
            _td(f"{badge}{label}", left=True)
            + _td(str(len(pfs)))
            + _td(str(best),  bg=_pf_color(best),  bold=True)
            + _td(str(avg),   bg=_pf_color(avg))
            + _td(str(n_gt1))
            + _td(str(n_gt13))
        )
        rows.append(f"<tr>{row}</tr>")

    return _table(headers, rows)


def _render_top_entries(all_results: List[Dict], top_n: int = 25) -> str:
    """Top-N entries ranked by composite score across all strategy types."""
    scored = [(r, _score(r)) for r in all_results
              if r.get("metrics", {}).get("profit_factor") is not None]
    scored.sort(key=lambda x: x[1], reverse=True)
    top    = scored[:top_n]

    if not top:
        return '<p style="color:#888;font-style:italic">No results to rank.</p>'

    headers = ["#", "Family", "Signal", "TF", "Dir", "Timing", "Outcome",
               "Trades", "Fill%", "PF", "WR", "Expectancy", "Score"]
    rows    = []
    for rank, (r, sc) in enumerate(top, 1):
        m     = r.get("metrics", {})
        stype = str(r.get("strategy_type", "other"))
        color = STRATEGY_COLORS.get(stype, "#7f8c8d")
        label = STRATEGY_LABELS.get(stype, stype.upper())
        badge = (f'<span style="display:inline-block;width:8px;height:8px;'
                 f'border-radius:50%;background:{color};margin-right:4px"></span>')
        pf    = float(m.get("profit_factor") or 0)
        wr    = float(m.get("win_rate") or 0)

        row = (
            _td(str(rank))
            + _td(f"{badge}{label}", left=True)
            + _td(str(r.get("pattern_id") or r.get("signal_id", "?")))
            + _td(f"{r.get('tf', '?')}m")
            + _td(str(r.get("direction_mode", "?")))
            + _td(str(r.get("entry_timing", "?")))
            + _td(str(r.get("outcome_mode", "?")))
            + _td(str(int(m.get("n_trades", 0))))
            + _td(_safe(r.get("fill_rate"), ".1%"))
            + _td(_safe(pf),  bg=_pf_color(pf),  bold=True)
            + _td(_safe(wr, ".1%"), bg=_wr_color(wr))
            + _td(_safe(m.get("expectancy")))
            + _td(_safe(sc, ".3f"))
        )
        rows.append(f"<tr>{row}</tr>")

    return _table(headers, rows)


def _render_best_by_regime(all_results: List[Dict]) -> str:
    """For each regime, show best strategy family (by avg PF) and its top signal."""
    regime_data: Dict[str, Dict[str, List[float]]] = {}
    for r in all_results:
        stype = str(r.get("strategy_type", "other"))
        for regime, stats in (r.get("regime_breakdown") or {}).items():
            pf = stats.get("profit_factor")
            nt = int(stats.get("n_trades", 0))
            if pf is None or nt < 5:
                continue
            regime_data.setdefault(regime, {}).setdefault(stype, []).append(float(pf))

    if not regime_data:
        return ""

    all_stypes = sorted(STRATEGY_COLORS.keys())
    headers    = ["Regime"] + [STRATEGY_LABELS.get(s, s.upper()) for s in all_stypes]
    rows       = []
    for regime in sorted(regime_data):
        row = _td(regime.title(), left=True)
        for stype in all_stypes:
            vals = regime_data[regime].get(stype)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _td(str(avg), bg=_pf_color(avg), bold=True)
            else:
                row += _td("—", "#f5f5f5")
        rows.append(f"<tr>{row}</tr>")

    html  = _section_title("Best Strategy by Market Regime", "#784212")
    html += '<p style="color:#666;font-size:12px">Average PF per (regime, strategy family) cell. Min 5 trades required.</p>'
    html += _table(headers, rows)
    return html


def _render_best_by_session(all_results: List[Dict]) -> str:
    """For each session, show avg PF per strategy family."""
    session_data: Dict[str, Dict[str, List[float]]] = {}
    for r in all_results:
        stype = str(r.get("strategy_type", "other"))
        for sess, stats in (r.get("session_breakdown") or {}).items():
            pf = stats.get("profit_factor")
            nt = int(stats.get("n_trades", 0))
            if pf is None or nt < 5:
                continue
            session_data.setdefault(sess, {}).setdefault(stype, []).append(float(pf))

    if not session_data:
        return ""

    all_stypes = sorted(STRATEGY_COLORS.keys())
    headers    = ["Session"] + [STRATEGY_LABELS.get(s, s.upper()) for s in all_stypes]
    rows       = []
    for sess in sorted(session_data):
        row = _td(sess, left=True)
        for stype in all_stypes:
            vals = session_data[sess].get(stype)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _td(str(avg), bg=_pf_color(avg), bold=True)
            else:
                row += _td("—", "#f5f5f5")
        rows.append(f"<tr>{row}</tr>")

    html  = _section_title("Best Strategy by Session", "#1a5276")
    html += '<p style="color:#666;font-size:12px">Average PF per (session, strategy family) cell. Min 5 trades required.</p>'
    html += _table(headers, rows)
    return html


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_strategy_discovery_unified(ctx: dict) -> str:
    """
    Render the unified cross-strategy discovery ranking.
    Aggregates candle, MA, ORB, and BB discovery results.
    """
    all_results = _collect_all_results(ctx)

    if not all_results:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px"><strong>Unified Strategy Discovery:</strong> '
            'No results found. Enable at least one discovery module '
            '(<code>candle_discovery</code>, <code>ma_discovery</code>, '
            '<code>orb_discovery</code>, <code>bb_discovery</code>) in your YAML.</div>'
        )

    # Summary stat boxes
    pfs    = [float(r["metrics"]["profit_factor"]) for r in all_results
              if r.get("metrics", {}).get("profit_factor") is not None]
    n_strats = len({r.get("strategy_type") for r in all_results})

    html = '<div style="font-family:Arial,sans-serif">'
    html += f'<p style="color:#666;font-size:13px">{len(all_results):,} total results across {n_strats} strategy families</p>'
    html += (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">'
        + _stat_box("Total Results",  str(len(all_results)),                    "#1a252f")
        + _stat_box("Best PF",        _safe(max(pfs) if pfs else None),          "#27ae60")
        + _stat_box("Avg PF",         _safe(sum(pfs)/len(pfs) if pfs else None), "#3498db")
        + _stat_box("PF ≥ 1.0",       str(sum(1 for p in pfs if p >= 1.0)),      "#16a085")
        + _stat_box("PF ≥ 1.3",       str(sum(1 for p in pfs if p >= 1.3)),      "#8e44ad")
        + '</div>'
    )

    # Family summary
    html += _section_title("Strategy Family Comparison")
    html += _render_family_summary(all_results)

    # Top entries
    html += _section_title("Top 25 Entries — All Strategy Types")
    html += '<p style="color:#666;font-size:12px">Ranked by composite score: 0.4×PF + 0.3×WR + 0.2×norm_expectancy + 0.1×fill_rate</p>'
    html += _render_top_entries(all_results, top_n=25)

    # Regime breakdown
    html += _render_best_by_regime(all_results)

    # Session breakdown
    html += _render_best_by_session(all_results)

    html += '</div>'
    return html
