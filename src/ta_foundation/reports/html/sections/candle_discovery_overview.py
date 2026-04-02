from __future__ import annotations

"""
Candle Discovery Overview Section
===================================
Renders a cross-TF × pattern × regime/session heatmap matrix from
candle_discovery sweep results.

Consumes ctx["all_options"]["candle_discovery"] results stored under
pkg.metadata["derived"]["candle_discovery"].

Sections rendered:
  1. Summary stats bar (total combos, results, top PF found)
  2. Pattern × TF raw PF heatmap
  3. Pattern × Regime PF heatmap  (requires regime breakdown)
  4. Pattern × Session PF heatmap
  5. MTF mode comparison table (independent vs confluence vs hierarchical)
"""

from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_pf(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, 2) if f == f else None  # NaN check
    except Exception:
        return None


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#f5f5f5"
    if pf >= 1.5:
        return "#c6efce"   # green
    if pf >= 1.2:
        return "#e2efda"   # light green
    if pf >= 1.0:
        return "#ffeb9c"   # yellow
    return "#ffc7ce"       # red


def _cell(value: str, bg: str, extra_style: str = "") -> str:
    return (
        f'<td style="background:{bg};text-align:center;padding:6px 10px;'
        f'border:1px solid #ddd;{extra_style}">{value}</td>'
    )


def _header_cell(text: str) -> str:
    return (
        f'<th style="background:#2c3e50;color:#fff;padding:8px 12px;'
        f'text-align:center;border:1px solid #ddd;font-weight:600">'
        f'{text}</th>'
    )


def _table_wrap(header_row: str, body_rows: List[str]) -> str:
    rows_html = "\n".join(body_rows)
    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>{header_row}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>'
    )


def _section_title(title: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:24px">{title}</h3>'
    )


# ---------------------------------------------------------------------------
# Sub-renders
# ---------------------------------------------------------------------------

def _render_summary(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888">No candle discovery results available.</p>'
    n_total   = len(results)
    pfs       = [float(r["metrics"].get("profit_factor", 0) or 0)
                 for r in results if r.get("metrics", {}).get("profit_factor")]
    top_pf    = round(max(pfs), 2) if pfs else 0.0
    avg_pf    = round(sum(pfs) / len(pfs), 2) if pfs else 0.0
    n_gt1     = sum(1 for p in pfs if p >= 1.0)
    n_gt13    = sum(1 for p in pfs if p >= 1.3)

    return (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">'
        + _stat_box("Results", str(n_total), "#3498db")
        + _stat_box("Best PF", str(top_pf), "#27ae60")
        + _stat_box("Avg PF",  str(avg_pf),  "#e67e22")
        + _stat_box("PF ≥ 1.0", str(n_gt1),  "#16a085")
        + _stat_box("PF ≥ 1.3", str(n_gt13), "#8e44ad")
        + '</div>'
    )


def _stat_box(label: str, value: str, color: str) -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {color};'
        f'padding:12px 18px;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.1);'
        f'min-width:110px">'
        f'<div style="font-size:11px;color:#888;text-transform:uppercase">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color}">{value}</div>'
        '</div>'
    )


def _render_pattern_tf_heatmap(results: List[Dict]) -> str:
    if not results:
        return ""

    # Build pivot: rows = pattern, cols = TF
    rows_data: Dict[str, Dict[int, List[float]]] = {}
    for r in results:
        if r.get("mtf_mode", "").startswith("confluence") or \
           r.get("mtf_mode", "").startswith("hierarchical"):
            continue
        pat = str(r.get("pattern_id", "?"))
        tf  = int(r.get("tf", 0))
        pf  = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None:
            continue
        rows_data.setdefault(pat, {}).setdefault(tf, []).append(pf)

    if not rows_data:
        return ""

    all_tfs = sorted({tf for vals in rows_data.values() for tf in vals})
    header  = _header_cell("Pattern") + "".join(_header_cell(f"{tf}m") for tf in all_tfs)
    body    = []
    for pat in sorted(rows_data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{pat}</td>'
        for tf in all_tfs:
            vals = rows_data[pat].get(tf)
            if vals:
                avg_pf_val = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg_pf_val), _pf_color(avg_pf_val))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Pattern × Timeframe — Average Profit Factor")
    html += '<p style="color:#666;font-size:12px">Average PF across all param combos and timing modes per cell. '
    html += 'Green ≥ 1.5 | Light green ≥ 1.2 | Yellow ≥ 1.0 | Red < 1.0</p>'
    html += _table_wrap(header, body)
    return html


def _render_regime_heatmap(results: List[Dict]) -> str:
    # Build pivot: rows = pattern, cols = regime
    rows_data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        pat = str(r.get("pattern_id", "?"))
        bk  = r.get("regime_breakdown", {})
        for regime, stats in bk.items():
            if not isinstance(stats, dict):
                continue
            pf = _safe_pf(stats.get("profit_factor"))
            nt = int(stats.get("n_trades", 0))
            if pf is None or nt < 5:
                continue
            rows_data.setdefault(pat, {}).setdefault(regime, []).append(pf)

    if not rows_data:
        return ""

    all_regimes = sorted({r for vals in rows_data.values() for r in vals})
    header = _header_cell("Pattern") + "".join(_header_cell(reg.title()) for reg in all_regimes)
    body   = []
    for pat in sorted(rows_data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{pat}</td>'
        for reg in all_regimes:
            vals = rows_data[pat].get(reg)
            if vals:
                avg_pf_val = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg_pf_val), _pf_color(avg_pf_val))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Pattern × Regime — Average Profit Factor")
    html += _table_wrap(header, body)
    return html


