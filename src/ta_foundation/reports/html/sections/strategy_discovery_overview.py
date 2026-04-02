from __future__ import annotations

"""
Strategy Discovery Overview Section
=====================================
Renders per-run collapsible cards summarising strategy_discovery analysis:
  - Validation (IS/OOS walk-forward, t-test, Monte Carlo)
  - MAE/MFE Profile (percentile table + exit-parameter bounds)
  - Performance Breakdown (equity sparkline, Long/Short, session, regime)
  - Regime Analysis (daily regime table + aggregate distribution)

Data source:
  pkg.metadata["derived"]["strategy_discovery"]

Section options (ctx["options"]):
  show_regime     : bool – show regime section (default True)
  show_mae_mfe    : bool – show MAE/MFE section (default True)
  show_validation : bool – show validation section (default True)
  show_evaluation : bool – show evaluation section (default True)
  min_trades      : int  – skip runs with fewer trades (default 5)
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def _section_css() -> str:
    return """<style>
/* === Strategy Discovery Overview (.sd-) === */
.sd-block {
  padding: 16px !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
  line-height: 1.4 !important;
  color: #111827 !important;
}
.sd-block h2 {
  margin: 0 0 14px 0 !important;
  font-size: 20px !important;
  font-weight: 700 !important;
  color: #111827 !important;
}
.sd-block h3 {
  margin: 0 0 8px 0 !important;
  font-size: 15px !important;
  font-weight: 700 !important;
  color: #111827 !important;
}

