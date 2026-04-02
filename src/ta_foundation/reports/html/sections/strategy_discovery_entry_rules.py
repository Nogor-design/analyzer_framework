from __future__ import annotations

"""
Strategy Discovery — Entry Rules
==================================
HTML renderer for the entry_discovery results produced by entry_discovery.py.

Shows per-run entry rule candidates in a ranked table:
  - Rule string (condition conjunction)
  - Score, Profit Factor, Win Rate, N Trades, WR Lift, Selectivity
  - Baseline comparison
  - By-condition-type breakdown
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
.sde-wrap  { display:flex; flex-direction:column; gap:12px; }
.sde-card  { background:rgba(255,255,255,0.035); border-radius:12px;
             padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sde-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
             letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sde-table { border-collapse:collapse; width:100%; font-size:0.85rem; }
.sde-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sde-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sde-table tr:last-child td { border-bottom:none; }
.sde-table tr:hover td { background:rgba(255,255,255,0.03); }
.sde-cond  { display:inline-block; padding:2px 7px; border-radius:10px;
             background:rgba(255,255,255,0.07); font-size:11px; margin:1px 2px;
             white-space:nowrap; }
.sde-muted { opacity:.65; font-size:.85rem; }
.sde-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sde-baseline { display:flex; flex-wrap:wrap; gap:12px; padding:8px 0; }
.sde-bs-stat { min-width:80px; }
.sde-bs-val { font-size:1rem; font-weight:700; }
.sde-bs-lbl { font-size:.7rem; opacity:.6; }
</style>
"""


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_conditions(conditions: List[Dict[str, Any]]) -> str:
    if not conditions:
        return "—"
    return " ".join(
        f'<span class="sde-cond">{html.escape(str(c.get("description", c.get("column", "?"))))}</span>'
        for c in conditions
    )


def _render_baseline(baseline: Dict[str, Any]) -> str:
    if not baseline:
        return ""
    stats = [
        (_fmt(baseline.get("n_trades_total"), 0), "Total Trades"),
        (_fmt_pct(baseline.get("win_rate")), "Win Rate"),
        (_fmt(baseline.get("profit_factor")), "Profit Factor"),
        (_fmt(baseline.get("avg_profit")), "Avg Profit"),
    ]
    items = "".join(
        f'<div class="sde-bs-stat">'
        f'<div class="sde-bs-val">{v}</div>'
        f'<div class="sde-bs-lbl">{html.escape(label)}</div>'
        f'</div>'
        for v, label in stats
    )
    return f'<div class="sde-lbl">Baseline (all trades)</div><div class="sde-baseline">{items}</div>'


