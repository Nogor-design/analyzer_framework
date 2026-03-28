from __future__ import annotations

"""
Strategy Discovery — Feature Importance
==========================================
HTML renderer for the importance results produced by importance.py.

Two views:

1. Cross-run feature frequency table
   Aggregates `top_features` across all runs to find which features
   consistently predict winning trades regardless of strategy.

2. Per-run deep-dive
   Numeric point-biserial correlations, categorical Cramér's V effects,
   and optional RF importance ranking.

Section options (ctx["options"]):
  max_runs          : int   — per-run cards shown (default 5)
  show_all_runs     : bool  — show every run (default false)
  top_n_features    : int   — features in per-run tables (default 12)
  show_cross_run    : bool  — cross-run frequency table (default true)
  show_rf           : bool  — show RF importance if available (default true)
"""

import html
from collections import Counter
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 3, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return default


def _corr_color(v: Optional[float]) -> str:
    if v is None:
        return "#888"
    av = abs(float(v))
    if av >= 0.15:
        return "#2ecc71"
    if av >= 0.08:
        return "#f39c12"
    return "#9ca3af"


def _bar(v: float, max_v: float, width: int = 80) -> str:
    """Horizontal bar proportional to v/max_v."""
    pct = min(1.0, abs(v) / max(abs(max_v), 1e-9))
    w   = max(2, int(pct * width))
    color = "#2ecc71" if v >= 0 else "#e74c3c"
    return (
        f'<span style="display:inline-block;width:{w}px;height:8px;'
        f'background:{color};border-radius:2px;vertical-align:middle"></span>'
    )


_CSS = """
<style>
.sdfi-wrap  { display:flex; flex-direction:column; gap:12px; }
.sdfi-card  { background:rgba(255,255,255,0.035); border-radius:12px;
              padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdfi-lbl   { font-size:0.75rem; font-weight:600; text-transform:uppercase;
              letter-spacing:.05em; opacity:.5; margin:8px 0 4px; }
.sdfi-table { border-collapse:collapse; width:100%; font-size:0.82rem; }
.sdfi-table th { background:rgba(255,255,255,0.06); padding:6px 10px;
  text-align:left; font-weight:600; white-space:nowrap;
  border-bottom:1px solid rgba(255,255,255,0.1); }
.sdfi-table td { padding:5px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  vertical-align:middle; }
.sdfi-table tr:last-child td { border-bottom:none; }
.sdfi-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdfi-muted  { opacity:.65; font-size:.85rem; }
.sdfi-run-hdr { font-size:.8rem; font-weight:700; margin:8px 0 4px; opacity:.8; }
.sdfi-badge  { display:inline-block; padding:2px 7px; border-radius:10px;
               background:rgba(99,102,241,0.15); color:#a5b4fc;
               font-size:11px; margin:1px 2px; white-space:nowrap; }
.sdfi-top-badge { display:inline-block; padding:1px 5px; border-radius:8px;
                  background:rgba(16,185,129,0.15); color:#6ee7b7;
                  font-size:10px; font-weight:700; margin-left:4px; }
</style>
"""


# ---------------------------------------------------------------------------
# Cross-run aggregation
# ---------------------------------------------------------------------------

