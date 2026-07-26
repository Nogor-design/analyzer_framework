from __future__ import annotations

"""Result ingestion for Recipe/Matrix optimizer stages."""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ta_foundation.core.pipeline import ingest_folder
from ta_foundation.core.registry import ParserRegistry
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.web.optimizer_recipe_templates import MANIFEST_FILENAME
from ta_foundation.web.optimizer_session import OptimizerSession


GENERATED_DIRNAME = "generated_templates"
NT_OUTPUT_DIRNAME = "nt_output"
PARSED_RESULTS_DIRNAME = "parsed_results"


class RecipeResultsError(Exception):
    pass


@dataclass(frozen=True)
class RecipeStageResults:
    recipe_id: str
    stage_id: str
    output_dir: str
    row_count: int
    batch_count: int
    parse_warnings: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    parsed_rows_csv: str | None = None
    parsed_rows_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_recipe_stage_results(
    session: OptimizerSession,
    *,
    stage_id: str,
    persist: bool = True,
) -> RecipeStageResults:
    recipe = load_recipe(session)
    output_dir = session.directory / NT_OUTPUT_DIRNAME / stage_id
    if not output_dir.exists():
        raise RecipeResultsError(f"No NinjaTrader output folder found: {output_dir}")

    registry = ParserRegistry(parsers=[NinjaTraderOptimizationCsvParser()])
    result = ingest_folder(output_dir, registry=registry, recursive=True, load_tick_data=False)
    store = result.optimization_store
    combined = store.combined_results() if store else None
    if combined is None:
        combined = pd.DataFrame()

    manifest = _load_template_manifest(session, stage_id)
    name_map = _load_batch_name_map(output_dir)
    enriched = _enrich_results(
        combined,
        recipe_id=recipe.recipe_id,
        stage_id=stage_id,
        manifest=manifest,
        name_map=name_map,
    )
    
    rows = [_json_safe(row) for row in enriched.to_dict(orient="records")] if not enriched.empty else []
    parsed_csv: str | None = None
    parsed_json: str | None = None
    if persist:
        parsed_dir = session.directory / PARSED_RESULTS_DIRNAME / stage_id
        parsed_dir.mkdir(parents=True, exist_ok=True)
        parsed_csv_path = parsed_dir / "scored_rows.csv"
        parsed_json_path = parsed_dir / "scored_rows.json"
        enriched.to_csv(parsed_csv_path, index=False)
        parsed_json_path.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        parsed_csv = str(parsed_csv_path)
        parsed_json = str(parsed_json_path)

    notes: list[str] = []
    if combined.empty:
        notes.append("No optimizer rows were parsed from the stage output folder.")
    if not manifest:
        notes.append("No recipe template manifest was found; lineage metadata is limited.")

    return RecipeStageResults(
        recipe_id=recipe.recipe_id,
        stage_id=stage_id,
        output_dir=str(output_dir),
        row_count=len(rows),
        batch_count=len(store.batches) if store else 0,
        parse_warnings=sum(len(batch.warnings) for batch in store.batches.values()) if store else 0,
        rows=rows,
        notes=notes,
        parsed_rows_csv=parsed_csv,
        parsed_rows_json=parsed_json,
    )


def _load_template_manifest(session: OptimizerSession, stage_id: str) -> dict[str, dict[str, Any]]:
    path = session.directory / GENERATED_DIRNAME / stage_id / MANIFEST_FILENAME
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    templates = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(templates, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in templates:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("template_id") or "")
        if template_id:
            out[template_id] = item
    return out


def _load_batch_name_map(output_dir: Path) -> dict[str, str]:
    """Map each run's (possibly truncated) output-folder basename to the full,
    untruncated template name from ``BatchRunSummary.csv``.

    NinjaTrader truncates per-run output-FOLDER names to satisfy path-length
    limits (e.g. ``refine_risk__parent_stage_1_stage_1_0001_<hash>``), so a parsed
    row's ``batch_id`` (derived from the folder) no longer matches the manifest
    ``template_id``. The batch summary, however, records BOTH the truncated
    ``Output folder`` and the full ``Template`` name — so it is the authoritative
    bridge between the two. Without it, child-stage rows lose
    ``parent_candidate_id`` and per-parent grouping collapses (see the deployment
    matrix lineage bug, 2026-06-05).
    """
    summary_path = output_dir / "BatchRunSummary.csv"
    if not summary_path.exists():
        return {}
    try:
        summary = pd.read_csv(summary_path)
    except (OSError, pd.errors.ParserError, ValueError):
        return {}
    summary.columns = [str(c).strip() for c in summary.columns]
    if "Output folder" not in summary.columns or "Template" not in summary.columns:
        return {}
    name_map: dict[str, str] = {}
    for folder, template in zip(summary["Output folder"], summary["Template"]):
        if folder is None or template is None:
            continue
        # NT writes Windows paths; take the final path segment regardless of OS.
        basename = re.split(r"[\\/]", str(folder).strip())[-1]
        template_name = str(template).strip()
        if basename and template_name:
            name_map.setdefault(basename, template_name)
    return name_map


