from __future__ import annotations

"""
Trade Pattern Audit Section
===========================
Renders per-run summary cards, a collapsible pattern correlation table,
and a collapsible color-coded per-trade audit table.

Data sources (populated by orchestrator before render):
  pkg.assets["trade_pattern_audit"]["audit_df"]      – per-trade DataFrame
  pkg.metadata["derived"]["trade_pattern_audit"]     – summary + diagnostics dicts

Section options (ctx["options"]):
  run_id         : str  – focus on a single run; null/omit to show all runs
  top_n_runs     : int  – cap the number of runs shown (default 20)
  show_per_trade : bool – include the per-trade table (default true)
  min_trades     : int  – skip runs with fewer than this many audited trades (default 1)
"""

import html
import json
from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CSS = """
<style>
/* === Trade Pattern Audit styles === */
.tpa-block {
  padding: 16px !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
  line-height: 1.4 !important;
  color: #111827 !important;
}
.tpa-block h2 {
  margin: 0 0 14px 0 !important;
  font-size: 20px !important;
  font-weight: 700 !important;
  color: #111827 !important;
}
.tpa-block h3 {
  margin: 0 0 10px 0 !important;
  font-size: 15px !important;
  font-weight: 700 !important;
  color: #111827 !important;
}

/* Cards */
.tpa-card {
  background: #ffffff !important;
  border: 1px solid #d1d5db !important;
  border-radius: 10px !important;
  padding: 14px !important;
  margin-bottom: 16px !important;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06) !important;
  color: #111827 !important;
}
.tpa-card * { color: #111827 !important; opacity: 1 !important; }

/* KPI pills row */
.tpa-kpi-row {
  display: flex !important;
  gap: 10px !important;
  flex-wrap: wrap !important;
  margin-bottom: 12px !important;
}
.tpa-kpi {
  border-radius: 8px !important;
  padding: 8px 14px !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  min-width: 120px !important;
  text-align: center !important;
}
.tpa-kpi-confirmed { background: #d1fae5 !important; border: 1px solid #6ee7b7 !important; }
.tpa-kpi-partial   { background: #fef3c7 !important; border: 1px solid #fcd34d !important; }
.tpa-kpi-rejected  { background: #fee2e2 !important; border: 1px solid #fca5a5 !important; }
.tpa-kpi-neutral   { background: #f3f4f6 !important; border: 1px solid #d1d5db !important; }

/* Profit table inside summary */
.tpa-profit-grid {
  display: grid !important;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)) !important;
  gap: 8px !important;
  margin-top: 10px !important;
}
.tpa-profit-cell {
  background: #f9fafb !important;
  border: 1px solid #e5e7eb !important;
  border-radius: 6px !important;
  padding: 8px !important;
  font-size: 12px !important;
}
.tpa-profit-cell b { display: block !important; margin-bottom: 4px !important; }

/* Tables */
table.tpa-table {
  width: 100% !important;
  border-collapse: collapse !important;
  font-size: 12px !important;
  background: #ffffff !important;
  margin-top: 8px !important;
}
.tpa-table th,
.tpa-table td {
  border: 1px solid #d1d5db !important;
  padding: 7px 9px !important;
  vertical-align: top !important;
  color: #111827 !important;
  word-break: break-word !important;
}
.tpa-table th { background: #f3f4f6 !important; font-weight: 700 !important; }
.tpa-table tr:nth-child(even) td { background: #fafafa !important; }

/* Verdict row colors */
.tpa-row-confirmed td { background: #ecfdf5 !important; }
.tpa-row-partial   td { background: #fffbeb !important; }
.tpa-row-rejected  td { background: #fef2f2 !important; }

/* Collapsible details */
details.tpa-details {
  border: 1px solid #e5e7eb !important;
  border-radius: 8px !important;
  padding: 10px 14px !important;
  margin-bottom: 12px !important;
  background: #ffffff !important;
}
details.tpa-details summary {
  cursor: pointer !important;
  font-weight: 600 !important;
  font-size: 14px !important;
  color: #1d4ed8 !important;
  user-select: none !important;
  list-style: none !important;
}
details.tpa-details summary::before {
  content: "▶ " !important;
  font-size: 11px !important;
}
details.tpa-details[open] summary::before { content: "▼ " !important; }

/* Diagnostics warning */
.tpa-warn {
  background: #fff7ed !important;
  border: 1px solid #fed7aa !important;
  border-radius: 6px !important;
  padding: 8px 12px !important;
  font-size: 12px !important;
  margin-bottom: 10px !important;
  color: #92400e !important;
}
.tpa-muted { color: #6b7280 !important; font-size: 11px !important; }
</style>
"""


