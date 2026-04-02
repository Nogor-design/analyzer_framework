from __future__ import annotations

"""
Strategy Discovery — Cross-Run Comparison Table
================================================
Pure HTML renderer that shows all runs side-by-side in a single rich table.

This is the "executive dashboard" view: a trader looks here first to compare
strategies across every key dimension without clicking into individual cards.

Columns (grouped):
  Identity    — Rank, Grade, Run ID, Validation, Cluster
  Performance — PF, WR, Net Profit, N Trades
  Risk-Adj    — Sharpe, Sortino, Calmar, Omega
  Drawdown    — Max DD%, Ulcer Index, Recovery Factor, Max Consec. Losses
  Sizing      — Full Kelly, Recommended Fraction
  Stability   — OOS Degrade, Decay Score, Trend, Decay Badge
  Classification — Label, Automation Score

Data source:
  cross_run_ranking["ranked"]  (built by ranking.py after every per-run phase)

Section options (ctx["options"]):
  group_columns      : bool  – show column group headers (default True)
  max_runs           : int   – cap rows (default 0 = all)
  highlight_best     : bool  – bold the best value in each metric column (default True)
  show_risk_adj      : bool  – show Sharpe/Sortino/Calmar/Omega group (default True)
  show_drawdown      : bool  – show drawdown group (default True)
  show_sizing        : bool  – show Kelly sizing group (default True)
  show_stability     : bool  – show OOS degrade / decay group (default True)
  show_classification: bool  – show classification group (default True)
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, decimals: int = 2, suffix: str = "", prefix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{prefix}{float(v):.{decimals}f}{suffix}"
    except Exception:
        return "—"


def _pct(v: Any, decimals: int = 1) -> str:
    return _fmt(v, decimals, suffix="%")


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    try:
        val = float(v)
        sign = "+" if val > 0 else ""
        return f"{sign}${val:,.0f}"
    except Exception:
        return "—"


def _grade_badge(grade: Optional[str]) -> str:
    colors = {"A": "#2ecc71", "B": "#27ae60", "C": "#f39c12", "D": "#e67e22", "F": "#e74c3c"}
    g = str(grade or "?")
    color = colors.get(g, "#888")
    return (
        f'<span style="background:{color};color:#fff;border-radius:4px;'
        f'padding:1px 6px;font-weight:800;font-size:12px">{html.escape(g)}</span>'
    )


def _val_badge(passed: bool) -> str:
    if passed:
        return '<span style="background:#d1fae5;color:#065f46;border-radius:10px;padding:1px 7px;font-size:10px;font-weight:700">✓ pass</span>'
    return '<span style="background:#fee2e2;color:#991b1b;border-radius:10px;padding:1px 7px;font-size:10px;font-weight:700">✗ fail</span>'


def _trend_badge(cls: Optional[str]) -> str:
    colors = {
        "improving": "#2ecc71", "stable": "#6b7280",
        "degrading": "#e74c3c", "volatile": "#f39c12", "unknown": "#9ca3af",
    }
    c = str(cls or "unknown")
    color = colors.get(c, "#9ca3af")
    return (
        f'<span style="background:{color};color:#fff;border-radius:8px;'
        f'padding:1px 6px;font-size:10px;font-weight:700">{html.escape(c)}</span>'
    )


def _classification_badge(label: Optional[str]) -> str:
    colors = {
        "automated":          "#2563eb",
        "hybrid":             "#7c3aed",
        "semi_discretionary": "#dc2626",
    }
    lbl = str(label or "unknown")
    color = colors.get(lbl, "#888")
    display = lbl.replace("_", " ").title()
    return (
        f'<span style="background:{color};color:#fff;border-radius:8px;'
        f'padding:1px 7px;font-size:10px;font-weight:700">{html.escape(display)}</span>'
    )


def _cluster_badge(row: Dict[str, Any]) -> str:
    cid = row.get("cluster_id")
    rep = row.get("is_cluster_representative")
    if cid is None:
        return "—"
    if rep:
        return f'<span title="Cluster {cid} representative" style="font-size:13px">★</span> <span style="font-size:11px;opacity:.7">C{cid}</span>'
    return f'<span title="Cluster {cid} member" style="font-size:11px;opacity:.6">○ C{cid}</span>'


def _ratio_color(v: Optional[float], lo: float, hi: float) -> str:
    if v is None:
        return "inherit"
    if v >= hi:
        return "#2ecc71"
    if v >= lo:
        return "#f39c12"
    return "#e74c3c"


def _colored(v: Any, lo: float, hi: float, decimals: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        fv = float(v)
        color = _ratio_color(fv, lo, hi)
        return f'<span style="color:{color};font-weight:600">{fv:.{decimals}f}{suffix}</span>'
    except Exception:
        return "—"


def _decay_colored(score: Optional[int]) -> str:
    if score is None:
        return "—"
    color = "#2ecc71" if score < 20 else "#f39c12" if score < 45 else "#e67e22" if score < 70 else "#e74c3c"
    return f'<span style="color:{color};font-weight:600">{score}</span>'


def _best_indices(rows: List[Dict[str, Any]], key: str, higher_is_better: bool = True) -> set:
    """Return set of run_ids that have the best value for a given key."""
    vals = [(r.get("run_id"), r.get(key)) for r in rows if r.get(key) is not None]
    if not vals:
        return set()
    try:
        best = max(vals, key=lambda x: float(x[1])) if higher_is_better else min(vals, key=lambda x: float(x[1]))
        return {best[0]}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

def _build_column_groups(options: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns list of column group dicts:
      {group, label, columns: [{key, header, render_fn, best_higher}]}
    """
    groups: List[Dict[str, Any]] = []

    # Identity always shown
    groups.append({
        "group": "identity",
        "label": "",
        "columns": [
            {"key": "_rank",           "header": "#",         "best_higher": False},
            {"key": "_grade",          "header": "Grade",     "best_higher": True},
            {"key": "_run_id",         "header": "Run",       "best_higher": None},
            {"key": "_validation",     "header": "WF",        "best_higher": None},
            {"key": "_cluster",        "header": "Cluster",   "best_higher": None},
        ],
    })

    # Performance always shown
    groups.append({
        "group": "performance",
        "label": "Performance",
        "columns": [
            {"key": "profit_factor",  "header": "PF",       "best_higher": True,  "lo": 1.2, "hi": 2.0, "dec": 2},
            {"key": "win_rate",       "header": "WR",       "best_higher": True,  "lo": 0.45, "hi": 0.60, "dec": 1, "suffix": "%", "scale": 100},
            {"key": "net_profit",     "header": "Net P&L",  "best_higher": True,  "usd": True},
            {"key": "n_trades",       "header": "Trades",   "best_higher": True,  "dec": 0},
        ],
    })

    if options.get("show_risk_adj", True):
        groups.append({
            "group": "risk_adj",
            "label": "Risk-Adjusted",
            "columns": [
                {"key": "sharpe_ratio",  "header": "Sharpe",  "best_higher": True, "lo": 0.5,  "hi": 1.0, "dec": 2},
                {"key": "sortino_ratio", "header": "Sortino", "best_higher": True, "lo": 1.0,  "hi": 2.0, "dec": 2},
                {"key": "calmar_ratio",  "header": "Calmar",  "best_higher": True, "lo": 0.3,  "hi": 1.0, "dec": 2},
                {"key": "omega_ratio",   "header": "Omega",   "best_higher": True, "lo": 1.2,  "hi": 2.0, "dec": 2},
            ],
        })

    if options.get("show_drawdown", True):
        groups.append({
            "group": "drawdown",
            "label": "Drawdown",
            "columns": [
                {"key": "max_dd_pct",       "header": "Max DD%",      "best_higher": False, "lo": 20.0, "hi": 10.0, "dec": 1, "suffix": "%"},
                {"key": "ulcer_index",      "header": "Ulcer",        "best_higher": False, "lo": 5.0,  "hi": 2.0,  "dec": 2, "suffix": "%"},
                {"key": "recovery_factor",  "header": "RecovFact",    "best_higher": True,  "lo": 1.0,  "hi": 3.0,  "dec": 2},
                {"key": "max_consec_losses","header": "ConsecL",      "best_higher": False, "dec": 0},
            ],
        })

    if options.get("show_sizing", True):
        groups.append({
            "group": "sizing",
            "label": "Sizing",
            "columns": [
                {"key": "kelly_full",          "header": "Full Kelly", "best_higher": True,  "lo": 0.05, "hi": 0.20, "dec": 3},
                {"key": "kelly_recommended_fok","header": "Rec. ×K",  "best_higher": None,  "dec": 2},
            ],
        })

    if options.get("show_stability", True):
        groups.append({
            "group": "stability",
            "label": "Stability",
            "columns": [
                {"key": "oos_degradation", "header": "OOS Deg.",   "best_higher": False, "lo": 0.20, "hi": 0.05, "dec": 1, "suffix": "%", "scale": 100},
                {"key": "decay_score",     "header": "Decay",      "best_higher": False, "special": "decay"},
                {"key": "trend_class",     "header": "Trend",      "best_higher": None,  "special": "trend"},
            ],
        })

    if options.get("show_classification", True):
        groups.append({
            "group": "classification",
            "label": "Classification",
            "columns": [
                {"key": "classification",   "header": "Type",    "best_higher": None,  "special": "classification"},
                {"key": "automation_score", "header": "Auto%",   "best_higher": True,  "lo": 50.0, "hi": 80.0, "dec": 0},
            ],
        })

    return groups


