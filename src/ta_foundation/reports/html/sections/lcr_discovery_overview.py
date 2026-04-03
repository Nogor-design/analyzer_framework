"""
LCR Discovery Overview section.

Renders a summary of the Large Candle Region discovery results:
  - Region-to-region continuation stats
  - Retrace stats
  - Top-ranked signal type results table
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def render_lcr_discovery_overview(ctx: dict) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}

    top_n = int(options.get("top_n", 20))
    show_r2r = bool(options.get("show_r2r_stats", True))
    show_retrace = bool(options.get("show_retrace_stats", True))
    show_break_tod = bool(options.get("show_break_time_of_day_stats", True))
    min_breaks_per_hour = int(options.get("min_breaks_per_hour", 5))

    # Collect lcr_discovery from any package
    disc: Optional[Dict[str, Any]] = None
    for pkg in packages.values():
        derived = (pkg.metadata or {}).get("derived", {})
        if "lcr_discovery" in derived:
            disc = derived["lcr_discovery"]
            break

    if not disc:
        return (
            "<section><h2>LCR Discovery Overview</h2>"
            "<p><em>No LCR discovery results found. Add <code>lcr_discovery:</code> "
            "to your YAML config.</em></p></section>"
        )

    results = disc.get("results") or []
    r2r = disc.get("r2r_stats") or {}
    retrace = disc.get("retrace_stats") or {}
    break_tod = disc.get("break_outcome_tod_stats") or {}
    n_run = disc.get("n_combinations_run", 0)
    n_results = disc.get("n_results", 0)

    html = ['<section id="lcr_discovery_overview">']
    html.append("<h2>Large Candle Region (LCR) Discovery</h2>")
    html.append(
        f"<p><strong>{n_run}</strong> parameter combinations evaluated &mdash; "
        f"<strong>{n_results}</strong> passed minimum trade threshold.</p>"
    )

    # ---- Region-to-Region stats ----
    if show_r2r and r2r:
        html.append("<h3>Region-to-Region Continuation</h3>")
        html.append("<p>After a region breaks, does price reach the next region?</p>")
        html.append(_r2r_table(r2r))

    # ---- Retrace stats ----
    if show_retrace and retrace:
        html.append("<h3>Retrace Behaviour</h3>")
        html.append("<p>Of regions that break, what fraction retrace back into the zone?</p>")
        html.append(_retrace_table(retrace))

    # ---- Break outcomes by time of day ----
    if show_break_tod and break_tod:
        html.append("<h3>Break Outcome by Time of Day (America/Denver)</h3>")
        html.append(
            "<p>For each hour, compares break outcomes: retrace back into zone vs "
            "continuation to next region.</p>"
        )
        html.append(_break_tod_table(break_tod, min_breaks_per_hour=min_breaks_per_hour))

    # ---- Top results table ----
    if results:
        html.append(f"<h3>Top {top_n} Signal Combinations</h3>")
        html.append(_results_table(results[:top_n]))
    else:
        html.append("<p><em>No results met the minimum trade threshold.</em></p>")

    html.append("</section>")
    return "\n".join(html)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.1f}%"


def _fmt(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{float(v):.{decimals}f}"


def _r2r_table(r2r: Dict) -> str:
    overall_rate = _pct(r2r.get("reach_rate"))
    avg_bars = _fmt(r2r.get("avg_bars_to_reach"), 1)
    avg_dist = _fmt(r2r.get("avg_dist_ticks"), 1)
    n_breaks = r2r.get("n_breaks", 0)
    n_reached = r2r.get("n_reached", 0)
    by_dir = r2r.get("by_direction", {})

    rows = ""
    for label, key in [("Bull break (continuation up)", "bull_break"),
                        ("Bear break (continuation down)", "bear_break")]:
        d = by_dir.get(key, {})
        rows += (
            f"<tr><td>{label}</td>"
            f"<td>{d.get('n_reached', 0)} / {d.get('n', 0)}</td>"
            f"<td>{_pct(d.get('reach_rate'))}</td>"
            f"<td>{_fmt(d.get('avg_bars'), 1)}</td>"
            f"<td>{_fmt(d.get('avg_dist_ticks'), 1)}</td></tr>"
        )

    return f"""
<table>
  <thead>
    <tr>
      <th>Scenario</th><th>Reached / Total</th><th>Reach Rate</th>
      <th>Avg Bars</th><th>Avg Dist (ticks)</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Overall</strong></td>
      <td>{n_reached} / {n_breaks}</td>
      <td><strong>{overall_rate}</strong></td>
      <td>{avg_bars}</td>
      <td>{avg_dist}</td>
    </tr>
    {rows}
  </tbody>
