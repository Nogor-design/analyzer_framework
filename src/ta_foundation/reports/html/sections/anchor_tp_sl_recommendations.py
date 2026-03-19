from __future__ import annotations

from typing import Any, Dict, List
import html

import pandas as pd


def _safe_str(x: Any) -> str:
    if x is None:
        return "—"
    s = str(x).strip()
    return html.escape(s) if s else "—"


def _safe_float(x: Any, digits: int = 3) -> str:
    try:
        if x is None or x == "":
            return "—"
        v = float(x)
        if pd.isna(v):
            return "—"
        return f"{v:.{digits}f}"
    except Exception:
        return html.escape(str(x))


def _profile_label(row: pd.Series, options: Dict[str, Any]) -> str:
    conservative_stability = float(options.get("conservative_min_stability", 0.75) or 0.75)
    balanced_stability = float(options.get("balanced_min_stability", 0.55) or 0.55)
    tail_cap = float(options.get("conservative_max_tail_dependency", 0.50) or 0.50)

    stability = pd.to_numeric(pd.Series([row.get("stability_score")]), errors="coerce").iloc[0]
    tail_share = pd.to_numeric(pd.Series([row.get("tail_dependency_share")]), errors="coerce").iloc[0]
    sample_flag = str(row.get("sample_quality_flag") or "").strip().lower()

    if pd.notna(stability) and stability >= conservative_stability and (pd.isna(tail_share) or tail_share <= tail_cap) and sample_flag == "ok":
        return "Conservative"
    if pd.notna(stability) and stability >= balanced_stability:
        return "Balanced"
    return "Aggressive"