# ---------------------------------------------------------------------------
# Cell rendering
# ---------------------------------------------------------------------------

def _render_cell(col: Dict[str, Any], row: Dict[str, Any], best_ids: Dict[str, set]) -> str:
    key     = col["key"]
    special = col.get("special")
    dec     = col.get("dec", 2)
    suffix  = col.get("suffix", "")
    scale   = col.get("scale", 1.0)
    lo      = col.get("lo")
    hi      = col.get("hi")
    best_h  = col.get("best_higher")
    run_id  = row.get("run_id")

    is_best = run_id in best_ids.get(key, set())
    bold    = ' style="font-weight:800"' if is_best else ""

    # Special rendering
    if key == "_rank":
        rank = row.get("rank")
        return f'<td style="text-align:center;font-weight:700;color:#6b7280">{rank if rank is not None else "—"}</td>'
    if key == "_grade":
        return f'<td style="text-align:center">{_grade_badge(row.get("grade"))}</td>'
    if key == "_run_id":
        return f'<td style="font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{html.escape(str(row.get("run_id", "")))}</td>'
    if key == "_validation":
        return f'<td style="text-align:center">{_val_badge(bool(row.get("validation_passed")))}</td>'
    if key == "_cluster":
        return f'<td style="text-align:center">{_cluster_badge(row)}</td>'

    if special == "decay":
        return f'<td style="text-align:right"{bold}>{_decay_colored(row.get(key))}</td>'
    if special == "trend":
        return f'<td style="text-align:center">{_trend_badge(row.get(key))}</td>'
    if special == "classification":
        return f'<td>{_classification_badge(row.get(key))}</td>'

    if col.get("usd"):
        val = row.get(key)
        if val is not None:
            try:
                fv = float(val)
                color = "#2ecc71" if fv > 0 else "#e74c3c"
                return f'<td style="text-align:right;color:{color}"{bold}>{_usd(val)}</td>'
            except Exception:
                pass
        return '<td style="text-align:right">—</td>'

    val = row.get(key)
    if val is None:
        return '<td style="text-align:right;opacity:.4">—</td>'

    try:
        fv = float(val) * scale
        if lo is not None and hi is not None:
            if best_h is False:  # lower is better — flip lo/hi for color logic
                color = _ratio_color(-fv, -lo, -hi)
            else:
                color = _ratio_color(fv, lo, hi)
            cell_inner = f'<span style="color:{color}">{fv:.{dec}f}{suffix}</span>'
        else:
            cell_inner = f'{fv:.{dec}f}{suffix}'
        return f'<td style="text-align:right"{bold}>{cell_inner}</td>'
    except Exception:
        return f'<td style="text-align:right;opacity:.4">—</td>'


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

