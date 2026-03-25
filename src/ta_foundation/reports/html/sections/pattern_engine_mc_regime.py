"""
Pattern Engine — Regime-aware MC (render-only)

STRICT:
- No disk IO
- No compute-heavy analytics
- Read only from:
  - pkg.assets["pattern_engine"]
  - pkg.metadata["derived"]["pattern_engine"]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import html

import pandas as pd


def _df_from_assets(pkg: Any, key: str) -> pd.DataFrame:
    try:
        pe = (getattr(pkg, "assets", None) or {}).get("pattern_engine", {}) or {}
        df = pe.get(key)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fmt_float(x: Any, *, digits: int = 4) -> str:
    try:
        if x is None:
            return ""
        v = float(x)
        if v != v:  # NaN
            return "NaN"
        return f"{v:.{digits}f}"
    except Exception:
        return str(x)


def _fmt_int(x: Any) -> str:
    try:
        if x is None:
            return ""
        v = int(x)
        return str(v)
    except Exception:
        return str(x)


def _render_table(df: pd.DataFrame, *, cols: List[str], max_rows: int) -> str:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return "<div class='muted'>No regime MC summary found in assets.</div>"

    cols_present = [c for c in cols if c in df.columns]
    if not cols_present:
        cols_present = list(df.columns)[: min(len(df.columns), 12)]

    d = df.copy()
    # Stable ordering preference
    sort_cols = [c for c in ["prop_survival_prob", "any_breach_prob", "dd_p90"] if c in d.columns]
    if sort_cols:
        d = d.sort_values(sort_cols[0], ascending=False, kind="mergesort")
    d = d.head(max_rows)

    # Basic formatting pass (render-only; cheap)
    fmt = {}
    for c in cols_present:
        if c in {"n_paths", "horizon"}:
            fmt[c] = _fmt_int
        elif c.endswith("_prob") or c in {"prop_survival_prob"}:
            fmt[c] = lambda v, _c=c: _fmt_float(v, digits=4)
        elif c.startswith("dd_"):
            fmt[c] = lambda v, _c=c: _fmt_float(v, digits=1)
        elif c == "expected_time_to_breach":
            fmt[c] = lambda v, _c=c: _fmt_float(v, digits=3)
        else:
            fmt[c] = lambda v: "" if v is None else html.escape(str(v))

    rows_html = []
    # header
    ths = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols_present)
    rows_html.append(f"<tr>{ths}</tr>")

    # body
    for _, r in d.iterrows():
        tds = []
        for c in cols_present:
            try:
                tds.append(f"<td>{fmt[c](r.get(c))}</td>")
            except Exception:
                tds.append(f"<td>{html.escape(str(r.get(c)))}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")

    return (
        "<div style='overflow-x:auto'>"
        "<table class='table table-sm'>"
        + "".join(rows_html)
        + "</table></div>"
    )


def render_pattern_engine_mc_regime(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    section = ctx.get("section") or {}
    section_options = (section.get("options") or {})  # legacy
    top_n = int((options.get("top_n") or section_options.get("top_n") or 25))
    show_surface = bool(options.get("show_surface") if "show_surface" in options else section_options.get("show_surface", True))

    # pick a focus run if provided, else first run with mc_regime_summary present
    focus_run_id = str(options.get("run_id") or section_options.get("run_id") or "").strip()
    focus_pkg = None
    if focus_run_id and focus_run_id in packages:
        focus_pkg = packages.get(focus_run_id)
    else:
        for _, pkg in packages.items():
            df = _df_from_assets(pkg, "mc_regime_summary")
            if isinstance(df, pd.DataFrame) and not df.empty:
                focus_pkg = pkg
                break

    if focus_pkg is None:
        return "<h3>Regime-aware Monte Carlo</h3><div class='muted'>No runs contain mc_regime_summary.</div>"

    run_id = getattr(focus_pkg, "run_id", "UNKNOWN")
    df = _df_from_assets(focus_pkg, "mc_regime_summary")

    # New columns added in MC v3: daily_loss_breach_prob, trailing_dd_breach_prob, any_breach_prob
    cols = [
        "entity_id",
        "horizon",
        "regime_model",
        "vol_bucket",
        "n_paths",
        "prop_survival_prob",
        "dd_p90",
        "daily_loss_breach_prob",
        "trailing_dd_breach_prob",
        "any_breach_prob",
        "intraday_breach_prob",
        "expected_time_to_breach",
    ]

    note_bits = []
    if "daily_loss_breach_prob" in df.columns or "trailing_dd_breach_prob" in df.columns:
        note_bits.append(
            "<li><b>daily_loss_breach_prob</b> = P(path breaches intraday daily-loss-from-open)</li>"
            "<li><b>trailing_dd_breach_prob</b> = P(path breaches trailing drawdown)</li>"
            "<li><b>any_breach_prob</b> = P(path breaches either rule)</li>"
            "<li><b>intraday_breach_prob / expected_time_to_breach</b> remain <i>trailing-breach timing</i> only.</li>"
        )
    else:
        note_bits.append(
            "<li>This run was generated by an older MC schema (no split breach probabilities).</li>"
        )

    html_out = []
    html_out.append("<h3>Regime-aware Monte Carlo</h3>")
    html_out.append(f"<div class='muted'>Run: {html.escape(str(run_id))}</div>")
    html_out.append("<div class='muted' style='margin-top:6px'><ul style='margin:6px 0 10px 18px'>"
                    + "".join(note_bits) + "</ul></div>")
    html_out.append(_render_table(df, cols=cols, max_rows=top_n))

    if show_surface:
        surf = _df_from_assets(focus_pkg, "mc_slippage_surface")
        if isinstance(surf, pd.DataFrame) and not surf.empty:
            html_out.append("<h4 style='margin-top:14px'>Slippage Stress Surface</h4>")
            surf_cols = [c for c in [
                "entity_id", "horizon", "regime_model",
                "slippage_ticks", "spread_widen_ticks", "fill_prob",
                "n_paths", "prop_survival_prob"
            ] if c in surf.columns]
            html_out.append(_render_table(surf, cols=surf_cols, max_rows=min(top_n, 50)))

    return "\n".join(html_out)
