from __future__ import annotations

"""Automatic candidate selection for Recipe/Matrix optimizer stages."""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.web.optimizer_recipe import RecipeStage, load_recipe
from ta_foundation.web.optimizer_recipe_results import (
    PARSED_RESULTS_DIRNAME,
    RecipeStageResults,
    load_recipe_stage_results,
)
from ta_foundation.web.optimizer_session import OptimizerSession


RECIPE_SELECTION_CSV = "recipe_selection.csv"
RECIPE_SELECTION_JSON = "recipe_selection.json"


class RecipeSelectionError(Exception):
    pass


@dataclass(frozen=True)
class RecipeSelectionSummary:
    recipe_id: str
    stage_id: str
    row_count: int
    passing_count: int
    selected_count: int
    rejected_count: int
    selected_rows: list[dict[str, Any]] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)
    selected_csv: str | None = None
    selected_json: str | None = None
    rejected_csv: str | None = None
    rejected_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_recipe_stage_candidates(
    session: OptimizerSession,
    *,
    stage_id: str,
    results: RecipeStageResults | None = None,
    persist: bool = True,
) -> RecipeSelectionSummary:
    recipe = load_recipe(session)
    stage = _find_stage(recipe.stages, stage_id)
    if stage is None:
        raise RecipeSelectionError(f"Recipe has no stage: {stage_id}")
    if stage.stage_type != "optimizer":
        raise RecipeSelectionError(f"Stage {stage_id} is not an optimizer stage.")

    stage_results = results or load_recipe_stage_results(session, stage_id=stage_id, persist=persist)
    df = pd.DataFrame(stage_results.rows)
    if df.empty:
        return _empty_summary(recipe.recipe_id, stage_id)

    selection = dict(stage.selection or {})
    ranked = _add_scores(df)
    filtered = _apply_hard_filters(ranked, selection.get("hard_filters") or selection)
    selected = _select_rows(filtered, selection)

    selected_ids = set(str(row.get("candidate_id") or "") for row in selected.to_dict(orient="records"))
    rejected = ranked[~ranked["candidate_id"].astype(str).isin(selected_ids)].copy()
    selected = selected.copy()
    selected["selection_status"] = "selected"
    selected["selection_reason"] = _selection_reason(selection)
    rejected["selection_status"] = "rejected"
    rejected["rejection_reason"] = rejected.apply(
        lambda row: _rejection_reason(row, selection, selected_ids),
        axis=1,
    )

    selected_rows = [_json_safe(row) for row in selected.to_dict(orient="records")]
    rejected_rows = [_json_safe(row) for row in rejected.to_dict(orient="records")]
    paths: dict[str, str | None] = {
        "selected_csv": None,
        "selected_json": None,
        "rejected_csv": None,
        "rejected_json": None,
    }
    if persist:
        paths = _write_selection_files(session, stage_id, selected, rejected, selected_rows, rejected_rows)

    return RecipeSelectionSummary(
        recipe_id=recipe.recipe_id,
        stage_id=stage_id,
        row_count=len(ranked),
        passing_count=len(filtered),
        selected_count=len(selected),
        rejected_count=len(rejected),
        selected_rows=selected_rows,
        rejected_rows=rejected_rows,
        **paths,
    )


def _find_stage(stages: tuple[RecipeStage, ...], stage_id: str) -> RecipeStage | None:
    for stage in stages:
        if stage.stage_id == stage_id:
            return stage
    return None


def _add_scores(df: pd.DataFrame) -> pd.DataFrame:
    ranked = df.copy()
    for col in ("profit_factor", "total_net_profit", "drawdown_abs", "total_trades"):
        if col not in ranked.columns:
            ranked[col] = 0
        ranked[col] = pd.to_numeric(ranked[col], errors="coerce").fillna(0)
    if "portfolio_score" not in ranked.columns:
        ranked["portfolio_score"] = (
            ranked["profit_factor"] * 1000
            + ranked["total_net_profit"] / 10
            - ranked["drawdown_abs"] / 2
            + ranked["total_trades"]
        )
    return ranked


