from __future__ import annotations

"""
Strategy Discovery — Signal Entry Discovery
============================================
Read-only renderer for ``sd["signal_entry_discovery"]``.

Shows entry rules discovered from the full pattern signal corpus
(all pattern firings, not just executed trades).

Sections rendered per run
--------------------------
1. Corpus Health Card — n_signals, n_patterns, corpus baseline win_rate / avg_ticks
2. Signal Rule Table — ranked rules: rule_str, n_signals, win_rate, avg_ticks,
                        win_rate_lift, mfe_p50, mae_p50, selectivity, score
3. Diagnostics        — atom/candidate counts, issues, profit_col_used

Options (from YAML)
-------------------
  max_runs      : int   (default 5)
  show_all_runs : bool  (default false)
  top_n_rules   : int   (default 20 — trim display even if backend returned more)
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Formatting helpers
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


def _lift_color(v: Optional[float]) -> str:
    if v is None:
        return "#888"
    return "#2ecc71" if (v or 0) > 0 else "#e74c3c"


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdse-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdse-card  { background:rgba(255,255,255,0.035); border-radius:12px;
              padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdse-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
              letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdse-muted { opacity:.65; font-size:.85rem; }
.sdse-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }

/* Corpus health row */
.sdse-health { display:flex; flex-wrap:wrap; gap:16px; padding:8px 0 12px; }
.sdse-stat   { min-width:90px; }
.sdse-stat-val { font-size:1.05rem; font-weight:700; }
.sdse-stat-lbl { font-size:.7rem; opacity:.6; }

/* Rule table */
.sdse-table { border-collapse:collapse; width:100%; font-size:0.84rem; }
.sdse-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdse-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdse-table tr:last-child td { border-bottom:none; }
.sdse-table tr:hover td { background:rgba(255,255,255,0.03); }

/* Condition pill */
.sdse-cond { display:inline-block; padding:2px 7px; border-radius:10px;
             background:rgba(99,179,237,0.15); color:#90cdf4;
             font-size:11px; margin:1px 2px; white-space:nowrap; }

/* Source badge */
.sdse-badge { display:inline-block; padding:1px 6px; border-radius:8px;
              font-size:10px; font-weight:700; margin-left:4px; }
.sdse-badge-corpus    { background:rgba(72,187,120,0.2); color:#68d391; }
.sdse-badge-execution { background:rgba(99,179,237,0.2); color:#90cdf4; }

/* Family breakdown table */
.sdse-fam-table { border-collapse:collapse; width:100%; font-size:0.80rem; }
.sdse-fam-table th { background:rgba(255,255,255,0.06); padding:4px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdse-fam-table td { padding:4px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdse-fam-table tr:last-child td { border-bottom:none; }
.sdse-fam-table tr:hover td { background:rgba(255,255,255,0.025); }
.sdse-fam-bar { display:inline-block; height:8px; border-radius:3px;
                background:rgba(72,187,120,0.5); vertical-align:middle; margin-left:4px; }
</style>
"""


# ---------------------------------------------------------------------------
# Corpus health card
# ---------------------------------------------------------------------------

