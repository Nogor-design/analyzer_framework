# src/ta_foundation/reports/html/sections/strategy_discovery_risk_metrics.py
"""
Strategy Discovery — Risk-Adjusted Metrics Section
===================================================
Renders Phase risk_metrics results: Sharpe, Sortino, Calmar, Omega ratios,
annualized return, total return, max DD, and downside deviation.

CSS prefix: .sdrm-
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdrm-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdrm-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }

.sdrm-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }
.sdrm-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 10px 16px; min-width: 120px; text-align: center; }
.sdrm-kpi-label { font-size: 10px; color: #888; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 3px; }
.sdrm-kpi-value { font-size: 20px; font-weight: 700; color: #e8e8e8; }
.sdrm-kpi-value.pos  { color: #22c55e; }
.sdrm-kpi-value.neg  { color: #ef4444; }
.sdrm-kpi-value.warn { color: #f59e0b; }
.sdrm-kpi-value.neutral { color: #94a3b8; }

.sdrm-table-wrap { overflow-x: auto; margin: 10px 0 20px; }
.sdrm-table { border-collapse: collapse; min-width: 700px; width: 100%; }
.sdrm-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdrm-table td { padding: 6px 10px; border-bottom: 1px solid #2a2a2a; color: #ccc;
                 font-size: 12px; }
.sdrm-table tr:hover td { background: #1a1a2a; }
.sdrm-table td.best { color: #86efac; font-weight: 700; }
.sdrm-table td.worst { color: #fca5a5; }

.sdrm-badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
              font-size: 10px; font-weight: 700; letter-spacing: .04em; }
.sdrm-badge.pass { background: #14532d; color: #86efac; }
.sdrm-badge.fail { background: #7f1d1d; color: #fca5a5; }
.sdrm-badge.warn { background: #451a03; color: #fcd34d; }

.sdrm-diag { font-size: 11px; color: #666; margin-top: 4px; }
.sdrm-diag-issue { color: #f87171; }
.sdrm-no-data { color: #666; font-style: italic; font-size: 12px; padding: 8px 0; }
.sdrm-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 14px 0 6px; }
.sdrm-ratio-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 14px; }
.sdrm-ratio-pill { display: flex; align-items: center; gap: 8px; background: #1a1a2a;
                   border: 1px solid #2a2a3a; border-radius: 6px; padding: 8px 14px; }
.sdrm-ratio-pill-name { font-size: 10px; color: #888; text-transform: uppercase;
                        letter-spacing: .05em; }
.sdrm-ratio-pill-val { font-size: 17px; font-weight: 700; }
</style>
"""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _pct(v: Any, decimals: int = 1) -> str:
    return _fmt(v, decimals, "%")


def _ratio_class(v: Any, thresholds: tuple = (0.5, 1.0)) -> str:
    """Colour ratio: neg → <0, warn → <low, pos → ≥high."""
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f < 0:
            return "neg"
        if f < thresholds[0]:
            return "warn"
        if f >= thresholds[1]:
            return "pos"
        return "warn"
    except (TypeError, ValueError):
        return "neutral"


def _ret_class(v: Any) -> str:
    if v is None:
        return "neutral"
    try:
        return "pos" if float(v) > 0 else "neg"
    except (TypeError, ValueError):
        return "neutral"


def _dd_class(v: Any) -> str:
    """Low DD is good."""
    if v is None:
        return "neutral"
    try:
        f = float(v)
        if f < 10:
            return "pos"
        if f < 20:
            return "warn"
        return "neg"
    except (TypeError, ValueError):
        return "neutral"


