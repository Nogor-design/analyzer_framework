# src/ta_foundation/reports/html/sections/strategy_discovery_position_sizing.py
"""
Strategy Discovery — Position Sizing Section
=============================================
Renders Phase-9 position-sizing results: Kelly criterion, Monte Carlo
ruin-risk simulation, fractional-Kelly comparison, and regime-conditional
Kelly breakdown.

CSS prefix: .sdps-
"""

from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdps-wrap { font-family: sans-serif; font-size: 13px; color: #e8e8e8; padding: 16px 0; }
.sdps-run-header { font-size: 15px; font-weight: 700; color: #a78bfa; margin: 20px 0 8px; }
.sdps-kpi-strip { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }
.sdps-kpi { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
            padding: 10px 16px; min-width: 100px; text-align: center; }
.sdps-kpi-label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 3px; }
.sdps-kpi-value { font-size: 18px; font-weight: 700; color: #e8e8e8; }
.sdps-kpi-value.pos { color: #22c55e; }
.sdps-kpi-value.neg { color: #ef4444; }
.sdps-kpi-value.warn { color: #f59e0b; }

.sdps-table-wrap { overflow-x: auto; margin: 10px 0 16px; }
.sdps-table { border-collapse: collapse; min-width: 600px; width: 100%; }
.sdps-table th { background: #252540; color: #a78bfa; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; padding: 7px 10px;
                 text-align: left; border-bottom: 2px solid #444; }
.sdps-table td { padding: 6px 10px; border-bottom: 1px solid #2a2a2a; color: #ccc; font-size: 12px; }
.sdps-table tr:hover td { background: #1a1a2a; }

.sdps-safe-badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
                   font-size: 10px; font-weight: 700; letter-spacing: .04em; }
.sdps-safe-badge.safe   { background: #14532d; color: #86efac; }
.sdps-safe-badge.unsafe { background: #7f1d1d; color: #fca5a5; }
.sdps-safe-badge.best   { background: #1e3a5f; color: #93c5fd; border: 1px solid #3b82f6; }

.sdps-reco-box { background: #1e2a1e; border: 1px solid #2d5a27; border-left: 4px solid #22c55e;
                 border-radius: 6px; padding: 12px 16px; margin: 12px 0 16px; }
.sdps-reco-box.warn { background: #2a2010; border-color: #b45309; border-left-color: #f59e0b; }
.sdps-reco-label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px; }
.sdps-reco-value { font-size: 16px; font-weight: 700; color: #22c55e; margin-bottom: 4px; }
.sdps-reco-value.warn { color: #f59e0b; }
.sdps-reco-reasoning { font-size: 12px; color: #aaa; line-height: 1.5; }

.sdps-section-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                      letter-spacing: .06em; margin: 16px 0 6px; }
.sdps-regime-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 16px; }
.sdps-regime-card { background: #1e1e2e; border: 1px solid #333; border-radius: 6px;
                    padding: 10px 14px; min-width: 180px; }
.sdps-regime-name { font-size: 12px; font-weight: 700; color: #a78bfa; margin-bottom: 6px; }
.sdps-regime-row { display: flex; justify-content: space-between; gap: 20px;
                   font-size: 11px; color: #aaa; margin-bottom: 2px; }
.sdps-regime-row span:last-child { color: #e8e8e8; font-weight: 600; }

.sdps-diag { font-size: 11px; color: #666; margin-top: 6px; }
.sdps-diag-issue { color: #f87171; }

.sdps-cross-run { margin-bottom: 20px; }
.sdps-cross-label { font-size: 12px; font-weight: 700; color: #888; text-transform: uppercase;
                    letter-spacing: .06em; margin-bottom: 8px; }
.sdps-no-data { color: #666; font-style: italic; font-size: 12px; padding: 10px 0; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(v: Any, decimals: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f > 0 else ""
        return f"{sign}${f:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, decimals: int = 4) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _kpi_class(v: Any, pos_good: bool = True) -> str:
    """Return CSS class for colouring KPI values."""
    if v is None:
        return ""
    try:
        f = float(v)
        if pos_good:
            return "pos" if f > 0 else "neg" if f < 0 else ""
        else:
            # lower is better (e.g. p_ruin)
            return "pos" if f < 0.05 else "warn" if f < 0.15 else "neg"
    except (TypeError, ValueError):
        return ""


def _safe_badge(entry: dict) -> str:
    safe = entry.get("safe")
    if safe is None:
        return ""
    if safe:
        return '<span class="sdps-safe-badge safe">SAFE</span>'
    return '<span class="sdps-safe-badge unsafe">RISK</span>'


def _best_badge() -> str:
    return '<span class="sdps-safe-badge best">★ RECOMMENDED</span>'


# ---------------------------------------------------------------------------
# Cross-run summary table
# ---------------------------------------------------------------------------

def _render_cross_run_table(run_items: List[tuple]) -> str:
    """
    run_items: list of (run_id, ps_dict)
    Shows: run_id | kelly_full | recommended frac | expected return | p_ruin | median max DD
    """
    rows_html = ""
    for run_id, ps in run_items:
        if not ps or ps.get("skipped") or ps.get("error"):
            rows_html += f"""
            <tr>
              <td style="color:#a78bfa">{run_id}</td>
              <td colspan="5" style="color:#666;font-style:italic">
                {ps.get("error") or "skipped" if ps else "no data"}
              </td>
            </tr>"""
            continue

        kf = ps.get("kelly_full")
        reco = ps.get("recommendation") or {}
        rec_fok = reco.get("recommended_fraction_of_kelly")
        rec_kf  = reco.get("recommended_kelly_fraction")

        # Find the recommended fraction row
        fracs = ps.get("kelly_fractions") or []
        rec_row = next(
            (f for f in fracs if f.get("fraction_of_kelly") == rec_fok),
            fracs[-1] if fracs else {},
        )

        exp_ret = rec_row.get("expected_return_pct")
        p_ruin  = rec_row.get("p_ruin")
        med_dd  = rec_row.get("median_max_drawdown_pct")

        rows_html += f"""
        <tr>
          <td style="color:#a78bfa">{run_id}</td>
          <td>{_num(kf, 3)}</td>
          <td>{_num(rec_fok, 2)}× (<code>{_num(rec_kf, 3)}</code>)</td>
          <td class="{_kpi_class(exp_ret)}">{_pct(exp_ret)}</td>
          <td class="{_kpi_class(p_ruin, pos_good=False)}">{_pct(p_ruin, 1) if p_ruin is not None else '—'}</td>
          <td>{_pct(med_dd)}</td>
        </tr>"""

    return f"""
<div class="sdps-cross-run">
  <div class="sdps-cross-label">Cross-Run Kelly Comparison</div>
  <div class="sdps-table-wrap">
    <table class="sdps-table">
      <thead>
        <tr>
          <th>Run</th>
          <th>Full Kelly (f*)</th>
          <th>Recommended Fraction</th>
          <th>Expected Return</th>
          <th>P(Ruin)</th>
          <th>Median Max DD</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Per-run renderers
# ---------------------------------------------------------------------------

def _render_trade_stats(trade_stats: dict) -> str:
    n      = trade_stats.get("n_trades", 0)
    wr     = trade_stats.get("win_rate")
    aw     = trade_stats.get("avg_win")
    al     = trade_stats.get("avg_loss")
    payoff = trade_stats.get("payoff_ratio")
    exp    = trade_stats.get("expectancy")

    wr_pct = f"{float(wr)*100:.1f}%" if wr is not None else "—"
    payoff_cls = "pos" if payoff and payoff > 1.0 else "neg" if payoff and payoff < 1.0 else ""
    exp_cls = _kpi_class(exp)

    return f"""
<div class="sdps-kpi-strip">
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Trades</div>
    <div class="sdps-kpi-value">{n}</div>
  </div>
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Win Rate</div>
    <div class="sdps-kpi-value">{wr_pct}</div>
  </div>
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Avg Win</div>
    <div class="sdps-kpi-value pos">{_usd(aw)}</div>
  </div>
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Avg Loss</div>
    <div class="sdps-kpi-value neg">-${abs(float(al)):,.2f}</div>
  </div>
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Payoff Ratio</div>
    <div class="sdps-kpi-value {payoff_cls}">{_num(payoff, 2)}</div>
  </div>
  <div class="sdps-kpi">
    <div class="sdps-kpi-label">Expectancy</div>
    <div class="sdps-kpi-value {exp_cls}">{_usd(exp)}</div>
  </div>
</div>""" if trade_stats else ""


def _render_kelly_fractions_table(
    fracs: List[dict],
    recommended_fok: Any,
) -> str:
    if not fracs:
        return '<div class="sdps-no-data">No Kelly fraction data available.</div>'

    rows_html = ""
    for f in fracs:
        fok      = f.get("fraction_of_kelly")
        kf       = f.get("kelly_fraction")
        exp_ret  = f.get("expected_return_pct")
        p_ruin   = f.get("p_ruin")
        p_dd     = f.get("p_dd_warn")
        med_dd   = f.get("median_max_drawdown_pct")
        p95_dd   = f.get("p95_max_drawdown_pct")
        med_eq   = f.get("median_final_equity")
        p5_eq    = f.get("p5_final_equity")

        is_reco = (fok == recommended_fok)
        row_style = ' style="background:#0d1a2e"' if is_reco else ""
        badge = (f'&nbsp;{_best_badge()}' if is_reco else '') + f'&nbsp;{_safe_badge(f)}'

        p_ruin_cls = _kpi_class(p_ruin, pos_good=False) if p_ruin is not None else ""

        rows_html += f"""
        <tr{row_style}>
          <td><b>{_num(fok, 2)}× Kelly</b> <code style="font-size:10px;color:#888">(f={_num(kf, 3)})</code>{badge}</td>
          <td class="{_kpi_class(exp_ret)}">{_pct(exp_ret)}</td>
          <td class="{p_ruin_cls}">{_pct(p_ruin, 1) if p_ruin is not None else '—'}</td>
          <td>{_pct(p_dd, 1) if p_dd is not None else '—'}</td>
          <td>{_pct(med_dd)}</td>
          <td style="color:#888">{_pct(p95_dd)}</td>
          <td>${float(med_eq):,.0f}</td>
          <td style="color:#888">${float(p5_eq):,.0f}</td>
        </tr>""" if (med_eq is not None and p5_eq is not None) else f"""
        <tr{row_style}>
          <td><b>{_num(fok, 2)}× Kelly</b> <code style="font-size:10px;color:#888">(f={_num(kf, 3)})</code>{badge}</td>
          <td class="{_kpi_class(exp_ret)}">{_pct(exp_ret)}</td>
          <td class="{p_ruin_cls}">{_pct(p_ruin, 1) if p_ruin is not None else '—'}</td>
          <td>{_pct(p_dd, 1) if p_dd is not None else '—'}</td>
          <td>{_pct(med_dd)}</td>
          <td style="color:#888">{_pct(p95_dd)}</td>
          <td>—</td>
          <td style="color:#888">—</td>
        </tr>"""

    return f"""
<div class="sdps-section-label">Fractional Kelly Comparison ({len(fracs)} variants)</div>
<div class="sdps-table-wrap">
  <table class="sdps-table">
    <thead>
      <tr>
        <th>Fraction</th>
        <th>Expected Return</th>
        <th>P(Ruin)</th>
        <th>P(DD Warn)</th>
        <th>Median Max DD</th>
        <th>p95 Max DD</th>
        <th>Median Equity</th>
        <th>p5 Equity</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


def _render_recommendation(reco: dict, kelly_full: Any) -> str:
    if not reco:
        return ""

    rec_fok = reco.get("recommended_fraction_of_kelly")
    rec_kf  = reco.get("recommended_kelly_fraction")
    reasoning = reco.get("reasoning", "")

    # Warn if recommended fraction is 0 or no positive edge
    is_warn = (rec_fok is not None and float(rec_fok) == 0.0) or "no positive edge" in reasoning.lower()

    box_cls = "sdps-reco-box warn" if is_warn else "sdps-reco-box"
    val_cls = "sdps-reco-value warn" if is_warn else "sdps-reco-value"

    kf_display = f"  |  Full Kelly: {_num(kelly_full, 3)}" if kelly_full else ""

    return f"""
<div class="{box_cls}">
  <div class="sdps-reco-label">Position Sizing Recommendation{kf_display}</div>
  <div class="{val_cls}">
    {_num(rec_fok, 2)}× Kelly &nbsp;
    <span style="font-size:13px;font-weight:normal;color:#aaa">(f = {_num(rec_kf, 3)})</span>
  </div>
  <div class="sdps-reco-reasoning">{reasoning}</div>
</div>"""


def _render_regime_conditional(regime_cond: dict) -> str:
    if not regime_cond:
        return ""

    cards = ""
    for regime_label, rd in sorted(regime_cond.items()):
        kf    = rd.get("kelly_full")
        kh    = rd.get("kelly_half")
        wr    = rd.get("win_rate")
        payoff = rd.get("payoff_ratio")
        n     = rd.get("n_trades", 0)

        wr_pct = f"{float(wr)*100:.1f}%" if wr is not None else "—"
        cards += f"""
        <div class="sdps-regime-card">
          <div class="sdps-regime-name">{regime_label}</div>
          <div class="sdps-regime-row"><span>Trades</span><span>{n}</span></div>
          <div class="sdps-regime-row"><span>Win Rate</span><span>{wr_pct}</span></div>
          <div class="sdps-regime-row"><span>Payoff</span><span>{_num(payoff, 2)}</span></div>
          <div class="sdps-regime-row"><span>Full Kelly</span><span style="color:#22c55e">{_num(kf, 3)}</span></div>
          <div class="sdps-regime-row"><span>Half Kelly</span><span style="color:#86efac">{_num(kh, 3)}</span></div>
        </div>"""

    return f"""
<div class="sdps-section-label">Regime-Conditional Kelly ({len(regime_cond)} regimes)</div>
<div class="sdps-regime-grid">{cards}</div>"""


def _render_diagnostics(diag: dict) -> str:
    issues = diag.get("issues") or []
    n_sim  = diag.get("n_sim", "—")
    pcol   = diag.get("profit_col_used", "—")

    issue_html = ""
    for iss in issues:
        issue_html += f'<span class="sdps-diag-issue"> ⚠ {iss}</span>'

    return f"""
<div class="sdps-diag">
  MC simulations: {n_sim} &nbsp;|&nbsp; profit col: <code>{pcol}</code>
  {issue_html}
</div>"""


def _render_run_card(run_id: str, ps: dict, options: dict) -> str:
    if not ps:
        return f'<div class="sdps-run-header">{run_id}</div><div class="sdps-no-data">No position sizing data.</div>'

    if ps.get("skipped"):
        return f'<div class="sdps-run-header">{run_id}</div><div class="sdps-no-data">Position sizing disabled.</div>'

    if ps.get("error"):
        return f'<div class="sdps-run-header">{run_id}</div><div class="sdps-no-data">Error: {ps["error"]}</div>'

    trade_stats  = ps.get("trade_stats") or {}
    kelly_full   = ps.get("kelly_full")
    fracs        = ps.get("kelly_fractions") or []
    reco         = ps.get("recommendation") or {}
    regime_cond  = ps.get("regime_conditional") or {}
    diag         = ps.get("diagnostics") or {}

    rec_fok = reco.get("recommended_fraction_of_kelly")

    show_regime = bool(options.get("show_regime_conditional", True))

    parts = [
        f'<div class="sdps-run-header">{run_id}</div>',
    ]

    if trade_stats:
        parts.append(_render_trade_stats(trade_stats))

    if fracs:
        parts.append(_render_kelly_fractions_table(fracs, rec_fok))
    elif kelly_full is not None:
        parts.append(f'<div class="sdps-no-data">Full Kelly: {_num(kelly_full, 3)} — no Monte Carlo results.</div>')

    parts.append(_render_recommendation(reco, kelly_full))

    if show_regime and regime_cond:
        parts.append(_render_regime_conditional(regime_cond))

    if diag:
        parts.append(_render_diagnostics(diag))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_position_sizing(ctx: dict) -> str:
    packages    = ctx.get("packages") or {}
    options     = ctx.get("options") or {}

    show_cross_run = bool(options.get("show_cross_run", True))
    show_per_run   = bool(options.get("show_per_run", True))

    # Collect per-run data
    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
        ps = sd.get("position_sizing") or {}
        run_items.append((run_id, ps))

    if not run_items:
        return f"{_CSS}<div class='sdps-wrap'><div class='sdps-no-data'>No strategy discovery data available.</div></div>"

    parts: List[str] = [_CSS, '<div class="sdps-wrap">']

    # Cross-run table
    if show_cross_run and len(run_items) > 1:
        parts.append(_render_cross_run_table(run_items))

    # Per-run detail
    if show_per_run:
        for run_id, ps in run_items:
            parts.append(_render_run_card(run_id, ps, options))

    parts.append("</div>")
    return "\n".join(parts)
