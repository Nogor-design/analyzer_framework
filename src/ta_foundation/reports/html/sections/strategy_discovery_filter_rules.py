from __future__ import annotations

"""
Strategy Discovery — Filter Rules
====================================
HTML renderer for the filter_discovery results produced by filter_discovery.py.

Shows per-run exclusion filter candidates in a ranked table:
  - Filter condition string (EXCLUDE when …)
  - Score, PF after exclusion, WR after exclusion, Avg Profit after
  - PF delta / WR delta vs baseline
  - Trades removed, removal %, trades remaining
  - Excluded-trade profile (what we're throwing away)
  - By-condition-column breakdown
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 2, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return default


def _fmt_pct(v: Any, decimals: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except Exception:
        return default


def _delta_cell(v: Optional[float], decimals: int = 3) -> str:
    """Render a delta value with green/red colour."""
    if v is None:
        return "—"
    val = float(v)
    color = "#2ecc71" if val > 0 else ("#e74c3c" if val < 0 else "#888")
    sign  = "+" if val > 0 else ""
    return f'<span style="color:{color};font-weight:700">{sign}{val:.{decimals}f}</span>'


def _score_color(v: Optional[float]) -> str:
    if v is None:
        return "#888"
    if v >= 65:
        return "#2ecc71"
    if v >= 40:
        return "#f39c12"
    if v >= 20:
        return "#e67e22"
    return "#e74c3c"


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#888"
    if pf >= 1.5:
        return "#2ecc71"
    if pf >= 1.0:
        return "#f39c12"
    return "#e74c3c"


_CSS = """
<style>
.sdf-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdf-card  { background:rgba(255,255,255,0.035); border-radius:12px;
             padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdf-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
             letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdf-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdf-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdf-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdf-table tr:last-child td { border-bottom:none; }
.sdf-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdf-cond  { display:inline-block; padding:2px 7px; border-radius:10px;
             background:rgba(255,0,80,0.12); font-size:11px; margin:1px 2px;
             white-space:nowrap; color:#f87171; }
.sdf-muted { opacity:.65; font-size:.85rem; }
.sdf-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sdf-baseline { display:flex; flex-wrap:wrap; gap:12px; padding:8px 0; }
.sdf-bs-stat { min-width:80px; }
.sdf-bs-val { font-size:1rem; font-weight:700; }
.sdf-bs-lbl { font-size:.7rem; opacity:.6; }
.sdf-excl-profile { font-size:11px; opacity:.7; white-space:nowrap; }
</style>
"""


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_baseline(baseline: Dict[str, Any]) -> str:
    if not baseline:
        return ""
    stats = [
        (_fmt(baseline.get("n_trades"), 0),         "Total Trades"),
        (_fmt_pct(baseline.get("win_rate")),         "Win Rate"),
        (_fmt(baseline.get("profit_factor")),        "Profit Factor"),
        (_fmt(baseline.get("avg_profit")),           "Avg Profit"),
    ]
    items = "".join(
        f'<div class="sdf-bs-stat">'
        f'<div class="sdf-bs-val">{v}</div>'
        f'<div class="sdf-bs-lbl">{html.escape(label)}</div>'
        f'</div>'
        for v, label in stats
    )
    return f'<div class="sdf-lbl">Baseline (all trades)</div><div class="sdf-baseline">{items}</div>'


def _render_excluded_profile(profile: Optional[Dict[str, Any]]) -> str:
    if not profile:
        return ""
    pf  = _fmt(profile.get("pf"))
    wr  = _fmt_pct(profile.get("wr"))
    avg = _fmt(profile.get("avg_profit"))
    n   = profile.get("n_trades", "?")
    return (
        f'<div class="sdf-excl-profile">'
        f'excl: PF {pf} · WR {wr} · avg ${avg} · n={n}'
        f'</div>'
    )


def _render_filters_table(top_filters: List[Dict[str, Any]]) -> str:
    if not top_filters:
        return '<div class="sdf-muted">No qualifying filter candidates found.</div>'

    rows = ""
    for r in top_filters:
        rank     = r.get("rank", "—")
        score    = r.get("score")
        cond     = r.get("condition") or {}
        filt_str = r.get("filter_str") or cond.get("description", "?")
        pf_after = r.get("pf_after")
        wr_after = r.get("wr_after")
        pf_delta = r.get("pf_delta")
        wr_delta = r.get("wr_delta")
        n_rem    = r.get("n_removed", "—")
        rem_pct  = r.get("removal_pct")
        n_remain = r.get("n_remaining", "—")
        excl_pr  = r.get("excluded_profile")
        avg_af   = r.get("avg_profit_after")

        removal_str = (
            f'{n_rem} ({_fmt(rem_pct, 1)}%)'
            if rem_pct is not None else str(n_rem)
        )

        rows += f"""
        <tr>
          <td style="text-align:center;font-weight:900;opacity:.7">{rank}</td>
          <td>
            <span class="sdf-cond">{html.escape(str(filt_str)[:80])}</span>
            {_render_excluded_profile(excl_pr)}
          </td>
          <td style="text-align:center">
            <span style="color:{_score_color(score)};font-weight:700">{_fmt(score, 1)}</span>
          </td>
          <td style="color:{_pf_color(pf_after)};font-weight:700;text-align:center">{_fmt(pf_after)}</td>
          <td style="text-align:center">{_delta_cell(pf_delta)}</td>
          <td style="text-align:center">{_fmt_pct(wr_after)}</td>
          <td style="text-align:center">{_delta_cell(wr_delta, 4)}</td>
          <td style="text-align:center;opacity:.8">{_fmt(avg_af)}</td>
          <td style="text-align:center;opacity:.7">{removal_str}</td>
          <td style="text-align:center;opacity:.7">{n_remain}</td>
        </tr>"""

    return f"""
    <div class="sdf-lbl">Top Exclusion Filter Candidates</div>
    <div style="overflow-x:auto">
    <table class="sdf-table">
      <thead><tr>
        <th>#</th>
        <th>Condition (EXCLUDE when…)</th>
        <th>Score ▼</th>
        <th>PF After</th>
        <th>PF Δ</th>
        <th>WR After</th>
        <th>WR Δ</th>
        <th>Avg $ After</th>
        <th>Removed</th>
        <th>Remaining</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


def _render_by_type(by_type: Dict[str, Dict[str, Any]]) -> str:
    if not by_type:
        return ""
    cards = ""
    for col, info in sorted(by_type.items(), key=lambda x: -(x[1].get("score") or 0)):
        best_str  = html.escape(str(info.get("best_filter_str") or "")[:80])
        score     = _fmt(info.get("score"), 1)
        pf_delta  = _delta_cell(info.get("pf_delta"))
        wr_delta  = _delta_cell(info.get("wr_delta"), 4)
        n_rem     = info.get("n_removed", "?")
        cards += f"""
        <div style="background:rgba(255,255,255,0.04);border-radius:8px;
                    padding:8px 12px;margin:4px 0;
                    border:1px solid rgba(255,255,255,0.07)">
          <div style="font-size:12px;font-weight:700;margin-bottom:4px;opacity:.9">
            {html.escape(str(col))}
          </div>
          <div style="font-size:11px;padding:2px 0;opacity:.85">
            Score {score} &nbsp; PF Δ {pf_delta} &nbsp; WR Δ {wr_delta} &nbsp;
            removed {n_rem} &nbsp; {best_str}
          </div>
        </div>"""
    return f'<div class="sdf-lbl">Best Filter by Condition Column</div>{cards}'


def _render_diagnostics(diag: Dict[str, Any]) -> str:
    if not diag:
        return ""
    issues = diag.get("issues") or []
    issue_html = (
        '<ul style="margin:4px 0;padding-left:16px;font-size:11px;color:#e74c3c">'
        + "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
        + "</ul>"
        if issues else ""
    )
    lines = [
        f"Candidates: {diag.get('n_candidates', '—')}",
        f"Valid: {diag.get('n_valid', '—')}",
        f"Improving (score>50): {diag.get('n_improving_filters', '—')}",
        f"Profit col: {diag.get('profit_col_used', '—')}",
    ]
    best_str = diag.get("best_filter_str")
    if best_str:
        delta = diag.get("best_pf_delta")
        lines.append(
            f"Best: {str(best_str)[:60]}"
            + (f" (PF Δ {delta:+.3f})" if delta is not None else "")
        )
    return (
        f'<div class="sdf-lbl">Diagnostics</div>'
        f'<div style="font-size:11px;opacity:.6">{" &nbsp;·&nbsp; ".join(lines)}</div>'
        + issue_html
    )


def _render_run_filters(run_id: str, fd: Dict[str, Any]) -> str:
    if not fd:
        return f'<div class="sdf-muted">No filter discovery data for {html.escape(run_id)}.</div>'

    error = fd.get("error")
    if error:
        return (
            f'<div class="sdf-muted" style="color:#e74c3c">'
            f'Filter discovery error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    if fd.get("skipped"):
        return f'<div class="sdf-muted">Filter discovery skipped for {html.escape(run_id)}.</div>'

    parts = []
    baseline    = fd.get("baseline") or {}
    top_filters = fd.get("top_filters") or []
    by_type     = fd.get("by_condition_type") or {}
    diag        = fd.get("diagnostics") or {}

    parts.append(_render_baseline(baseline))
    parts.append(_render_filters_table(top_filters))
    if by_type:
        parts.append(_render_by_type(by_type))
    parts.append(_render_diagnostics(diag))

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_filter_rules(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"]["filter_discovery"].
    """
    packages    = ctx.get("packages") or {}
    options     = ctx.get("options") or {}

    max_runs      = int(options.get("max_runs", 5))
    show_all_runs = bool(options.get("show_all_runs", False))

    parts = [_CSS, '<div class="sdf-wrap">']

    shown = 0
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        if not show_all_runs and shown >= max_runs:
            break

        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        if not isinstance(sd, dict):
            continue
        fd = sd.get("filter_discovery")
        if fd is None:
            continue

        run_html = _render_run_filters(str(run_id), fd)
        short_id = html.escape(str(run_id)[:60])
        parts.append(f"""
        <div class="sdf-card">
          <details open>
            <summary class="sdf-run-hdr">{short_id}</summary>
            <div style="margin-top:8px">{run_html}</div>
          </details>
        </div>""")
        shown += 1

    if shown == 0:
        parts.append(
            '<div class="sdf-card sdf-muted">'
            'No filter discovery data found. '
            'Ensure strategy_discovery is enabled and filter_discovery ran without errors.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