# ---------------------------------------------------------------------------
# Cross-run comparison table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    # Collect column values for best-highlighting
    metrics = ["sharpe_ratio", "sortino_ratio", "calmar_ratio", "omega_ratio",
               "annualized_return_pct", "max_drawdown_pct"]
    # higher_better[m] = True means bigger number is better
    higher_better = {
        "sharpe_ratio": True, "sortino_ratio": True, "calmar_ratio": True,
        "omega_ratio": True, "annualized_return_pct": True,
        "max_drawdown_pct": False,  # lower DD is better
    }

    # Pre-compute best per column
    col_vals: Dict[str, list] = {m: [] for m in metrics}
    for _, rm in run_items:
        if rm and not rm.get("skipped") and not rm.get("error"):
            for m in metrics:
                v = rm.get(m)
                if v is not None:
                    try:
                        col_vals[m].append(float(v))
                    except (TypeError, ValueError):
                        pass

    best: Dict[str, Optional[float]] = {}
    for m in metrics:
        vals = col_vals[m]
        if not vals:
            best[m] = None
        elif higher_better[m]:
            best[m] = max(vals)
        else:
            best[m] = min(vals)

    def _cell(m: str, v: Any) -> str:
        if v is None:
            return "<td>—</td>"
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return f"<td>{v}</td>"
        is_best = (best[m] is not None and abs(fv - best[m]) < 1e-9)
        cls = " class=\"best\"" if is_best else ""
        fmt = _pct(v) if "pct" in m else _fmt(v, 2)
        return f"<td{cls}>{fmt}</td>"

    rows_html = ""
    for run_id, rm in run_items:
        if not rm or rm.get("skipped") or rm.get("error"):
            msg = (rm or {}).get("error") or "skipped"
            rows_html += f"""
            <tr>
              <td style="color:#a78bfa">{run_id}</td>
              <td colspan="{len(metrics)}" style="color:#666;font-style:italic">{msg}</td>
            </tr>"""
            continue
        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          {_cell("sharpe_ratio",          rm.get("sharpe_ratio"))}
          {_cell("sortino_ratio",         rm.get("sortino_ratio"))}
          {_cell("calmar_ratio",          rm.get("calmar_ratio"))}
          {_cell("omega_ratio",           rm.get("omega_ratio"))}
          {_cell("annualized_return_pct", rm.get("annualized_return_pct"))}
          {_cell("max_drawdown_pct",      rm.get("max_drawdown_pct"))}
        </tr>"""

    return f"""
<div class="sdrm-section-label">Cross-Run Risk-Adjusted Comparison</div>
<div class="sdrm-table-wrap">
  <table class="sdrm-table">
    <thead>
      <tr>
        <th>Run</th>
        <th>Sharpe</th>
        <th>Sortino</th>
        <th>Calmar</th>
        <th>Omega</th>
        <th>Ann. Return</th>
        <th>Max DD</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


# ---------------------------------------------------------------------------
# Per-run renderers
# ---------------------------------------------------------------------------

def _ratio_pill(name: str, v: Any, thresholds: tuple = (0.5, 1.0)) -> str:
    cls = _ratio_class(v, thresholds)
    display = _fmt(v, 2)
    return f"""
    <div class="sdrm-ratio-pill">
      <div>
        <div class="sdrm-ratio-pill-name">{name}</div>
        <div class="sdrm-ratio-pill-val" style="color:var(--c)">{display}</div>
      </div>
    </div>""".replace(
        "var(--c)",
        "#22c55e" if cls == "pos" else "#f59e0b" if cls == "warn" else
        "#ef4444" if cls == "neg" else "#94a3b8"
    )