def _audit_assets(pkg: Any) -> Dict[str, Any]:
    try:
        return (getattr(pkg, "assets", None) or {}).get("trade_pattern_audit") or {}
    except Exception:
        return {}


def _audit_meta(pkg: Any) -> Dict[str, Any]:
    try:
        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        return derived.get("trade_pattern_audit") or {}
    except Exception:
        return {}


def _audit_df(pkg: Any) -> pd.DataFrame:
    v = _audit_assets(pkg).get("audit_df")
    return v if isinstance(v, pd.DataFrame) else pd.DataFrame()


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return "—"


def _fmt_usd(v: Any) -> str:
    try:
        f = float(v)
        sign = "+" if f >= 0 else ""
        # Show extra decimal places when the value is very small but non-zero
        if f != 0.0 and abs(f) < 1.0:
            return f"{sign}${f:,.4f}"
        return f"{sign}${f:,.2f}"
    except Exception:
        return "—"


def _fmt_wr(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_n(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except Exception:
        return "—"


def _pick_run_ids(packages: Dict[str, Any], run_id: Optional[str], top_n: int) -> List[str]:
    if run_id and run_id in packages:
        return [run_id]
    ids = [r for r in packages if not str(r).startswith("__")]
    return ids[:max(1, int(top_n))]


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_diagnostics_warn(meta: Dict[str, Any]) -> str:
    diag = meta.get("diagnostics", {}) or {}
    issues = diag.get("issues") or []
    if not issues:
        return ""
    items = "".join(f"<li>{html.escape(str(i))}</li>" for i in issues)
    return f'<div class="tpa-warn"><b>Diagnostics:</b><ul style="margin:4px 0 0 16px">{items}</ul></div>'


def _render_summary_card(run_id: str, meta: Dict[str, Any], df: pd.DataFrame) -> str:
    summary = meta.get("summary", {}) or {}
    diag    = meta.get("diagnostics", {}) or {}
    counts  = diag.get("counts", {}) or {}

    n_audited   = counts.get("n_audited", len(df))
    n_confirmed = summary.get("n_confirmed", 0)
    n_partial   = summary.get("n_partial", 0)
    n_rejected  = summary.get("n_rejected", 0)
    pct_conf    = summary.get("pct_confirmed", 0)
    pct_part    = summary.get("pct_partial", 0)
    pct_rej     = summary.get("pct_rejected", 0)

    instrument = counts.get("instrument", "")
    contract   = counts.get("contract", "")
    timeframe  = counts.get("timeframe", "")
    meta_line  = f"{instrument} {contract} @ {timeframe}" if instrument else ""

    kpi_row = f"""
    <div class="tpa-kpi-row">
      <div class="tpa-kpi tpa-kpi-neutral">Trades Audited<br><b>{_fmt_n(n_audited)}</b></div>
      <div class="tpa-kpi tpa-kpi-confirmed">Confirmed<br><b>{_fmt_n(n_confirmed)} ({_fmt_pct(pct_conf)})</b></div>
      <div class="tpa-kpi tpa-kpi-partial">Partial<br><b>{_fmt_n(n_partial)} ({_fmt_pct(pct_part)})</b></div>
      <div class="tpa-kpi tpa-kpi-rejected">Rejected<br><b>{_fmt_n(n_rejected)} ({_fmt_pct(pct_rej)})</b></div>
    </div>
    """

    # Profit grid by verdict
    cells = ""
    for verdict, label, cls in [
        ("confirmed", "Confirmed", "tpa-kpi-confirmed"),
        ("partial",   "Partial",   "tpa-kpi-partial"),
        ("rejected",  "Rejected",  "tpa-kpi-rejected"),
    ]:
        avg     = summary.get(f"avg_profit_{verdict}")
        wr      = summary.get(f"win_rate_{verdict}")
        tot     = summary.get(f"total_profit_{verdict}")
        n_valid = summary.get(f"n_valid_{verdict}", "")
        n_nan   = summary.get(f"n_nan_{verdict}", 0)
        nan_note = f'<span style="color:#b45309;font-size:10px"> ({n_nan} trades had no P&L data)</span>' if n_nan else ""
        cells += f"""
        <div class="tpa-profit-cell {cls}">
          <b>{label}</b>
          Avg P&L: {_fmt_usd(avg)}<br>
          Win Rate: {_fmt_wr(wr)}<br>
          Total P&L: {_fmt_usd(tot)}<br>
          <span style="font-size:10px;color:#6b7280">n={n_valid} valid trades{""}</span>{nan_note}
        </div>"""

    profit_grid = f'<div class="tpa-profit-grid">{cells}</div>'

    warn = _render_diagnostics_warn(meta)

    return f"""
    <div class="tpa-card">
      <h3>{html.escape(str(run_id))}</h3>
      {f'<div class="tpa-muted">{html.escape(meta_line)}</div>' if meta_line else ""}
      {warn}
      {kpi_row}
      {profit_grid}
    </div>
    """


def _render_correlation_table(meta: Dict[str, Any]) -> str:
    summary = meta.get("summary", {}) or {}
    corr    = summary.get("pattern_correlation", {}) or {}
    if not corr:
        return "<p>No pattern correlation data available.</p>"

    rows = ""
    for pattern_key, stats in corr.items():
        if not isinstance(stats, dict):
            continue
        rows += f"""
        <tr>
          <td>{html.escape(str(pattern_key))}</td>
          <td>{_fmt_n(stats.get('n_fired'))}</td>
          <td>{_fmt_wr(stats.get('win_rate_fired'))}</td>
          <td>{_fmt_usd(stats.get('avg_profit_fired'))}</td>
          <td>{_fmt_n(stats.get('n_not_fired'))}</td>
          <td>{_fmt_wr(stats.get('win_rate_not_fired'))}</td>
          <td>{_fmt_usd(stats.get('avg_profit_not_fired'))}</td>
          <td>{html.escape(str(stats.get('weight', 1.0)))}</td>
        </tr>"""

    if not rows:
        return "<p>No pattern correlation rows.</p>"

    return f"""
    <table class="tpa-table">
      <thead><tr>
        <th>Pattern</th>
        <th>Fired (n)</th>
        <th>Win% When Fired</th>
        <th>Avg P&amp;L When Fired</th>
        <th>Not Fired (n)</th>
        <th>Win% When Not Fired</th>
        <th>Avg P&amp;L When Not Fired</th>
        <th>Weight</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def _render_trade_table(df: pd.DataFrame, max_rows: int = 500) -> str:
    if df is None or len(df) == 0:
        return "<p>No trade audit rows available.</p>"

    display_cols = [
        "trade_id", "entry_time", "direction", "profit",
        "n_patterns_fired", "confirming_score", "max_possible_score",
        "pct_confirmed", "confirming_patterns", "verdict",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    d2 = df[display_cols].head(max_rows).copy()

    # Format columns
    if "profit" in d2.columns:
        d2["profit"] = d2["profit"].apply(lambda x: f"${float(x):,.2f}" if pd.notna(x) else "—")
    if "pct_confirmed" in d2.columns:
        d2["pct_confirmed"] = d2["pct_confirmed"].apply(lambda x: f"{float(x):.1f}%" if pd.notna(x) else "—")
    if "entry_time" in d2.columns:
        d2["entry_time"] = d2["entry_time"].apply(
            lambda x: str(x)[:19] if pd.notna(x) else "—"
        )

    # Build row HTML with verdict color classes
    verdict_col  = df["verdict"].head(max_rows).tolist() if "verdict" in df.columns else [""] * len(d2)
    verdict_class = {
        "CONFIRMED": "tpa-row-confirmed",
        "PARTIAL":   "tpa-row-partial",
        "REJECTED":  "tpa-row-rejected",
    }

    header = "".join(f"<th>{html.escape(c)}</th>" for c in d2.columns)
    body   = ""
    for i, (_, row) in enumerate(d2.iterrows()):
        v   = verdict_col[i] if i < len(verdict_col) else ""
        cls = verdict_class.get(v, "")
        cells = "".join(f"<td>{html.escape(str(val) if pd.notna(val) else '—')}</td>" for val in row)
        body += f'<tr class="{cls}">{cells}</tr>\n'

    truncated = f'<div class="tpa-muted">Showing first {max_rows:,} of {len(df):,} trades.</div>' if len(df) > max_rows else ""

    return f"""
    {truncated}
    <table class="tpa-table">
      <thead><tr>{header}</tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_trade_pattern_audit(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.assets["trade_pattern_audit"] and pkg.metadata["derived"]["trade_pattern_audit"].
    """
    packages    = ctx.get("packages") or {}
    sec         = ctx.get("options") or {}

    focus_run_id  = sec.get("run_id")
    top_n_runs    = int(sec.get("top_n_runs", 20))
    show_per_trade = bool(sec.get("show_per_trade", True))
    min_trades     = int(sec.get("min_trades", 1))

    if not packages:
        return f"{_CSS}<div class='tpa-block'><h2>Trade Pattern Audit</h2><p>No packages loaded.</p></div>"

    run_ids = _pick_run_ids(packages, focus_run_id, top_n_runs)

    body = ""

    for rid in run_ids:
        pkg  = packages.get(rid)
        if pkg is None:
            continue
        meta = _audit_meta(pkg)
        df   = _audit_df(pkg)

        diag   = meta.get("diagnostics", {}) or {}
        counts = diag.get("counts", {}) or {}
        n_audited = counts.get("n_audited", len(df))

        if n_audited < min_trades and len(df) < min_trades:
            body += f"""
            <div class="tpa-card">
              <h3>{html.escape(str(rid))}</h3>
              <p class="tpa-muted">Skipped — fewer than {min_trades} audited trades.</p>
            </div>"""
            continue

        if not meta:
            body += f"""
            <div class="tpa-card">
              <h3>{html.escape(str(rid))}</h3>
              <div class="tpa-warn">No trade_pattern_audit metadata attached.
              Ensure trade_pattern_audit.enabled=true in the pattern_engine YAML block
              and that the orchestrator ran before rendering.</div>
            </div>"""
            continue

        # Summary card
        body += _render_summary_card(rid, meta, df)

        # Pattern correlation — collapsible
        corr_table = _render_correlation_table(meta)
        body += f"""
        <details class="tpa-details">
          <summary>Pattern Correlation — {html.escape(str(rid))}</summary>
          <div style="margin-top:10px">{corr_table}</div>
        </details>"""

        # Per-trade table — collapsible
        if show_per_trade and len(df) > 0:
            n_label = f"{len(df):,} trades"
            trade_tbl = _render_trade_table(df)
            legend = """
            <div style="margin-bottom:8px;font-size:11px">
              <span style="background:#ecfdf5;padding:2px 8px;border-radius:4px;margin-right:6px">&#9632; CONFIRMED</span>
              <span style="background:#fffbeb;padding:2px 8px;border-radius:4px;margin-right:6px">&#9632; PARTIAL</span>
              <span style="background:#fef2f2;padding:2px 8px;border-radius:4px">&#9632; REJECTED</span>
            </div>"""
            body += f"""
            <details class="tpa-details">
              <summary>Trade-by-Trade Audit — {html.escape(str(rid))} ({n_label})</summary>
              <div style="margin-top:10px">
                {legend}
                {trade_tbl}
              </div>
            </details>"""

    if not body:
        body = "<p>No runs with trade audit data found.</p>"

    return f"""
{_CSS}
<div class="tpa-block">
  <h2>Trade Pattern Audit</h2>
  <p class="tpa-muted">
    Each trade is scored against market patterns active at entry.
    <b>CONFIRMED</b> = weighted score &ge; threshold &nbsp;|&nbsp;
    <b>PARTIAL</b> = some patterns fired &nbsp;|&nbsp;
    <b>REJECTED</b> = no patterns confirmed the entry.
  </p>
  {body}
</div>
"""