def _render_corpus_health(signal_corpus: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    n_signals = signal_corpus.get("n_signals") or baseline.get("n_signals") or 0
    n_patterns = signal_corpus.get("n_patterns") or baseline.get("n_patterns") or 0
    cb = signal_corpus.get("corpus_baseline") or baseline or {}
    base_wr = cb.get("win_rate")
    base_avg = cb.get("avg_ticks")

    stats = [
        (str(int(n_signals)), "Total Signals"),
        (str(int(n_patterns)), "Patterns"),
        (_fmt_pct(base_wr), "Baseline Win Rate"),
        (_fmt(base_avg, 1), "Baseline Avg Ticks"),
    ]
    items = "".join(
        f'<div class="sdse-stat">'
        f'<div class="sdse-stat-val">{v}</div>'
        f'<div class="sdse-stat-lbl">{html.escape(label)}</div>'
        f'</div>'
        for v, label in stats
    )
    return (
        f'<div class="sdse-lbl">Signal Corpus</div>'
        f'<div class="sdse-health">{items}</div>'
    )


# ---------------------------------------------------------------------------
# Family breakdown table
# ---------------------------------------------------------------------------

def _render_family_breakdown(family_breakdown: List[Dict[str, Any]]) -> str:
    if not family_breakdown:
        return ""

    max_wr = max((r.get("win_rate") or 0.0) for r in family_breakdown) or 1.0

    rows = ""
    for r in family_breakdown:
        family   = html.escape(str(r.get("family", "?")))
        n_sig    = r.get("n_signals", 0)
        n_str    = r.get("n_structures", 0)
        wr       = r.get("win_rate")
        avg_t    = r.get("avg_ticks")
        wr_lift  = r.get("win_rate_lift")
        sel      = r.get("selectivity")
        mfe      = r.get("mfe_p50")
        mae      = r.get("mae_p50")

        # Win-rate bar width proportional to best family
        bar_w = int(min(100, max(0, ((wr or 0.0) / max_wr) * 80)))

        lift_str = (
            f'<span style="color:{_lift_color(wr_lift)};font-weight:700">'
            f'{_fmt_pct(wr_lift, 1)}</span>'
        ) if wr_lift is not None else "—"

        wr_str = (
            f'<span style="color:{_score_color((wr or 0) * 100)}">'
            f'{_fmt_pct(wr)}</span>'
            f'<span class="sdse-fam-bar" style="width:{bar_w}px"></span>'
        ) if wr is not None else "—"

        rows += (
            f"<tr>"
            f"<td><strong>{family}</strong></td>"
            f"<td>{n_sig:,}</td>"
            f"<td>{n_str}</td>"
            f"<td>{wr_str}</td>"
            f"<td>{lift_str}</td>"
            f"<td>{_fmt(avg_t, 1)}</td>"
            f"<td>{_fmt(sel, 3)}</td>"
            f"<td>{_fmt(mfe, 1)}</td>"
            f"<td>{_fmt(mae, 1)}</td>"
            f"</tr>"
        )

    return (
        '<div class="sdse-lbl">Pattern Family Breakdown</div>'
        '<div style="overflow-x:auto">'
        '<table class="sdse-fam-table">'
        "<thead><tr>"
        "<th>Family</th><th>Signals</th><th>Structures</th>"
        "<th>Win Rate</th><th>WR Lift</th><th>Avg Ticks</th>"
        "<th>Selectivity</th><th>MFE p50</th><th>MAE p50</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table></div>"
    )


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

def _render_conditions(conditions: List[Dict[str, Any]]) -> str:
    if not conditions:
        return "—"
    return " ".join(
        f'<span class="sdse-cond">'
        f'{html.escape(str(c.get("description", c.get("column", "?"))))}'
        f'</span>'
        for c in conditions
    )


def _render_signal_rules_table(top_rules: List[Dict[str, Any]], top_n: int) -> str:
    if not top_rules:
        return '<div class="sdse-muted">No qualifying signal rules found.</div>'

    display_rules = top_rules[:top_n]
    rows = ""
    for r in display_rules:
        rank      = r.get("rank", "—")
        score     = r.get("score")
        conds     = r.get("conditions") or []
        source    = str(r.get("_source", ""))
        n_sig     = r.get("n_signals")
        wr        = r.get("win_rate")
        avg_t     = r.get("avg_ticks")
        wr_lift   = r.get("win_rate_lift") or r.get("wr_lift")
        sel       = r.get("selectivity")
        mfe       = r.get("mfe_p50")
        mae       = r.get("mae_p50")

        wr_lift_str = (
            f'<span style="color:{_lift_color(wr_lift)};font-weight:700">'
            f'{_fmt_pct(wr_lift, 1)}</span>'
            if wr_lift is not None else "—"
        )

        badge = ""
        if source == "corpus":
            badge = '<span class="sdse-badge sdse-badge-corpus">corpus</span>'
        elif source == "execution":
            badge = '<span class="sdse-badge sdse-badge-execution">trade</span>'

        rows += f"""
        <tr>
          <td style="text-align:center;font-weight:900;opacity:.7">{rank}</td>
          <td>{_render_conditions(conds)}{badge}</td>
          <td style="text-align:center">
            <span style="color:{_score_color(score)};font-weight:700">{_fmt(score, 1)}</span>
          </td>
          <td style="text-align:center;font-weight:600">{n_sig if n_sig is not None else "—"}</td>
          <td style="text-align:center">{_fmt_pct(wr)}</td>
          <td style="text-align:center">{_fmt(avg_t, 1)}</td>
          <td style="text-align:center">{wr_lift_str}</td>
          <td style="text-align:center;opacity:.8">{_fmt(mfe, 1)}</td>
          <td style="text-align:center;opacity:.8">{_fmt(mae, 1)}</td>
          <td style="text-align:center;opacity:.6">{_fmt_pct(sel)}</td>
        </tr>"""

    return f"""
    <div class="sdse-lbl">Top Signal Entry Rule Candidates</div>
    <div style="overflow-x:auto">
    <table class="sdse-table">
      <thead><tr>
        <th>#</th>
        <th>Conditions</th>
        <th>Score ▼</th>
        <th>Signals</th>
        <th>Win Rate</th>
        <th>Avg Ticks</th>
        <th>WR Lift</th>
        <th>MFE p50</th>
        <th>MAE p50</th>
        <th>Select.</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

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
        f"Signals: {diag.get('n_signals', '—')}",
        f"Patterns: {diag.get('n_patterns', '—')}",
        f"Atoms: {diag.get('n_atoms', '—')}",
        f"Candidates: {diag.get('n_candidates', '—')}",
        f"Valid: {diag.get('n_valid_candidates', '—')}",
        f"Horizon: {diag.get('primary_horizon', '—')} bars",
        f"Profit col: {diag.get('profit_col_used', '—')}",
    ]
    return (
        f'<div class="sdse-lbl">Diagnostics</div>'
        f'<div style="font-size:11px;opacity:.6">{" &nbsp;·&nbsp; ".join(lines)}</div>'
        + issue_html
    )


# ---------------------------------------------------------------------------
# Per-run renderer
# ---------------------------------------------------------------------------

def _render_run_signal_entries(run_id: str, sed: Dict[str, Any], top_n: int) -> str:
    if not sed:
        return f'<div class="sdse-muted">No signal entry discovery data for {html.escape(run_id)}.</div>'

    error = sed.get("error")
    if error:
        return (
            f'<div class="sdse-muted" style="color:#e74c3c">'
            f'Signal entry discovery error ({html.escape(run_id)}): '
            f'{html.escape(str(error))}</div>'
        )

    if sed.get("skipped"):
        reason = sed.get("reason", "")
        return (
            f'<div class="sdse-muted">Signal entry discovery skipped'
            + (f' — {html.escape(str(reason))}' if reason else "")
            + f' for {html.escape(run_id)}.</div>'
        )

    parts: List[str] = []

    signal_corpus = sed.get("signal_corpus") or {}
    baseline = sed.get("baseline") or {}
    top_rules = sed.get("top_signal_rules") or []
    diag = sed.get("diagnostics") or {}

    family_breakdown = sed.get("family_breakdown") or []
    parts.append(_render_corpus_health(signal_corpus, baseline))
    parts.append(_render_family_breakdown(family_breakdown))
    parts.append(_render_signal_rules_table(top_rules, top_n))
    parts.append(_render_diagnostics(diag))

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_signal_entries(ctx: Dict[str, Any]) -> str:
    """
    Render signal-corpus entry discovery results.

    Shows the market corpus (unbiased) result first — from the
    __market_discovery__ synthetic package produced by the pattern engine's
    market_discovery scope. This result has no trade bias: it discovers entry
    conditions purely from pattern signal firings across the full market history.

    Per-run results (tied to each strategy's trade window) follow below.

    Reads: pkg.metadata["derived"]["strategy_discovery"]["signal_entry_discovery"]
    """
    packages = ctx.get("packages") or {}
    options = ctx.get("options") or {}

    max_runs = int(options.get("max_runs", 5))
    show_all_runs = bool(options.get("show_all_runs", False))
    top_n_rules = int(options.get("top_n_rules", 20))

    parts = [_CSS, '<div class="sdse-wrap">']

    shown_total = 0

    # -----------------------------------------------------------------------
    # 1. Market corpus block — unbiased, no executed trades
    # -----------------------------------------------------------------------
    market_corpus_html = ""
    for run_id, pkg in packages.items():
        if not str(run_id).startswith("__market_discovery__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        sed = sd.get("signal_entry_discovery") if isinstance(sd, dict) else None
        if sed is None:
            continue
        market_corpus_html = _render_run_signal_entries("Market Corpus", sed, top_n_rules)
        break

    if market_corpus_html:
        parts.append(f"""
        <div class="sdse-card" style="border-color:rgba(72,187,120,0.35);background:rgba(72,187,120,0.04);">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
            <span style="font-size:.75rem;font-weight:800;text-transform:uppercase;
                         letter-spacing:.06em;color:#68d391;">Market Corpus Discovery</span>
            <span style="font-size:.7rem;opacity:.6;font-style:italic;">
              Full market history · no trade bias · pattern engine signals only
            </span>
          </div>
          {market_corpus_html}
        </div>""")
        shown_total += 1
    else:
        parts.append(
            '<div class="sdse-card" style="border-color:rgba(72,187,120,0.2)">'
            '<div style="font-size:.75rem;font-weight:700;color:#68d391;margin-bottom:4px;">'
            'MARKET CORPUS DISCOVERY</div>'
            '<div class="sdse-muted" style="font-size:.82rem">'
            'Not yet available. Add <code>market_discovery</code> to '
            '<code>pattern_engine.scopes</code> in your YAML to enable unbiased '
            'signal entry discovery across the full market history without requiring '
            'any uploaded strategy data.'
            '</div></div>'
        )

    # -----------------------------------------------------------------------
    # 2. Per-run results (tied to each strategy's executed trade window)
    # -----------------------------------------------------------------------
    shown_runs = 0
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        if not show_all_runs and shown_runs >= max_runs:
            break

        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        if not isinstance(sd, dict):
            continue
        sed = sd.get("signal_entry_discovery")
        if sed is None:
            continue

        run_html = _render_run_signal_entries(str(run_id), sed, top_n_rules)
        short_id = html.escape(str(run_id)[:60])
        parts.append(f"""
        <div class="sdse-card">
          <details>
            <summary class="sdse-run-hdr">{short_id}
              <span style="font-size:.65rem;opacity:.45;font-weight:400;margin-left:6px;">
                (signals during this run's trade window)
              </span>
            </summary>
            <div style="margin-top:8px">{run_html}</div>
          </details>
        </div>""")
        shown_runs += 1
        shown_total += 1

    if shown_total == 0:
        parts.append(
            '<div class="sdse-card sdse-muted">'
            'No signal entry discovery data found. '
            'Enable <code>pattern_engine: enabled: true</code>, add '
            '<code>market_discovery</code> to <code>pattern_engine.scopes</code>, '
            'and enable <code>strategy_discovery: enabled: true</code> in your YAML.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