def _render_run_card(run_id: str, rm: dict) -> str:
    if not rm:
        return f'<div class="sdrm-run-header">{run_id}</div><div class="sdrm-no-data">No risk metrics data.</div>'
    if rm.get("skipped"):
        return f'<div class="sdrm-run-header">{run_id}</div><div class="sdrm-no-data">Risk metrics disabled.</div>'
    if rm.get("error"):
        return f'<div class="sdrm-run-header">{run_id}</div><div class="sdrm-no-data">Error: {rm["error"]}</div>'

    sharpe   = rm.get("sharpe_ratio")
    sortino  = rm.get("sortino_ratio")
    calmar   = rm.get("calmar_ratio")
    omega    = rm.get("omega_ratio")
    ann_ret  = rm.get("annualized_return_pct")
    tot_ret  = rm.get("total_return_pct")
    max_dd   = rm.get("max_drawdown_pct")
    dd_ann   = rm.get("downside_deviation")
    tpy      = rm.get("trades_per_year")
    net_p    = rm.get("net_profit")
    diag     = rm.get("diagnostics") or {}

    issues = diag.get("issues") or []
    issue_html = "".join(f'<span class="sdrm-diag-issue"> ⚠ {i}</span>' for i in issues)

    return f"""
<div class="sdrm-run-header">{run_id}</div>

<div class="sdrm-kpi-grid">
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Ann. Return</div>
    <div class="sdrm-kpi-value {_ret_class(ann_ret)}">{_pct(ann_ret)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Total Return</div>
    <div class="sdrm-kpi-value {_ret_class(tot_ret)}">{_pct(tot_ret)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Net Profit</div>
    <div class="sdrm-kpi-value {_ret_class(net_p)}">${float(net_p):,.0f}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Max DD</div>
    <div class="sdrm-kpi-value {_dd_class(max_dd)}">{_pct(max_dd)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Trades/yr</div>
    <div class="sdrm-kpi-value neutral">{_fmt(tpy, 0)}</div>
  </div>
</div>

<div class="sdrm-section-label">Risk-Adjusted Ratios</div>
<div class="sdrm-ratio-row">
  {_ratio_pill("Sharpe",  sharpe,  (0.5,  1.0))}
  {_ratio_pill("Sortino", sortino, (0.5,  1.5))}
  {_ratio_pill("Calmar",  calmar,  (0.3,  1.0))}
  {_ratio_pill("Omega",   omega,   (1.0,  2.0))}
</div>

<div class="sdrm-section-label">Additional</div>
<div class="sdrm-kpi-grid">
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Downside Dev.</div>
    <div class="sdrm-kpi-value neutral">{_fmt(dd_ann, 4)}</div>
  </div>
</div>

<div class="sdrm-diag">
  n={diag.get("n_trades","?")} &nbsp;|&nbsp;
  equity base: ${float(diag.get("initial_equity", 10000)):,.0f} &nbsp;|&nbsp;
  profit col: <code>{diag.get("profit_col_used","?")}</code>
  {issue_html}
</div>""" if net_p is not None else f"""
<div class="sdrm-run-header">{run_id}</div>
<div class="sdrm-kpi-grid">
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Ann. Return</div>
    <div class="sdrm-kpi-value {_ret_class(ann_ret)}">{_pct(ann_ret)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Total Return</div>
    <div class="sdrm-kpi-value {_ret_class(tot_ret)}">{_pct(tot_ret)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Max DD</div>
    <div class="sdrm-kpi-value {_dd_class(max_dd)}">{_pct(max_dd)}</div>
  </div>
  <div class="sdrm-kpi">
    <div class="sdrm-kpi-label">Trades/yr</div>
    <div class="sdrm-kpi-value neutral">{_fmt(tpy, 0)}</div>
  </div>
</div>
<div class="sdrm-section-label">Risk-Adjusted Ratios</div>
<div class="sdrm-ratio-row">
  {_ratio_pill("Sharpe",  sharpe,  (0.5,  1.0))}
  {_ratio_pill("Sortino", sortino, (0.5,  1.5))}
  {_ratio_pill("Calmar",  calmar,  (0.3,  1.0))}
  {_ratio_pill("Omega",   omega,   (1.0,  2.0))}
</div>
<div class="sdrm-diag">
  n={diag.get("n_trades","?")} &nbsp;|&nbsp; profit col: <code>{diag.get("profit_col_used","?")}</code>
  {issue_html}
</div>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_risk_metrics(ctx: dict) -> str:
    packages       = ctx.get("packages") or {}
    options        = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        rm = sd.get("risk_metrics") or {}
        run_items.append((run_id, rm))

    if not run_items:
        return f"{_CSS}<div class='sdrm-wrap'><div class='sdrm-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdrm-wrap">']

    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    if show_per_run:
        for run_id, rm in run_items:
            parts.append(_render_run_card(run_id, rm))

    parts.append("</div>")
    return "\n".join(parts)
