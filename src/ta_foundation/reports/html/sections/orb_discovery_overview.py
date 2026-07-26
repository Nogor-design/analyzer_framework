from __future__ import annotations

"""
ORB Discovery Overview Section
================================
Renders Opening Range Breakout discovery results:
  1. Summary stat boxes
  2. ORB minutes × direction heatmap (avg PF)
  3. Range width × require_close_beyond comparison
  4. Session breakdown (ORBs are strongly session-dependent)
"""

from typing import Any, Dict, List, Optional

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
    return (f'<h3 style="color:#2c3e50;border-bottom:2px solid #e67e22;'
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
        return '<p style="color:#888">No ORB discovery results available.</p>'
    pfs    = [float(r["metrics"].get("profit_factor") or 0)
              for r in results if r.get("metrics", {}).get("profit_factor")]
    top_pf = round(max(pfs), 2) if pfs else 0.0
    avg_pf = round(sum(pfs) / len(pfs), 2) if pfs else 0.0
    n_gt1  = sum(1 for p in pfs if p >= 1.0)
    n_gt13 = sum(1 for p in pfs if p >= 1.3)
    return (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">'
        + _stat_box("Results",  str(len(results)), "#e67e22")
        + _stat_box("Best PF",  str(top_pf),       "#27ae60")
        + _stat_box("Avg PF",   str(avg_pf),        "#3498db")
        + _stat_box("PF ≥ 1.0", str(n_gt1),         "#16a085")
        + _stat_box("PF ≥ 1.3", str(n_gt13),        "#8e44ad")
        + '</div>'
    )


def _render_orb_minutes_heatmap(results: List[Dict]) -> str:
    """ORB window length × direction — average PF."""
    data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        mins = str(r.get("params", {}).get("orb_minutes", "?"))
        d    = str(r.get("direction_mode", "?"))
        pf   = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None: continue
        data.setdefault(mins, {}).setdefault(d, []).append(pf)

    if not data: return ""

    all_dirs = sorted({d for vals in data.values() for d in vals})
    all_mins = sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    header   = _header_cell("ORB Window") + "".join(_header_cell(d.title()) for d in all_dirs)
    body     = []
    for mins in all_mins:
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{mins}m window</td>'
        for d in all_dirs:
            vals = data[mins].get(d)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("ORB Window × Direction — Average PF")
    html += '<p style="color:#666;font-size:12px">Average PF across all parameter combos per (window, direction) cell.</p>'
    html += _table_wrap(header, body)
    return html


def _render_range_filter_comparison(results: List[Dict]) -> str:
    """min_range_ticks × require_close_beyond comparison."""
    data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        mrt  = str(r.get("params", {}).get("min_range_ticks", "?"))
        rcb  = str(r.get("params", {}).get("require_close_beyond", "?"))
        pf   = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None: continue
        data.setdefault(mrt, {}).setdefault(rcb, []).append(pf)

    if not data: return ""

    all_rcb  = sorted({rcb for vals in data.values() for rcb in vals})
    all_mrts = sorted(data.keys(), key=lambda x: float(x) if x.replace(".", "").isdigit() else 0)
    header   = _header_cell("Min Range Ticks") + "".join(_header_cell(f"Close Beyond: {r}") for r in all_rcb)
    body     = []
    for mrt in all_mrts:
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{mrt} ticks min</td>'
        for rcb in all_rcb:
            vals = data[mrt].get(rcb)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Range Filter × Entry Style — Average PF")
    html += _table_wrap(header, body)
    return html


def _render_session_breakdown(results: List[Dict]) -> str:
    """Aggregate session breakdown across all ORB results."""
    session_data: Dict[str, List[float]] = {}
    for r in results:
        for sess, stats in (r.get("session_breakdown") or {}).items():
            pf = _safe_pf(stats.get("profit_factor"))
            if pf is None: continue
            session_data.setdefault(sess, []).append(pf)

    if not session_data: return ""

    header = _header_cell("Session") + _header_cell("Avg PF") + _header_cell("N Combos")
    body   = []
    for sess in sorted(session_data):
        vals = session_data[sess]
        avg  = round(sum(vals) / len(vals), 2)
        row  = (
            f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{sess}</td>'
            + _cell(str(avg), _pf_color(avg))
            + _cell(str(len(vals)), "#f8f9fa")
        )
        body.append(f"<tr>{row}</tr>")

    html  = _section_title("Session Performance Summary")
    html += '<p style="color:#666;font-size:12px">ORB signals are highly session-dependent. Cells show avg PF across all parameter combos that produced trades in that session.</p>'
    html += _table_wrap(header, body)
    return html


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_orb_discovery_overview(ctx: dict) -> str:
    """Render ORB discovery overview."""
    od_data: Optional[Dict] = None

    if "orb_discovery" in ctx:
        od_data = ctx["orb_discovery"]
    else:
        ao_od = ctx.get("all_options", {}).get("orb_discovery")
        if ao_od and isinstance(ao_od, dict) and "sweep_results" in ao_od:
            od_data = ao_od
        else:
            for pkg in (ctx.get("packages") or {}).values():
                derived = getattr(pkg, "metadata", {}).get("derived", {})
                if "orb_discovery" in derived:
                    od_data = derived["orb_discovery"]
                    break

    if not od_data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px"><strong>ORB Discovery:</strong> No results found. '
            'Enable <code>orb_discovery.enabled: true</code> in your report YAML.</div>'
        )

    results = od_data.get("sweep_results", [])
    n_run   = od_data.get("n_combinations_run", 0)
    n_res   = od_data.get("n_results", len(results))

    html = '<div style="font-family:Arial,sans-serif">'
    html += f'<p style="color:#666;font-size:13px">{n_run:,} combinations evaluated — {n_res:,} results</p>'
    html += _render_summary(results)
    html += _render_orb_minutes_heatmap(results)
    html += _render_range_filter_comparison(results)
    html += _render_session_breakdown(results)
    html += '</div>'
    return html
