from __future__ import annotations

"""
MA Discovery Overview Section
================================
Renders MA cross/pullback discovery results:
  1. Summary stat boxes
  2. Signal type × TF heatmap (avg PF)
  3. Signal type × Period heatmap
  4. Regime breakdown table
"""

from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers (shared style with candle discovery sections)
# ---------------------------------------------------------------------------

def _pf_color(pf: Optional[float]) -> str:
    if pf is None:    return "#f5f5f5"
    if pf >= 1.5:     return "#c6efce"
    if pf >= 1.2:     return "#e2efda"
    if pf >= 1.0:     return "#ffeb9c"
    return "#ffc7ce"


def _safe_pf(v: Any) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return round(f, 2) if f == f else None
    except Exception:
        return None


def _cell(value: str, bg: str) -> str:
    return (f'<td style="background:{bg};text-align:center;padding:6px 10px;'
            f'border:1px solid #ddd">{value}</td>')


def _header_cell(text: str) -> str:
    return (f'<th style="background:#2c3e50;color:#fff;padding:8px 12px;'
            f'text-align:center;border:1px solid #ddd;font-weight:600">{text}</th>')


def _table_wrap(header_row: str, body_rows: List[str]) -> str:
    return (
        '<div style="overflow-x:auto;margin-bottom:24px">'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>{header_row}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _section_title(title: str) -> str:
    return (f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
            f'padding-bottom:6px;margin-top:24px">{title}</h3>')


def _stat_box(label: str, value: str, color: str) -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {color};'
        f'padding:12px 18px;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.1);'
        f'min-width:110px">'
        f'<div style="font-size:11px;color:#888;text-transform:uppercase">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color}">{value}</div>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_summary(results: List[Dict]) -> str:
    if not results:
        return '<p style="color:#888">No MA discovery results available.</p>'
    pfs    = [float(r["metrics"].get("profit_factor") or 0)
              for r in results if r.get("metrics", {}).get("profit_factor")]
    top_pf = round(max(pfs), 2) if pfs else 0.0
    avg_pf = round(sum(pfs) / len(pfs), 2) if pfs else 0.0
    n_gt1  = sum(1 for p in pfs if p >= 1.0)
    n_gt13 = sum(1 for p in pfs if p >= 1.3)
    return (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">'
        + _stat_box("Results",  str(len(results)), "#3498db")
        + _stat_box("Best PF",  str(top_pf),       "#27ae60")
        + _stat_box("Avg PF",   str(avg_pf),        "#e67e22")
        + _stat_box("PF ≥ 1.0", str(n_gt1),         "#16a085")
        + _stat_box("PF ≥ 1.3", str(n_gt13),        "#8e44ad")
        + '</div>'
    )


def _render_signal_tf_heatmap(results: List[Dict]) -> str:
    """Signal type × TF average PF."""
    data: Dict[str, Dict[int, List[float]]] = {}
    for r in results:
        sig = str(r.get("signal_id", "?"))
        tf  = int(r.get("tf", 0))
        pf  = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None: continue
        data.setdefault(sig, {}).setdefault(tf, []).append(pf)

    if not data: return ""

    all_tfs = sorted({tf for vals in data.values() for tf in vals})
    header  = _header_cell("Signal") + "".join(_header_cell(f"{tf}m") for tf in all_tfs)
    body    = []
    for sig in sorted(data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{sig}</td>'
        for tf in all_tfs:
            vals = data[sig].get(tf)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Signal Type × Timeframe — Average PF")
    html += _table_wrap(header, body)
    return html


def _render_signal_period_heatmap(results: List[Dict]) -> str:
    """Signal type × MA period average PF."""
    data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        sig    = str(r.get("signal_id", "?"))
        period = str(r.get("params", {}).get("period", "?"))
        pf     = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None: continue
        data.setdefault(sig, {}).setdefault(period, []).append(pf)

    if not data: return ""

    all_periods = sorted({p for vals in data.values() for p in vals}, key=lambda x: int(x) if x.isdigit() else 0)
    header = _header_cell("Signal") + "".join(_header_cell(f"Period {p}") for p in all_periods)
    body   = []
    for sig in sorted(data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{sig}</td>'
        for p in all_periods:
            vals = data[sig].get(p)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Signal Type × MA Period — Average PF")
    html += _table_wrap(header, body)
    return html


def _render_direction_comparison(results: List[Dict]) -> str:
    """Signal type × direction (long/short) average PF."""
    data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        sig = str(r.get("signal_id", "?"))
        d   = str(r.get("direction_mode", "?"))
        pf  = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None: continue
        data.setdefault(sig, {}).setdefault(d, []).append(pf)

    if not data: return ""

    all_dirs = sorted({d for vals in data.values() for d in vals})
    header   = _header_cell("Signal") + "".join(_header_cell(d.title()) for d in all_dirs)
    body     = []
    for sig in sorted(data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{sig}</td>'
        for d in all_dirs:
            vals = data[sig].get(d)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Signal × Direction — Average PF")
    html += _table_wrap(header, body)
    return html


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_ma_discovery_overview(ctx: dict) -> str:
    """Render MA cross/pullback discovery overview."""
    md_data: Optional[Dict] = None

    if "ma_discovery" in ctx:
        md_data = ctx["ma_discovery"]
    elif ctx.get("all_options", {}).get("ma_discovery"):
        md_data = ctx["all_options"]["ma_discovery"]
    else:
        for pkg in (ctx.get("packages") or {}).values():
            derived = getattr(pkg, "metadata", {}).get("derived", {})
            if "ma_discovery" in derived:
                md_data = derived["ma_discovery"]
                break

    if not md_data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px"><strong>MA Discovery:</strong> No results found. '
            'Enable <code>ma_discovery.enabled: true</code> in your report YAML.</div>'
        )

    results = md_data.get("sweep_results", [])
    n_run   = md_data.get("n_combinations_run", 0)
    n_res   = md_data.get("n_results", len(results))

    html = '<div style="font-family:Arial,sans-serif">'
    html += f'<p style="color:#666;font-size:13px">{n_run:,} combinations evaluated — {n_res:,} results</p>'
    html += _render_summary(results)
    html += _render_signal_tf_heatmap(results)
    html += _render_signal_period_heatmap(results)
    html += _render_direction_comparison(results)
    html += '</div>'
    return html
