from __future__ import annotations

"""
Strategy Discovery — Signal Rule Walk-Forward Validation
=========================================================
Reads: pkg.metadata["derived"]["strategy_discovery"]["signal_validation"]
       (from the __market_discovery__ synthetic package)

Shows:
  1. Summary card — verdict, n_stable/moderate/fragile, avg degradation
  2. Rule consistency table — cross-fold rule stability
  3. Per-fold accordion — IS/OOS signal counts + rule metrics per fold
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.sdsv-wrap  { display:flex; flex-direction:column; gap:14px; }
.sdsv-card  { background:rgba(255,255,255,0.035); border-radius:12px;
              padding:14px 16px; border:1px solid rgba(255,255,255,0.07); }

/* Verdict banner */
.sdsv-verdict { display:flex; align-items:center; gap:14px;
                padding:10px 16px; border-radius:10px; margin-bottom:12px; }
.sdsv-verdict-pass { background:rgba(72,187,120,0.12); border:1px solid rgba(72,187,120,0.3); }
.sdsv-verdict-warn { background:rgba(237,137,54,0.12);  border:1px solid rgba(237,137,54,0.3); }
.sdsv-verdict-fail { background:rgba(245,101,101,0.12); border:1px solid rgba(245,101,101,0.3); }
.sdsv-verdict-skip { background:rgba(160,174,192,0.10); border:1px solid rgba(160,174,192,0.2); }
.sdsv-verdict-badge { font-size:.85rem; font-weight:800; letter-spacing:.06em;
                       padding:4px 12px; border-radius:6px; white-space:nowrap; }
.sdsv-verdict-pass .sdsv-verdict-badge { background:rgba(72,187,120,0.2); color:#68d391; }
.sdsv-verdict-warn .sdsv-verdict-badge { background:rgba(237,137,54,0.2);  color:#f6ad55; }
.sdsv-verdict-fail .sdsv-verdict-badge { background:rgba(245,101,101,0.2); color:#fc8181; }
.sdsv-verdict-skip .sdsv-verdict-badge { background:rgba(160,174,192,0.15); color:#a0aec0; }
.sdsv-verdict-text  { font-size:.82rem; opacity:.75; }

/* Stats row */
.sdsv-stats { display:flex; flex-wrap:wrap; gap:20px; padding:6px 0 10px; }
.sdsv-stat  { min-width:80px; }
.sdsv-stat-val { font-size:1.05rem; font-weight:700; }
.sdsv-stat-lbl { font-size:.68rem; opacity:.55; margin-top:1px; }

/* Rule consistency table */
.sdsv-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdsv-table th { background:rgba(255,255,255,0.06); padding:5px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdsv-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdsv-table tr:last-child td { border-bottom:none; }
.sdsv-table tr:hover td { background:rgba(255,255,255,0.025); }

/* Verdict pill */
.sdsv-pill { display:inline-block; padding:2px 8px; border-radius:8px;
             font-size:10px; font-weight:700; }
.sdsv-pill-stable   { background:rgba(72,187,120,0.2); color:#68d391; }
.sdsv-pill-moderate { background:rgba(237,137,54,0.2);  color:#f6ad55; }
.sdsv-pill-fragile  { background:rgba(245,101,101,0.2); color:#fc8181; }

/* Fold accordion */
.sdsv-lbl { font-size:.72rem; font-weight:600; text-transform:uppercase;
            letter-spacing:.05em; opacity:.5; margin:10px 0 6px; }
.sdsv-fold-row { display:flex; gap:16px; flex-wrap:wrap; font-size:.78rem;
                 padding:4px 0; opacity:.8; border-bottom:1px solid rgba(255,255,255,0.04); }
.sdsv-fold-row:last-child { border-bottom:none; }
.sdsv-fold-key   { opacity:.55; width:110px; flex-shrink:0; }
.sdsv-fold-badge { font-size:10px; font-weight:700; padding:1px 6px;
                   border-radius:5px; margin-left:4px; }
.sdsv-muted { opacity:.55; font-size:.82rem; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, d: int = 2, default: str = "—") -> str:
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


def _verdict_class(v: str) -> str:
    return {
        "PASS": "sdsv-verdict-pass",
        "WARN": "sdsv-verdict-warn",
        "FAIL": "sdsv-verdict-fail",
    }.get(v, "sdsv-verdict-skip")


def _pill(v: str) -> str:
    cls = {"stable": "sdsv-pill-stable", "moderate": "sdsv-pill-moderate"}.get(v, "sdsv-pill-fragile")
    return f'<span class="sdsv-pill {cls}">{html.escape(v)}</span>'


def _deg_color(d: Optional[float]) -> str:
    if d is None:
        return "#888"
    if d <= 0.05:
        return "#68d391"
    if d <= 0.15:
        return "#f6ad55"
    return "#fc8181"


# ---------------------------------------------------------------------------
# Summary card
# ---------------------------------------------------------------------------

def _render_summary(sv: Dict[str, Any]) -> str:
    summary = sv.get("summary") or {}
    verdict = str(summary.get("verdict", "SKIP"))
    vc = _verdict_class(verdict)
    assessment = html.escape(str(summary.get("assessment", "")))
    avg_deg = summary.get("avg_wr_degradation")
    n_folds = summary.get("n_folds_completed", 0)

    stats = [
        (str(summary.get("n_consistent_rules", 0)), "Stable Rules"),
        (str(summary.get("n_moderate_rules", 0)), "Moderate Rules"),
        (str(summary.get("n_fragile_rules", 0)), "Fragile Rules"),
        (str(n_folds), "Folds Completed"),
        (_fmt_pct(avg_deg) if avg_deg is not None else "—", "Avg WR Degradation"),
    ]
    stats_html = "".join(
        f'<div class="sdsv-stat">'
        f'<div class="sdsv-stat-val">{v}</div>'
        f'<div class="sdsv-stat-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for v, lbl in stats
    )

    return (
        f'<div class="sdsv-verdict {vc}">'
        f'<span class="sdsv-verdict-badge">{verdict}</span>'
        f'<span class="sdsv-verdict-text">{assessment}</span>'
        f'</div>'
        f'<div class="sdsv-stats">{stats_html}</div>'
    )


# ---------------------------------------------------------------------------
# Rule consistency table
# ---------------------------------------------------------------------------

def _render_rule_consistency(sv: Dict[str, Any]) -> str:
    rules = sv.get("rule_consistency") or []
    if not rules:
        return '<div class="sdsv-muted">No cross-fold rule consistency data.</div>'

    rows = ""
    for r in rules:
        verdict = str(r.get("verdict", "fragile"))
        deg = r.get("avg_degradation")
        rows += f"""
        <tr>
          <td>{_pill(verdict)}</td>
          <td style="font-size:.78rem">{html.escape(str(r.get("rule_str", ""))[:80])}</td>
          <td style="text-align:center">{r.get("n_folds_found", "—")}</td>
          <td style="text-align:center">{r.get("n_folds_consistent", "—")}</td>
          <td style="text-align:center">{_fmt_pct(r.get("avg_is_win_rate"))}</td>
          <td style="text-align:center">{_fmt_pct(r.get("avg_oos_win_rate"))}</td>
          <td style="text-align:center;color:{_deg_color(deg)};font-weight:700">{_fmt_pct(deg)}</td>
        </tr>"""

    return f"""
    <div class="sdsv-lbl">Rule Consistency Across Folds</div>
    <div style="overflow-x:auto">
    <table class="sdsv-table">
      <thead><tr>
        <th>Verdict</th>
        <th>Rule</th>
        <th>Folds Found</th>
        <th>Folds ✓</th>
        <th>Avg IS WR</th>
        <th>Avg OOS WR</th>
        <th>Avg Degrad.</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