</table>"""


def _retrace_table(retrace: Dict) -> str:
    n_breaks = retrace.get("n_breaks", 0)
    n_retraces = retrace.get("n_retraces", 0)
    rate = _pct(retrace.get("retrace_rate"))
    avg_age = _fmt(retrace.get("avg_age_at_break"), 1)
    by_dir = retrace.get("by_direction", {})

    rows = ""
    for label, key in [("Bull regions (broke down)", "bull_regions"),
                        ("Bear regions (broke up)", "bear_regions")]:
        d = by_dir.get(key, {})
        rows += (
            f"<tr><td>{label}</td>"
            f"<td>{d.get('n_retraces', 0)} / {d.get('n_breaks', 0)}</td>"
            f"<td>{_pct(d.get('retrace_rate'))}</td></tr>"
        )

    return f"""
<table>
  <thead>
    <tr><th>Scenario</th><th>Retraced / Total</th><th>Retrace Rate</th></tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Overall</strong></td>
      <td>{n_retraces} / {n_breaks}</td>
      <td><strong>{rate}</strong></td>
    </tr>
    {rows}
    <tr>
      <td colspan="3"><em>Avg region age at break: {avg_age} bars</em></td>
    </tr>
  </tbody>
</table>"""


def _break_tod_table(stats: Dict, min_breaks_per_hour: int = 5) -> str:
    hourly = stats.get("hourly") or []
    if not hourly:
        return "<p><em>No broken-region time-of-day stats available.</em></p>"

    filtered = [row for row in hourly if int(row.get("n_breaks", 0)) >= min_breaks_per_hour]
    if not filtered:
        return (
            "<p><em>Time-of-day stats available, but no hour meets the minimum "
            f"sample filter ({min_breaks_per_hour} breaks/hour).</em></p>"
        )

    rows = []
    for row in filtered:
        rows.append(
            "<tr>"
            f"<td>{row.get('label', '')}</td>"
            f"<td>{row.get('n_breaks', 0)}</td>"
            f"<td>{row.get('n_retraces', 0)} ({_pct(row.get('retrace_rate'))})</td>"
            f"<td>{row.get('n_reach_next_region', 0)} ({_pct(row.get('reach_next_region_rate'))})</td>"
            f"<td>{row.get('n_neither', 0)} ({_pct(row.get('neither_rate'))})</td>"
            f"<td>{row.get('n_bull_breaks', 0)} / {row.get('n_bear_breaks', 0)}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr>"
        "<th>Hour</th><th>Breaks</th><th>Retrace</th><th>Reach Next Region</th>"
        "<th>Neither</th><th>Bull/Bear Breaks</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


_SIGNAL_LABELS = {
    "fresh": "Fresh Region",
    "touch": "Touch (Bounce)",
    "break": "Break (Continuation)",
    "retrace": "Break Retrace",
}

_TIER_COLORS = {
    "Most Robust": "#2e7d32",
    "High Quality": "#388e3c",
    "Solid": "#f57c00",
    "Marginal": "#9e9e9e",
}


def _results_table(results: list) -> str:
    header = (
        "<tr>"
        "<th>#</th><th>Signal</th><th>Mult</th><th>Lookback</th><th>Zone</th>"
        "<th>TP</th><th>SL</th><th>Trades</th>"
        "<th>Win%</th><th>PF</th><th>Net $</th><th>IS/OOS</th><th>Tier</th>"
        "</tr>"
    )
    rows = []
    for i, r in enumerate(results, 1):
        tier = r.get("tier", "")
        color = _TIER_COLORS.get(tier, "#555")
        oos = r.get("is_oos_degradation")
        oos_str = f"{float(oos):.3f}" if oos is not None else "—"
        pf = r.get("profit_factor")
        pf_str = f"{float(pf):.2f}" if pf is not None else "—"
        label = _SIGNAL_LABELS.get(r.get("signal_type", ""), r.get("signal_type", ""))
        rows.append(
            f"<tr>"
            f"<td>{i}</td>"
            f"<td>{label}</td>"
            f"<td>{r.get('size_multiplier','')}</td>"
            f"<td>{r.get('lookback','')}</td>"
            f"<td>{r.get('zone_type','')}</td>"
            f"<td>{r.get('tp_ticks','')}</td>"
            f"<td>{r.get('sl_ticks','')}</td>"
            f"<td>{r.get('n_trades','')}</td>"
            f"<td>{_pct(r.get('win_rate'))}</td>"
            f"<td>{pf_str}</td>"
            f"<td>{_fmt(r.get('total_net'), 0)}</td>"
            f"<td>{oos_str}</td>"
            f"<td style='color:{color};font-weight:bold'>{tier}</td>"
            f"</tr>"
        )

    return (
        "<div style='overflow-x:auto'>"
        "<table><thead>"
        + header
        + "</thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table></div>"
    )