_CSS = """<style>
.sd-cmp-wrap {
  padding: 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  line-height: 1.4;
  color: #111827;
}
.sd-cmp-wrap h2 {
  margin: 0 0 12px 0;
  font-size: 18px;
  font-weight: 700;
}
.sd-cmp-scroll {
  overflow-x: auto;
}
table.sd-cmp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  background: #fff;
}
.sd-cmp-table th,
.sd-cmp-table td {
  padding: 6px 10px;
  border-bottom: 1px solid #e5e7eb;
  vertical-align: middle;
  white-space: nowrap;
}
.sd-cmp-table th {
  background: #f3f4f6;
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  position: sticky;
  top: 0;
}
.sd-cmp-table .sd-cmp-group-header th {
  background: #1e3a5f;
  color: #fff;
  text-align: center;
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  border-right: 2px solid #3b82f6;
  padding: 3px 10px;
}
.sd-cmp-table tr:nth-child(even) td { background: #fafafa; }
.sd-cmp-table tr:hover td { background: #eff6ff; }
.sd-cmp-table td.sd-cmp-group-sep { border-right: 2px solid #d1d5db; }
.sd-cmp-non-rep { opacity: 0.65; }
.sd-cmp-muted { color: #9ca3af; font-size: 11px; }
</style>"""