def _render_rules_table(top_rules: List[Dict[str, Any]]) -> str:
    if not top_rules:
        return '<div class="sde-muted">No qualifying rule candidates found.</div>'

    rows = ""
    for r in top_rules:
        rank     = r.get("rank", "—")
        score    = r.get("score")
        conds    = r.get("conditions") or []
        pf       = r.get("profit_factor")
        wr       = r.get("win_rate")
        n        = r.get("n_trades")
        wr_lift  = r.get("wr_lift")
        sel      = r.get("selectivity")
        avg_p    = r.get("avg_profit")

        wr_lift_str = (
            f'<span style="color:{"#2ecc71" if (wr_lift or 0) > 0 else "#e74c3c"}">'
            f'{_fmt_pct(wr_lift, 1)}</span>'
            if wr_lift is not None else "—"
        )

        rows += f"""
        <tr>
          <td style="text-align:center;font-weight:900;opacity:.7">{rank}</td>
          <td>{_render_conditions(conds)}</td>
          <td style="text-align:center">
            <span style="color:{_score_color(score)};font-weight:700">{_fmt(score, 1)}</span>
          </td>
          <td style="color:{_pf_color(pf)};font-weight:700;text-align:center">{_fmt(pf)}</td>
          <td style="text-align:center">{_fmt_pct(wr)}</td>
          <td style="text-align:center">{n if n is not None else "—"}</td>
          <td style="text-align:center">{wr_lift_str}</td>
          <td style="text-align:center;opacity:.7">{_fmt_pct(sel)}</td>
          <td style="text-align:center;opacity:.7">{_fmt(avg_p)}</td>
        </tr>"""

    return f"""
    <div class="sde-lbl">Top Entry Rule Candidates</div>
    <div style="overflow-x:auto">
    <table class="sde-table">
      <thead><tr>
        <th>#</th>
        <th>Conditions</th>
        <th>Score ▼</th>
        <th>PF</th>
        <th>WR</th>
        <th>Trades</th>
        <th>WR Lift</th>
        <th>Select.</th>
        <th>Avg $</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


def _render_by_type(by_type: Dict[str, List[Dict[str, Any]]]) -> str:
    if not by_type:
        return ""
    cards = ""
    for ctype, rules in sorted(by_type.items()):
        rows = "".join(
            f'<div style="font-size:11px;padding:2px 0;opacity:.85">'
            f'<span style="color:{_pf_color(r.get("pf"))};font-weight:700">'
            f'PF {_fmt(r.get("pf"))}</span>'
            f' &nbsp; WR {_fmt_pct(r.get("wr"))} &nbsp; '
            f'({r.get("n_trades","?")} trades) &nbsp;'
            f'{html.escape(str(r.get("rule_str",""))[:70])}'
            f'</div>'
            for r in rules
        )
        cards += f"""
        <div style="background:rgba(255,255,255,0.04);border-radius:8px;
                    padding:8px 12px;margin:4px 0;
                    border:1px solid rgba(255,255,255,0.07)">
          <div style="font-size:12px;font-weight:700;margin-bottom:4px;opacity:.9">
            {html.escape(str(ctype))}
          </div>
          {rows}
        </div>"""

    return f'<div class="sde-lbl">Top Rules by Condition Type</div>{cards}'


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
        f"Atoms: {diag.get('n_atoms', '—')}",
        f"Candidates evaluated: {diag.get('n_candidates', '—')}",
        f"Valid candidates: {diag.get('n_valid_candidates', '—')}",
        f"Profit col: {diag.get('profit_col_used', '—')}",
    ]
    return (
        f'<div class="sde-lbl">Diagnostics</div>'
        f'<div style="font-size:11px;opacity:.6">{" &nbsp;·&nbsp; ".join(lines)}</div>'
        + issue_html
    )


def _render_run_entry(run_id: str, ed: Dict[str, Any]) -> str:
    if not ed:
        return f'<div class="sde-muted">No entry discovery data for {html.escape(run_id)}.</div>'

    error = ed.get("error")
    if error:
        return (
            f'<div class="sde-muted" style="color:#e74c3c">'
            f'Entry discovery error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    if ed.get("skipped"):
        return f'<div class="sde-muted">Entry discovery skipped for {html.escape(run_id)}.</div>'

    parts = []
    baseline  = ed.get("baseline") or {}
    top_rules = ed.get("top_rules") or []
    by_type   = ed.get("by_condition_type") or {}
    diag      = ed.get("diagnostics") or {}

    parts.append(_render_baseline(baseline))
    parts.append(_render_rules_table(top_rules))
    if by_type:
        parts.append(_render_by_type(by_type))
    parts.append(_render_diagnostics(diag))

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_entry_rules(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages") or {}
    options  = ctx.get("options") or {}

    max_runs    = int(options.get("max_runs", 5))
    show_all_runs = bool(options.get("show_all_runs", False))

    parts = [_CSS, '<div class="sde-wrap">']

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
        ed = sd.get("entry_discovery")
        if ed is None:
            continue

        run_html = _render_run_entry(str(run_id), ed)
        short_id = html.escape(str(run_id)[:60])
        parts.append(f"""
        <div class="sde-card">
          <details open>
            <summary class="sde-run-hdr">{short_id}</summary>
            <div style="margin-top:8px">{run_html}</div>
          </details>
        </div>""")
        shown += 1

    if shown == 0:
        parts.append(
            '<div class="sde-card sde-muted">'
            'No entry discovery data found. '
            'Ensure strategy_discovery is enabled and entry_discovery ran without errors.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
