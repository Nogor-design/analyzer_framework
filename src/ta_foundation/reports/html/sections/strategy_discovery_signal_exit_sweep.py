from __future__ import annotations

"""
Strategy Discovery — Signal Corpus Exit Sweep
===============================================
Reads: pkg.metadata["derived"]["strategy_discovery"]["signal_exit_sweep"]
       (from the __market_discovery__ synthetic package)

Shows:
  1. Overall best stop/target card — corpus-optimised parameters
  2. Baseline percentile card — MAE/MFE percentile-derived reference
  3. Per-rule sweep table — best (stop, target, RR, WR, PF) per signal rule
  4. Top combos accordion — ranked grid cells for each rule
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.ses-wrap  { display:flex; flex-direction:column; gap:14px; }
.ses-card  { background:rgba(255,255,255,0.035); border-radius:12px;
             padding:14px 16px; border:1px solid rgba(255,255,255,0.07); }

/* Best-params banner */
.ses-best  { display:flex; flex-wrap:wrap; gap:20px; padding:8px 0 4px; }
.ses-kpi   { min-width:72px; }
.ses-kpi-val { font-size:1.1rem; font-weight:800; }
.ses-kpi-lbl { font-size:.65rem; opacity:.5; margin-top:2px; text-transform:uppercase; letter-spacing:.04em; }

/* Per-rule table */
.ses-table { border-collapse:collapse; width:100%; font-size:0.80rem; }
.ses-table th { background:rgba(255,255,255,0.06); padding:5px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.ses-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.ses-table tr:last-child td { border-bottom:none; }
.ses-table tr:hover td { background:rgba(255,255,255,0.025); }

/* Combo grid inside accordion */
.ses-grid { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
.ses-combo { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);
             border-radius:6px; padding:5px 8px; font-size:.72rem; white-space:nowrap; }
.ses-combo-best { border-color:rgba(104,211,145,0.4); background:rgba(72,187,120,0.08); }
.ses-combo-lbl  { font-size:.6rem; opacity:.5; }

.ses-lbl  { font-size:.72rem; font-weight:600; text-transform:uppercase;
            letter-spacing:.05em; opacity:.5; margin:10px 0 6px; }
.ses-muted { opacity:.55; font-size:.82rem; }
.ses-pill  { display:inline-block; padding:2px 8px; border-radius:8px;
             font-size:10px; font-weight:700; }
.ses-pill-green { background:rgba(72,187,120,0.2); color:#68d391; }
.ses-pill-yellow { background:rgba(237,137,54,0.2); color:#f6ad55; }
.ses-pill-red    { background:rgba(245,101,101,0.2); color:#fc8181; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, d: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{d}f}"
    except Exception:
        return default


def _fmt_pct(v: Any, d: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v) * 100:.{d}f}%"
    except Exception:
        return default


def _wr_pill(wr: Optional[float]) -> str:
    if wr is None:
        return "—"
    pct = float(wr)
    cls = (
        "ses-pill-green"  if pct >= 0.55 else
        "ses-pill-yellow" if pct >= 0.50 else
        "ses-pill-red"
    )
    return f'<span class="ses-pill {cls}">{pct * 100:.1f}%</span>'


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#888"
    f = float(pf)
    return "#68d391" if f >= 1.5 else ("#f6ad55" if f >= 1.0 else "#fc8181")


# ---------------------------------------------------------------------------
# Overall best card
# ---------------------------------------------------------------------------

def _render_overall_best(ses: Dict[str, Any]) -> str:
    ob = ses.get("overall_best") or {}
    if not ob or not ob.get("stop"):
        return '<div class="ses-muted">No sweep result available.</div>'

    stop   = ob.get("stop", "—")
    target = ob.get("target", "—")
    rr     = ob.get("rr", "—")
    n_vote = ob.get("n_rules_voting", 0)
    avg_wr = ob.get("avg_win_rate")
    avg_pf = ob.get("avg_profit_factor")
    avg_at = ob.get("avg_avg_ticks")
    source = ob.get("source", "")

    source_note = ""
    if source == "baseline_fallback":
        source_note = (
            '<span style="font-size:.65rem;color:#f6ad55;margin-left:8px;">'
            'baseline fallback (no rule filtering)'
            '</span>'
        )

    kpis = [
        (str(stop),           "Stop Ticks"),
        (str(target),         "Target Ticks"),
        (str(rr),             "R:R"),
        (_fmt_pct(avg_wr),    "Avg Win Rate"),
        (_fmt(avg_pf, 2),     "Avg PF"),
        (_fmt(avg_at, 1),     "Avg Ticks"),
        (str(n_vote),         "Rules Voting"),
    ]
    kpi_html = "".join(
        f'<div class="ses-kpi">'
        f'<div class="ses-kpi-val">{v}</div>'
        f'<div class="ses-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )

    return (
        '<div class="ses-lbl">Corpus-Optimised Stop / Target'
        f'{source_note}</div>'
        f'<div class="ses-best">{kpi_html}</div>'
    )


# ---------------------------------------------------------------------------
# Baseline percentile card
# ---------------------------------------------------------------------------

def _render_baseline(ses: Dict[str, Any]) -> str:
    bp = ses.get("baseline_params") or {}
    if not bp:
        return ""

    rows = []
    if bp.get("mae_p50") is not None:
        rows.append(f"MAE p50 = {_fmt(bp.get('mae_p50'))} ticks")
    if bp.get("mae_p75") is not None:
        rows.append(f"MAE p75 = {_fmt(bp.get('mae_p75'))} ticks")
    if bp.get("mfe_p50") is not None:
        rows.append(f"MFE p50 (wins) = {_fmt(bp.get('mfe_p50'))} ticks")
    if bp.get("mfe_p75") is not None:
        rows.append(f"MFE p75 (wins) = {_fmt(bp.get('mfe_p75'))} ticks")

    bc = bp.get("best_grid_combo") or {}
    if bc:
        bc_stop   = bc.get("stop", "—")
        bc_target = bc.get("target", "—")
        bc_wr     = bc.get("win_rate")
        bc_pf     = bc.get("profit_factor")
        rows.append(
            f"Best grid (no filter): stop={bc_stop} / target={bc_target} | "
            f"WR={_fmt_pct(bc_wr)} PF={_fmt(bc_pf, 2)}"
        )

    if not rows:
        return ""

    items = "".join(
        f'<span style="font-size:.75rem;opacity:.7;margin-right:12px;">{html.escape(r)}</span>'
        for r in rows
    )
    return (
        f'<div class="ses-lbl" style="margin-top:8px;">Percentile Reference (Full Corpus)</div>'
        f'<div style="padding:4px 0 6px">{items}</div>'
    )


# ---------------------------------------------------------------------------
# Per-rule sweep table
# ---------------------------------------------------------------------------

def _render_combo_grid(combos: List[Dict[str, Any]]) -> str:
    if not combos:
        return ""
    items = []
    for i, c in enumerate(combos):
        cls = "ses-combo ses-combo-best" if i == 0 else "ses-combo"
        wr  = _fmt_pct(c.get("win_rate"))
        pf  = _fmt(c.get("profit_factor"), 2)
        at  = _fmt(c.get("avg_ticks"), 1)
        items.append(
            f'<div class="{cls}">'
            f'<div>S:{c.get("stop","?")}/T:{c.get("target","?")}'
            f' RR:{c.get("rr","?")}</div>'
            f'<div class="ses-combo-lbl">WR:{wr} PF:{pf} avg:{at}t</div>'
            f'</div>'
        )
    return f'<div class="ses-grid">{"".join(items)}</div>'


def _render_per_rule_table(ses: Dict[str, Any]) -> str:
    rules = ses.get("per_rule_sweep") or []
    if not rules:
        return '<div class="ses-muted">No per-rule exit sweep data.</div>'

    rows = ""
    for r in rules:
        rule_str = html.escape(str(r.get("rule_str", ""))[:70])
        n_sig    = r.get("n_signals", "—")
        best_s   = r.get("best_stop", "—")
        best_t   = r.get("best_target", "—")
        best_rr  = _fmt(r.get("best_rr"), 2)
        best_wr  = r.get("best_win_rate")
        best_pf  = r.get("best_pf")
        best_at  = r.get("best_avg_ticks")
        combos   = r.get("top_combos") or []

        combos_html = _render_combo_grid(combos)
        inner = (
            f'<div style="font-size:.72rem;padding:6px 8px;">'
            f'{combos_html}'
            f'</div>'
        ) if combos_html else ""

        rows += f"""
        <tr>
          <td style="font-size:.75rem;max-width:220px">{rule_str}</td>
          <td style="text-align:center">{n_sig}</td>
          <td style="text-align:center;font-weight:700">{best_s}</td>
          <td style="text-align:center;font-weight:700">{best_t}</td>
          <td style="text-align:center">{best_rr}</td>
          <td style="text-align:center">{_wr_pill(best_wr)}</td>
          <td style="text-align:center;color:{_pf_color(best_pf)};font-weight:700">{_fmt(best_pf, 2)}</td>
          <td style="text-align:center;opacity:.7">{_fmt(best_at, 1)}</td>
        </tr>"""
        if inner:
            rows += f"""
        <tr>
          <td colspan="8" style="padding:0 0 8px 20px;border-bottom:1px solid rgba(255,255,255,0.06)">
            {inner}
          </td>
        </tr>"""

    return f"""
    <div class="ses-lbl" style="margin-top:10px;">Per-Rule Exit Sweep</div>
    <div style="overflow-x:auto">
    <table class="ses-table">
      <thead><tr>
        <th>Signal Rule</th>
        <th>N Signals</th>
        <th>Best Stop</th>
        <th>Best Target</th>
        <th>R:R</th>
        <th>Win Rate</th>
        <th>PF</th>
        <th>Avg Ticks</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _render_diagnostics(ses: Dict[str, Any]) -> str:
    diag = ses.get("diagnostics") or {}
    n_sig    = diag.get("n_signals", "?")
    n_swept  = diag.get("n_rules_swept", "?")
    n_found  = diag.get("n_rules_found", "?")
    issues   = diag.get("issues") or []
    parts = [f"Signals: {n_sig} · Rules swept: {n_swept} · Rules with results: {n_found}"]
    if issues:
        parts += issues
    return f'<div style="font-size:.68rem;opacity:.45;margin-top:8px">{" · ".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_signal_exit_sweep(ctx: Dict[str, Any]) -> str:
    """
    Render signal corpus exit sweep results.

    Reads from the __market_discovery__ synthetic package only.
    Shows corpus-optimised stop/target, baseline percentiles, and per-rule
    grid of (stop, target) combos ranked by composite score.
    """
    packages = ctx.get("packages") or {}

    parts = [_CSS, '<div class="ses-wrap">']
    found = False

    for run_id, pkg in packages.items():
        if not str(run_id).startswith("__market_discovery__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd  = meta.get("derived", {}).get("strategy_discovery", {})
        ses = sd.get("signal_exit_sweep") if isinstance(sd, dict) else None
        if ses is None:
            continue

        error = ses.get("error")
        if error:
            parts.append(
                f'<div class="ses-card" style="color:#fc8181">'
                f'Signal exit sweep error: {html.escape(str(error))}'
                f'</div>'
            )
            found = True
            break

        parts.append('<div class="ses-card">')
        parts.append(
            '<div style="font-size:.75rem;font-weight:800;text-transform:uppercase;'
            'letter-spacing:.06em;color:#68d391;margin-bottom:8px;">'
            'Signal Corpus Exit Parameter Sweep'
            '<span style="font-size:.65rem;opacity:.5;font-weight:400;margin-left:8px;">'
            'corpus-only · MAE/MFE simulation · no trade bias'
            '</span></div>'
        )
        parts.append(_render_overall_best(ses))
        parts.append(_render_baseline(ses))
        parts.append(_render_per_rule_table(ses))
        parts.append(_render_diagnostics(ses))
        parts.append('</div>')
        found = True
        break

    if not found:
        parts.append(
            '<div class="ses-card ses-muted">'
            'No signal exit sweep data found. '
            'Add <code>market_discovery</code> to <code>pattern_engine.scopes</code> '
            'and enable <code>strategy_discovery: enabled: true</code>.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