def _html_table(headers: List[str], rows: List[List[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return (
        "<div style='overflow-x:auto'>"
        "<table class='table table-sm'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )


def _recommendations_table(run_id: str, df: pd.DataFrame, options: Dict[str, Any]) -> str:
    if df.empty:
        return ""

    max_rows = int(options.get("max_rows_per_run", 20) or 20)
    rows: List[List[str]] = []
    for _, row in df.head(max_rows).iterrows():
        rows.append(
            [
                _safe_str(_profile_label(row, options)),
                _safe_str(row.get("anchor_id")),
                _safe_float(row.get("tp_atr")),
                _safe_float(row.get("sl_atr")),
                _safe_float(row.get("stability_score")),
                _safe_float(row.get("robust_score")),
                _safe_float(row.get("expectancy_score")),
                _safe_float(row.get("fold_agreement")),
                _safe_float(row.get("neighbor_consistency")),
                _safe_float(row.get("tail_dependency_share")),
                _safe_str(row.get("sample_quality_flag")),
            ]
        )

    return (
        f"<h4 style='margin-top:16px'>{html.escape(run_id)} — Recommendations</h4>"
        + _html_table(
            [
                "profile",
                "anchor_id",
                "tp_atr",
                "sl_atr",
                "stability_score",
                "robust_score",
                "expectancy",
                "fold_agreement",
                "neighbor_consistency",
                "tail_dependency_share",
                "sample_quality_flag",
            ],
            rows,
        )
    )


def _candidate_grid_table(run_id: str, df: pd.DataFrame, options: Dict[str, Any]) -> str:
    if df.empty:
        return ""

    max_rows = int(options.get("max_candidate_rows_per_run", 40) or 40)
    rows: List[List[str]] = []
    for _, row in df.head(max_rows).iterrows():
        rows.append(
            [
                _safe_str(row.get("anchor_id")),
                _safe_float(row.get("tp_atr")),
                _safe_float(row.get("sl_atr")),
                _safe_float(row.get("stability_score")),
                _safe_float(row.get("robust_score")),
                _safe_float(row.get("expectancy_score")),
                _safe_str(row.get("sample_quality_flag")),
            ]
        )

    return (
        f"<h4 style='margin-top:16px'>{html.escape(run_id)} — TP/SL Grid</h4>"
        + _html_table(
            [
                "anchor_id",
                "tp_atr",
                "sl_atr",
                "stability_score",
                "robust_score",
                "expectancy",
                "sample_quality_flag",
            ],
            rows,
        )
    )


def _trade_alignment_table(run_id: str, df: pd.DataFrame, options: Dict[str, Any]) -> str:
    if df.empty:
        return ""

    max_rows = int(options.get("max_trade_alignment_rows", 25) or 25)
    rows: List[List[str]] = []
    for _, row in df.head(max_rows).iterrows():
        rows.append(
            [
                _safe_str(row.get("trade_id")),
                _safe_str(row.get("matched_anchor_id")),
                _safe_float(row.get("recommended_tp_atr")),
                _safe_float(row.get("recommended_sl_atr")),
                _safe_float(row.get("realized_tp_atr")),
                _safe_float(row.get("realized_sl_atr")),
                _safe_float(row.get("realized_outcome_atr")),
                _safe_float(row.get("fit_distance")),
                _safe_str(row.get("recommended_sample_quality_flag")),
            ]
        )

    return (
        f"<h4 style='margin-top:16px'>{html.escape(run_id)} — Strategy Trade Alignment</h4>"
        + _html_table(
            [
                "trade_id",
                "matched_anchor_id",
                "recommended_tp_atr",
                "recommended_sl_atr",
                "realized_tp_atr",
                "realized_sl_atr",
                "realized_outcome_atr",
                "fit_distance",
                "recommended_sample_quality_flag",
            ],
            rows,
        )
    )


def render_anchor_tp_sl_recommendations(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")

    _ = market, report_config

    if not packages:
        return "<h3>MA Anchor TP/SL Recommendations</h3><div class='muted'>No packages loaded.</div>"

    selected_run_ids = options.get("run_ids") or []
    if isinstance(selected_run_ids, str):
        selected_run_ids = [selected_run_ids]
    selected_run_ids = {str(x).strip() for x in selected_run_ids if str(x).strip()}

    show_candidate_grid = bool(options.get("show_candidate_grid", True))
    show_trade_alignment = bool(options.get("show_trade_alignment", True))

    sections: List[str] = [
        "<h3>MA Anchor TP/SL Recommendations</h3>",
        "<div class='muted'>Renderer-only section. Reads in-memory MA Anchor recommendation artifacts and optional trade-alignment artifacts only.</div>",
    ]

    rendered = 0
    for run_id, pkg in packages.items():
        if selected_run_ids and run_id not in selected_run_ids:
            continue

        assets = getattr(pkg, "assets", {}) or {}
        ai_assets = assets.get("anchor_interaction") or {}
        recs = ai_assets.get("recommendations")
        candidates = ai_assets.get("tp_sl_candidates")
        trade_alignment = ai_assets.get("trade_recommendation_alignment")

        if isinstance(recs, pd.DataFrame) and not recs.empty:
            sort_cols = [c for c in ["stability_score", "robust_score", "expectancy_score"] if c in recs.columns]
            if sort_cols:
                recs = recs.sort_values(sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
            sections.append(_recommendations_table(str(run_id), recs, options))
            rendered += 1

        if show_candidate_grid and isinstance(candidates, pd.DataFrame) and not candidates.empty:
            grid_sort_cols = [c for c in ["stability_score", "robust_score", "expectancy_score"] if c in candidates.columns]
            if grid_sort_cols:
                candidates = candidates.sort_values(grid_sort_cols, ascending=[False] * len(grid_sort_cols)).reset_index(drop=True)
            sections.append(_candidate_grid_table(str(run_id), candidates, options))

        if show_trade_alignment and isinstance(trade_alignment, pd.DataFrame) and not trade_alignment.empty:
            sections.append(_trade_alignment_table(str(run_id), trade_alignment, options))

    if rendered == 0:
        sections.append("<div class='muted'>No in-memory MA Anchor recommendations are attached for the selected runs.</div>")

    sections.append(
        "<ul style='margin-left:18px'>"
        "<li><b>TP/SL Grid</b> shows the scored ATR target/stop combinations even when no strategies are loaded.</li>"
        "<li><b>Strategy Trade Alignment</b> appears only when a run has trades and compares realized trade paths against the closest MA Anchor recommendation.</li>"
        "<li><b>Conservative / Balanced / Aggressive</b> labels come only from section-local option thresholds.</li>"
        "</ul>"
    )
    return "\n".join(sections)