def _apply_hard_filters(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    out = df
    min_trades = _optional_float(filters.get("min_trades"))
    if min_trades is not None and "total_trades" in out.columns:
        out = out[out["total_trades"] >= min_trades]
    min_pf = _optional_float(filters.get("min_profit_factor"))
    if min_pf is not None and "profit_factor" in out.columns:
        out = out[out["profit_factor"] >= min_pf]
    max_dd = _optional_float(filters.get("max_drawdown"))
    if max_dd is not None and "drawdown_abs" in out.columns:
        out = out[out["drawdown_abs"] <= max_dd]
    min_net = _optional_float(filters.get("min_net_profit"))
    if min_net is not None and "total_net_profit" in out.columns:
        out = out[out["total_net_profit"] >= min_net]
    return out.copy()


def _select_rows(df: pd.DataFrame, selection: dict[str, Any]) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    keep_per_group = max(1, int(selection.get("keep_per_group") or 1))
    target_total = _optional_int(selection.get("target_total_candidates"))
    group_by = [str(value) for value in (selection.get("group_by") or [])]
    
    # Check if custom fitness_metrics are specified (ranked fitness checkboxes in UI)
    fitness_metrics = selection.get("fitness_metrics")
    if fitness_metrics and isinstance(fitness_metrics, list):
        selected_parts: list[pd.DataFrame] = []
        resolved_group_by = [_resolve_column(df, col) for col in group_by]
        resolved_group_by = [col for col in resolved_group_by if col]
        
        # Define how to sort each metric
        metric_column_map = {
            "profit_factor": ("profit_factor", False),
            "total_net_profit": ("total_net_profit", False),
            "net_profit": ("total_net_profit", False),
            "drawdown_abs": ("drawdown_abs", True),
            "drawdown": ("drawdown_abs", True),
            "total_trades": ("total_trades", False),
            "trades": ("total_trades", False),
            "portfolio_score": ("portfolio_score", False),
        }
        
        if resolved_group_by:
            for _, group in df.groupby(resolved_group_by, dropna=False, sort=True):
                for metric in fitness_metrics:
                    metric_key = str(metric).lower().strip()
                    col_info = metric_column_map.get(metric_key, (metric, False))
                    col_name = _resolve_column(group, col_info[0]) or col_info[0]
                    if col_name in group.columns:
                        selected_parts.append(
                            group.sort_values(col_name, ascending=col_info[1]).head(keep_per_group)
                        )
        else:
            for metric in fitness_metrics:
                metric_key = str(metric).lower().strip()
                col_info = metric_column_map.get(metric_key, (metric, False))
                col_name = _resolve_column(df, col_info[0]) or col_info[0]
                if col_name in df.columns:
                    selected_parts.append(
                        df.sort_values(col_name, ascending=col_info[1]).head(keep_per_group)
                    )
                    
        if selected_parts:
            selected = pd.concat(selected_parts, ignore_index=False)
            selected = selected.drop_duplicates(subset=["candidate_id"])
        else:
            selected = df.head(0)
            
        # Re-sort final selections by portfolio_score descending
        if "portfolio_score" in selected.columns:
            selected = selected.sort_values("portfolio_score", ascending=False)
    else:
        # Legacy fallback/default behavior
        sort_cols, ascending = _sort_spec(selection)
        selected_parts: list[pd.DataFrame] = []
        if group_by:
            resolved = [_resolve_column(df, col) for col in group_by]
            resolved = [col for col in resolved if col]
            if resolved:
                for _, group in df.groupby(resolved, dropna=False, sort=True):
                    selected_parts.append(
                        group.sort_values(sort_cols, ascending=ascending).head(keep_per_group)
                    )
            else:
                selected_parts.append(df.sort_values(sort_cols, ascending=ascending).head(keep_per_group))
        else:
            selected_parts.append(df.sort_values(sort_cols, ascending=ascending).head(keep_per_group))
        selected = pd.concat(selected_parts, ignore_index=False) if selected_parts else df.head(0)
        selected = selected.sort_values(sort_cols, ascending=ascending)
        
    if target_total is not None:
        selected = selected.head(max(1, target_total))
    return selected


def _sort_spec(selection: dict[str, Any]) -> tuple[list[str], list[bool]]:
    rank_by = str(selection.get("rank_by") or "portfolio_score")
    sort_cols = [rank_by]
    ascending = [False]
    tie_breakers = list(selection.get("tie_breakers") or [])
    for item in tie_breakers:
        key = str(item)
        if key == "lower_drawdown":
            sort_cols.append("drawdown_abs")
            ascending.append(True)
        elif key == "higher_trade_count":
            sort_cols.append("total_trades")
            ascending.append(False)
        elif key == "higher_net_profit":
            sort_cols.append("total_net_profit")
            ascending.append(False)
        elif key:
            sort_cols.append(key)
            ascending.append(False)
    return sort_cols, ascending


def _resolve_column(df: pd.DataFrame, name: str) -> str | None:
    if name in df.columns:
        return name
    param_name = f"param_{name}"
    if param_name in df.columns:
        return param_name
    wanted = _canonical_column_name(name)
    param_wanted = _canonical_column_name(param_name)
    for column in df.columns:
        canonical = _canonical_column_name(str(column))
        if canonical in {wanted, param_wanted}:
            return str(column)
    return None


def _canonical_column_name(value: str) -> str:
    text = str(value or "")
    if text.startswith("param_"):
        text = text.removeprefix("param_")
    normalized = re.sub(r"[^a-z0-9]+", "", text.lower())
    aliases = {
        "starttimehh": "starttimeh",
        "starttimemm": "starttimem",
        "durationtimehh": "durationtimeh",
        "durationtimemm": "durationtimem",
        "botname": "botname",
        "cornerimage": "filename",
        "backgroundimage": "filename2",
        "imageopacity": "iopacity",
        "usetimefilter": "usetimefilter",
        "usemaxstop": "usemaxstop",
        "usemaxtp": "usemaxtp",
        "usekill": "usekill",
        "killprofitstop": "killprofitstop",
        "killlossstop": "killlossstop",
        "showcurrentpnl": "showcurrentpnl",
        "showstatsbox": "showstatsbox",
    }
    return aliases.get(normalized, normalized)


def _selection_reason(selection: dict[str, Any]) -> str:
    rank_by = str(selection.get("rank_by") or "portfolio_score")
    keep = int(selection.get("keep_per_group") or 1)
    groups = ", ".join(str(item) for item in (selection.get("group_by") or []))
    fitness_metrics = selection.get("fitness_metrics")
    if fitness_metrics and isinstance(fitness_metrics, list):
        metrics_str = ", ".join(fitness_metrics)
        if groups:
            return f"top {keep} per {groups} by metrics: {metrics_str}"
        return f"top {keep} by metrics: {metrics_str}"
    if groups:
        return f"top {keep} per {groups} by {rank_by}"
    return f"top {keep} by {rank_by}"


def _rejection_reason(row: pd.Series, selection: dict[str, Any], selected_ids: set[str]) -> str:
    candidate_id = str(row.get("candidate_id") or "")
    if candidate_id in selected_ids:
        return ""
    filters = selection.get("hard_filters") or selection
    reasons: list[str] = []
    min_trades = _optional_float(filters.get("min_trades"))
    if min_trades is not None and float(row.get("total_trades") or 0) < min_trades:
        reasons.append("below_min_trades")
    min_pf = _optional_float(filters.get("min_profit_factor"))
    if min_pf is not None and float(row.get("profit_factor") or 0) < min_pf:
        reasons.append("below_min_profit_factor")
    max_dd = _optional_float(filters.get("max_drawdown"))
    if max_dd is not None and float(row.get("drawdown_abs") or 0) > max_dd:
        reasons.append("above_max_drawdown")
    min_net = _optional_float(filters.get("min_net_profit"))
    if min_net is not None and float(row.get("total_net_profit") or 0) < min_net:
        reasons.append("below_min_net_profit")
    if reasons:
        return ";".join(reasons)
    return "not_selected_by_rank"


def _write_selection_files(
    session: OptimizerSession,
    stage_id: str,
    selected: pd.DataFrame,
    rejected: pd.DataFrame,
    selected_rows: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
) -> dict[str, str | None]:
    stage_dir = session.directory / PARSED_RESULTS_DIRNAME / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    selected_csv = stage_dir / "selected.csv"
    selected_json = stage_dir / "selected.json"
    rejected_csv = stage_dir / "rejected.csv"
    rejected_json = stage_dir / "rejected.json"
    selected.to_csv(selected_csv, index=False)
    rejected.to_csv(rejected_csv, index=False)
    selected_json.write_text(json.dumps(selected_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    rejected_json.write_text(json.dumps(rejected_rows, indent=2, ensure_ascii=False), encoding="utf-8")

    root_selected = session.directory / RECIPE_SELECTION_CSV
    root_selected_json = session.directory / RECIPE_SELECTION_JSON
    selected.to_csv(root_selected, index=False)
    root_selected_json.write_text(json.dumps(selected_rows, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "selected_csv": str(selected_csv),
        "selected_json": str(selected_json),
        "rejected_csv": str(rejected_csv),
        "rejected_json": str(rejected_json),
    }


def _empty_summary(recipe_id: str, stage_id: str) -> RecipeSelectionSummary:
    return RecipeSelectionSummary(
        recipe_id=recipe_id,
        stage_id=stage_id,
        row_count=0,
        passing_count=0,
        selected_count=0,
        rejected_count=0,
    )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if pd.isna(value):
            clean[key] = None
        elif hasattr(value, "item"):
            clean[key] = value.item()
        else:
            clean[key] = value
    return clean