# ---------------------------------------------------------------------------
# Per-fold accordion
# ---------------------------------------------------------------------------

def _render_fold_rules(rules: List[Dict[str, Any]]) -> str:
    if not rules:
        return '<div class="sdsv-muted" style="font-size:.78rem">No rules evaluated.</div>'
    rows = ""
    for r in rules:
        consistent = r.get("consistent")
        badge = ""
        if consistent is True:
            badge = '<span class="sdsv-fold-badge" style="background:rgba(72,187,120,0.2);color:#68d391">✓</span>'
        elif consistent is False:
            badge = '<span class="sdsv-fold-badge" style="background:rgba(245,101,101,0.2);color:#fc8181">✗</span>'
        deg = r.get("degradation")
        rows += f"""
        <div class="sdsv-fold-row">
          <span>{html.escape(str(r.get("rule_str", ""))[:60])}{badge}</span>
          <span style="white-space:nowrap;opacity:.7">IS:{_fmt_pct(r.get("is_win_rate"))} OOS:{_fmt_pct(r.get("oos_win_rate"))}</span>
          <span style="white-space:nowrap;color:{_deg_color(deg)}">Δ{_fmt_pct(deg)}</span>
          <span style="opacity:.45">IS n={r.get("is_n_signals","?")}</span>
        </div>"""
    return rows


