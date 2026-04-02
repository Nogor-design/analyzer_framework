from __future__ import annotations

"""
Strategy Discovery — Walk-Forward Validation
=============================================
HTML renderer for the validation results produced by validation.py.

Two views:

1. Cross-run gate summary table
   Each row = one run, columns = min_counts | degradation | t-test | monte_carlo | OVERALL
   Immediately shows which strategies survived all four statistical hard gates.

2. Per-run detail accordion
   IS vs OOS profit factor + degradation, t-test (t-stat / p-value),
   Monte Carlo drawdown (actual vs p95), and any failure issues.

Section options (ctx["options"]):
  max_runs         : int  — per-run accordion rows shown (default 10)
  show_all_runs    : bool — show all runs (default false)
  show_cross_run   : bool — cross-run gate table (default true)
  show_per_run     : bool — per-run detail cards (default true)
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


def _gate_badge(passed: Optional[bool], label: str = "") -> str:
    if passed is None:
        return (
            f'<span style="background:#374151;color:#9ca3af;border-radius:4px;'
            f'padding:2px 8px;font-size:11px;font-weight:700">?</span>'
        )
    if passed:
        return (
            f'<span style="background:#d1fae5;color:#065f46;border-radius:4px;'
            f'padding:2px 8px;font-size:11px;font-weight:700">PASS</span>'
        )
    return (
        f'<span style="background:#fee2e2;color:#991b1b;border-radius:4px;'
        f'padding:2px 8px;font-size:11px;font-weight:700">FAIL</span>'
    )


def _overall_badge(passed: Optional[bool]) -> str:
    if passed is None:
        return (
            f'<span style="background:#374151;color:#9ca3af;border-radius:6px;'
            f'padding:3px 10px;font-size:12px;font-weight:900">?</span>'
        )
    if passed:
        return (
            f'<span style="background:#065f46;color:#d1fae5;border-radius:6px;'
            f'padding:3px 10px;font-size:12px;font-weight:900">✓ PASS</span>'
        )
    return (
        f'<span style="background:#991b1b;color:#fee2e2;border-radius:6px;'
        f'padding:3px 10px;font-size:12px;font-weight:900">✗ FAIL</span>'
    )


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#9ca3af"
    if pf >= 1.3:
        return "#2ecc71"
    if pf >= 1.0:
        return "#f39c12"
    return "#e74c3c"


def _degrad_color(d: Optional[float]) -> str:
    """d = fraction degradation; negative = improvement."""
    if d is None:
        return "#9ca3af"
    if d <= 0.10:
        return "#2ecc71"
    if d <= 0.20:
        return "#f39c12"
    return "#e74c3c"


_CSS = """
<style>
.sdv-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdv-card  { background:rgba(255,255,255,0.035); border-radius:12px;
             padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdv-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
             letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdv-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdv-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdv-table td { padding:6px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdv-table tr:last-child td { border-bottom:none; }
.sdv-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdv-muted  { opacity:.65; font-size:.85rem; }
.sdv-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sdv-kpi-row { display:flex; flex-wrap:wrap; gap:14px; margin:6px 0 10px; }
.sdv-kpi { min-width:90px; }
.sdv-kpi-val { font-size:1rem; font-weight:700; }
.sdv-kpi-lbl { font-size:.7rem; opacity:.6; }
</style>
"""


# ---------------------------------------------------------------------------
# Cross-run gate table
# ---------------------------------------------------------------------------

def _render_cross_run_gate_table(run_items: List[tuple]) -> str:
    """
    run_items: [(run_id, validation_dict), ...]
    Builds cross-run gate table: run × [min_counts, degradation, t_test, mc, OVERALL].
    """
    if not run_items:
        return '<div class="sdv-muted">No validation data available.</div>'

    n_pass = sum(1 for _, v in run_items if v.get("passed") is True)
    n_fail = len(run_items) - n_pass

    rows = ""
    for run_id, val in run_items:
        if not val or val.get("error"):
            rows += (
                f'<tr><td style="font-size:12px">{html.escape(str(run_id)[:50])}</td>'
                f'<td colspan="5" class="sdv-muted">error or no data</td></tr>'
            )
            continue

        wf  = val.get("wf_results") or {}
        tt  = val.get("t_test") or {}
        mc  = val.get("monte_carlo") or {}
        ovr = val.get("passed")

        gates = {
            "min_counts":  wf.get("passed_min_counts"),
            "degradation": wf.get("passed_degradation"),
            "t_test":      tt.get("passed"),
            "monte_carlo": mc.get("passed"),
        }

        is_pf  = wf.get("is_pf")
        oos_pf = wf.get("oos_pf")
        pf_str = (
            f'<span style="color:{_pf_color(is_pf)}">{_fmt(is_pf)}</span>'
            f' → '
            f'<span style="color:{_pf_color(oos_pf)}">{_fmt(oos_pf)}</span>'
            if is_pf is not None else "—"
        )
        n_is  = wf.get("is_trades", "—")
        n_oos = wf.get("oos_trades", "—")

        rows += (
            f'<tr>'
            f'<td style="font-size:12px;font-weight:600">{html.escape(str(run_id)[:50])}</td>'
            f'<td style="text-align:center">{_gate_badge(gates["min_counts"])}</td>'
            f'<td style="text-align:center">{_gate_badge(gates["degradation"])}</td>'
            f'<td style="text-align:center">{_gate_badge(gates["t_test"])}</td>'
            f'<td style="text-align:center">{_gate_badge(gates["monte_carlo"])}</td>'
            f'<td style="text-align:center">{_overall_badge(ovr)}</td>'
            f'<td style="text-align:right;white-space:nowrap">{pf_str}</td>'
            f'<td style="text-align:right;opacity:.6;font-size:11px">'
            f'IS {n_is} / OOS {n_oos}</td>'
            f'</tr>'
        )

    summary_line = (
        f'<div style="font-size:11px;opacity:.6;margin-bottom:8px">'
        f'{len(run_items)} run(s) evaluated &nbsp;·&nbsp; '
        f'<span style="color:#2ecc71;font-weight:700">{n_pass} PASS</span>'
        f' &nbsp;·&nbsp; '
        f'<span style="color:#e74c3c;font-weight:700">{n_fail} FAIL</span>'
        f'</div>'
    )

    table = (
        f'<div style="overflow-x:auto">'
        f'<table class="sdv-table">'
        f'<thead><tr>'
        f'<th>Run</th>'
        f'<th style="text-align:center">Min Counts</th>'
        f'<th style="text-align:center">IS→OOS Degrad.</th>'
        f'<th style="text-align:center">T-Test</th>'
        f'<th style="text-align:center">Monte Carlo</th>'
        f'<th style="text-align:center">Overall</th>'
        f'<th style="text-align:right">IS PF → OOS PF</th>'
        f'<th style="text-align:right">Trades</th>'
        f'</tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>'
    )

    return (
        f'<div class="sdv-lbl">Cross-Run Walk-Forward Validation Gates</div>'
        f'{summary_line}{table}'
    )


# ---------------------------------------------------------------------------
# Per-run detail renderers
# ---------------------------------------------------------------------------

def _render_wf_kpis(wf: Dict[str, Any]) -> str:
    is_pf  = wf.get("is_pf")
    oos_pf = wf.get("oos_pf")
    degrad = wf.get("oos_degradation")
    n_is   = wf.get("is_trades", "—")
    n_oos  = wf.get("oos_trades", "—")
    wf_type = wf.get("wf_type", "—")

    degrad_str = (
        f'<span style="color:{_degrad_color(degrad)};font-weight:700">'
        f'{_fmt(degrad, 3)}</span>'
        if degrad is not None else "—"
    )
    kpis = [
        (f'<span style="color:{_pf_color(is_pf)}">{_fmt(is_pf)}</span>', "IS Profit Factor"),
        (f'<span style="color:{_pf_color(oos_pf)}">{_fmt(oos_pf)}</span>', "OOS Profit Factor"),
        (degrad_str,      "OOS Degradation"),
        (str(n_is),       "IS Trades"),
        (str(n_oos),      "OOS Trades"),
        (str(wf_type),    "WF Type"),
    ]
    items = "".join(
        f'<div class="sdv-kpi">'
        f'<div class="sdv-kpi-val">{v}</div>'
        f'<div class="sdv-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )
    return f'<div class="sdv-lbl">Walk-Forward Summary</div><div class="sdv-kpi-row">{items}</div>'


def _render_t_test(tt: Dict[str, Any]) -> str:
    if not tt:
        return ""
    t_stat  = tt.get("t_stat")
    p_value = tt.get("p_value")
    passed  = tt.get("passed")
    n_valid = tt.get("n_valid", "—")

    p_color = "#2ecc71" if (p_value is not None and p_value < 0.05) else "#e74c3c"
    t_color = "#2ecc71" if (t_stat is not None and t_stat > 0) else "#e74c3c"

    kpis = [
        (f'<span style="color:{t_color}">{_fmt(t_stat, 3)}</span>', "t-statistic"),
        (f'<span style="color:{p_color}">{_fmt(p_value, 4)}</span>', "p-value"),
        (_gate_badge(passed),                                         "Gate"),
        (str(n_valid),                                                "Valid Trades"),
    ]
    items = "".join(
        f'<div class="sdv-kpi">'
        f'<div class="sdv-kpi-val">{v}</div>'
        f'<div class="sdv-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )
    note = (
        '<div style="font-size:11px;opacity:.5;margin-top:4px">'
        'Welch\'s t-test: p &lt; 0.05 AND t &gt; 0 required to pass</div>'
    )
    return f'<div class="sdv-lbl">T-Test (profit mean vs zero)</div><div class="sdv-kpi-row">{items}</div>{note}'


def _render_monte_carlo(mc: Dict[str, Any]) -> str:
    if not mc:
        return ""
    actual   = mc.get("actual_max_dd")
    p95      = mc.get("mc_dd_p95")
    passed   = mc.get("passed")
    n_sim    = mc.get("n_simulations", "—")

    dd_color = "#2ecc71" if (actual is not None and p95 is not None and actual < p95) else "#e74c3c"

    kpis = [
        (f'<span style="color:{dd_color}">${_fmt(actual, 0)}</span>',   "Actual Max DD"),
        (f'${_fmt(p95, 0)}',                                             "MC p95 Max DD"),
        (_gate_badge(passed),                                            "Gate"),
        (str(n_sim),                                                     "Simulations"),
    ]
    items = "".join(
        f'<div class="sdv-kpi">'
        f'<div class="sdv-kpi-val">{v}</div>'
        f'<div class="sdv-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in kpis
    )
    note = (
        '<div style="font-size:11px;opacity:.5;margin-top:4px">'
        'Actual max drawdown must be less than Monte Carlo p95 to pass</div>'
    )
    return (
        f'<div class="sdv-lbl">Monte Carlo Drawdown Test</div>'
        f'<div class="sdv-kpi-row">{items}</div>{note}'
    )


def _render_run_validation(run_id: str, val: Dict[str, Any]) -> str:
    if not val:
        return f'<div class="sdv-muted">No validation data for {html.escape(run_id)}.</div>'

    error = val.get("error")
    if error:
        return (
            f'<div class="sdv-muted" style="color:#e74c3c">'
            f'Validation error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    wf  = val.get("wf_results") or {}
    tt  = val.get("t_test") or {}
    mc  = val.get("monte_carlo") or {}
    issues = val.get("issues") or []

    parts = [
        _render_wf_kpis(wf),
        _render_t_test(tt),
        _render_monte_carlo(mc),
    ]

    if issues:
        issue_html = (
            f'<div class="sdv-lbl">Failure Reasons</div>'
            + "".join(
                f'<div style="font-size:12px;color:#e74c3c;margin:2px 0">'
                f'⚠ {html.escape(str(i))}</div>'
                for i in issues
            )
        )
        parts.append(issue_html)

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_validation(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"]["validation"].
    """
    packages      = ctx.get("packages") or {}
    options       = ctx.get("options") or {}

    max_runs       = int(options.get("max_runs", 10))
    show_all_runs  = bool(options.get("show_all_runs", False))
    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    parts = [_CSS, '<div class="sdv-wrap">']

    # Collect all run validation dicts
    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd  = meta.get("derived", {}).get("strategy_discovery", {})
        val = sd.get("validation") if isinstance(sd, dict) else None
        if val is not None:
            run_items.append((str(run_id), val))

    # Cross-run gate table
    if show_cross_run and run_items:
        cross_html = _render_cross_run_gate_table(run_items)
        parts.append(f'<div class="sdv-card">{cross_html}</div>')

    # Per-run detail cards
    if show_per_run:
        shown = 0
        for run_id, val in run_items:
            if not show_all_runs and shown >= max_runs:
                break
            passed = val.get("passed")
            run_html = _render_run_validation(run_id, val)
            short_id = html.escape(run_id[:60])
            # First card open; FAIL cards open too (need attention)
            open_attr = "open" if (shown == 0 or not passed) else ""
            parts.append(f"""
            <div class="sdv-card">
              <details {open_attr}>
                <summary class="sdv-run-hdr">
                  {short_id} &nbsp; {_overall_badge(passed)}
                </summary>
                <div style="margin-top:8px">{run_html}</div>
              </details>
            </div>""")
            shown += 1

    if not run_items:
        parts.append(
            '<div class="sdv-card sdv-muted">'
            'No validation data found. '
            'Ensure strategy_discovery is enabled and walk-forward validation ran.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