def _enrich_results(
    df: pd.DataFrame,
    *,
    recipe_id: str,
    stage_id: str,
    manifest: dict[str, dict[str, Any]],
    name_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    enriched = df.copy()
    enriched.insert(0, "recipe_id", recipe_id)
    enriched.insert(1, "stage_id", stage_id)

    # NinjaTrader truncates long output-folder names (deep session path + long
    # template names), so a result row's ``batch_id`` can diverge from the
    # manifest ``template_id`` and an exact lookup misses — dropping
    # ``parent_candidate_id`` and collapsing per-candidate grouping. Build a
    # fallback index keyed by the manifest ``bucket_id`` token, which NT
    # preserves as a substring of the truncated name. Longest tokens first so a
    # shorter bucket id can't shadow a longer one that contains it.
    bucket_index: dict[str, dict[str, Any]] = {}
    for meta in manifest.values():
        token = str(meta.get("bucket_id") or "")
        if token:
            bucket_index.setdefault(token, meta)
    bucket_tokens = sorted(bucket_index, key=len, reverse=True)

    name_map = name_map or {}

    def _meta_for(batch_id: str) -> dict[str, Any]:
        meta = manifest.get(batch_id)
        if meta:
            return meta
        # Authoritative bridge: resolve the truncated folder name to the full
        # template name via BatchRunSummary, then match the manifest exactly. This
        # is what preserves parent_candidate_id for child stages whose folder names
        # NT truncated past the point where the bucket_id token survives.
        full_name = name_map.get(batch_id)
        if full_name:
            meta = manifest.get(full_name)
            if meta:
                return meta
        for token in bucket_tokens:
            if token in batch_id:
                return bucket_index[token]
        # Last resort: the resolved full name may itself contain a bucket token.
        if full_name:
            for token in bucket_tokens:
                if token in full_name:
                    return bucket_index[token]
        return {}

    template_ids: list[str] = []
    bucket_ids: list[str] = []
    parent_ids: list[str | None] = []
    optimizer_targets: list[str] = []
    optimizer_csv_files: list[str] = []
    metadata_rows: list[dict[str, Any]] = []

    for _, row in enriched.iterrows():
        template_id = str(row.get("batch_id") or "")
        meta = _meta_for(template_id)
        template_ids.append(template_id)
        bucket_ids.append(str(meta.get("bucket_id") or template_id))
        parent_ids.append(meta.get("parent_candidate_id"))
        optimizer_targets.append(str(meta.get("optimization_target") or _target_from_template_id(template_id)))
        optimizer_csv_files.append(str(row.get("source_file") or ""))

        metadata_row: dict[str, Any] = {}
        metadata_row.update(dict(meta.get("matrix_values") or {}))
        metadata_row.update(dict(meta.get("fixed_values") or {}))
        metadata_rows.append(metadata_row)

    enriched.insert(2, "template_id", template_ids)
    enriched.insert(3, "bucket_id", bucket_ids)
    enriched.insert(4, "parent_candidate_id", parent_ids)
    enriched.insert(5, "optimizer_csv_file", optimizer_csv_files)
    enriched.insert(6, "optimizer_target", optimizer_targets)

    metadata_keys = sorted({key for row in metadata_rows for key in row.keys()})
    for name in metadata_keys:
        values = [row.get(name) for row in metadata_rows]
        if name not in enriched.columns:
            enriched[name] = values

    row_numbers = enriched.groupby("template_id").cumcount() + 1
    enriched.insert(6, "optimizer_row_id", row_numbers)
    enriched.insert(
        7,
        "candidate_id",
        [
            f"{stage_id}__{template_id}__row{int(row_number):05d}"
            for template_id, row_number in zip(template_ids, row_numbers)
        ],
    )
    if "max_drawdown" in enriched.columns and "drawdown_abs" not in enriched.columns:
        enriched["drawdown_abs"] = pd.to_numeric(enriched["max_drawdown"], errors="coerce").abs()
    return enriched


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


def _target_from_template_id(template_id: str) -> str:
    if "__opt_" not in template_id:
        return "MaxProfitFactor"
    return template_id.rsplit("__opt_", 1)[-1]