def _build_cross_run_table(run_items: List[tuple]) -> str:
    """
    run_items: [(run_id, importance_dict), ...]

    Shows feature frequency (how many runs list it in top_features)
    plus the median |correlation| across runs where available.
    """
    if not run_items:
        return '<div class="sdfi-muted">No cross-run data.</div>'

    # Count feature frequency in top_features across runs
    freq: Counter = Counter()
    # Collect abs_correlation values per feature across runs
    corr_by_feat: Dict[str, List[float]] = {}

    for _, imp in run_items:
        if not imp or imp.get("error"):
            continue
        for feat in (imp.get("top_features") or []):
            freq[feat] += 1

        for row in (imp.get("numeric_correlations") or []):
            feat = row.get("feature")
            ac   = row.get("abs_correlation")
            if feat and ac is not None:
                corr_by_feat.setdefault(feat, []).append(float(ac))

        for row in (imp.get("categorical_effects") or []):
            feat = row.get("feature")
            cv   = row.get("cramers_v")
            if feat and cv is not None:
                corr_by_feat.setdefault(feat, []).append(float(cv))

    if not freq:
        return '<div class="sdfi-muted">No top_features found across runs.</div>'

    n_runs = len(run_items)
    rows = ""
    for feat, count in freq.most_common(20):
        vals = corr_by_feat.get(feat) or []
        median_v = sorted(vals)[len(vals) // 2] if vals else None
        pct = count / n_runs * 100
        bar_w = max(4, int(pct / 100 * 120))
        runs_str = f"{count}/{n_runs}"
        rows += (
            f'<tr>'
            f'<td style="font-size:12px">{html.escape(str(feat))}</td>'
            f'<td style="text-align:right;font-weight:700;color:{"#2ecc71" if count == n_runs else "#f39c12"}">'
            f'{runs_str}</td>'
            f'<td>'
            f'<span style="display:inline-block;width:{bar_w}px;height:8px;'
            f'background:#6366f1;border-radius:2px;vertical-align:middle"></span>'
            f' <span style="opacity:.6;font-size:11px">{pct:.0f}%</span>'
            f'</td>'
            f'<td style="text-align:right;color:{_corr_color(median_v)}">'
            f'{_fmt(median_v, 3) if median_v is not None else "—"}</td>'
            f'</tr>'
        )

    return (
        f'<div class="sdfi-lbl">Cross-Run Feature Frequency (top_features appearances)</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdfi-table">'
        f'<thead><tr>'
        f'<th>Feature</th>'
        f'<th style="text-align:right">Runs ▼</th>'
        f'<th>Frequency</th>'
        f'<th style="text-align:right">Median |corr|</th>'
        f'</tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'</table></div>'
        f'<div style="font-size:11px;opacity:.5;margin-top:4px">'
        f'{n_runs} run(s) analysed · |corr| = abs point-biserial or Cramér\'s V</div>'
    )


# ---------------------------------------------------------------------------
# Per-run renderers
# ---------------------------------------------------------------------------

def _render_top_features(top_features: List[str]) -> str:
    if not top_features:
        return ""
    badges = " ".join(
        f'<span class="sdfi-badge">{html.escape(str(f))}</span>'
        for f in top_features
    )
    return f'<div class="sdfi-lbl">Top Features (combined rank)</div><div>{badges}</div>'


def _render_numeric_table(rows: List[Dict[str, Any]], top_n: int) -> str:
    if not rows:
        return '<div class="sdfi-muted">No numeric correlations.</div>'

    max_ac = max((r.get("abs_correlation") or 0.0) for r in rows[:top_n]) or 1.0
    table_rows = ""
    for r in rows[:top_n]:
        feat = html.escape(str(r.get("feature", "?")))
        corr = r.get("correlation")
        ac   = r.get("abs_correlation") or 0.0
        rank = r.get("rank", "—")
        sign = "+" if (corr or 0) >= 0 else ""
        col  = _corr_color(corr)
        table_rows += (
            f'<tr>'
            f'<td style="text-align:center;opacity:.6">{rank}</td>'
            f'<td style="font-size:12px">{feat}</td>'
            f'<td style="text-align:right;color:{col};font-weight:700">'
            f'{sign}{_fmt(corr, 4)}</td>'
            f'<td>{_bar(ac, max_ac)}</td>'
            f'</tr>'
        )

    return (
        f'<div class="sdfi-lbl">Numeric Correlations (point-biserial, win/loss)</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdfi-table">'
        f'<thead><tr>'
        f'<th>#</th><th>Feature</th>'
        f'<th style="text-align:right">Corr ▼</th>'
        f'<th>Magnitude</th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table></div>'
    )


def _render_categorical_table(rows: List[Dict[str, Any]], top_n: int) -> str:
    if not rows:
        return ""

    table_rows = ""
    max_v = max((r.get("cramers_v") or 0.0) for r in rows[:top_n]) or 1.0
    for r in rows[:top_n]:
        feat = html.escape(str(r.get("feature", "?")))
        v    = r.get("cramers_v")
        rank = r.get("rank", "—")
        gm   = r.get("group_means") or {}
        col  = _corr_color(v)

        # Top-3 group means as mini pills (group_means values are dicts: {n_trades, mean_profit, win_rate})
        group_str = ""
        if gm:
            def _gm_sort_key(item):
                v = item[1]
                if isinstance(v, dict):
                    return abs(v.get("mean_profit") or 0)
                try:
                    return abs(float(v) if v is not None else 0)
                except (TypeError, ValueError):
                    return 0
            top_groups = sorted(gm.items(), key=_gm_sort_key, reverse=True)[:3]
            group_str = " ".join(
                f'<span style="font-size:10px;opacity:.7">'
                f'{html.escape(str(g)[:12])}: '
                f'{_fmt(val.get("mean_profit") if isinstance(val, dict) else val, 1)}'
                f'</span>'
                for g, val in top_groups
            )

        table_rows += (
            f'<tr>'
            f'<td style="text-align:center;opacity:.6">{rank}</td>'
            f'<td style="font-size:12px">{feat}</td>'
            f'<td style="text-align:right;color:{col};font-weight:700">{_fmt(v, 4)}</td>'
            f'<td>{_bar(v or 0.0, max_v)}</td>'
            f'<td style="font-size:11px">{group_str}</td>'
            f'</tr>'
        )

    return (
        f'<div class="sdfi-lbl">Categorical Effects (Cramér\'s V)</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdfi-table">'
        f'<thead><tr>'
        f'<th>#</th><th>Feature</th>'
        f'<th style="text-align:right">Cramér\'s V ▼</th>'
        f'<th>Magnitude</th>'
        f'<th>Group means (avg $)</th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table></div>'
    )


def _render_rf_table(rf_rows: Optional[List[Dict[str, Any]]], top_n: int) -> str:
    if not rf_rows:
        return ""

    max_imp = max((r.get("importance") or 0.0) for r in rf_rows[:top_n]) or 1.0
    table_rows = ""
    for r in rf_rows[:top_n]:
        feat = html.escape(str(r.get("feature", "?")))
        imp  = r.get("importance") or 0.0
        rank = r.get("rank", "—")
        table_rows += (
            f'<tr>'
            f'<td style="text-align:center;opacity:.6">{rank}</td>'
            f'<td style="font-size:12px">{feat}</td>'
            f'<td style="text-align:right;font-weight:700;color:#a5b4fc">'
            f'{_fmt(imp, 4)}</td>'
            f'<td>{_bar(imp, max_imp)}</td>'
            f'</tr>'
        )

    return (
        f'<div class="sdfi-lbl">Random Forest Feature Importance</div>'
        f'<div style="overflow-x:auto">'
        f'<table class="sdfi-table">'
        f'<thead><tr>'
        f'<th>#</th><th>Feature</th>'
        f'<th style="text-align:right">Importance ▼</th>'
        f'<th>Magnitude</th>'
        f'</tr></thead>'
        f'<tbody>{table_rows}</tbody>'
        f'</table></div>'
    )


def _render_run_importance(run_id: str, imp: Dict[str, Any], top_n: int, show_rf: bool) -> str:
    if not imp:
        return f'<div class="sdfi-muted">No importance data for {html.escape(run_id)}.</div>'

    error = imp.get("error")
    if error:
        return (
            f'<div class="sdfi-muted" style="color:#e74c3c">'
            f'Importance error ({html.escape(run_id)}): {html.escape(str(error))}'
            f'</div>'
        )

    diag     = imp.get("diagnostics") or {}
    top_feat = imp.get("top_features") or []
    num_rows = imp.get("numeric_correlations") or []
    cat_rows = imp.get("categorical_effects") or []
    rf_rows  = imp.get("rf_importance")

    parts = []

    # Top features strip
    parts.append(_render_top_features(top_feat))

    # Numeric correlations
    if num_rows:
        parts.append(_render_numeric_table(num_rows, top_n))

    # Categorical effects
    if cat_rows:
        parts.append(_render_categorical_table(cat_rows, min(top_n, 8)))

    # RF importance
    if show_rf and rf_rows:
        parts.append(_render_rf_table(rf_rows, top_n))

    # Diagnostics
    n_trades = diag.get("n_trades", "—")
    rf_used  = diag.get("rf_used")
    n_feats  = diag.get("n_features_analyzed", "—")
    issues   = diag.get("issues") or []
    diag_line = (
        f'<div style="font-size:11px;opacity:.5;margin-top:6px">'
        f'{n_trades} trades · {n_feats} features analysed'
        f'{" · RF ✓" if rf_used else " · RF unavailable"}'
        f'</div>'
    )
    parts.append(diag_line)

    if issues:
        parts.append(
            "".join(f'<div class="sdfi-muted" style="color:#e74c3c;font-size:11px">'
                    f'⚠ {html.escape(str(i))}</div>' for i in issues)
        )

    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_feature_importance(ctx: Dict[str, Any]) -> str:
    """
    Pure renderer — no IO, no analytics, no YAML parsing.
    Reads from pkg.metadata["derived"]["strategy_discovery"]["importance"].
    """
    packages      = ctx.get("packages") or {}
    options       = ctx.get("options") or {}

    max_runs       = int(options.get("max_runs", 5))
    show_all_runs  = bool(options.get("show_all_runs", False))
    top_n          = int(options.get("top_n_features", 12))
    show_cross_run = bool(options.get("show_cross_run", True))
    show_rf        = bool(options.get("show_rf", True))

    parts = [_CSS, '<div class="sdfi-wrap">']

    # Collect (run_id, importance_dict) for cross-run table
    run_items: List[tuple] = []
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd  = meta.get("derived", {}).get("strategy_discovery", {})
        imp = sd.get("importance") if isinstance(sd, dict) else None
        if imp is not None:
            run_items.append((str(run_id), imp))

    # Cross-run summary card
    if show_cross_run and len(run_items) > 1:
        cross_html = _build_cross_run_table(run_items)
        parts.append(f'<div class="sdfi-card">{cross_html}</div>')

    # Per-run cards
    shown = 0
    for run_id, imp in run_items:
        if not show_all_runs and shown >= max_runs:
            break
        run_html = _render_run_importance(run_id, imp, top_n, show_rf)
        short_id = html.escape(run_id[:60])
        parts.append(f"""
        <div class="sdfi-card">
          <details {"open" if shown == 0 else ""}>
            <summary class="sdfi-run-hdr">{short_id}</summary>
            <div style="margin-top:8px">{run_html}</div>
          </details>
        </div>""")
        shown += 1

    if shown == 0:
        parts.append(
            '<div class="sdfi-card sdfi-muted">'
            'No feature importance data found. '
            'Ensure strategy_discovery is enabled and the feature matrix was built.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
