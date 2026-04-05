from __future__ import annotations

"""
Bollinger Band Discovery Overview Section
============================================
Renders BB discovery results:
  1. Summary stat boxes
  2. Signal type × TF average PF heatmap
  3. Signal type × period average PF heatmap
  4. Direction comparison table
  5. Squeeze vs non-squeeze comparison (for bb_continuation and bb_squeeze_breakout)
"""

from typing import Any, Dict, List, Optional


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
    return (f'<h3 style="color:#2c3e50;border-bottom:2px solid #9b59b6;'
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


def _heatmap(results: List[Dict], row_key: str, col_key: str,
             row_label: str, col_label_fn=None, title: str = "") -> str:
    data: Dict[str, Dict[str, List[float]]] = {}
    for r in results:
        row_val = str(r.get(row_key, "?"))
        col_val = str(r.get(col_key) or r.get("params", {}).get(col_key, "?"))
        pf      = _safe_pf(r.get("metrics", {}).get("profit_factor"))
        if pf is None:
            continue
        data.setdefault(row_val, {}).setdefault(col_val, []).append(pf)

    if not data:
        return ""

    all_cols = sorted({c for vals in data.values() for c in vals})
    if col_label_fn:
        all_col_labels = [(c, col_label_fn(c)) for c in all_cols]
    else:
        all_col_labels = [(c, c) for c in all_cols]

    header = _header_cell(row_label) + "".join(_header_cell(lbl) for _, lbl in all_col_labels)
    body   = []
    for row_val in sorted(data):
        row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{row_val}</td>'
        for c, _ in all_col_labels:
            vals = data[row_val].get(c)
            if vals:
                avg = round(sum(vals) / len(vals), 2)
                row += _cell(str(avg), _pf_color(avg))
            else:
                row += _cell("—", "#f5f5f5")
        body.append(f"<tr>{row}</tr>")

    html = _section_title(title) if title else ""
    html += _table_wrap(header, body)
    return html


def render_bb_discovery_overview(ctx: dict) -> str:
    """Render Bollinger Band discovery overview."""
    bd_data: Optional[Dict] = None

    if "bb_discovery" in ctx:
        bd_data = ctx["bb_discovery"]
    else:
        ao_bd = ctx.get("all_options", {}).get("bb_discovery")
        if ao_bd and isinstance(ao_bd, dict) and "sweep_results" in ao_bd:
            bd_data = ao_bd
        else:
            for pkg in (ctx.get("packages") or {}).values():
                derived = getattr(pkg, "metadata", {}).get("derived", {})
                if "bb_discovery" in derived:
                    bd_data = derived["bb_discovery"]
                    break

    if not bd_data:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px"><strong>BB Discovery:</strong> No results found. '
            'Enable <code>bb_discovery.enabled: true</code> in your report YAML.</div>'
        )

    results = bd_data.get("sweep_results", [])
    n_run   = bd_data.get("n_combinations_run", 0)
    n_res   = bd_data.get("n_results", len(results))

    pfs    = [float(r["metrics"].get("profit_factor") or 0)
              for r in results if r.get("metrics", {}).get("profit_factor")]
    top_pf = round(max(pfs), 2) if pfs else 0.0
    avg_pf = round(sum(pfs) / len(pfs), 2) if pfs else 0.0

    html = '<div style="font-family:Arial,sans-serif">'
    html += f'<p style="color:#666;font-size:13px">{n_run:,} combinations evaluated — {n_res:,} results</p>'
    html += (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">'
        + _stat_box("Results",  str(n_res),  "#9b59b6")
        + _stat_box("Best PF",  str(top_pf), "#27ae60")
        + _stat_box("Avg PF",   str(avg_pf), "#3498db")
        + _stat_box("PF ≥ 1.0", str(sum(1 for p in pfs if p >= 1.0)), "#16a085")
        + _stat_box("PF ≥ 1.3", str(sum(1 for p in pfs if p >= 1.3)), "#e67e22")
        + '</div>'
    )

    html += _heatmap(results, "signal_id", "tf",
                     "BB Signal", col_label_fn=lambda x: f"{x}m",
                     title="BB Signal Type × Timeframe — Average PF")

    html += _heatmap(results, "signal_id", "direction_mode",
                     "BB Signal", title="BB Signal × Direction — Average PF")

    # Squeeze vs no-squeeze for relevant signals
    sq_results = [r for r in results if "require_prior_squeeze" in r.get("params", {})]
    if sq_results:
        html += _heatmap(sq_results, "signal_id", "require_prior_squeeze",
                         "Signal", col_label_fn=lambda x: f"Prior Squeeze: {x}",
                         title="Effect of Prior Squeeze Requirement")

    html += '</div>'
    return html