/* Run card */
.sd-card {
  background: #ffffff !important;
  border: 1px solid #d1d5db !important;
  border-radius: 10px !important;
  padding: 16px !important;
  margin-bottom: 18px !important;
  box-shadow: 0 1px 4px rgba(0,0,0,0.07) !important;
  color: #111827 !important;
}
.sd-card * { color: #111827 !important; opacity: 1 !important; }

/* Header row */
.sd-header {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  flex-wrap: wrap !important;
  margin-bottom: 10px !important;
}
.sd-header-title {
  font-size: 16px !important;
  font-weight: 700 !important;
  color: #111827 !important;
}

/* Pass / fail badges */
.sd-pass {
  background: #d1fae5 !important;
  border: 1px solid #6ee7b7 !important;
  color: #065f46 !important;
  border-radius: 20px !important;
  padding: 3px 10px !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  white-space: nowrap !important;
}
.sd-fail {
  background: #fee2e2 !important;
  border: 1px solid #fca5a5 !important;
  color: #991b1b !important;
  border-radius: 20px !important;
  padding: 3px 10px !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  white-space: nowrap !important;
}

/* KPI row */
.sd-kpi-row {
  display: flex !important;
  gap: 10px !important;
  flex-wrap: wrap !important;
  margin-bottom: 12px !important;
}
.sd-kpi {
  background: #f9fafb !important;
  border: 1px solid #e5e7eb !important;
  border-radius: 8px !important;
  padding: 8px 14px !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  min-width: 110px !important;
  text-align: center !important;
  color: #111827 !important;
}
.sd-kpi .sd-kpi-label {
  font-size: 10px !important;
  font-weight: 500 !important;
  color: #6b7280 !important;
  display: block !important;
  margin-bottom: 3px !important;
}
.sd-kpi .sd-kpi-value {
  font-size: 14px !important;
  font-weight: 700 !important;
  display: block !important;
}

/* Tables */
table.sd-table {
  width: 100% !important;
  border-collapse: collapse !important;
  font-size: 12px !important;
  background: #ffffff !important;
  margin-top: 8px !important;
}
.sd-table th,
.sd-table td {
  padding: 7px 10px !important;
  border-bottom: 1px solid #e5e7eb !important;
  vertical-align: middle !important;
  color: #111827 !important;
}
.sd-table th {
  background: #f3f4f6 !important;
  font-weight: 700 !important;
  font-size: 11px !important;
  text-transform: uppercase !important;
  letter-spacing: 0.03em !important;
}
.sd-table tr:last-child td { border-bottom: none !important; }
.sd-table tr:nth-child(even) td { background: #fafafa !important; }

/* Two-column layout for MAE/MFE */
.sd-two-col {
  display: grid !important;
  grid-template-columns: 1fr 1fr !important;
  gap: 16px !important;
  margin-top: 10px !important;
}
@media (max-width: 700px) {
  .sd-two-col { grid-template-columns: 1fr !important; }
}

/* Exit bounds box */
.sd-bounds-box {
  background: #eff6ff !important;
  border: 1px solid #bfdbfe !important;
  border-radius: 8px !important;
  padding: 12px 14px !important;
  font-size: 12px !important;
}
.sd-bounds-box b {
  display: block !important;
  margin-bottom: 8px !important;
  font-size: 13px !important;
  color: #1d4ed8 !important;
}
.sd-bounds-row {
  display: flex !important;
  justify-content: space-between !important;
  padding: 3px 0 !important;
  border-bottom: 1px solid #dbeafe !important;
}
.sd-bounds-row:last-child { border-bottom: none !important; }

/* Sparkline */
.sd-sparkline {
  display: inline-block !important;
  vertical-align: middle !important;
}

/* Collapsible details */
details.sd-details {
  border: 1px solid #e5e7eb !important;
  border-radius: 8px !important;
  padding: 10px 14px !important;
  margin-bottom: 10px !important;
  background: #fdfdfd !important;
}
details.sd-details summary {
  cursor: pointer !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  color: #1d4ed8 !important;
  user-select: none !important;
  list-style: none !important;
}
details.sd-details summary::before { content: "▶ " !important; font-size: 10px !important; }
details.sd-details[open] summary::before { content: "▼ " !important; }

/* Info / warn */
.sd-warn {
  background: #fff7ed !important;
  border: 1px solid #fed7aa !important;
  border-radius: 6px !important;
  padding: 8px 12px !important;
  font-size: 12px !important;
  margin-bottom: 8px !important;
  color: #92400e !important;
}
.sd-info {
  background: #f0f9ff !important;
  border: 1px solid #bae6fd !important;
  border-radius: 6px !important;
  padding: 8px 12px !important;
  font-size: 12px !important;
  margin-bottom: 8px !important;
  color: #0c4a6e !important;
}
.sd-muted {
  color: #6b7280 !important;
  font-size: 11px !important;
}
.sd-section-label {
  font-size: 12px !important;
  font-weight: 700 !important;
  color: #374151 !important;
  margin: 12px 0 6px 0 !important;
}
</style>"""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_usd(v: Any) -> str:
    """Format as USD with commas, 0 decimal places. None → '—'."""
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "-" if f < 0 else ""
        return f"{sign}${abs(f):,.0f}"
    except Exception:
        return "—"


def _fmt_pct(v: Any, decimals: int = 1) -> str:
    """Format as percentage. None → '—'."""
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except Exception:
        return "—"


def _fmt_num(v: Any, decimals: int = 2) -> str:
    """Format as float. None → '—'."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return "—"


def _pf_color(pf: Any) -> str:
    """Return CSS color based on profit factor value."""
    try:
        f = float(pf)
        if f >= 1.5:
            return "#2ecc71"
        if f >= 1.0:
            return "#f39c12"
        return "#e74c3c"
    except Exception:
        return "#6b7280"


def _regime_label_color(regime: str) -> str:
    regime_lower = str(regime).lower()
    if "trending_up" in regime_lower or "trend_up" in regime_lower:
        return "#2ecc71"
    if "trending_down" in regime_lower or "trend_down" in regime_lower:
        return "#e74c3c"
    if "ranging" in regime_lower or "range" in regime_lower:
        return "#f39c12"
    if "high_vol" in regime_lower:
        return "#9b59b6"
    return "#3498db"


# ---------------------------------------------------------------------------
# Sparkline SVG
# ---------------------------------------------------------------------------

def _sparkline_svg(values: List[Any], width: int = 120, height: int = 30) -> str:
    """
    Render a tiny inline SVG polyline sparkline.
    Blue if final > initial, red otherwise.
    """
    if not values or len(values) < 2:
        return ""
    try:
        floats = [float(v) for v in values if v is not None]
    except Exception:
        return ""
    if len(floats) < 2:
        return ""

    mn = min(floats)
    mx = max(floats)
    rng = mx - mn if mx != mn else 1.0

    n = len(floats)
    pts = []
    for i, val in enumerate(floats):
        x = i / (n - 1) * width
        y = height - ((val - mn) / rng) * (height - 2) - 1
        pts.append(f"{x:.1f},{y:.1f}")

    color = "#3498db" if floats[-1] >= floats[0] else "#e74c3c"
    points_str = " ".join(pts)

    return (
        f'<svg class="sd-sparkline" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{points_str}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# Validation section
# ---------------------------------------------------------------------------

def _render_validation_section(validation: Dict[str, Any]) -> str:
    if not validation:
        return '<div class="sd-warn">Validation not run.</div>'

    passed = validation.get("passed", False)
    summary_text = html.escape(str(validation.get("summary", "")))
    issues = validation.get("issues") or []
    wf = validation.get("wf_results") or {}
    tt = validation.get("t_test") or {}
    mc = validation.get("monte_carlo") or {}

    badge = (
        '<span class="sd-pass">&#9650; PASS</span>'
        if passed else
        '<span class="sd-fail">&#10007; FAIL</span>'
    )

    # IS / OOS comparison table
    is_trades = wf.get("is_trades", "—")
    oos_trades = wf.get("oos_trades", "—")
    is_pf = wf.get("is_pf")
    oos_pf = wf.get("oos_pf")
    is_exp = wf.get("is_expectancy")
    oos_exp = wf.get("oos_expectancy")
    oos_deg = wf.get("oos_degradation")
    deg_passed = wf.get("passed_degradation", True)

    deg_color = "#2ecc71" if deg_passed else "#e74c3c"

    wf_table = ""
    if wf:
        wf_table = f"""
        <div class="sd-section-label">Walk-Forward (IS vs OOS)</div>
        <table class="sd-table">
          <thead><tr>
            <th>Metric</th><th>In-Sample</th><th>Out-of-Sample</th>
          </tr></thead>
          <tbody>
            <tr>
              <td>Trades</td>
              <td>{html.escape(str(is_trades))}</td>
              <td>{html.escape(str(oos_trades))}</td>
            </tr>
            <tr>
              <td>Profit Factor</td>
              <td style="color:{_pf_color(is_pf)};font-weight:700">{_fmt_num(is_pf)}</td>
              <td style="color:{_pf_color(oos_pf)};font-weight:700">{_fmt_num(oos_pf)}</td>
            </tr>
            <tr>
              <td>Expectancy</td>
              <td>{_fmt_usd(is_exp)}</td>
              <td>{_fmt_usd(oos_exp)}</td>
            </tr>
            <tr>
              <td>OOS Degradation</td>
              <td colspan="2" style="color:{deg_color};font-weight:700">
                {_fmt_pct(oos_deg)} {"&#10003;" if deg_passed else "&#10007;"}
              </td>
            </tr>
          </tbody>
        </table>"""

    # T-test row
    tt_row = ""
    if tt:
        t_stat = _fmt_num(tt.get("t_stat"))
        p_val = tt.get("p_value")
        p_str = f"{float(p_val):.4f}" if p_val is not None else "—"
        n_valid = tt.get("n_valid", "—")
        tt_passed = tt.get("passed", False)
        tt_color = "#2ecc71" if tt_passed else "#e74c3c"
        tt_row = f"""
        <div class="sd-section-label">T-Test (H0: mean trade = 0)</div>
        <table class="sd-table">
          <thead><tr><th>t-stat</th><th>p-value</th><th>n</th><th>Result</th></tr></thead>
          <tbody>
            <tr>
              <td>{t_stat}</td>
              <td>{p_str}</td>
              <td>{html.escape(str(n_valid))}</td>
              <td style="color:{tt_color};font-weight:700">
                {"PASS &#10003;" if tt_passed else "FAIL &#10007;"}
              </td>
            </tr>
          </tbody>
        </table>"""

    # Monte Carlo row
    mc_row = ""
    if mc:
        actual_dd = _fmt_usd(mc.get("actual_max_dd"))
        mc_dd_p95 = _fmt_usd(mc.get("mc_dd_p95"))
        dd_pct_rank = mc.get("dd_percentile_rank")
        dd_rank_str = f"{float(dd_pct_rank):.0f}th" if dd_pct_rank is not None else "—"
        n_sim = mc.get("n_simulations", "—")
        mc_passed = mc.get("passed", False)
        mc_color = "#2ecc71" if mc_passed else "#e74c3c"
        mc_row = f"""
        <div class="sd-section-label">Monte Carlo ({html.escape(str(n_sim))} simulations)</div>
        <table class="sd-table">
          <thead><tr>
            <th>Actual Max DD</th><th>MC DD p95</th><th>DD Percentile Rank</th><th>Result</th>
          </tr></thead>
          <tbody>
            <tr>
              <td>{actual_dd}</td>
              <td>{mc_dd_p95}</td>
              <td>{dd_rank_str}</td>
              <td style="color:{mc_color};font-weight:700">
                {"PASS &#10003;" if mc_passed else "FAIL &#10007;"}
              </td>
            </tr>
          </tbody>
        </table>"""

    issues_html = ""
    if issues:
        items = "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
        issues_html = f'<div class="sd-warn"><b>Issues:</b><ul style="margin:4px 0 0 16px">{items}</ul></div>'

    return f"""
    <div style="margin-bottom:8px">
      {badge}&nbsp;&nbsp;<span class="sd-muted">{summary_text}</span>
    </div>
    {issues_html}
    {wf_table}
    {tt_row}
    {mc_row}
    """


# ---------------------------------------------------------------------------
# MAE/MFE section
# ---------------------------------------------------------------------------

def _render_mae_mfe_section(profile: Dict[str, Any]) -> str:
    if not profile:
        return '<div class="sd-warn">MAE/MFE data not available.</div>'

    dist = profile.get("distributions") or {}
    bounds = profile.get("exit_parameter_bounds") or {}
    by_dir = profile.get("by_direction") or {}
    diag = profile.get("diagnostics") or {}

    # Left: percentile table
    pct_rows = ""
    pct_defs = [
        ("MAE Winners p50", dist.get("mae_winners_p50")),
        ("MAE Winners p80", dist.get("mae_winners_p80")),
        ("MAE Winners p90", dist.get("mae_winners_p90")),
        ("MFE All p60",     dist.get("mfe_all_p60")),
        ("MFE All p90",     dist.get("mfe_all_p90")),
        ("ETD All p50",     dist.get("etd_all_p50")),
        ("ETD All p80",     dist.get("etd_all_p80")),
    ]
    for label, val in pct_defs:
        val_str = _fmt_usd(val) if val is not None else "—"
        pct_rows += f"<tr><td>{html.escape(label)}</td><td>{val_str}</td></tr>"

    pct_table = f"""
    <div class="sd-section-label">Distribution Percentiles</div>
    <table class="sd-table">
      <thead><tr><th>Metric</th><th>Value</th></tr></thead>
      <tbody>{pct_rows}</tbody>
    </table>"""

    # Right: exit bounds box
    stop_usd = _fmt_usd(bounds.get("stop_natural_usd"))
    stop_pts = _fmt_num(bounds.get("stop_natural_pts"))
    tgt_usd  = _fmt_usd(bounds.get("target_natural_usd"))
    tgt_pts  = _fmt_num(bounds.get("target_natural_pts"))
    trail_act = _fmt_usd(bounds.get("trail_activation_usd"))
    trail_dist = _fmt_usd(bounds.get("trail_distance_usd"))
    mfe_mae_ratio = _fmt_num(bounds.get("mfe_mae_ratio"))

    bounds_box = f"""
    <div class="sd-bounds-box">
      <b>&#128683; Exit Parameter Bounds</b>
      <div class="sd-bounds-row"><span>Stop (natural)</span><span>{stop_usd} / {stop_pts} pts</span></div>
      <div class="sd-bounds-row"><span>Target (natural)</span><span>{tgt_usd} / {tgt_pts} pts</span></div>
      <div class="sd-bounds-row"><span>Trail activation</span><span>{trail_act}</span></div>
      <div class="sd-bounds-row"><span>Trail distance</span><span>{trail_dist}</span></div>
      <div class="sd-bounds-row"><span>MFE/MAE ratio</span><span><b>{mfe_mae_ratio}</b></span></div>
    </div>"""

    # Long vs Short
    dir_rows = ""
    for side in ("Long", "Short"):
        d = by_dir.get(side) or {}
        n = d.get("n_trades", "—")
        n_w = d.get("n_winners", "—")
        mae_p80 = _fmt_usd(d.get("mae_winners_p80"))
        dir_rows += f"<tr><td>{side}</td><td>{n}</td><td>{n_w}</td><td>{mae_p80}</td></tr>"

    dir_table = ""
    if dir_rows:
        dir_table = f"""
        <div class="sd-section-label">By Direction</div>
        <table class="sd-table">
          <thead><tr><th>Side</th><th>Trades</th><th>Winners</th><th>MAE Winners p80</th></tr></thead>
          <tbody>{dir_rows}</tbody>
        </table>"""

    diag_note = ""
    n_trades_d = diag.get("n_trades")
    mae_cov = diag.get("mae_coverage")
    if n_trades_d is not None:
        diag_note = f'<div class="sd-muted">n={n_trades_d} trades, MAE coverage={_fmt_pct(mae_cov)}</div>'

    return f"""
    <div class="sd-two-col">
      <div>
        {pct_table}
        {dir_table}
        {diag_note}
      </div>
      <div>
        {bounds_box}
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Evaluation section
# ---------------------------------------------------------------------------

def _render_evaluation_section(evaluation: Dict[str, Any]) -> str:
    if not evaluation:
        return '<div class="sd-info">Evaluation data not yet computed.</div>'

    n_trades    = evaluation.get("n_trades", 0)
    net_profit  = evaluation.get("net_profit")
    pf          = evaluation.get("profit_factor")
    wr          = evaluation.get("win_rate")
    avg_trade   = evaluation.get("avg_trade")
    max_dd      = evaluation.get("max_drawdown")
    avg_dur     = evaluation.get("avg_duration_min")
    equity_curve = evaluation.get("equity_curve") or []
    by_dir      = evaluation.get("by_direction") or {}
    by_session  = evaluation.get("by_session") or {}
    by_regime   = evaluation.get("by_regime") or {}

    # KPI row
    pf_color = _pf_color(pf)
    spark = _sparkline_svg(equity_curve) if equity_curve else ""
    kpi_row = f"""
    <div class="sd-kpi-row">
      <div class="sd-kpi">
        <span class="sd-kpi-label">Net Profit</span>
        <span class="sd-kpi-value">{_fmt_usd(net_profit)}</span>
      </div>
      <div class="sd-kpi">
        <span class="sd-kpi-label">Profit Factor</span>
        <span class="sd-kpi-value" style="color:{pf_color}">{_fmt_num(pf)}</span>
      </div>
      <div class="sd-kpi">
        <span class="sd-kpi-label">Win Rate</span>
        <span class="sd-kpi-value">{_fmt_pct(wr)}</span>
      </div>
      <div class="sd-kpi">
        <span class="sd-kpi-label">Avg Trade</span>
        <span class="sd-kpi-value">{_fmt_usd(avg_trade)}</span>
      </div>
      <div class="sd-kpi">
        <span class="sd-kpi-label">Max DD</span>
        <span class="sd-kpi-value" style="color:#e74c3c">{_fmt_usd(max_dd)}</span>
      </div>
      <div class="sd-kpi">
        <span class="sd-kpi-label">Avg Duration</span>
        <span class="sd-kpi-value">{_fmt_num(avg_dur, 1)} min</span>
      </div>
    </div>"""

    spark_row = ""
    if spark:
        spark_row = f'<div style="margin-bottom:10px"><span class="sd-muted">Equity curve: </span>{spark}</div>'

    # Long/Short table
    dir_rows = ""
    for side in ("Long", "Short"):
        d = by_dir.get(side) or {}
        nt = d.get("n_trades", "—")
        dpf = d.get("profit_factor")
        dwr = d.get("win_rate")
        dir_rows += (
            f'<tr><td>{side}</td><td>{nt}</td>'
            f'<td style="color:{_pf_color(dpf)};font-weight:700">{_fmt_num(dpf)}</td>'
            f'<td>{_fmt_pct(dwr)}</td></tr>'
        )
    dir_table = ""
    if dir_rows:
        dir_table = f"""
        <div class="sd-section-label">Long vs Short</div>
        <table class="sd-table">
          <thead><tr><th>Side</th><th>Trades</th><th>PF</th><th>Win Rate</th></tr></thead>
          <tbody>{dir_rows}</tbody>
        </table>"""

    # Session table — sorted by PF desc
    sess_rows = ""
    if by_session:
        sorted_sessions = sorted(
            by_session.items(),
            key=lambda kv: float(kv[1].get("profit_factor", 0) or 0),
            reverse=True,
        )
        for sess_name, s in sorted_sessions:
            nt = s.get("n_trades", "—")
            spf = s.get("profit_factor")
            swr = s.get("win_rate")
            sess_rows += (
                f'<tr><td>{html.escape(str(sess_name))}</td><td>{nt}</td>'
                f'<td style="color:{_pf_color(spf)};font-weight:700">{_fmt_num(spf)}</td>'
                f'<td>{_fmt_pct(swr)}</td></tr>'
            )
    sess_table = ""
    if sess_rows:
        sess_table = f"""
        <div class="sd-section-label">By Session</div>
        <table class="sd-table">
          <thead><tr><th>Session</th><th>Trades</th><th>PF</th><th>Win Rate</th></tr></thead>
          <tbody>{sess_rows}</tbody>
        </table>"""

    # Regime table from evaluation
    regime_rows = ""
    if by_regime:
        for rname, r in sorted(by_regime.items()):
            nt = r.get("n_trades", "—")
            rpf = r.get("profit_factor")
            rwr = r.get("win_rate")
            dot_color = _regime_label_color(rname)
            regime_rows += (
                f'<tr>'
                f'<td><span style="color:{dot_color};font-weight:700">&#9679;</span> '
                f'{html.escape(str(rname))}</td>'
                f'<td>{nt}</td>'
                f'<td style="color:{_pf_color(rpf)};font-weight:700">{_fmt_num(rpf)}</td>'
                f'<td>{_fmt_pct(rwr)}</td></tr>'
            )
    regime_eval_table = ""
    if regime_rows:
        regime_eval_table = f"""
        <div class="sd-section-label">By Regime</div>
        <table class="sd-table">
          <thead><tr><th>Regime</th><th>Trades</th><th>PF</th><th>Win Rate</th></tr></thead>
          <tbody>{regime_rows}</tbody>
        </table>"""

    return f"""
    {kpi_row}
    {spark_row}
    {dir_table}
    {sess_table}
    {regime_eval_table}
    """


# ---------------------------------------------------------------------------
# Regime section
# ---------------------------------------------------------------------------

def _render_regime_section(sd: Dict[str, Any]) -> str:
    regime_summary = sd.get("regime_summary") or []
    regime_issues  = sd.get("regime_issues") or []

    issues_html = ""
    if regime_issues:
        items = "".join(f"<li>{html.escape(str(i))}</li>" for i in regime_issues)
        issues_html = (
            f'<div class="sd-warn"><b>Regime issues:</b>'
            f'<ul style="margin:4px 0 0 16px">{items}</ul></div>'
        )

    if not regime_summary:
        if issues_html:
            return issues_html
        return '<div class="sd-muted">No regime summary data available.</div>'

    # Daily table
    day_rows = ""
    for entry in regime_summary:
        day_id     = entry.get("day_id", "—")
        dom_regime = entry.get("dominant_regime", "—")
        pct_trend  = entry.get("pct_trending")
        avg_adx    = entry.get("avg_adx")
        avg_atr    = entry.get("avg_atr")
        dot_color  = _regime_label_color(dom_regime)
        day_rows += (
            f'<tr>'
            f'<td>{html.escape(str(day_id))}</td>'
            f'<td><span style="color:{dot_color};font-weight:700">&#9679;</span> '
            f'{html.escape(str(dom_regime))}</td>'
            f'<td>{_fmt_pct(pct_trend)}</td>'
            f'<td>{_fmt_num(avg_adx, 1)}</td>'
            f'<td>{_fmt_num(avg_atr, 1)}</td>'
            f'</tr>'
        )

    day_table = f"""
    <div class="sd-section-label">Daily Regime Summary ({len(regime_summary)} days)</div>
    <table class="sd-table">
      <thead><tr>
        <th>Date</th><th>Dominant Regime</th>
        <th>% Trending</th><th>Avg ADX</th><th>Avg ATR</th>
      </tr></thead>
      <tbody>{day_rows}</tbody>
    </table>"""

    # Aggregate distribution
    regime_counts: Dict[str, int] = {}
    for entry in regime_summary:
        dr = str(entry.get("dominant_regime", "unknown"))
        regime_counts[dr] = regime_counts.get(dr, 0) + 1

    total_days = len(regime_summary)
    agg_rows = ""
    for rname, cnt in sorted(regime_counts.items(), key=lambda kv: kv[1], reverse=True):
        pct = cnt / total_days if total_days > 0 else 0
        dot_color = _regime_label_color(rname)
        agg_rows += (
            f'<tr>'
            f'<td><span style="color:{dot_color};font-weight:700">&#9679;</span> '
            f'{html.escape(rname)}</td>'
            f'<td>{cnt}</td>'
            f'<td>{pct * 100:.1f}%</td>'
            f'</tr>'
        )

    agg_table = f"""
    <div class="sd-section-label">Aggregate Regime Distribution</div>
    <table class="sd-table">
      <thead><tr><th>Regime</th><th>Days</th><th>% of Period</th></tr></thead>
      <tbody>{agg_rows}</tbody>
    </table>"""

    return f"""
    {issues_html}
    {day_table}
    {agg_table}
    """


# ---------------------------------------------------------------------------
# Feature Importance section
# ---------------------------------------------------------------------------

def _render_importance_section(imp: Dict[str, Any]) -> str:
    if not imp:
        return '<div class="sd-muted">No importance data.</div>'

    diag = imp.get("diagnostics") or {}
    top = imp.get("top_features") or []
    numeric = imp.get("numeric_correlations") or []
    cats = imp.get("categorical_effects") or []
    rf = imp.get("rf_importance") or []

    # Top features pill strip
    top_pills = "".join(
        f'<span style="background:rgba(52,152,219,0.2);border:1px solid #3498db;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;margin:2px">'
        f'{html.escape(str(f))}</span> '
        for f in top[:8]
    )
    top_section = f'<div class="sd-section-label">Top Features</div><div style="margin-bottom:8px">{top_pills}</div>' if top_pills else ""

    # Numeric correlations table (top 10)
    num_rows = ""
    for row in numeric[:10]:
        feat = row.get("feature", "")
        corr = row.get("correlation")
        abs_corr = row.get("abs_correlation")
        bar_w = int((abs_corr or 0) * 120)
        direction = "↑" if (corr or 0) > 0 else "↓"
        color = "#2ecc71" if (corr or 0) > 0 else "#e74c3c"
        num_rows += (
            f'<tr>'
            f'<td>{row.get("rank","—")}</td>'
            f'<td>{html.escape(str(feat))}</td>'
            f'<td style="color:{color};font-weight:700">{direction} {_fmt_num(corr, 3)}</td>'
            f'<td><div style="height:8px;width:{bar_w}px;background:{color};border-radius:3px;opacity:0.8"></div></td>'
            f'</tr>'
        )
    num_table = ""
    if num_rows:
        num_table = f"""
        <div class="sd-section-label">Numeric Correlations (vs win/loss)</div>
        <table class="sd-table">
          <thead><tr><th>#</th><th>Feature</th><th>Correlation</th><th>Magnitude</th></tr></thead>
          <tbody>{num_rows}</tbody>
        </table>"""

    # Categorical effects table (top 6)
    cat_rows_html = ""
    for row in cats[:6]:
        feat = row.get("feature", "")
        v = row.get("cramers_v")
        group_means = row.get("group_means") or {}
        best_group = max(group_means.items(), key=lambda x: x[1].get("mean_profit") or 0, default=(None, {}))
        worst_group = min(group_means.items(), key=lambda x: x[1].get("mean_profit") or 0, default=(None, {}))
        best_str = f'{html.escape(str(best_group[0]))} ({_fmt_num(best_group[1].get("mean_profit"), 0)})' if best_group[0] else "—"
        worst_str = f'{html.escape(str(worst_group[0]))} ({_fmt_num(worst_group[1].get("mean_profit"), 0)})' if worst_group[0] else "—"
        cat_rows_html += (
            f'<tr>'
            f'<td>{row.get("rank","—")}</td>'
            f'<td>{html.escape(str(feat))}</td>'
            f'<td style="font-weight:700">{_fmt_num(v, 3) if v is not None else "—"}</td>'
            f'<td style="color:#2ecc71;font-size:11px">{best_str}</td>'
            f'<td style="color:#e74c3c;font-size:11px">{worst_str}</td>'
            f'</tr>'
        )
    cat_table = ""
    if cat_rows_html:
        cat_table = f"""
        <div class="sd-section-label">Categorical Effects (Cramér's V)</div>
        <table class="sd-table">
          <thead><tr><th>#</th><th>Feature</th><th>V</th><th>Best Group</th><th>Worst Group</th></tr></thead>
          <tbody>{cat_rows_html}</tbody>
        </table>"""

    # RF importance table (top 8)
    rf_rows_html = ""
    for row in (rf or [])[:8]:
        feat = row.get("feature", "")
        imp_val = row.get("importance")
        bar_w = int((imp_val or 0) * 200)
        rf_rows_html += (
            f'<tr>'
            f'<td>{row.get("rank","—")}</td>'
            f'<td style="font-size:11px">{html.escape(str(feat))}</td>'
            f'<td>{_fmt_num(imp_val, 4)}</td>'
            f'<td><div style="height:8px;width:{min(bar_w, 120)}px;background:#3498db;border-radius:3px;opacity:0.7"></div></td>'
            f'</tr>'
        )
    rf_table = ""
    if rf_rows_html:
        rf_table = f"""
        <div class="sd-section-label">RandomForest Importance</div>
        <table class="sd-table">
          <thead><tr><th>#</th><th>Feature</th><th>Importance</th><th>Bar</th></tr></thead>
          <tbody>{rf_rows_html}</tbody>
        </table>"""

    diag_line = (
        f'<div class="sd-muted" style="font-size:11px;margin-top:6px">'
        f'n={diag.get("n_trades","?")} trades · {diag.get("n_features_analyzed","?")} features · '
        f'profit_col={html.escape(str(diag.get("profit_col","?")))} · '
        f'RF={"yes" if diag.get("rf_used") else "no"}'
        f'</div>'
    )

    return f"{top_section}{num_table}{cat_table}{rf_table}{diag_line}"


# ---------------------------------------------------------------------------
# Exit Discovery section
# ---------------------------------------------------------------------------

def _render_exit_discovery_section(exit_disc: Dict[str, Any]) -> str:
    if not exit_disc:
        return '<div class="sd-info">Exit discovery not yet run.</div>'

    err = exit_disc.get("error")
    if err:
        return f'<div class="sd-warn">Exit discovery error: {html.escape(str(err))}</div>'

    if not exit_disc.get("enabled", True):
        return '<div class="sd-muted">Exit discovery disabled.</div>'

    diag = exit_disc.get("diagnostics") or {}
    issues = diag.get("issues") or []
    sweep = exit_disc.get("sweep_summary") or {}
    ranking = exit_disc.get("policy_ranking") or []
    best_by_family = exit_disc.get("best_by_family") or {}
    best_by_regime = exit_disc.get("best_by_regime") or {}
    bounds = exit_disc.get("parameter_bounds_used") or {}

    issues_html = ""
    if issues:
        items = "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
        issues_html = f'<div class="sd-warn"><b>Issues:</b><ul style="margin:4px 0 0 16px">{items}</ul></div>'

    if not ranking:
        return issues_html + '<div class="sd-muted">No policy ranking data — simulation may have failed or returned no trades.</div>'

    # Sweep summary line
    best_policy = sweep.get("best_policy") or "—"
    best_net = sweep.get("best_net_ticks")
    n_combos = sweep.get("n_combos", diag.get("n_combos_evaluated", 0))
    families = sweep.get("families_evaluated") or []
    summary_line = (
        f'<div class="sd-muted" style="margin-bottom:8px">'
        f'Evaluated <b>{n_combos}</b> combos across {html.escape(", ".join(families))} families. '
        f'Best: <b>{html.escape(str(best_policy))}</b>'
        f'{f" ({_fmt_num(best_net)} ticks net)" if best_net is not None else ""}'
        f'</div>'
    )

    # Parameter bounds used
    bounds_rows = ""
    bounds_labels = [
        ("Stop (min / natural / max pts)", f'{_fmt_num(bounds.get("stop_min_pts"), 1)} / {_fmt_num(bounds.get("stop_natural_pts"), 1)} / {_fmt_num(bounds.get("stop_max_pts"), 1)}'),
        ("Target (min / natural / max pts)", f'{_fmt_num(bounds.get("target_min_pts"), 1)} / {_fmt_num(bounds.get("target_natural_pts"), 1)} / {_fmt_num(bounds.get("target_max_pts"), 1)}'),
        ("Trail activation / distance pts", f'{_fmt_num(bounds.get("trail_activation_pts"), 1)} / {_fmt_num(bounds.get("trail_distance_pts"), 1)}'),
        ("MFE/MAE ratio", _fmt_num(bounds.get("mfe_mae_ratio"))),
        ("Source", html.escape(str(bounds.get("source", "—")))),
    ]
    for label, val in bounds_labels:
        bounds_rows += f"<tr><td>{html.escape(label)}</td><td>{val}</td></tr>"
    bounds_table = f"""
    <div class="sd-section-label">Parameter Bounds (from MAE/MFE)</div>
    <table class="sd-table">
      <thead><tr><th>Bound</th><th>Value</th></tr></thead>
      <tbody>{bounds_rows}</tbody>
    </table>"""

    # Top-N ranking table
    rank_rows = ""
    for row in ranking:
        rank = row.get("rank", "—")
        name = row.get("policy_name", "—")
        family = row.get("family", "—")
        n = row.get("n_trades", "—")
        net = row.get("net_ticks")
        pf = row.get("profit_factor")
        wr = row.get("win_rate")
        dd = row.get("max_dd_ticks")
        net_color = "#2ecc71" if (net or 0) > 0 else "#e74c3c"
        rank_rows += (
            f'<tr>'
            f'<td>{rank}</td>'
            f'<td style="font-size:11px">{html.escape(str(name))}</td>'
            f'<td>{html.escape(str(family))}</td>'
            f'<td>{n}</td>'
            f'<td style="color:{net_color};font-weight:700">{_fmt_num(net, 1)}</td>'
            f'<td style="color:{_pf_color(pf)};font-weight:700">{_fmt_num(pf)}</td>'
            f'<td>{_fmt_pct(wr)}</td>'
            f'<td style="color:#e74c3c">{_fmt_num(dd, 1)}</td>'
            f'</tr>'
        )
    ranking_table = f"""
    <div class="sd-section-label">Policy Ranking (top {len(ranking)} combos)</div>
    <table class="sd-table">
      <thead><tr>
        <th>#</th><th>Policy</th><th>Family</th><th>Trades</th>
        <th>Net Ticks</th><th>PF</th><th>Win Rate</th><th>Max DD tks</th>
      </tr></thead>
      <tbody>{rank_rows}</tbody>
    </table>"""

    # Best by family (compact)
    fam_rows = ""
    for fam, row in sorted(best_by_family.items()):
        net = row.get("net_ticks")
        pf = row.get("profit_factor")
        wr = row.get("win_rate")
        net_color = "#2ecc71" if (net or 0) > 0 else "#e74c3c"
        fam_rows += (
            f'<tr>'
            f'<td>{html.escape(str(fam))}</td>'
            f'<td style="font-size:10px">{html.escape(str(row.get("policy_name", "—")))}</td>'
            f'<td style="color:{net_color};font-weight:700">{_fmt_num(net, 1)}</td>'
            f'<td style="color:{_pf_color(pf)};font-weight:700">{_fmt_num(pf)}</td>'
            f'<td>{_fmt_pct(wr)}</td>'
            f'</tr>'
        )
    family_table = ""
    if fam_rows:
        family_table = f"""
        <div class="sd-section-label">Best Per Family</div>
        <table class="sd-table">
          <thead><tr><th>Family</th><th>Best Policy</th><th>Net Ticks</th><th>PF</th><th>Win Rate</th></tr></thead>
          <tbody>{fam_rows}</tbody>
        </table>"""

    # Best by regime
    regime_rows = ""
    for regime_label, row in sorted(best_by_regime.items()):
        net = row.get("net_ticks")
        pf = row.get("profit_factor")
        wr = row.get("win_rate")
        dot_color = _regime_label_color(regime_label)
        net_color = "#2ecc71" if (net or 0) > 0 else "#e74c3c"
        regime_rows += (
            f'<tr>'
            f'<td><span style="color:{dot_color};font-weight:700">&#9679;</span> '
            f'{html.escape(str(regime_label))}</td>'
            f'<td style="font-size:10px">{html.escape(str(row.get("policy_name", "—")))}</td>'
            f'<td>{row.get("n_trades", "—")}</td>'
            f'<td style="color:{net_color};font-weight:700">{_fmt_num(net, 1)}</td>'
            f'<td style="color:{_pf_color(pf)};font-weight:700">{_fmt_num(pf)}</td>'
            f'<td>{_fmt_pct(wr)}</td>'
            f'</tr>'
        )
    regime_table = ""
    if regime_rows:
        regime_table = f"""
        <div class="sd-section-label">Best Policy by Regime</div>
        <table class="sd-table">
          <thead><tr>
            <th>Regime</th><th>Best Policy</th><th>Trades</th>
            <th>Net Ticks</th><th>PF</th><th>Win Rate</th>
          </tr></thead>
          <tbody>{regime_rows}</tbody>
        </table>"""

    return f"""
    {issues_html}
    {summary_line}
    {ranking_table}
    {family_table}
    {regime_table}
    {bounds_table}
    """


# ---------------------------------------------------------------------------
# Entry Discovery section
# ---------------------------------------------------------------------------

def _render_entry_discovery_section(ed: Dict[str, Any]) -> str:
    if not ed:
        return '<div class="sd-muted">No entry discovery data.</div>'

    diag      = ed.get("diagnostics") or {}
    top_rules = ed.get("top_rules") or []
    baseline  = ed.get("baseline") or {}
    by_type   = ed.get("by_condition_type") or {}
    issues    = diag.get("issues") or []

    if issues:
        items = "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
        issues_html = f'<div class="sd-warn"><b>Issues:</b><ul style="margin:4px 0 0 16px">{items}</ul></div>'
    else:
        issues_html = ""

    # Baseline line
    baseline_html = ""
    if baseline:
        bwr = baseline.get("win_rate")
        bpf = baseline.get("profit_factor")
        bn  = baseline.get("n_trades_total", "—")
        baseline_html = (
            f'<div class="sd-muted" style="margin-bottom:8px">'
            f'Baseline (all {bn} trades): WR {_fmt_pct(bwr)}  PF {_fmt_num(bpf)}'
            f'</div>'
        )

    if not top_rules:
        return issues_html + baseline_html + '<div class="sd-muted">No qualifying entry rule candidates found.</div>'

    # Diag summary line
    diag_line = (
        f'<div class="sd-muted" style="font-size:11px;margin-bottom:6px">'
        f'{diag.get("n_candidates","?")} candidates → {diag.get("n_valid_candidates","?")} qualifying '
        f'(min {diag.get("profit_col_used","profit_net")})'
        f'</div>'
    )

    # Top rules table
    rule_rows = ""
    for r in top_rules[:10]:
        conds    = r.get("conditions") or []
        cond_str = " AND ".join(
            html.escape(str(c.get("description", c.get("column", "?"))))
            for c in conds
        )
        cond_pills = " ".join(
            f'<span style="background:rgba(52,152,219,0.15);border:1px solid rgba(52,152,219,0.3);'
            f'border-radius:4px;padding:1px 6px;font-size:11px">{html.escape(str(c.get("description","?")))}</span>'
            for c in conds
        )
        score  = r.get("score")
        pf     = r.get("profit_factor")
        wr     = r.get("win_rate")
        n      = r.get("n_trades")
        lift   = r.get("wr_lift")
        lift_str = (
            f'<span style="color:{"#2ecc71" if (lift or 0) > 0 else "#e74c3c"}">'
            f'{_fmt_pct(lift)}</span>'
        ) if lift is not None else "—"

        rule_rows += (
            f'<tr>'
            f'<td>{r.get("rank","—")}</td>'
            f'<td>{cond_pills}</td>'
            f'<td style="font-weight:700;color:{_pf_color(pf)}">{_fmt_num(pf)}</td>'
            f'<td>{_fmt_pct(wr)}</td>'
            f'<td>{n if n is not None else "—"}</td>'
            f'<td>{lift_str}</td>'
            f'<td style="opacity:.7;font-size:11px">{_fmt_num(score, 1)}</td>'
            f'</tr>'
        )

    rules_table = f"""
    <div class="sd-section-label">Top Entry Conditions</div>
    <table class="sd-table">
      <thead><tr>
        <th>#</th><th>Conditions</th><th>PF</th>
        <th>Win Rate</th><th>Trades</th><th>WR Lift</th><th>Score</th>
      </tr></thead>
      <tbody>{rule_rows}</tbody>
    </table>"""

    # By-type summary (compact)
    type_pills = ""
    for ctype, rules in sorted(by_type.items()):
        best = rules[0] if rules else {}
        type_pills += (
            f'<span style="display:inline-block;margin:2px 4px;padding:3px 8px;'
            f'border-radius:10px;background:rgba(255,255,255,0.06);font-size:11px">'
            f'<b>{html.escape(str(ctype))}</b>'
            f' PF {_fmt_num(best.get("pf"))}'
            f' ({best.get("n_trades","?")} tr)'
            f'</span>'
        )
    type_html = f'<div class="sd-section-label">By Condition Type</div><div style="margin-bottom:6px">{type_pills}</div>' if type_pills else ""

    return f"{issues_html}{baseline_html}{diag_line}{rules_table}{type_html}"


# ---------------------------------------------------------------------------
# Filter Discovery section
# ---------------------------------------------------------------------------

def _render_filter_discovery_section(fd: Dict[str, Any]) -> str:
    if not fd:
        return '<div class="sd-muted">No filter discovery data.</div>'

    diag        = fd.get("diagnostics") or {}
    top_filters = fd.get("top_filters") or []
    baseline    = fd.get("baseline") or {}
    by_type     = fd.get("by_condition_type") or {}
    issues      = diag.get("issues") or []

    if issues:
        items = "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
        issues_html = f'<div class="sd-warn"><b>Issues:</b><ul style="margin:4px 0 0 16px">{items}</ul></div>'
    else:
        issues_html = ""

    baseline_html = ""
    if baseline:
        bwr = baseline.get("win_rate")
        bpf = baseline.get("profit_factor")
        bn  = baseline.get("n_trades_total", "—")
        baseline_html = (
            f'<div class="sd-muted" style="margin-bottom:8px">'
            f'Baseline (all {bn} trades): WR {_fmt_pct(bwr)}  PF {_fmt_num(bpf)}'
            f' &nbsp;·&nbsp; {diag.get("n_improving_filters", 0)} improving filters found'
            f'</div>'
        )

    if not top_filters:
        return issues_html + baseline_html + '<div class="sd-muted">No beneficial exclusion filters found.</div>'

    diag_line = (
        f'<div class="sd-muted" style="font-size:11px;margin-bottom:6px">'
        f'{diag.get("n_atoms","?")} conditions evaluated → '
        f'{diag.get("n_valid","?")} valid → '
        f'{diag.get("n_improving_filters","?")} improving'
        f'</div>'
    )

    # Top filters table
    filter_rows = ""
    for r in top_filters[:10]:
        cond        = r.get("condition") or {}
        desc        = cond.get("description", "—")
        score       = r.get("score")
        pf_after    = r.get("pf_after")
        wr_after    = r.get("wr_after")
        pf_delta    = r.get("pf_delta")
        wr_delta    = r.get("wr_delta")
        n_removed   = r.get("n_removed")
        removal_pct = r.get("removal_pct")
        excl        = r.get("excluded_profile") or {}

        pf_delta_str = (
            f'<span style="color:{"#2ecc71" if (pf_delta or 0) > 0 else "#e74c3c"}">'
            f'{"+" if (pf_delta or 0) > 0 else ""}{_fmt_num(pf_delta)}'
            f'</span>'
        ) if pf_delta is not None else "—"

        wr_delta_str = (
            f'<span style="color:{"#2ecc71" if (wr_delta or 0) > 0 else "#e74c3c"}">'
            f'{"+" if (wr_delta or 0) > 0 else ""}{_fmt_pct(wr_delta)}'
            f'</span>'
        ) if wr_delta is not None else "—"

        excl_label = (
            f'PF {_fmt_num(excl.get("pf"))} WR {_fmt_pct(excl.get("wr"))}'
            if excl else "—"
        )

        filter_rows += (
            f'<tr>'
            f'<td>{r.get("rank","—")}</td>'
            f'<td style="font-size:11px">'
            f'<span style="background:rgba(231,76,60,0.15);border:1px solid rgba(231,76,60,0.3);'
            f'border-radius:4px;padding:1px 6px">{html.escape(str(desc))}</span>'
            f'</td>'
            f'<td style="font-weight:700;color:{_pf_color(pf_after)}">{_fmt_num(pf_after)}</td>'
            f'<td>{_fmt_pct(wr_after)}</td>'
            f'<td>{pf_delta_str}</td>'
            f'<td>{wr_delta_str}</td>'
            f'<td style="opacity:.7;font-size:11px">'
            f'{n_removed} ({_fmt_num(removal_pct, 1)}%)'
            f'</td>'
            f'<td style="opacity:.6;font-size:10px">{excl_label}</td>'
            f'<td style="opacity:.7;font-size:11px">{_fmt_num(score, 1)}</td>'
            f'</tr>'
        )

    filters_table = f"""
    <div class="sd-section-label">Top Exclusion Filters</div>
    <table class="sd-table">
      <thead><tr>
        <th>#</th><th>Exclude When</th><th>PF After</th>
        <th>WR After</th><th>ΔPF</th><th>ΔWR</th>
        <th>Removed</th><th>Excluded Profile</th><th>Score</th>
      </tr></thead>
      <tbody>{filter_rows}</tbody>
    </table>"""

    # By-type summary
    type_pills = ""
    for ctype, best in sorted(by_type.items()):
        pd_val = best.get("pf_delta") or 0.0
        type_pills += (
            f'<span style="display:inline-block;margin:2px 4px;padding:3px 8px;'
            f'border-radius:10px;background:rgba(255,255,255,0.06);font-size:11px">'
            f'<b>{html.escape(str(ctype))}</b>'
            f' ΔPF {_fmt_num(pd_val)}'
            f' ({best.get("n_removed","?")} removed)'
            f'</span>'
        )
    type_html = (
        f'<div class="sd-section-label">By Condition Type</div>'
        f'<div style="margin-bottom:6px">{type_pills}</div>'
        if type_pills else ""
    )

    return f"{issues_html}{baseline_html}{diag_line}{filters_table}{type_html}"


# ---------------------------------------------------------------------------
# Classification section
# ---------------------------------------------------------------------------

def _render_classification_section(classif: Dict[str, Any]) -> str:
    if not classif:
        return '<div class="sd-muted">No classification data.</div>'

    label      = str(classif.get("label", "—"))
    score      = classif.get("automation_score")
    confidence = str(classif.get("confidence", "—"))
    signals    = classif.get("signals") or {}
    reasoning  = classif.get("reasoning") or []

    # Label colour
    label_colors = {
        "automated":        "#2ecc71",
        "hybrid":           "#f39c12",
        "semi_discretionary": "#e74c3c",
    }
    label_color = label_colors.get(label, "#888")

    # Score bar
    score_pct = int(score or 0)
    bar_color = label_color
    score_bar = (
        f'<div style="background:rgba(255,255,255,0.08);border-radius:6px;height:10px;margin:6px 0;width:100%">'
        f'<div style="height:10px;border-radius:6px;width:{score_pct}%;background:{bar_color}"></div></div>'
    )

    header = (
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">'
        f'<span style="font-size:1.1rem;font-weight:800;color:{label_color}">'
        f'{html.escape(label.replace("_", " ").title())}</span>'
        f'<span class="sd-muted">Score: {_fmt_num(score, 1)} / 100</span>'
        f'<span class="sd-muted">Confidence: {html.escape(confidence)}</span>'
        f'</div>'
        f'{score_bar}'
    )

    # Signals table
    signal_rows = ""
    signal_weights = {
        "has_settings": 25, "has_regime_data": 15, "pattern_coverage": 15,
        "pattern_predictive": 15, "session_spread": 7, "hold_time_consistency": 7,
        "session_wr_consistency": 8, "trade_count": 8,
    }
    for sig_key, sig_val in sorted(signals.items(), key=lambda kv: signal_weights.get(kv[0], 0), reverse=True):
        wt = signal_weights.get(sig_key, 0)
        pct = int((sig_val or 0) * 100)
        bar_w = int((sig_val or 0) * 80)
        s_color = "#2ecc71" if pct >= 70 else "#f39c12" if pct >= 40 else "#e74c3c"
        signal_rows += (
            f'<tr>'
            f'<td style="font-size:11px">{html.escape(sig_key.replace("_", " "))}</td>'
            f'<td style="text-align:right;font-size:11px;opacity:.6">{wt}pt</td>'
            f'<td><div style="height:6px;width:{bar_w}px;background:{s_color};border-radius:3px"></div></td>'
            f'<td style="font-size:11px;color:{s_color}">{pct}%</td>'
            f'</tr>'
        )
    signals_table = f"""
    <div class="sd-section-label">Signal Breakdown</div>
    <table class="sd-table">
      <thead><tr><th>Signal</th><th>Weight</th><th>Score</th><th></th></tr></thead>
      <tbody>{signal_rows}</tbody>
    </table>"""

    # Reasoning
    reason_items = "".join(
        f'<li style="font-size:12px;margin-bottom:2px">{html.escape(str(r))}</li>'
        for r in reasoning
    )
    reasoning_html = (
        f'<div class="sd-section-label">Reasoning</div>'
        f'<ul style="margin:4px 0;padding-left:16px">{reason_items}</ul>'
        if reason_items else ""
    )

    return f"{header}{signals_table}{reasoning_html}"


# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------

def _render_position_sizing_section(ps: Dict[str, Any]) -> str:
    if not ps:
        return '<div class="sd-muted">No position sizing data.</div>'

    trade_stats    = ps.get("trade_stats") or {}
    kelly_full     = ps.get("kelly_full")
    kelly_fracs    = ps.get("kelly_fractions") or []
    recommendation = ps.get("recommendation") or {}
    regime_cond    = ps.get("regime_conditional") or {}
    diag           = ps.get("diagnostics") or {}

    # --- trade stats KPIs ---
    kpi_items = [
        ("Trades",   trade_stats.get("n_trades")),
        ("Win Rate", f"{(trade_stats.get('win_rate') or 0)*100:.1f}%" if trade_stats.get("win_rate") is not None else "—"),
        ("Avg Win",  f"${_fmt_num(trade_stats.get('avg_win'), 0)}"),
        ("Avg Loss", f"${_fmt_num(trade_stats.get('avg_loss'), 0)}"),
        ("Payoff",   _fmt_num(trade_stats.get("payoff_ratio"), 2)),
        ("Expectancy", f"${_fmt_num(trade_stats.get('expectancy'), 0)}"),
    ]
    kpi_html = "".join(
        f'<div class="sd-kpi"><span class="sd-kpi-label">{lbl}</span>'
        f'<span class="sd-kpi-value">{val if val is not None else "—"}</span></div>'
        for lbl, val in kpi_items
    )
    stats_html = f'<div class="sd-kpi-row">{kpi_html}</div>'

    # --- recommendation banner ---
    rec_text = str(recommendation.get("reasoning") or "")
    rec_fok  = recommendation.get("recommended_fraction_of_kelly")
    rec_f    = recommendation.get("recommended_kelly_fraction")
    rec_label = (
        f"<b>Recommended: {rec_fok}× Kelly</b> (f = {rec_f:.3f})"
        if rec_fok is not None and rec_f is not None else "<b>No recommendation</b>"
    )
    rec_banner = (
        f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;'
        f'padding:10px 14px;margin-bottom:10px;font-size:12px">'
        f'{rec_label}<br><span style="color:#6b7280">{html.escape(rec_text)}</span></div>'
    )

    # Full Kelly row
    kelly_full_html = (
        f'<div style="font-size:12px;margin-bottom:6px">'
        f'<b>Full Kelly: </b>'
        f'<span style="font-size:1.0rem;font-weight:700;color:#2563eb">{_fmt_num(kelly_full, 4)}</span>'
        f'  <span class="sd-muted">(risk {_fmt_num((kelly_full or 0)*100, 1)}% of equity per trade at full Kelly)</span>'
        f'</div>'
        if kelly_full is not None else ""
    )

    # --- fraction table ---
    frac_rows = ""
    for fr in kelly_fracs:
        fok       = fr.get("fraction_of_kelly")
        f_val     = fr.get("kelly_fraction")
        exp_ret   = fr.get("expected_return_pct")
        p_ruin    = fr.get("p_ruin")
        p_dd      = fr.get("p_dd_warn")
        med_dd    = fr.get("median_max_drawdown_pct")
        p95_dd    = fr.get("p95_max_drawdown_pct")
        safe      = fr.get("safe", False)
        safe_pill = (
            '<span style="background:#d1fae5;border-radius:10px;padding:1px 7px;font-size:10px;color:#065f46">✓ safe</span>'
            if safe else
            '<span style="background:#fee2e2;border-radius:10px;padding:1px 7px;font-size:10px;color:#991b1b">✗ risky</span>'
        )
        ruin_color  = "#e74c3c" if (p_ruin or 0) >= 0.05 else "#111827"
        dd_color    = "#f39c12" if (p_dd or 0) >= 0.20 else "#111827"
        frac_rows += (
            f'<tr>'
            f'<td style="font-weight:700">{fok}× Kelly</td>'
            f'<td style="text-align:right">{_fmt_num(f_val, 4)}</td>'
            f'<td style="text-align:right">{_fmt_num(exp_ret, 1)}%</td>'
            f'<td style="text-align:right;color:{ruin_color}">{_fmt_num((p_ruin or 0)*100, 1)}%</td>'
            f'<td style="text-align:right;color:{dd_color}">{_fmt_num((p_dd or 0)*100, 1)}%</td>'
            f'<td style="text-align:right">{_fmt_num(med_dd, 1)}%</td>'
            f'<td style="text-align:right;opacity:.6">{_fmt_num(p95_dd, 1)}%</td>'
            f'<td style="text-align:center">{safe_pill}</td>'
            f'</tr>'
        )
    frac_table = (
        f'<div class="sd-section-label">Fractional Kelly — Monte Carlo ({diag.get("n_sim", "?")} sims)</div>'
        f'<table class="sd-table">'
        f'<thead><tr>'
        f'<th>Fraction</th><th style="text-align:right">f</th>'
        f'<th style="text-align:right">E[Return]</th>'
        f'<th style="text-align:right">P(Ruin)</th>'
        f'<th style="text-align:right">P(DD≥20%)</th>'
        f'<th style="text-align:right">Med Max-DD</th>'
        f'<th style="text-align:right">P95 Max-DD</th>'
        f'<th style="text-align:center">Safe</th>'
        f'</tr></thead>'
        f'<tbody>{frac_rows}</tbody>'
        f'</table>'
        if frac_rows else ""
    )

    # --- regime-conditional Kelly ---
    regime_rows = ""
    for reg_label, reg_data in sorted(regime_cond.items()):
        wr    = reg_data.get("win_rate")
        kf    = reg_data.get("kelly_full")
        kh    = reg_data.get("kelly_half")
        pr    = reg_data.get("payoff_ratio")
        n_tr  = reg_data.get("n_trades")
        wr_pct = f"{(wr or 0)*100:.1f}%" if wr is not None else "—"
        regime_rows += (
            f'<tr>'
            f'<td>{html.escape(str(reg_label))}</td>'
            f'<td style="text-align:right">{n_tr}</td>'
            f'<td style="text-align:right">{wr_pct}</td>'
            f'<td style="text-align:right">{_fmt_num(pr, 2)}</td>'
            f'<td style="text-align:right;font-weight:700">{_fmt_num(kf, 4)}</td>'
            f'<td style="text-align:right;opacity:.7">{_fmt_num(kh, 4)}</td>'
            f'</tr>'
        )
    regime_table = (
        f'<div class="sd-section-label" style="margin-top:12px">Regime-Conditional Kelly</div>'
        f'<table class="sd-table">'
        f'<thead><tr>'
        f'<th>Regime</th><th style="text-align:right">Trades</th>'
        f'<th style="text-align:right">WR</th>'
        f'<th style="text-align:right">Payoff</th>'
        f'<th style="text-align:right">Full Kelly</th>'
        f'<th style="text-align:right">Half Kelly</th>'
        f'</tr></thead>'
        f'<tbody>{regime_rows}</tbody>'
        f'</table>'
        if regime_rows else ""
    )

    issues = diag.get("issues") or []
    issues_html = (
        "<br>".join(f'<span class="sd-muted">⚠ {html.escape(str(i))}</span>' for i in issues)
        if issues else ""
    )

    return f"{stats_html}{rec_banner}{kelly_full_html}{frac_table}{regime_table}{issues_html}"


# ---------------------------------------------------------------------------
# Cohort Analysis
# ---------------------------------------------------------------------------

_TREND_COLORS = {
    "improving":  "#2ecc71",
    "stable":     "#6b7280",
    "degrading":  "#e74c3c",
    "volatile":   "#f39c12",
    "unknown":    "#9ca3af",
}

_DECAY_THRESHOLDS = [
    (0,  20,  "#2ecc71", "Healthy"),
    (20, 45,  "#f39c12", "Watch"),
    (45, 70,  "#e67e22", "Degrading"),
    (70, 101, "#e74c3c", "Decayed"),
]


def _decay_badge(score: Optional[int]) -> str:
    if score is None:
        return '<span class="sd-muted">n/a</span>'
    for lo, hi, color, label in _DECAY_THRESHOLDS:
        if lo <= score < hi:
            return (
                f'<span style="background:{color};color:#fff;border-radius:10px;'
                f'padding:2px 10px;font-size:11px;font-weight:700">'
                f'{label} ({score})</span>'
            )
    return f'<span class="sd-muted">{score}</span>'


def _trend_badge(classification: str) -> str:
    color = _TREND_COLORS.get(classification, "#9ca3af")
    return (
        f'<span style="background:{color};color:#fff;border-radius:10px;'
        f'padding:2px 10px;font-size:11px;font-weight:700">'
        f'{html.escape(classification.title())}</span>'
    )


def _mini_bar(value: Optional[float], scale: float = 3.0, color: str = "#2563eb") -> str:
    """Inline bar proportional to value (used for PF sparkline in table)."""
    if value is None or not (value == value):  # nan check
        return ""
    width = int(min(max(float(value) * scale, 1), 120))
    return f'<div style="height:6px;width:{width}px;background:{color};border-radius:3px;display:inline-block"></div>'


def _render_cohort_analysis_section(ca: Dict[str, Any]) -> str:
    if not ca:
        return '<div class="sd-muted">No cohort analysis data.</div>'

    cohorts    = ca.get("cohorts") or []
    trend      = ca.get("trend") or {}
    decay_score = ca.get("decay_score")
    evl        = ca.get("early_vs_late") or {}
    overall    = ca.get("overall") or {}
    diag       = ca.get("diagnostics") or {}

    trend_cls   = str(trend.get("classification", "unknown"))
    slope       = trend.get("slope")
    r2          = trend.get("r_squared")

    # --- header KPIs ---
    kpi_items = [
        ("Cohorts",     diag.get("n_cohorts")),
        ("Split by",    diag.get("cohort_by")),
        ("Overall PF",  _fmt_num(overall.get("pf"), 2)),
        ("Overall WR",  f"{(overall.get('win_rate') or 0)*100:.1f}%" if overall.get("win_rate") is not None else "—"),
        ("Trend",       trend_badge_inline := _trend_badge(trend_cls)),
        ("Decay Score", _decay_badge(decay_score)),
    ]
    kpi_html = "".join(
        f'<div class="sd-kpi"><span class="sd-kpi-label">{lbl}</span>'
        f'<span class="sd-kpi-value">{val if val is not None else "—"}</span></div>'
        for lbl, val in kpi_items
    )
    header_kpi = f'<div class="sd-kpi-row">{kpi_html}</div>'

    # Trend detail line
    slope_str = _fmt_num(slope, 4) if slope is not None else "—"
    r2_str    = _fmt_num(r2, 3) if r2 is not None else "—"
    trend_detail = (
        f'<div style="font-size:12px;margin-bottom:8px">'
        f'<b>Trend:</b> {_trend_badge(trend_cls)}'
        f'  <span class="sd-muted">slope={slope_str} PF/cohort, R²={r2_str}</span>'
        f'</div>'
    )

    # --- early vs late ---
    pf_delta  = evl.get("pf_delta")
    wr_delta  = evl.get("wr_delta")
    ap_delta  = evl.get("avg_profit_delta")

    def _delta_cell(v: Optional[float], decimals: int = 2, suffix: str = "") -> str:
        if v is None:
            return '<span class="sd-muted">—</span>'
        color = "#2ecc71" if v > 0 else ("#e74c3c" if v < 0 else "#6b7280")
        sign  = "+" if v > 0 else ""
        return f'<span style="color:{color};font-weight:700">{sign}{round(v, decimals)}{suffix}</span>'

    evl_html = f"""
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:10px;font-size:12px">
      <div><span class="sd-muted">Early PF:</span> <b>{_fmt_num(evl.get("early_pf"), 2)}</b>
           &nbsp;→&nbsp; <b>{_fmt_num(evl.get("late_pf"), 2)}</b>
           &nbsp;{_delta_cell(pf_delta)}</div>
      <div><span class="sd-muted">Early WR:</span> <b>{_fmt_num((evl.get("early_wr") or 0)*100, 1)}%</b>
           &nbsp;→&nbsp; <b>{_fmt_num((evl.get("late_wr") or 0)*100, 1)}%</b>
           &nbsp;{_delta_cell(wr_delta, 3)}</div>
      <div><span class="sd-muted">Avg Profit:</span> {_delta_cell(ap_delta, 0, "$" if ap_delta is None else "")}</div>
    </div>"""

    # --- cohort table ---
    has_regime = any(c.get("regime_mix") for c in cohorts)
    regime_th  = "<th>Top Regime</th>" if has_regime else ""

    cohort_rows = ""
    overall_pf = _safe_float(overall.get("pf"))

    for c in cohorts:
        idx    = c.get("cohort_index", "")
        pstart = c.get("period_start", "")
        pend   = c.get("period_end", "")
        n_tr   = c.get("n_trades")
        pf_val = c.get("pf")
        wr_val = c.get("win_rate")
        net    = c.get("net_profit")

        # Colour PF cell relative to overall
        if pf_val is not None and overall_pf is not None and overall_pf > 0:
            ratio = pf_val / overall_pf
            pf_color = "#2ecc71" if ratio >= 0.9 else ("#f39c12" if ratio >= 0.7 else "#e74c3c")
        else:
            pf_color = "#6b7280"

        pf_cell = (
            f'<span style="font-weight:700;color:{pf_color}">{_fmt_num(pf_val, 2)}</span>'
            f'&nbsp;{_mini_bar(pf_val, scale=30.0, color=pf_color)}'
            if pf_val is not None else '<span class="sd-muted">—</span>'
        )
        wr_cell = (
            f'{_fmt_num((wr_val or 0)*100, 1)}%' if wr_val is not None else "—"
        )
        net_color = "#2ecc71" if (net or 0) >= 0 else "#e74c3c"
        net_cell  = f'<span style="color:{net_color}">${_fmt_num(net, 0)}</span>'

        regime_td = ""
        if has_regime:
            rm = c.get("regime_mix") or {}
            top_reg = max(rm, key=rm.get) if rm else "—"
            top_pct = f"{rm[top_reg]*100:.0f}%" if rm else ""
            regime_td = f'<td style="font-size:11px">{html.escape(str(top_reg))} {top_pct}</td>'

        cohort_rows += (
            f'<tr>'
            f'<td style="text-align:right;opacity:.5">{idx}</td>'
            f'<td style="font-size:11px">{html.escape(str(pstart))}</td>'
            f'<td style="font-size:11px">{html.escape(str(pend))}</td>'
            f'<td style="text-align:right">{n_tr}</td>'
            f'<td>{pf_cell}</td>'
            f'<td style="text-align:right">{wr_cell}</td>'
            f'<td style="text-align:right">{net_cell}</td>'
            f'{regime_td}'
            f'</tr>'
        )

    cohort_table = (
        f'<div class="sd-section-label">Cohort Breakdown</div>'
        f'<div style="max-height:320px;overflow-y:auto">'
        f'<table class="sd-table">'
        f'<thead><tr>'
        f'<th>#</th><th>Start</th><th>End</th>'
        f'<th style="text-align:right">Trades</th>'
        f'<th>PF</th>'
        f'<th style="text-align:right">WR</th>'
        f'<th style="text-align:right">Net P&L</th>'
        f'{regime_th}'
        f'</tr></thead>'
        f'<tbody>{cohort_rows}</tbody>'
        f'</table></div>'
        if cohort_rows else ""
    )

    issues = diag.get("issues") or []
    issues_html = (
        "".join(f'<div class="sd-warn" style="margin-top:6px">⚠ {html.escape(str(i))}</div>' for i in issues)
        if issues else ""
    )

    return f"{header_kpi}{trend_detail}{evl_html}{cohort_table}{issues_html}"


def _safe_float(v: Any) -> Optional[float]:  # local re-export for renderer
    try:
        f = float(v)
        return None if not (f == f) else f  # nan check
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Drawdown Analysis
# ---------------------------------------------------------------------------

def _render_risk_metrics_section(rm: Dict[str, Any]) -> str:
    if not rm:
        return '<div class="sd-muted">No risk metrics data.</div>'

    diag = rm.get("diagnostics") or {}

    def _ratio_color(v: Optional[float], lo: float, hi: float) -> str:
        if v is None:
            return "#6b7280"
        if v >= hi:
            return "#2ecc71"
        if v >= lo:
            return "#f39c12"
        return "#e74c3c"

    def _ratio_cell(v: Optional[float], lo: float, hi: float, decimals: int = 2) -> str:
        if v is None:
            return '<span class="sd-muted">—</span>'
        color = _ratio_color(v, lo, hi)
        return f'<span style="font-weight:700;color:{color}">{_fmt_num(v, decimals)}</span>'

    # Benchmark thresholds: lo = acceptable, hi = good
    kpi_items = [
        ("Sharpe",     _ratio_cell(rm.get("sharpe_ratio"),  0.5, 1.0)),
        ("Sortino",    _ratio_cell(rm.get("sortino_ratio"), 1.0, 2.0)),
        ("Calmar",     _ratio_cell(rm.get("calmar_ratio"),  0.3, 1.0)),
        ("Omega",      _ratio_cell(rm.get("omega_ratio"),   1.2, 2.0)),
        ("Ann. Return", f"{_fmt_num(rm.get('annualized_return_pct'), 1)}%"
                        if rm.get("annualized_return_pct") is not None else "—"),
        ("Total Return", f"{_fmt_num(rm.get('total_return_pct'), 1)}%"
                         if rm.get("total_return_pct") is not None else "—"),
        ("Trades/Yr",  _fmt_num(rm.get("trades_per_year"), 0)),
    ]
    kpi_html = "".join(
        f'<div class="sd-kpi"><span class="sd-kpi-label">{lbl}</span>'
        f'<span class="sd-kpi-value">{val if val is not None else "—"}</span></div>'
        for lbl, val in kpi_items
    )
    header_kpi = f'<div class="sd-kpi-row">{kpi_html}</div>'

    # Detail table
    rows_data = [
        ("Sharpe Ratio",          rm.get("sharpe_ratio"),          "≥ 1.0 = good",  0.5, 1.0, 3),
        ("Sortino Ratio",         rm.get("sortino_ratio"),         "≥ 2.0 = good",  1.0, 2.0, 3),
        ("Calmar Ratio",          rm.get("calmar_ratio"),          "≥ 1.0 = good",  0.3, 1.0, 3),
        ("Omega Ratio",           rm.get("omega_ratio"),           "≥ 2.0 = good",  1.2, 2.0, 3),
        ("Annualized Return",     rm.get("annualized_return_pct"), "%",             0.0, 20.0, 1),
        ("Total Return",          rm.get("total_return_pct"),      "%",             0.0, 50.0, 1),
        ("Max DD (from curve)",   rm.get("max_drawdown_pct"),      "% equity",       0.0, 10.0, 2),
        ("Downside Deviation",    rm.get("downside_deviation"),    "annualized",     None, None, 4),
        ("Trades / Year",         rm.get("trades_per_year"),       "estimated",      None, None, 1),
    ]
    detail_rows = ""
    for label, val, note, lo, hi, dec in rows_data:
        if val is not None and lo is not None and hi is not None:
            cell = _ratio_cell(val, lo, hi, dec)
        elif val is not None:
            cell = _fmt_num(val, dec)
        else:
            cell = '<span class="sd-muted">—</span>'
        detail_rows += (
            f'<tr><td style="font-size:12px">{html.escape(label)}</td>'
            f'<td style="text-align:right">{cell}</td>'
            f'<td style="font-size:11px;opacity:.6">{html.escape(str(note))}</td></tr>'
        )

    detail_table = (
        f'<table class="sd-table" style="width:auto;margin-top:8px">'
        f'<thead><tr><th>Metric</th><th style="text-align:right">Value</th>'
        f'<th>Benchmark</th></tr></thead>'
        f'<tbody>{detail_rows}</tbody></table>'
    )

    n_sim_note = (
        f'<div class="sd-muted" style="font-size:11px;margin-top:6px">'
        f'Computed on {diag.get("n_trades", "?")} trades · '
        f'initial equity ${_fmt_num(diag.get("initial_equity"), 0)} · '
        f'annualized using per-trade sqrt(N_annual) factor</div>'
    )

    issues = diag.get("issues") or []
    issues_html = (
        "".join(f'<div class="sd-warn" style="margin-top:4px">⚠ {html.escape(str(i))}</div>' for i in issues)
        if issues else ""
    )

    return f"{header_kpi}{detail_table}{n_sim_note}{issues_html}"


def _render_parameter_sensitivity_section(ps: Dict[str, Any]) -> str:
    """Render parameter sensitivity sweep results for top entry rules."""
    if not ps:
        return '<div class="sd-muted">No parameter sensitivity data.</div>'

    summary = ps.get("summary") or {}
    rule_sweeps = ps.get("rule_sweeps") or []
    diag = ps.get("diagnostics") or {}

    n_robust   = summary.get("n_robust", 0)
    n_moderate = summary.get("n_moderate", 0)
    n_fragile  = summary.get("n_fragile", 0)
    n_sweeps   = summary.get("n_numeric_sweeps", 0)

    def _cls_badge(cls: Optional[str]) -> str:
        colors = {
            "robust":   ("#d1fae5", "#065f46"),
            "moderate": ("#fef3c7", "#92400e"),
            "fragile":  ("#fee2e2", "#991b1b"),
            "unknown":  ("#f3f4f6", "#374151"),
        }
        bg, fg = colors.get(str(cls), colors["unknown"])
        label = str(cls or "unknown").title()
        return (
            f'<span style="background:{bg};color:{fg};border-radius:4px;'
            f'padding:2px 6px;font-size:11px;font-weight:700">{html.escape(label)}</span>'
        )

    # Summary strip
    summary_html = (
        f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px">'
        f'<div class="sd-kpi"><span class="sd-kpi-label">Rules Analysed</span>'
        f'<span class="sd-kpi-value">{summary.get("n_rules_analysed", 0)}</span></div>'
        f'<div class="sd-kpi"><span class="sd-kpi-label">Numeric Sweeps</span>'
        f'<span class="sd-kpi-value">{n_sweeps}</span></div>'
        f'<div class="sd-kpi"><span class="sd-kpi-label">Robust</span>'
        f'<span class="sd-kpi-value" style="color:#065f46">{n_robust}</span></div>'
        f'<div class="sd-kpi"><span class="sd-kpi-label">Moderate</span>'
        f'<span class="sd-kpi-value" style="color:#92400e">{n_moderate}</span></div>'
        f'<div class="sd-kpi"><span class="sd-kpi-label">Fragile</span>'
        f'<span class="sd-kpi-value" style="color:#991b1b">{n_fragile}</span></div>'
        f'</div>'
    )

    # Per-rule cards
    rule_cards = ""
    for rule in rule_sweeps[:5]:
        rule_id  = html.escape(str(rule.get("rule_id", "?")))
        rule_desc = html.escape(str(rule.get("rule_desc") or rule_id))
        pf_val   = rule.get("pf")
        wr_val   = rule.get("wr")
        rule_meta = (
            f'PF={_fmt_num(pf_val, 2) if pf_val is not None else "—"}  '
            f'WR={(_fmt_num((wr_val or 0)*100, 1) + "%") if wr_val is not None else "—"}'
        )

        sweeps = rule.get("sweeps") or []
        sweep_rows = ""
        for sw in sweeps:
            if sw.get("note"):
                sweep_rows += f'<tr><td colspan="7" class="sd-muted">{html.escape(sw["note"])}</td></tr>'
                continue
            col  = html.escape(str(sw.get("column", "?")))
            op   = html.escape(str(sw.get("op", "?")))
            orig = _fmt_num(sw.get("original_value"), 3)
            step = _fmt_num(sw.get("step"), 3)
            sens = sw.get("sensitivity_score")
            sens_str = str(sens) if sens is not None else "—"
            cls_badge = _cls_badge(sw.get("classification"))

            # Mini sweep table (threshold → PF)
            thr_sweep = sw.get("threshold_sweep") or []
            mini = ""
            if thr_sweep:
                mini_cells = ""
                for pt in thr_sweep:
                    thr = _fmt_num(pt.get("threshold"), 3)
                    pf  = _fmt_num(pt.get("pf"), 2) if pt.get("pf") is not None else "—"
                    is_orig = abs((pt.get("threshold") or 0) - (sw.get("original_value") or 0)) < 1e-9
                    fw = "font-weight:700" if is_orig else ""
                    mini_cells += (
                        f'<td style="text-align:right;font-size:10px;padding:2px 4px;{fw}">'
                        f'{thr}<br><span style="opacity:.7">{pf}</span></td>'
                    )
                mini = (
                    f'<table style="border-collapse:collapse;margin-top:4px">'
                    f'<tr>{mini_cells}</tr></table>'
                )

            sweep_rows += (
                f'<tr>'
                f'<td style="font-size:12px">{col} {op}</td>'
                f'<td style="text-align:right">{orig}</td>'
                f'<td style="text-align:right">{step}</td>'
                f'<td style="text-align:right">{sens_str}</td>'
                f'<td>{cls_badge}</td>'
                f'<td colspan="2">{mini}</td>'
                f'</tr>'
            )

        sweep_table = (
            f'<table class="sd-table" style="width:100%;margin-top:6px">'
            f'<thead><tr>'
            f'<th>Condition</th><th style="text-align:right">Original</th>'
            f'<th style="text-align:right">Step</th>'
            f'<th style="text-align:right">Score</th>'
            f'<th>Class</th><th colspan="2">Threshold → PF sweep</th>'
            f'</tr></thead>'
            f'<tbody>{sweep_rows}</tbody></table>'
        )

        rule_cards += (
            f'<div style="margin-bottom:10px;padding:8px;background:#f9fafb;'
            f'border-radius:6px;border:1px solid #e5e7eb">'
            f'<div style="font-size:12px;font-weight:700">{rule_desc} '
            f'<span class="sd-muted" style="font-weight:400">({rule_meta})</span></div>'
            f'{sweep_table}'
            f'</div>'
        )

    issues = diag.get("issues") or []
    issues_html = (
        "".join(f'<div class="sd-warn" style="margin-top:4px">⚠ {html.escape(str(i))}</div>' for i in issues)
        if issues else ""
    )

    return f"{summary_html}{rule_cards}{issues_html}"


def _render_drawdown_analysis_section(da: Dict[str, Any]) -> str:
    if not da:
        return '<div class="sd-muted">No drawdown data.</div>'

    overall  = da.get("overall") or {}
    streaks  = da.get("streaks") or {}
    rolling  = da.get("rolling_max_dd") or []
    by_reg   = da.get("by_regime") or {}
    diag     = da.get("diagnostics") or {}

    max_dd_usd  = overall.get("max_drawdown_usd")
    max_dd_pct  = overall.get("max_drawdown_pct")
    ulcer       = overall.get("ulcer_index")
    avg_dd      = overall.get("avg_drawdown_pct")
    rec_factor  = overall.get("recovery_factor")
    longest_dd  = overall.get("longest_drawdown_bars")
    rec_bars    = overall.get("recovery_bars")
    net_profit  = overall.get("net_profit")
    dd_start    = overall.get("max_dd_start")
    dd_trough   = overall.get("max_dd_trough")
    dd_recovery = overall.get("max_dd_recovery")

    # Severity colour for max DD
    def _dd_color(pct: Optional[float]) -> str:
        if pct is None:
            return "#6b7280"
        if pct < 10:
            return "#2ecc71"
        if pct < 20:
            return "#f39c12"
        if pct < 35:
            return "#e67e22"
        return "#e74c3c"

    dd_color = _dd_color(max_dd_pct)

    # --- top KPI row ---
    kpi_items = [
        ("Max DD $",       f"${_fmt_num(max_dd_usd, 0)}"),
        ("Max DD %",       f'<span style="color:{dd_color};font-weight:700">{_fmt_num(max_dd_pct, 1)}%</span>'
                           if max_dd_pct is not None else "—"),
        ("Recovery Factor", _fmt_num(rec_factor, 2)),
        ("Ulcer Index",    f"{_fmt_num(ulcer, 2)}%"),
        ("Avg DD",         f"{_fmt_num(avg_dd, 2)}%"),
        ("Longest DD",     f"{longest_dd} trades" if longest_dd is not None else "—"),
    ]
    kpi_html = "".join(
        f'<div class="sd-kpi"><span class="sd-kpi-label">{lbl}</span>'
        f'<span class="sd-kpi-value">{val if val is not None else "—"}</span></div>'
        for lbl, val in kpi_items
    )
    header_kpi = f'<div class="sd-kpi-row">{kpi_html}</div>'

    # --- max DD timeline ---
    dd_timeline = ""
    if dd_start or dd_trough or dd_recovery:
        rec_text = (
            f"recovered {dd_recovery} ({rec_bars} trades after trough)"
            if dd_recovery else
            '<span style="color:#e74c3c">not yet recovered</span>'
        )
        dd_timeline = (
            f'<div style="font-size:12px;margin-bottom:8px">'
            f'<b>Worst drawdown:</b> peak {dd_start or "?"} → trough {dd_trough or "?"} → {rec_text}'
            f'</div>'
        )

    # --- streak KPIs ---
    max_loss    = streaks.get("max_consecutive_losses")
    loss_usd    = streaks.get("max_loss_streak_usd")
    avg_loss_l  = streaks.get("avg_loss_streak_len")
    max_win     = streaks.get("max_consecutive_wins")
    avg_win_l   = streaks.get("avg_win_streak_len")
    n_loss_st   = streaks.get("n_loss_streaks")

    streak_items = [
        ("Max Consec. Losses",  max_loss),
        ("Worst Streak $",      f"${_fmt_num(loss_usd, 0)}" if loss_usd is not None else "—"),
        ("Avg Loss Streak",     _fmt_num(avg_loss_l, 1)),
        ("Loss Streaks Total",  n_loss_st),
        ("Max Consec. Wins",    max_win),
        ("Avg Win Streak",      _fmt_num(avg_win_l, 1)),
    ]
    streak_kpi = "".join(
        f'<div class="sd-kpi"><span class="sd-kpi-label">{lbl}</span>'
        f'<span class="sd-kpi-value">{val if val is not None else "—"}</span></div>'
        for lbl, val in streak_items
    )
    streak_section = (
        f'<div class="sd-section-label" style="margin-top:10px">Streak Statistics</div>'
        f'<div class="sd-kpi-row">{streak_kpi}</div>'
    )

    # --- rolling max-DD table (top 5 worst windows) ---
    rolling_html = ""
    if rolling:
        sorted_r = sorted(rolling, key=lambda x: (x.get("max_dd_pct") or 0), reverse=True)
        top5 = sorted_r[:5]
        rows = "".join(
            f'<tr>'
            f'<td style="font-size:11px">{html.escape(str(r.get("period_end") or ""))}</td>'
            f'<td style="text-align:right;color:{_dd_color(r.get("max_dd_pct"))};font-weight:700">'
            f'{_fmt_num(r.get("max_dd_pct"), 1)}%</td>'
            f'</tr>'
            for r in top5
        )
        rolling_html = (
            f'<div class="sd-section-label" style="margin-top:10px">'
            f'Worst 20-Trade Windows</div>'
            f'<table class="sd-table" style="width:auto">'
            f'<thead><tr><th>Window End</th>'
            f'<th style="text-align:right">Max DD</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )

    # --- regime breakdown ---
    regime_html = ""
    if by_reg:
        reg_rows = "".join(
            f'<tr>'
            f'<td>{html.escape(str(reg))}</td>'
            f'<td style="text-align:right">{rd.get("n_trades")}</td>'
            f'<td style="text-align:right;color:{_dd_color(rd.get("max_dd_pct"))};font-weight:700">'
            f'{_fmt_num(rd.get("max_dd_pct"), 1)}%</td>'
            f'<td style="text-align:right">{rd.get("max_consecutive_losses")}</td>'
            f'</tr>'
            for reg, rd in sorted(by_reg.items())
        )
        regime_html = (
            f'<div class="sd-section-label" style="margin-top:10px">By Regime</div>'
            f'<table class="sd-table">'
            f'<thead><tr><th>Regime</th>'
            f'<th style="text-align:right">Trades</th>'
            f'<th style="text-align:right">Max DD</th>'
            f'<th style="text-align:right">Max Consec. Losses</th>'
            f'</tr></thead>'
            f'<tbody>{reg_rows}</tbody></table>'
        )

    issues = diag.get("issues") or []
    issues_html = (
        "".join(f'<div class="sd-warn" style="margin-top:6px">⚠ {html.escape(str(i))}</div>' for i in issues)
        if issues else ""
    )

    return f"{header_kpi}{dd_timeline}{streak_section}{rolling_html}{regime_html}{issues_html}"


# ---------------------------------------------------------------------------
# Run card
# ---------------------------------------------------------------------------

def _render_run_card(run_id: str, sd: Dict[str, Any], options: Dict[str, Any]) -> str:
    show_validation   = bool(options.get("show_validation", True))
    show_mae_mfe      = bool(options.get("show_mae_mfe", True))
    show_evaluation   = bool(options.get("show_evaluation", True))
    show_regime       = bool(options.get("show_regime", True))
    show_exit_disc    = bool(options.get("show_exit_discovery", True))

    evaluation = sd.get("evaluation") or {}
    validation = sd.get("validation") or {}
    mae_mfe    = sd.get("mae_mfe_profile") or {}

    # Header badges
    val_passed = validation.get("passed") if validation else None
    if val_passed is True:
        val_badge = '<span class="sd-pass">&#9650; PASS</span>'
    elif val_passed is False:
        val_badge = '<span class="sd-fail">&#10007; FAIL</span>'
    else:
        val_badge = '<span class="sd-muted">Validation: n/a</span>'

    # Top KPIs from evaluation or validation
    n_trades    = evaluation.get("n_trades") or (
        (validation.get("wf_results") or {}).get("is_trades", "—")
    )
    net_profit  = evaluation.get("net_profit")
    is_pf       = (validation.get("wf_results") or {}).get("is_pf")
    oos_pf      = (validation.get("wf_results") or {}).get("oos_pf")
    p_value     = (validation.get("t_test") or {}).get("p_value")
    mc_rank     = (validation.get("monte_carlo") or {}).get("dd_percentile_rank")

    p_str    = f"{float(p_value):.4f}" if p_value is not None else "—"
    mc_str   = f"{float(mc_rank):.0f}th pct" if mc_rank is not None else "—"
    n_str    = f"n={n_trades} trades" if n_trades not in (None, "—") else ""
    net_str  = f"Net: {_fmt_usd(net_profit)}" if net_profit is not None else ""
    is_str   = f"IS PF: {_fmt_num(is_pf)}" if is_pf is not None else ""
    oos_str  = f"OOS PF: {_fmt_num(oos_pf)}" if oos_pf is not None else ""

    header_meta_parts = [p for p in [n_str, net_str] if p]
    wf_parts = [p for p in [is_str, oos_str] if p]
    stat_parts = [p for p in [f"p={p_str}", f"MC: {mc_str}"] if p_str != "—" or mc_str != "—"]

    meta_line = "  |  ".join(header_meta_parts)
    wf_line   = "  →  ".join(wf_parts) if wf_parts else ""
    stat_line = "  |  ".join(stat_parts)

    subtitle_parts = [p for p in [meta_line, wf_line, stat_line] if p]
    subtitle = "  &nbsp;&nbsp;|&nbsp;&nbsp;  ".join(subtitle_parts)

    # Sub-section blocks
    parts = []

    if show_validation:
        val_html = _render_validation_section(validation)
        parts.append(f"""
        <details class="sd-details">
          <summary>Validation Details</summary>
          <div style="margin-top:10px">{val_html}</div>
        </details>""")

    if show_mae_mfe:
        mae_html = _render_mae_mfe_section(mae_mfe)
        parts.append(f"""
        <details class="sd-details">
          <summary>MAE / MFE Profile</summary>
          <div style="margin-top:10px">{mae_html}</div>
        </details>""")

    if show_evaluation:
        eval_html = _render_evaluation_section(evaluation)
        parts.append(f"""
        <details class="sd-details">
          <summary>Performance Breakdown</summary>
          <div style="margin-top:10px">{eval_html}</div>
        </details>""")

    if show_regime:
        regime_html = _render_regime_section(sd)
        parts.append(f"""
        <details class="sd-details">
          <summary>Regime Analysis</summary>
          <div style="margin-top:10px">{regime_html}</div>
        </details>""")

    if show_exit_disc:
        exit_disc = sd.get("exit_discovery") or {}
        if exit_disc:
            ed_html = _render_exit_discovery_section(exit_disc)
            parts.append(f"""
            <details class="sd-details">
              <summary>Exit Policy Discovery</summary>
              <div style="margin-top:10px">{ed_html}</div>
            </details>""")

    if bool(options.get("show_importance", True)):
        imp = sd.get("importance") or {}
        if imp and not imp.get("error"):
            imp_html = _render_importance_section(imp)
            parts.append(f"""
            <details class="sd-details">
              <summary>Feature Importance</summary>
              <div style="margin-top:10px">{imp_html}</div>
            </details>""")

    if bool(options.get("show_entry_discovery", True)):
        entry_disc = sd.get("entry_discovery") or {}
        if entry_disc and not entry_disc.get("error") and not entry_disc.get("skipped"):
            ed_html = _render_entry_discovery_section(entry_disc)
            parts.append(f"""
            <details class="sd-details">
              <summary>Entry Rule Discovery</summary>
              <div style="margin-top:10px">{ed_html}</div>
            </details>""")

    if bool(options.get("show_filter_discovery", True)):
        filter_disc = sd.get("filter_discovery") or {}
        if filter_disc and not filter_disc.get("error") and not filter_disc.get("skipped"):
            fd_html = _render_filter_discovery_section(filter_disc)
            parts.append(f"""
            <details class="sd-details">
              <summary>Filter Discovery (Exclusion Conditions)</summary>
              <div style="margin-top:10px">{fd_html}</div>
            </details>""")

    if bool(options.get("show_classification", True)):
        classif = sd.get("classification") or {}
        if classif and not classif.get("error"):
            cl_html = _render_classification_section(classif)
            parts.append(f"""
            <details class="sd-details">
              <summary>Strategy Classification</summary>
              <div style="margin-top:10px">{cl_html}</div>
            </details>""")

    if bool(options.get("show_position_sizing", True)):
        ps = sd.get("position_sizing") or {}
        if ps and not ps.get("error") and not ps.get("skipped"):
            ps_html = _render_position_sizing_section(ps)
            parts.append(f"""
            <details class="sd-details">
              <summary>Position Sizing (Kelly + Monte Carlo)</summary>
              <div style="margin-top:10px">{ps_html}</div>
            </details>""")

    if bool(options.get("show_cohort_analysis", True)):
        ca = sd.get("cohort_analysis") or {}
        if ca and not ca.get("error") and not ca.get("skipped"):
            ca_html = _render_cohort_analysis_section(ca)
            parts.append(f"""
            <details class="sd-details">
              <summary>Cohort Analysis (Drift &amp; Decay)</summary>
              <div style="margin-top:10px">{ca_html}</div>
            </details>""")

    if bool(options.get("show_drawdown_analysis", True)):
        da = sd.get("drawdown_analysis") or {}
        if da and not da.get("error") and not da.get("skipped"):
            da_html = _render_drawdown_analysis_section(da)
            parts.append(f"""
            <details class="sd-details">
              <summary>Drawdown Analysis</summary>
              <div style="margin-top:10px">{da_html}</div>
            </details>""")

    if bool(options.get("show_risk_metrics", True)):
        rm = sd.get("risk_metrics") or {}
        if rm and not rm.get("error") and not rm.get("skipped"):
            rm_html = _render_risk_metrics_section(rm)
            parts.append(f"""
            <details class="sd-details">
              <summary>Risk-Adjusted Metrics (Sharpe / Sortino / Calmar)</summary>
              <div style="margin-top:10px">{rm_html}</div>
            </details>""")

    if bool(options.get("show_parameter_sensitivity", True)):
        ps_data = sd.get("parameter_sensitivity") or {}
        if ps_data and not ps_data.get("error") and not ps_data.get("skipped"):
            ps_html = _render_parameter_sensitivity_section(ps_data)
            parts.append(f"""
            <details class="sd-details">
              <summary>Parameter Sensitivity (Threshold Robustness)</summary>
              <div style="margin-top:10px">{ps_html}</div>
            </details>""")

    subsections = "\n".join(parts)

    return f"""
    <div class="sd-card">
      <div class="sd-header">
        <span class="sd-header-title">{html.escape(str(run_id))}</span>
        {val_badge}
        <span class="sd-muted">{subtitle}</span>
      </div>
      {subsections}
    </div>"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_overview(ctx: dict) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"].
    """
    try:
        packages   = ctx.get("packages") or {}
        options    = ctx.get("options") or {}

        min_trades = int(options.get("min_trades", 5))

        if not packages:
            return (
                f"{_section_css()}"
                '<div class="sd-block"><h2>Strategy Discovery Overview</h2>'
                '<p>No packages loaded.</p></div>'
            )

        html_parts = [_section_css()]
        html_parts.append('<div class="sd-block">')
        html_parts.append("<h2>Strategy Discovery Overview</h2>")

        has_any = False
        for run_id, pkg in packages.items():
            if pkg is None:
                continue
            try:
                derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
                sd = derived.get("strategy_discovery") or {}
            except Exception:
                sd = {}

            if not sd:
                html_parts.append(
                    f'<div class="sd-card">'
                    f'<div class="sd-header">'
                    f'<span class="sd-header-title">{html.escape(str(run_id))}</span>'
                    f'</div>'
                    f'<div class="sd-muted">No strategy_discovery data attached to this run.</div>'
                    f'</div>'
                )
                continue

            # Check trade count
            eval_data = sd.get("evaluation") or {}
            wf = (sd.get("validation") or {}).get("wf_results") or {}
            n_trades = eval_data.get("n_trades") or wf.get("is_trades") or 0
            try:
                n_trades_int = int(n_trades)
            except Exception:
                n_trades_int = 0

            if n_trades_int < min_trades:
                html_parts.append(
                    f'<div class="sd-card">'
                    f'<div class="sd-header">'
                    f'<span class="sd-header-title">{html.escape(str(run_id))}</span>'
                    f'</div>'
                    f'<div class="sd-muted">Skipped — fewer than {min_trades} trades '
                    f'(found {n_trades_int}).</div>'
                    f'</div>'
                )
                continue

            try:
                card_html = _render_run_card(str(run_id), sd, options)
            except Exception as exc:
                card_html = (
                    f'<div class="sd-card">'
                    f'<div class="sd-warn">Error rendering run {html.escape(str(run_id))}: '
                    f'{html.escape(str(exc))}</div>'
                    f'</div>'
                )

            html_parts.append(card_html)
            has_any = True

        if not has_any:
            html_parts.append(
                '<p class="sd-muted">No runs had strategy_discovery data above the min_trades threshold.</p>'
            )

        html_parts.append("</div>")
        return "\n".join(html_parts)

    except Exception as exc:
        return (
            f"<div class='sd-warn'>Strategy Discovery Overview: render failed — "
            f"{html.escape(str(exc))}</div>"
        )