def _render_session_heatmap(results: List[Dict]) -> str:
    rows_data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        pat = str(r.get("pattern_id", "?"))
        bk  = r.get("session_breakdown", {})
        for sess, stats in bk.items():
            if not isinstance(stats, dict):
                continue
            pf = _safe_pf(stats.get("profit_factor"))
            nt = int(stats.get("n_trades", 0))
            if pf is None or nt < 5:
                continue
            rows_data.setdefault(pat, {}).setdefault(sess, []).append(pf)

    if not rows_data:
        return ""

    all_sessions = sorted({s for vals in rows_data.values() for s in vals})
    header = _header_cell("Pattern") + "".join(_header_cell(s) for s in all_sessions)
    body   = []
    for pat in sorted(rows_data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{pat}</td>'
        for sess in all_sessions:
            vals = rows_data[pat].get(sess)
            if vals:
                avg_pf_val = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg_pf_val), _pf_color(avg_pf_val))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Pattern × Session — Average Profit Factor")
    html += _table_wrap(header, body)
    return html


def _render_mtf_comparison(
    sweep: List[Dict],
    confluence: List[Dict],
    hierarchical: List[Dict],
) -> str:
    rows: List[Dict] = []

    def _avg_pf(results: List[Dict], mode_label: str) -> None:
        if not results:
            return
        pf_vals = [float(r["metrics"].get("profit_factor") or 0)
                   for r in results
                   if r.get("metrics", {}).get("profit_factor")]
        if not pf_vals:
            return
        rows.append({
            "mtf_mode":  mode_label,
            "n_results": len(results),
            "avg_pf":    round(sum(pf_vals) / len(pf_vals), 3),
            "best_pf":   round(max(pf_vals), 3),
            "n_gt1":     sum(1 for p in pf_vals if p >= 1.0),
            "n_gt13":    sum(1 for p in pf_vals if p >= 1.3),
        })

    _avg_pf(sweep, "Independent (per TF)")
    _avg_pf(confluence, "Confluence (N-of-M)")
    _avg_pf(hierarchical, "Hierarchical (HTF bias)")

    if not rows:
        return ""

    header = "".join(_header_cell(c) for c in
                     ["MTF Mode", "Results", "Avg PF", "Best PF", "PF≥1.0", "PF≥1.3"])
    body = []
    for r in rows:
        row = (
            f'<td style="padding:6px 10px;border:1px solid #ddd">{r["mtf_mode"]}</td>'
            + _cell(str(r["n_results"]), "#f8f9fa")
            + _cell(str(r["avg_pf"]),   _pf_color(r["avg_pf"]))
            + _cell(str(r["best_pf"]),  _pf_color(r["best_pf"]))
            + _cell(str(r["n_gt1"]),    "#f8f9fa")
            + _cell(str(r["n_gt13"]),   "#f8f9fa")
        )
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("MTF Mode Comparison")
    html += _table_wrap(header, body)
    return html


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_candle_discovery_overview(ctx: dict) -> str:
    """
    Render candle discovery overview section.

    Expects candle_discovery results in one of:
      ctx["candle_discovery"]
      ctx["all_options"]["candle_discovery"]
      packages[first_key].metadata["derived"]["candle_discovery"]
    """
    # Locate results
    cd_data: Optional[Dict] = None

    if "candle_discovery" in ctx:
        cd_data = ctx["candle_discovery"]
    elif ctx.get("all_options", {}).get("candle_discovery"):
        cd_data = ctx["all_options"]["candle_discovery"]
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
            'border-radius:4px"><strong>Candle Discovery:</strong> No results found. '
            'Enable <code>candle_discovery.enabled: true</code> in your report YAML '
            'and ensure market data is available.</div>'
        )

    sweep        = cd_data.get("sweep_results", [])
    confluence   = cd_data.get("mtf_confluence_results", [])
    hierarchical = cd_data.get("mtf_hierarchical_results", [])
    all_results  = sweep + confluence + hierarchical

    n_run = cd_data.get("n_combinations_run", 0)
    n_res = cd_data.get("n_results", len(all_results))

    html = '<div style="font-family:Arial,sans-serif">'
    html += f'<p style="color:#666;font-size:13px">{n_run:,} combinations evaluated — {n_res:,} results (≥ min_trades threshold)</p>'

    html += _render_summary(all_results)
    html += _render_pattern_tf_heatmap(sweep)
    html += _render_regime_heatmap(all_results)
    html += _render_session_heatmap(all_results)
    html += _render_mtf_comparison(sweep, confluence, hierarchical)
    html += '</div>'
    return html