def _render_folds(sv: Dict[str, Any]) -> str:
    folds = sv.get("fold_results") or []
    if not folds:
        return ""

    fold_parts = []
    for fold in folds:
        f_num = fold.get("fold", "?")
        skipped = fold.get("skipped", False)
        is_s = fold.get("is_start", "?")
        oos_e = fold.get("oos_end", "?")

        if skipped:
            reason = html.escape(str(fold.get("skip_reason", "skipped")))
            fold_parts.append(
                f'<details style="margin:4px 0">'
                f'<summary style="font-size:.75rem;cursor:pointer;opacity:.6">'
                f'Fold {f_num} ({is_s} → {oos_e}) — skipped: {reason}'
                f'</summary></details>'
            )
            continue

        n_is = fold.get("n_is_signals", 0)
        n_oos = fold.get("n_oos_signals", 0)
        n_rules = fold.get("n_rules_found", 0)
        rules_html = _render_fold_rules(fold.get("rules") or [])
        fold_parts.append(f"""
        <details style="margin:4px 0">
          <summary style="font-size:.75rem;cursor:pointer;opacity:.8">
            Fold {f_num} &nbsp;·&nbsp; {html.escape(str(is_s))} → {html.escape(str(oos_e))}
            &nbsp;·&nbsp; IS={n_is} signals &nbsp;·&nbsp; OOS={n_oos} signals
            &nbsp;·&nbsp; {n_rules} rules discovered
          </summary>
          <div style="margin-top:6px;padding-left:8px">{rules_html}</div>
        </details>""")

    return (
        '<div class="sdsv-lbl">Per-Fold Detail</div>'
        + "".join(fold_parts)
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_signal_validation(ctx: Dict[str, Any]) -> str:
    """
    Render signal rule walk-forward validation results.

    Reads from the __market_discovery__ synthetic package only.
    The validation is trade-free: IS/OOS split on pattern signal firing times.
    """
    packages = ctx.get("packages") or {}

    parts = [_CSS, '<div class="sdsv-wrap">']
    found = False

    for run_id, pkg in packages.items():
        if not str(run_id).startswith("__market_discovery__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        sv = sd.get("signal_validation") if isinstance(sd, dict) else None
        if sv is None:
            continue

        error = sv.get("error")
        if error:
            parts.append(
                f'<div class="sdsv-card" style="color:#fc8181">'
                f'Signal validation error: {html.escape(str(error))}'
                f'</div>'
            )
            found = True
            break

        diag = sv.get("diagnostics") or {}
        issues = diag.get("issues") or []

        parts.append(f'<div class="sdsv-card">')
        parts.append(
            '<div style="font-size:.75rem;font-weight:800;text-transform:uppercase;'
            'letter-spacing:.06em;color:#68d391;margin-bottom:8px;">'
            'Signal Rule Walk-Forward Validation'
            '<span style="font-size:.65rem;opacity:.5;font-weight:400;margin-left:8px;">'
            'corpus-only · no trade bias'
            '</span></div>'
        )
        parts.append(_render_summary(sv))
        parts.append(_render_rule_consistency(sv))
        parts.append(_render_folds(sv))

        if issues:
            issue_html = "".join(
                f'<div style="font-size:.72rem;color:#f6ad55">⚠ {html.escape(str(i))}</div>'
                for i in issues
            )
            parts.append(f'<div style="margin-top:8px">{issue_html}</div>')

        parts.append('</div>')
        found = True
        break

    if not found:
        parts.append(
            '<div class="sdsv-card sdsv-muted">'
            'No signal validation data found. '
            'Add <code>market_discovery</code> to <code>pattern_engine.scopes</code> '
            'and enable <code>strategy_discovery: enabled: true</code>.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
