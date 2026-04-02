from __future__ import annotations

"""
Strategy Discovery — MAE/MFE Profile
=======================================
HTML renderer for the mae_mfe_profile results produced by mae_mfe.py.

Two views:

1. Cross-run stop/target bounds comparison
   Aggregates exit_parameter_bounds across all runs into a single table:
   stop_min / stop_natural / stop_max and target equivalents, in both
   USD and points.  Ideal for quickly picking stop/target levels that
   work across all analysed strategies.

2. Per-run MAE/MFE distribution
   Percentile tables (p10 … p90) for mae_winners, mfe_all, etd_all;
   by-direction breakdown; exit parameter bounds; MFE/MAE ratio.

Section options (ctx["options"]):
  max_runs        : int  — per-run accordions shown (default 5)
  show_all_runs   : bool — show all runs (default false)
  show_cross_run  : bool — cross-run bounds comparison table (default true)
  show_per_run    : bool — per-run percentile tables (default true)
  show_direction  : bool — by-direction breakdown (default true)
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return default


def _ratio_color(v: Optional[float]) -> str:
    if v is None:
        return "#9ca3af"
    if v >= 2.0:
        return "#2ecc71"
    if v >= 1.5:
        return "#f39c12"
    return "#e74c3c"


def _pct_bar(vals: List[Optional[float]], max_v: float, width: int = 80) -> str:
    """Mini bar showing the spread p10…p90 as a range strip."""
    valid = [float(v) for v in vals if v is not None]
    if len(valid) < 2:
        return ""
    lo, hi = min(valid), max(valid)
    if max_v <= 0:
        return ""
    lo_pct = max(0, lo / max_v)
    hi_pct = min(1, hi / max_v)
    left_w  = int(lo_pct * width)
    range_w = max(4, int((hi_pct - lo_pct) * width))
    return (
        f'<span style="display:inline-block;width:{left_w}px"></span>'
        f'<span style="display:inline-block;width:{range_w}px;height:8px;'
        f'background:#6366f1;border-radius:3px;vertical-align:middle"></span>'
    )


_PERCENTILES = [10, 25, 50, 75, 90]

_CSS = """
<style>
.sdmm-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdmm-card  { background:rgba(255,255,255,0.035); border-radius:12px;
              padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdmm-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
              letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdmm-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdmm-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:right; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdmm-table th:first-child { text-align:left; }
.sdmm-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  text-align:right; vertical-align:middle; }
.sdmm-table td:first-child { text-align:left; }
.sdmm-table tr:last-child td { border-bottom:none; }
.sdmm-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdmm-muted  { opacity:.65; font-size:.85rem; }
.sdmm-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sdmm-kpi-row { display:flex; flex-wrap:wrap; gap:14px; margin:6px 0 10px; }
.sdmm-kpi { min-width:110px; }
.sdmm-kpi-val { font-size:1rem; font-weight:700; }
.sdmm-kpi-lbl { font-size:.7rem; opacity:.6; }
.sdmm-bounds-cell { font-weight:700; color:#a5b4fc; }
.sdmm-tag { display:inline-block; padding:1px 6px; border-radius:8px;
            font-size:10px; font-weight:700; margin-left:4px;
            background:rgba(99,102,241,0.15); color:#a5b4fc; }
</style>
"""


# ---------------------------------------------------------------------------
# Cross-run bounds comparison
# ---------------------------------------------------------------------------

def _render_cross_run_bounds(run_items: List[tuple]) -> str:
    if not run_items:
        return '<div class="sdmm-muted">No MAE/MFE data.</div>'

    # Build table: rows = [stop_min, stop_natural, stop_max, target_min, target_natural,
    #                       target_max, trail_activation, trail_distance, mfe_mae_ratio]
    bound_keys = [
        ("stop_min_usd",          "Stop Min ($)",          "stop_min_pts",          "pts"),
        ("stop_natural_usd",      "Stop Natural ($)",      "stop_natural_pts",      "pts"),
        ("stop_max_usd",          "Stop Max ($)",          "stop_max_pts",          "pts"),
        ("target_min_usd",        "Target Min ($)",        "target_min_pts",        "pts"),
        ("target_natural_usd",    "Target Natural ($)",    "target_natural_pts",    "pts"),
        ("target_max_usd",        "Target Max ($)",        "target_max_pts",        "pts"),
        ("trail_activation_usd",  "Trail Activation ($)",  "trail_activation_pts",  "pts"),
        ("trail_distance_usd",    "Trail Distance ($)",    "trail_distance_pts",    "pts"),
        ("mfe_mae_ratio",         "MFE/MAE Ratio",         None,                    None),
    ]

    run_ids = [r for r, _ in run_items]
    header_cells = "".join(
        f'<th style="text-align:right">{html.escape(str(rid)[:20])}</th>'
        for rid, _ in run_items
    )

    rows = ""
    for usd_key, label, pts_key, pts_label in bound_keys:
        cells = ""
        for _, mae_data in run_items:
            bounds = (mae_data.get("exit_parameter_bounds") or {})
            v = bounds.get(usd_key)
            v_pts = bounds.get(pts_key) if pts_key else None
            if v is not None:
                cell = f'<span class="sdmm-bounds-cell">${_fmt(v, 0)}</span>'
                if v_pts is not None:
                    cell += f'<span class="sdmm-tag">{_fmt(v_pts, 1)}pts</span>'
            elif usd_key == "mfe_mae_ratio" and v is None:
                v = bounds.get("mfe_mae_ratio")
                color = _ratio_color(v)
                cell = f'<span style="color:{color};font-weight:700">{_fmt(v, 2)}</span>' if v else "—"
            else:
                cell = "—"
            cells += f'<td>{cell}</td>'
        rows += f'<tr><td style="font-size:12px">{html.escape(label)}</td>{cells}</tr>'

    return (
        f'<div class="sdmm-lbl">Cross-Run Exit Parameter Bounds</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdmm-table">'
        f'<thead><tr><th>Parameter</th>{header_cells}</tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>'
        f'<div style="font-size:11px;opacity:.5;margin-top:4px">'
        f'stop_natural = MAE(winners) p80 · target_natural = MFE(all) p60</div>'
    )


# ---------------------------------------------------------------------------
# Per-run renderers
# ---------------------------------------------------------------------------

def _render_bounds_kpis(bounds: Dict[str, Any]) -> str:
    ratio = bounds.get("mfe_mae_ratio")
    kpis = [
        (f'${_fmt(bounds.get("stop_min_usd"), 0)}',       "Stop Min"),
        (f'${_fmt(bounds.get("stop_natural_usd"), 0)}',   "Stop Natural (p80)"),
        (f'${_fmt(bounds.get("stop_max_usd"), 0)}',       "Stop Max"),
        (f'${_fmt(bounds.get("target_min_usd"), 0)}',     "Target Min"),
        (f'${_fmt(bounds.get("target_natural_usd"), 0)}', "Target Natural (p60)"),
        (f'${_fmt(bounds.get("target_max_usd"), 0)}',     "Target Max"),
        (
            f'<span style="color:{_ratio_color(ratio)};font-weight:700">'
            f'{_fmt(ratio, 2)}</span>',
            "MFE/MAE Ratio"
        ),
    ]
    items = "".join(
        f'<div class="sdmm-kpi">'
        f'<div class="sdmm-kpi-val">{v}</div>'
        f'<div class="sdmm-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )
    return (
        f'<div class="sdmm-lbl">Exit Parameter Bounds</div>'
        f'<div class="sdmm-kpi-row">{items}</div>'
    )


def _render_percentile_table(dists: Dict[str, Any]) -> str:
    series_keys = [
        ("mae_winners", "MAE (winners)",   "How far winning trades moved against before winning"),
        ("mfe_all",     "MFE (all trades)", "Max favorable excursion before exit"),
        ("etd_all",     "ETD (all trades)", "End-trade drawdown from MFE peak"),
    ]
    rows = ""
    max_val = 1.0
    for key, label, _ in series_keys:
        vals = [dists.get(f"{key}_p{p}") for p in _PERCENTILES]
        valid = [float(v) for v in vals if v is not None]
        if valid:
            max_val = max(max_val, max(valid))

    for key, label, hint in series_keys:
        vals = [dists.get(f"{key}_p{p}") for p in _PERCENTILES]
        cells = "".join(
            f'<td>${_fmt(v, 0)}</td>' if v is not None else '<td>—</td>'
            for v in vals
        )
        bar_html = _pct_bar(vals, max_val)
        rows += f'<tr><td style="font-size:12px" title="{html.escape(hint)}">{html.escape(label)}</td>{cells}<td>{bar_html}</td></tr>'

    header_cells = "".join(
        f'<th>p{p}</th>' for p in _PERCENTILES
    )

    return (
        f'<div class="sdmm-lbl">MAE/MFE/ETD Percentile Distributions ($)</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdmm-table">'
        f'<thead><tr><th>Series</th>{header_cells}<th>Spread</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>'
    )


def _render_by_direction(by_direction: Dict[str, Any]) -> str:
    if not by_direction:
        return ""
    rows = ""
    for direction, info in sorted(by_direction.items()):
        if not info or info.get("n_trades", 0) == 0:
            rows += f'<tr><td>{html.escape(direction)}</td><td colspan="5" class="sdmm-muted">no trades</td></tr>'
            continue
        mae_p50 = info.get("mae_winners_p50")
        mae_p80 = info.get("mae_winners_p80")
        mfe_p60 = info.get("mfe_all_p60")
        mfe_p90 = info.get("mfe_all_p90")
        n       = info.get("n_trades", "—")
        rows += (
            f'<tr>'
            f'<td style="font-size:12px;font-weight:600">{html.escape(direction)}</td>'
            f'<td>${_fmt(mae_p50, 0)}</td>'
            f'<td>${_fmt(mae_p80, 0)}</td>'
            f'<td>${_fmt(mfe_p60, 0)}</td>'
            f'<td>${_fmt(mfe_p90, 0)}</td>'
            f'<td style="opacity:.6">{n}</td>'
            f'</tr>'
        )
    return (
        f'<div class="sdmm-lbl">By Direction</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdmm-table">'
        f'<thead><tr><th>Direction</th>'
        f'<th>MAE p50</th><th>MAE p80</th>'
        f'<th>MFE p60</th><th>MFE p90</th>'
        f'<th>Trades</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>'
    )


def _render_run_mae_mfe(run_id: str, profile: Dict[str, Any], show_dir: bool) -> str:
    if not profile:
        return f'<div class="sdmm-muted">No MAE/MFE data for {html.escape(run_id)}.</div>'

    error = profile.get("error")
    if error:
        return (
            f'<div class="sdmm-muted" style="color:#e74c3c">'
            f'MAE/MFE error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    dists         = profile.get("distributions") or {}
    bounds        = profile.get("exit_parameter_bounds") or {}
    by_direction  = profile.get("by_direction") or {}
    diag          = profile.get("diagnostics") or {}

    parts = [
        _render_bounds_kpis(bounds),
        _render_percentile_table(dists),
    ]
    if show_dir and by_direction:
        parts.append(_render_by_direction(by_direction))

    n_trades   = diag.get("n_trades", "—")
    n_winners  = diag.get("n_winners", "—")
    issues     = diag.get("issues") or []
    parts.append(
        f'<div style="font-size:11px;opacity:.5;margin-top:6px">'
        f'{n_trades} trades · {n_winners} winners</div>'
    )
    if issues:
        parts.append(
            "".join(
                f'<div style="font-size:11px;color:#e74c3c">⚠ {html.escape(str(i))}</div>'
                for i in issues
            )
        )

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_mae_mfe(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"]["mae_mfe_profile"].
    """
    packages      = ctx.get("packages") or {}
    options       = ctx.get("options") or {}

    max_runs       = int(options.get("max_runs", 5))
    show_all_runs  = bool(options.get("show_all_runs", False))
    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))
    show_dir       = bool(options.get("show_direction", True))

    parts = [_CSS, '<div class="sdmm-wrap">']

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        profile = sd.get("mae_mfe_profile") if isinstance(sd, dict) else None
        if profile is not None:
            run_items.append((str(run_id), profile))

    # Cross-run bounds table
    if show_cross_run and run_items:
        parts.append(
            f'<div class="sdmm-card">{_render_cross_run_bounds(run_items)}</div>'
        )

    # Per-run detail cards
    if show_per_run:
        shown = 0
        for run_id, profile in run_items:
            if not show_all_runs and shown >= max_runs:
                break
            run_html = _render_run_mae_mfe(run_id, profile, show_dir)
            short_id = html.escape(run_id[:60])
            parts.append(f"""
            <div class="sdmm-card">
              <details {"open" if shown == 0 else ""}>
                <summary class="sdmm-run-hdr">{short_id}</summary>
                <div style="margin-top:8px">{run_html}</div>
              </details>
            </div>""")
            shown += 1

    if not run_items:
        parts.append(
            '<div class="sdmm-card sdmm-muted">'
            'No MAE/MFE profile data found. '
            'Ensure strategy_discovery is enabled and MAE/MFE columns are present.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