def render_strategy_discovery_comparison(ctx: dict) -> str:
    packages     = ctx.get("packages") or {}
    options      = ctx.get("options") or {}
    max_runs     = int(options.get("max_runs", 0))
    group_cols   = bool(options.get("group_columns", True))
    highlight    = bool(options.get("highlight_best", True))

    # Locate cross_run_ranking
    ranked: List[Dict[str, Any]] = []
    for pkg in packages.values():
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery") or {}
        cross = sd.get("cross_run_ranking")
        if isinstance(cross, dict):
            ranked = cross.get("ranked") or []
            break

    if not ranked:
        return (
            f'{_CSS}<div class="sd-cmp-wrap">'
            f'<h2>Cross-Run Comparison</h2>'
            f'<div style="color:#6b7280;font-size:13px">No cross-run ranking data available. '
            f'Run with strategy_discovery enabled.</div></div>'
        )

    if max_runs > 0:
        ranked = ranked[:max_runs]

    # Build column groups
    col_groups = _build_column_groups(options)
    all_cols = [col for g in col_groups for col in g["columns"]]

    # Identify "best" run for each metric column
    best_ids: Dict[str, set] = {}
    if highlight:
        for col in all_cols:
            bh = col.get("best_higher")
            if bh is None:
                continue
            best_ids[col["key"]] = _best_indices(ranked, col["key"], bh)

    # ---- group header row ----
    group_header = ""
    if group_cols:
        group_header = '<tr class="sd-cmp-group-header">'
        for g in col_groups:
            n = len(g["columns"])
            lbl = g["label"]
            sep_style = "border-right: 2px solid #3b82f6;" if lbl else ""
            group_header += f'<th colspan="{n}" style="{sep_style}">{html.escape(lbl)}</th>'
        group_header += "</tr>"

    # ---- column header row ----
    col_header = "<tr>"
    for g in col_groups:
        for i, col in enumerate(g["columns"]):
            is_last = i == len(g["columns"]) - 1 and g["label"]
            sep = "border-right:2px solid #d1d5db;" if is_last else ""
            col_header += f'<th style="{sep}">{html.escape(col["header"])}</th>'
    col_header += "</tr>"

    # ---- data rows ----
    data_rows = ""
    for row in ranked:
        is_rep = row.get("is_cluster_representative", True)
        row_class = "" if is_rep else ' class="sd-cmp-non-rep"'
        data_rows += f"<tr{row_class}>"
        for g in col_groups:
            for i, col in enumerate(g["columns"]):
                is_last = i == len(g["columns"]) - 1 and g["label"]
                cell_html = _render_cell(col, row, best_ids)
                # Inject group separator on last col of named group
                if is_last:
                    cell_html = cell_html.replace("<td", '<td class="sd-cmp-group-sep"', 1)
                data_rows += cell_html
        data_rows += "</tr>"

    # ---- summary banner ----
    n_runs   = len(ranked)
    n_passed = sum(1 for r in ranked if r.get("validation_passed"))
    n_a      = sum(1 for r in ranked if r.get("grade") == "A")
    best_row = ranked[0] if ranked else {}

    banner = (
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px;font-size:12px">'
        f'<span><b>{n_runs}</b> <span style="color:#6b7280">runs</span></span>'
        f'<span><b>{n_passed}</b> <span style="color:#6b7280">passed WF validation</span></span>'
        f'<span><b>{n_a}</b> <span style="color:#6b7280">grade A</span></span>'
        + (
            f'<span style="color:#6b7280">Top: </span>'
            f'<b>{html.escape(str(best_row.get("run_id", "")))}</b>'
            f' {_grade_badge(best_row.get("grade"))}'
            if best_row else ""
        )
        + f'</div>'
    )

    note = (
        f'<div class="sd-cmp-muted" style="margin-top:8px">'
        f'★ = cluster representative &nbsp;|&nbsp; ○ = cluster member (dimmed) &nbsp;|&nbsp; '
        f'<b>bold</b> = best in column</div>'
    )

    return (
        f'{_CSS}'
        f'<div class="sd-cmp-wrap">'
        f'<h2>Cross-Run Comparison</h2>'
        f'{banner}'
        f'<div class="sd-cmp-scroll">'
        f'<table class="sd-cmp-table">'
        f'<thead>{group_header}{col_header}</thead>'
        f'<tbody>{data_rows}</tbody>'
        f'</table></div>'
        f'{note}'
        f'</div>'
    )
