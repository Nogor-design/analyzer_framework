from __future__ import annotations

"""
Strategy Discovery — Exit Policies
=====================================
HTML renderer for the exit_discovery results produced by exit_discovery.py.

Shows per-run exit policy sweep results:
  - Sweep summary (best policy, families evaluated, n combos tested)
  - Top policies ranked table (policy name, family, net ticks, PF, WR, max DD)
  - Best-by-family breakdown
  - Best-by-regime breakdown
  - Exit reason distribution for the top policy
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


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#888"
    if pf >= 1.5:
        return "#2ecc71"
    if pf >= 1.0:
        return "#f39c12"
    return "#e74c3c"


def _ticks_color(v: Optional[float]) -> str:
    if v is None:
        return "#888"
    if v > 0:
        return "#2ecc71"
    if v < 0:
        return "#e74c3c"
    return "#888"


# Map family names to short display labels and colours
_FAMILY_DISPLAY: Dict[str, tuple] = {
    "fixed_rr":     ("Fixed R:R",   "#6366f1"),
    "atr_trail":    ("ATR Trail",   "#0ea5e9"),
    "be_atr_trail": ("BE Trail",    "#8b5cf6"),
    "chandelier":   ("Chandelier",  "#f59e0b"),
    "giveback":     ("Giveback",    "#10b981"),
}


def _family_badge(family: str) -> str:
    label, color = _FAMILY_DISPLAY.get(str(family), (str(family), "#6b7280"))
    return (
        f'<span style="background:{color}22;color:{color};border-radius:4px;'
        f'padding:2px 6px;font-size:11px;font-weight:700;white-space:nowrap">'
        f'{html.escape(label)}</span>'
    )


_CSS = """
<style>
.sdep-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdep-card  { background:rgba(255,255,255,0.035); border-radius:12px;
              padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdep-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
              letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdep-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdep-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdep-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdep-table tr:last-child td { border-bottom:none; }
.sdep-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdep-policy { font-size:11px; font-family:monospace; opacity:.9; }
.sdep-muted  { opacity:.65; font-size:.85rem; }
.sdep-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sdep-kpi-row { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px; }
.sdep-kpi { min-width:90px; }
.sdep-kpi-val { font-size:1rem; font-weight:700; }
.sdep-kpi-lbl { font-size:.7rem; opacity:.6; }
.sdep-reason-bar { display:inline-block; background:#4b5563; height:8px;
                   border-radius:3px; vertical-align:middle; margin-left:4px; }
</style>
"""


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_sweep_summary(sweep: Dict[str, Any], diag: Dict[str, Any]) -> str:
    if not sweep:
        return ""
    best_policy = sweep.get("best_policy") or "—"
    best_fam    = sweep.get("best_family") or "—"
    best_ticks  = sweep.get("best_net_ticks")
    n_combos    = sweep.get("n_combos", 0)
    families    = sweep.get("families_evaluated") or []
    n_trades    = diag.get("n_trades", "—")

    kpis = [
        (f'<span style="color:{_ticks_color(best_ticks)}">{_fmt(best_ticks, 1)}</span>', "Best Net Ticks"),
        (html.escape(str(best_policy)[:40]),     "Best Policy"),
        (_family_badge(best_fam),                "Best Family"),
        (str(n_combos),                          "Combos Tested"),
        (str(n_trades),                          "Trades"),
    ]
    kpi_html = "".join(
        f'<div class="sdep-kpi">'
        f'<div class="sdep-kpi-val">{v}</div>'
        f'<div class="sdep-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )
    fam_badges = " ".join(_family_badge(f) for f in families)
    return (
        f'<div class="sdep-kpi-row">{kpi_html}</div>'
        f'<div style="font-size:11px;opacity:.6;margin-bottom:6px">'
        f'Families: {fam_badges}</div>'
    )


def _render_policy_table(ranking: List[Dict[str, Any]], top_n: int = 10) -> str:
    if not ranking:
        return '<div class="sdep-muted">No policy ranking data.</div>'

    rows = ""
    for r in ranking[:top_n]:
        rank       = r.get("rank", "—")
        policy     = html.escape(str(r.get("policy_name") or "—"))
        family     = r.get("family") or "—"
        net_ticks  = r.get("net_ticks")
        avg_ticks  = r.get("avg_ticks")
        pf         = r.get("profit_factor")
        wr         = r.get("win_rate")
        max_dd     = r.get("max_dd_ticks")
        n          = r.get("n_trades", "—")

        rows += f"""
        <tr>
          <td style="text-align:center;font-weight:900;opacity:.7">{rank}</td>
          <td class="sdep-policy">{policy}</td>
          <td>{_family_badge(family)}</td>
          <td style="text-align:right">
            <span style="color:{_ticks_color(net_ticks)};font-weight:700">{_fmt(net_ticks, 1)}</span>
          </td>
          <td style="text-align:right;opacity:.7">{_fmt(avg_ticks, 2)}</td>
          <td style="text-align:right;color:{_pf_color(pf)};font-weight:700">{_fmt(pf)}</td>
          <td style="text-align:right">{_fmt_pct(wr)}</td>
          <td style="text-align:right;opacity:.7">{_fmt(max_dd, 1)}</td>
          <td style="text-align:right;opacity:.6">{n}</td>
        </tr>"""

    return f"""
    <div class="sdep-lbl">Top Exit Policies (ranked by net ticks)</div>
    <div style="overflow-x:auto">
    <table class="sdep-table">
      <thead><tr>
        <th>#</th>
        <th>Policy</th>
        <th>Family</th>
        <th style="text-align:right">Net Ticks ▼</th>
        <th style="text-align:right">Avg Ticks</th>
        <th style="text-align:right">PF</th>
        <th style="text-align:right">WR</th>
        <th style="text-align:right">Max DD</th>
        <th style="text-align:right">Trades</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


def _render_best_by_family(best_by_family: Dict[str, Any]) -> str:
    if not best_by_family:
        return ""
    rows = ""
    for fam, info in sorted(best_by_family.items()):
        policy = html.escape(str(info.get("policy_name") or "—")[:50])
        net    = info.get("net_ticks")
        pf     = info.get("profit_factor")
        wr     = info.get("win_rate")
        rows += (
            f'<tr>'
            f'<td>{_family_badge(fam)}</td>'
            f'<td class="sdep-policy" style="font-size:11px">{policy}</td>'
            f'<td style="text-align:right;color:{_ticks_color(net)};font-weight:700">{_fmt(net, 1)}</td>'
            f'<td style="text-align:right;color:{_pf_color(pf)};font-weight:700">{_fmt(pf)}</td>'
            f'<td style="text-align:right">{_fmt_pct(wr)}</td>'
            f'</tr>'
        )
    return (
        f'<div class="sdep-lbl">Best Policy per Family</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdep-table">'
        f'<thead><tr><th>Family</th><th>Best Policy</th>'
        f'<th style="text-align:right">Net Ticks</th>'
        f'<th style="text-align:right">PF</th>'
        f'<th style="text-align:right">WR</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def _render_best_by_regime(best_by_regime: Dict[str, Any]) -> str:
    if not best_by_regime:
        return ""
    rows = ""
    for regime, info in sorted(best_by_regime.items()):
        policy = html.escape(str(info.get("best_policy") or "—")[:50])
        net    = info.get("net_ticks")
        wr     = info.get("win_rate")
        n      = info.get("n_trades", "—")
        rows += (
            f'<tr>'
            f'<td style="font-size:12px">{html.escape(str(regime))}</td>'
            f'<td class="sdep-policy" style="font-size:11px">{policy}</td>'
            f'<td style="text-align:right;color:{_ticks_color(net)};font-weight:700">{_fmt(net, 1)}</td>'
            f'<td style="text-align:right">{_fmt_pct(wr)}</td>'
            f'<td style="text-align:right;opacity:.6">{n}</td>'
            f'</tr>'
        )
    return (
        f'<div class="sdep-lbl">Best Policy per Market Regime</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdep-table">'
        f'<thead><tr><th>Regime</th><th>Best Policy</th>'
        f'<th style="text-align:right">Net Ticks</th>'
        f'<th style="text-align:right">WR</th>'
        f'<th style="text-align:right">Trades</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def _render_exit_reasons(ranking: List[Dict[str, Any]]) -> str:
    """Show exit reason distribution for the top policy."""
    if not ranking:
        return ""
    top = ranking[0]
    reasons = top.get("exit_reasons") or {}
    if not reasons:
        return ""
    total = sum(reasons.values()) or 1
    sorted_reasons = sorted(reasons.items(), key=lambda x: -x[1])
    rows = ""
    for reason, cnt in sorted_reasons:
        pct = cnt / total
        bar_w = max(4, int(pct * 120))
        rows += (
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px">'
            f'<span style="min-width:130px;opacity:.8">{html.escape(str(reason))}</span>'
            f'<span class="sdep-reason-bar" style="width:{bar_w}px"></span>'
            f'<span style="opacity:.6">{cnt} ({pct*100:.0f}%)</span>'
            f'</div>'
        )
    policy_name = html.escape(str(top.get("policy_name", "top policy")[:40]))
    return (
        f'<div class="sdep-lbl">Exit Reasons — {policy_name}</div>'
        f'<div>{rows}</div>'
    )


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
        f"Trades: {diag.get('n_trades', '—')}",
        f"Profit col: {diag.get('profit_col_used', '—')}",
    ]
    return (
        f'<div class="sdep-lbl">Diagnostics</div>'
        f'<div style="font-size:11px;opacity:.6">{" &nbsp;·&nbsp; ".join(lines)}</div>'
        + issue_html
    )


def _render_run_exit(run_id: str, ed: Dict[str, Any], top_n: int) -> str:
    if not ed:
        return f'<div class="sdep-muted">No exit discovery data for {html.escape(run_id)}.</div>'

    error = ed.get("error")
    if error:
        return (
            f'<div class="sdep-muted" style="color:#e74c3c">'
            f'Exit discovery error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    if not ed.get("enabled", True):
        return f'<div class="sdep-muted">Exit discovery disabled for {html.escape(run_id)}.</div>'

    sweep          = ed.get("sweep_summary") or {}
    ranking        = ed.get("policy_ranking") or []
    best_by_family = ed.get("best_by_family") or {}
    best_by_regime = ed.get("best_by_regime") or {}
    diag           = ed.get("diagnostics") or {}

    parts = [
        _render_sweep_summary(sweep, diag),
        _render_policy_table(ranking, top_n),
        _render_best_by_family(best_by_family),
        _render_best_by_regime(best_by_regime),
        _render_exit_reasons(ranking),
        _render_diagnostics(diag),
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_exit_policies(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"]["exit_discovery"].
    """
    packages    = ctx.get("packages") or {}
    options     = ctx.get("options") or {}

    max_runs      = int(options.get("max_runs", 5))
    show_all_runs = bool(options.get("show_all_runs", False))
    top_n_policies = int(options.get("top_n_policies", 10))

    parts = [_CSS, '<div class="sdep-wrap">']

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
        ed = sd.get("exit_discovery")
        if ed is None:
            continue

        run_html = _render_run_exit(str(run_id), ed, top_n_policies)
        short_id = html.escape(str(run_id)[:60])
        parts.append(f"""
        <div class="sdep-card">
          <details open>
            <summary class="sdep-run-hdr">{short_id}</summary>
            <div style="margin-top:8px">{run_html}</div>
          </details>
        </div>""")
        shown += 1

    if shown == 0:
        parts.append(
            '<div class="sdep-card sdep-muted">'
            'No exit discovery data found. '
            'Ensure strategy_discovery is enabled and exit_discovery ran without errors.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
