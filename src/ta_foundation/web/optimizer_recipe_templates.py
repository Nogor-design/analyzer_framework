from __future__ import annotations

"""Generate NinjaTrader XML templates for recipe stages."""

import json
import re
import shutil
from decimal import Decimal, InvalidOperation
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_recipe import RecipeStage, load_recipe
from ta_foundation.web.optimizer_recipe_plan import (
    RecipeJobPlan,
    RecipePlanError,
    RecipeSweep,
    build_and_save_recipe_plan,
    load_recipe_plan,
)
from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_strategy_catalog import get_strategy_detail
from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.optimizer_template_writer import (
    _align_bars_period_parameter,
    _patch_fixed,
    _patch_optimizer_parameter,
    _patch_sweep,
    _remove_top_level_tag,
    _replace_or_insert_strategy_tag,
    _replace_or_insert_top_level_tag,
    _replace_tag_text,
    _template_instrument_for,
)


GENERATED_DIRNAME = "generated_templates"
MANIFEST_FILENAME = "recipe_template_manifest.json"
PARSED_RESULTS_DIRNAME = "parsed_results"
NT_OPTIMIZATION_FITNESS_NAMESPACE = "NinjaTrader.NinjaScript.OptimizationFitnesses"
NT_OPTIMIZATION_FITNESSES = {
    "MaxAvgProfit",
    "MaxAvgProfitLong",
    "MaxAvgProfitShort",
    "MaxNetProfit",
    "MaxNetProfitLong",
    "MaxNetProfitShort",
    "MaxProbability",
    "MaxProbabilityLong",
    "MaxProbabilityShort",
    "MaxProfitFactor",
    "MaxProfitFactorLong",
    "MaxProfitFactorShort",
    "MaxR2",
    "MaxR2Long",
    "MaxR2Short",
    "MaxSharpeRatio",
    "MaxSharpeRatioLong",
    "MaxSharpeRatioShort",
    "MaxSortinoRatio",
    "MaxSortinoRatioLong",
    "MaxSortinoRatioShort",
    "MaxStrength",
    "MaxStrengthLong",
    "MaxStrengthShort",
    "MaxUlcerRatio",
    "MaxUlcerRatioLong",
    "MaxUlcerRatioShort",
    "MaxWinLossRatio",
    "MaxWinLossRatioLong",
    "MaxWinLossRatioShort",
    "MinAvgMae",
    "MinAvgMaeLong",
    "MinAvgMaeShort",
    "MinDrawDown",
    "MinDrawDownLong",
    "MinDrawDownShort",
}
NT_OPTIMIZATION_FITNESS_ALIASES = {
    "profit_factor": "MaxProfitFactor",
    "total_net_profit": "MaxNetProfit",
    "drawdown_abs": "MinDrawDown",
    "drawdown": "MinDrawDown",
    "max_drawdown": "MinDrawDown",
    "portfolio_score": "MaxProfitFactor",
}


class RecipeTemplateWriteError(Exception):
    pass


@dataclass(frozen=True)
class GeneratedRecipeTemplate:
    stage_id: str
    template_id: str
    bucket_id: str
    path: str
    combination_count: int
    parent_candidate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_recipe_stage_templates(
    session: OptimizerSession,
    *,
    stage_id: str,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> list[GeneratedRecipeTemplate]:
    doc = session.load_document()
    seed_path = Path(doc.seed_template_path)
    if not seed_path.exists():
        raise RecipeTemplateWriteError(f"Seed template not found: {seed_path}")

    plan = load_recipe_plan(session)
    if not plan:
        try:
            plan = build_and_save_recipe_plan(session).to_dict()
        except RecipePlanError as exc:
            raise RecipeTemplateWriteError(str(exc)) from exc

    stage = _find_stage(plan, stage_id)
    if stage is None:
        raise RecipeTemplateWriteError(f"Recipe plan has no stage: {stage_id}")

    seed_text = seed_path.read_text(encoding="utf-8")
    instrument = _template_instrument_for(doc.instrument, seed_path)
    if stage.get("stage_type") == "fixed_backtest":
        return _generate_final_backtest_stage_templates(
            session,
            stage_id=stage_id,
            seed_path=seed_path,
            seed_text=seed_text,
            instrument=instrument,
        )

    jobs = stage.get("jobs") or []
    if stage.get("deferred"):
        jobs = _child_stage_jobs_from_selection(session, stage_id=stage_id)
    if not jobs:
        raise RecipeTemplateWriteError(f"Stage {stage_id} has no template jobs.")

    output_dir = session.directory / GENERATED_DIRNAME / stage_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[GeneratedRecipeTemplate] = []
    manifest_jobs: list[dict[str, Any]] = []

    recipe = load_recipe(session)
    opt_type_val = optimizer_type or getattr(recipe, "optimizer_type", "Default")
    if opt_type_val == "Default":
        opt_type_val = "NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer"
    elif opt_type_val == "Genetic":
        opt_type_val = "NinjaTrader.NinjaScript.Optimizers.GeneticOptimizer"

    active_targets = getattr(recipe, "active_targets", ["MaxProfitFactor"])
    if not active_targets:
        active_targets = ["MaxProfitFactor"]

    for raw_job in jobs:
        job = _job_from_dict(raw_job)
        for target_name in active_targets:
            fitness_name = _normalize_optimization_fitness_name(target_name)
            fitness_class = _optimization_fitness_class(fitness_name)
            target_suffix = f"__opt_{_slug_value(fitness_name)}"
            
            suffixed_job = RecipeJobPlan(
                stage_id=job.stage_id,
                template_id=f"{job.template_id}{target_suffix}",
                bucket_id=job.bucket_id,
                combination_count=job.combination_count,
                matrix_values=job.matrix_values,
                fixed_values=job.fixed_values,
                optimized=job.optimized,
                parent_candidate_id=job.parent_candidate_id,
            )
            
            try:
                text = _patch_recipe_job(
                    seed_text,
                    suffixed_job,
                    instrument=instrument,
                    keep_best_results=int(getattr(recipe, "keep_best_results", 1000)),
                    optimizer_type=opt_type_val,
                    optimization_fitness=fitness_class,
                    from_date=doc.oos_from_date or None,
                    to_date=doc.oos_to_date or None,
                    entries_per_direction=recipe.entries_per_direction,
                    strategy_id=recipe.strategy_id,
                )
            except Exception as exc:
                raise RecipeTemplateWriteError(
                    f"Failed to generate template for job '{suffixed_job.template_id}': {exc}"
                ) from exc
            
            target_path = output_dir / f"{suffixed_job.template_id}.xml"
            target_path.write_text(text, encoding="utf-8")
            item = GeneratedRecipeTemplate(
                stage_id=stage_id,
                template_id=suffixed_job.template_id,
                bucket_id=suffixed_job.bucket_id,
                path=str(target_path),
                combination_count=suffixed_job.combination_count,
                parent_candidate_id=suffixed_job.parent_candidate_id,
            )
            written.append(item)
            manifest_jobs.append({
                **suffixed_job.to_dict(),
                "path": str(target_path),
                "optimization_fitness": fitness_class,
                "optimization_target": fitness_name,
            })

    manifest = {
        "schema_version": 1,
        "stage_id": stage_id,
        "template_count": len(written),
        "templates": manifest_jobs,
    }
    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return written


def _generate_final_backtest_stage_templates(
    session: OptimizerSession,
    *,
    stage_id: str,
    seed_path: Path,
    seed_text: str,
    instrument: str,
) -> list[GeneratedRecipeTemplate]:
    recipe = load_recipe(session)
    stage = _find_recipe_stage(recipe.stages, stage_id)
    if stage is None:
        raise RecipeTemplateWriteError(f"Recipe has no stage: {stage_id}")
    parent_stage_id = _parent_stage_id(stage.from_ref)
    if not parent_stage_id:
        raise RecipeTemplateWriteError(f"Final stage {stage_id} does not identify a parent selected_rows source.")

    selected_rows, bucket_report = _final_backtest_source_rows(
        session,
        recipe=recipe,
        parent_stage_id=parent_stage_id,
        finalists_per_bucket=int(stage.raw.get("finalists_per_bucket") or 2),
    )
    if not selected_rows:
        selected_path = session.directory / PARSED_RESULTS_DIRNAME / parent_stage_id / "selected.json"
        raise RecipeTemplateWriteError(
            f"Final stage {stage_id} is deferred until parent selections exist: {selected_path}"
        )

    output_dir = session.directory / GENERATED_DIRNAME / stage_id
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    named_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "named_backtest_templates"
        / "recipe"
    )
    stale_renamed_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "renamed_backtest_templates"
    )
    if named_dir.exists():
        shutil.rmtree(named_dir)
    named_dir.mkdir(parents=True, exist_ok=True)
    if stale_renamed_dir.exists():
        shutil.rmtree(stale_renamed_dir)

    doc = session.load_document()
    from_date = doc.oos_from_date or None
    to_date = doc.oos_to_date or None
    from ta_foundation.web.optimizer_final_templates import final_template_export_name
    written: list[GeneratedRecipeTemplate] = []
    manifest_jobs: list[dict[str, Any]] = []

    for rank, row in enumerate(selected_rows, start=1):
        if not isinstance(row, dict):
            continue
        parent_id = str(row.get("candidate_id") or f"{parent_stage_id}_row_{rank}")
        values = _strategy_values_from_row(row, allowed_names=_final_strategy_param_names(recipe, parent_stage_id))
        for entry in recipe.base_matrix:
            if entry.role == "fixed":
                values[entry.param] = entry.value
        template_id = f"{stage_id}__F_{rank:03d}"
        target = output_dir / f"{template_id}.xml"
        generated = generate_fixed_backtest_template(
            seed_path,
            target,
            values,
            from_date=from_date,
            to_date=to_date,
        )
        text = generated.read_text(encoding="utf-8")
        if instrument:
            text = _replace_or_insert_strategy_tag(text, "InstrumentOrInstrumentList", instrument)
        if recipe.entries_per_direction is not None:
            text = _replace_tag_text(text, "EntriesPerDirection", str(recipe.entries_per_direction), count=1)
        text = _strip_recipe_placeholder_parameter(text)
        text = _align_bars_period_parameter(text)
        generated.write_text(text, encoding="utf-8")
        mirror = named_dir / final_template_export_name(generated)
        shutil.copy2(generated, mirror)
        item = GeneratedRecipeTemplate(
            stage_id=stage_id,
            template_id=template_id,
            bucket_id=str(row.get("bucket_id") or parent_id),
            path=str(generated),
            combination_count=1,
            parent_candidate_id=parent_id,
        )
        written.append(item)
        manifest_jobs.append({
            **item.to_dict(),
            "parent_stage_id": parent_stage_id,
            "initial_bucket_key": row.get("initial_bucket_key"),
            "initial_bucket_values": row.get("initial_bucket_values"),
            "final_selection_source_stage": row.get("stage_id"),
            "deployment_package_path": str(mirror),
            "strategy_values": values,
        })

    manifest = {
        "schema_version": 1,
        "stage_id": stage_id,
        "stage_type": "fixed_backtest",
        "template_count": len(written),
        "target_buckets": len(bucket_report),
        "finalists_per_bucket": int(stage.raw.get("finalists_per_bucket") or 2),
        "bucket_report": bucket_report,
        "templates": manifest_jobs,
    }
    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return written


def _normalize_optimization_fitness_name(target: Any) -> str:
    raw = str(target or "").strip()
    if not raw:
        return "MaxProfitFactor"
    short_name = raw.rsplit(".", 1)[-1]
    alias = NT_OPTIMIZATION_FITNESS_ALIASES.get(short_name)
    if alias is None:
        alias = NT_OPTIMIZATION_FITNESS_ALIASES.get(short_name.lower())
    if alias:
        return alias
    if short_name in NT_OPTIMIZATION_FITNESSES:
        return short_name
    return "MaxProfitFactor"


def _optimization_fitness_class(target: Any) -> str:
    short_name = _normalize_optimization_fitness_name(target)
    return f"{NT_OPTIMIZATION_FITNESS_NAMESPACE}.{short_name}"


def _patch_recipe_job(
    seed_text: str,
    job: RecipeJobPlan,
    *,
    instrument: str,
    keep_best_results: int,
    optimizer_type: str | None,
    optimization_fitness: str | None,
    from_date: str | None = None,
    to_date: str | None = None,
    entries_per_direction: int | None = None,
    strategy_id: str | None = None,
) -> str:
    text = seed_text
    if optimizer_type:
        text = _replace_or_insert_top_level_tag(text, "OptimizerType", optimizer_type)
    if optimization_fitness:
        text = _replace_or_insert_top_level_tag(text, "OptimizationFitness", optimization_fitness)
    text = _remove_top_level_tag(text, "BacktestType")
    text = _replace_tag_text(text, "Category", "Optimize", count=1)
    if instrument:
        text = _replace_or_insert_strategy_tag(text, "InstrumentOrInstrumentList", instrument)
    text = _patch_optimizer_parameter(text, "KeepBestResults", str(keep_best_results))

    if from_date:
        text = _replace_tag_text(text, "From", f"{from_date}T00:00:00", count=1)
    if to_date:
        text = _replace_tag_text(text, "To", f"{to_date}T00:00:00", count=1)
    if entries_per_direction is not None:
        text = _replace_tag_text(text, "EntriesPerDirection", str(entries_per_direction), count=1)

    strategy_parameters = _strategy_parameters(strategy_id)
    type_map = {parameter.name: parameter.type_name for parameter in strategy_parameters}
    seeded_optimization_parameters = "<OptimizationParameters" not in text
    if seeded_optimization_parameters:
        text = _seed_missing_optimization_parameters(
            text,
            strategy_parameters=strategy_parameters,
            values={**job.fixed_values, **job.matrix_values},
        )

    matrix_names = set(job.matrix_values)
    for name, value in {**job.fixed_values, **job.matrix_values}.items():
        text = _patch_fixed(
            text,
            {
                "name": name,
                "value": value,
                "type_name": type_map.get(name),
                "insert_if_missing": (name in matrix_names and not seeded_optimization_parameters),
            },
        )

    for sweep in job.optimized:
        text = _patch_sweep(text, {
            "name": sweep.name,
            "minimum": sweep.minimum,
            "maximum": sweep.maximum,
            "increment": sweep.increment,
            "type_name": type_map.get(sweep.name),
        })

    text = _strip_recipe_placeholder_parameter(text)
    text = _align_bars_period_parameter(text)
    return text


def _strip_recipe_placeholder_parameter(text: str) -> str:
    """Remove the synthetic ``__recipe_placeholder__`` parameter that
    ``regenerate_recipe_seed`` injects so the regenerated seed XML passes
    NinjaTrader's strict "Optimize template needs at least one swept parameter"
    check. The placeholder must not survive into stage-generated templates or
    NT will sweep over its fake values and produce phantom optimizer rows.
    """
    return re.sub(
        r"\s*<Parameter>\s*(?:(?!</Parameter>).)*?<Name>\s*__recipe_placeholder__\s*</Name>(?:(?!</Parameter>).)*?</Parameter>",
        "",
        text,
        flags=re.DOTALL,
    )


def _strategy_parameters(strategy_id: str | None):
    if not strategy_id:
        return []
    try:
        detail = get_strategy_detail(strategy_id)
    except Exception:
        return []
    if detail is None:
        return []
    return list(detail.parameters)


def _seed_missing_optimization_parameters(
    text: str,
    *,
    strategy_parameters: list[Any],
    values: dict[str, Any],
) -> str:
    for parameter in strategy_parameters:
        type_name = str(parameter.type_name or "").strip().lower()
        if type_name not in {"bool", "boolean", "int", "int32", "double"}:
            continue
        value = values.get(parameter.name)
        if value is None:
            value = _strategy_tag_value(text, parameter.name)
        if value is None or value == "":
            value = parameter.default
        text = _patch_fixed(
            text,
            {
                "name": parameter.name,
                "value": value,
                "type_name": parameter.type_name,
                "insert_if_missing": True,
            },
        )
    return text


def _strategy_tag_value(text: str, name: str) -> str | None:
    match = re.search(
        r"<" + re.escape(name) + r"(?:\s+[^>]*)?>(.*?)</" + re.escape(name) + r">",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(1).strip()


def _find_stage(plan: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
    for stage in plan.get("stages") or []:
        if str(stage.get("stage_id") or "") == stage_id:
            return stage
    return None


def _child_stage_jobs_from_selection(session: OptimizerSession, *, stage_id: str) -> list[dict[str, Any]]:
    recipe = load_recipe(session)
    stage = _find_recipe_stage(recipe.stages, stage_id)
    if stage is None:
        raise RecipeTemplateWriteError(f"Recipe has no stage: {stage_id}")
    if stage.stage_type != "optimizer":
        raise RecipeTemplateWriteError(f"Stage {stage_id} is not an optimizer stage.")
    parent_stage_id = _parent_stage_id(stage.from_ref)
    if not parent_stage_id:
        raise RecipeTemplateWriteError(f"Stage {stage_id} does not identify a parent selected_rows source.")

    selected_path = session.directory / PARSED_RESULTS_DIRNAME / parent_stage_id / "selected.json"
    if not selected_path.exists():
        raise RecipeTemplateWriteError(
            f"Stage {stage_id} is deferred until parent selections exist: {selected_path}"
        )
    try:
        selected_rows = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeTemplateWriteError(f"Could not read parent selections for {stage_id}: {exc}") from exc
    if not isinstance(selected_rows, list) or not selected_rows:
        raise RecipeTemplateWriteError(f"Stage {stage_id} has no parent selections.")

    pin_names = list(stage.pin) or [entry.param for entry in recipe.base_matrix]
    fixed_base = {
        entry.param: entry.value
        for entry in recipe.base_matrix
        if entry.role == "fixed"
    }

    jobs: list[dict[str, Any]] = []
    for index, row in enumerate(selected_rows, start=1):
        if not isinstance(row, dict):
            continue
        parent_id = str(row.get("candidate_id") or f"{parent_stage_id}_row_{index}")
        fixed_values = dict(fixed_base)
        for name in pin_names:
            value = _row_value(row, name)
            if value is not None:
                fixed_values[name] = value

        optimized = _child_stage_sweeps(stage, row, strategy_id=recipe.strategy_id)
        combination_count = 1
        for sweep in optimized:
            combination_count *= max(1, sweep.step_count)

        parent_slug = _slug_value(parent_id)
        bucket_id = f"parent_{parent_slug}"
        template_id = f"{stage_id}__{bucket_id}"
        jobs.append(
            RecipeJobPlan(
                stage_id=stage_id,
                template_id=template_id,
                bucket_id=bucket_id,
                combination_count=combination_count,
                matrix_values={},
                fixed_values=fixed_values,
                optimized=tuple(optimized),
                parent_candidate_id=parent_id,
            ).to_dict()
        )
    return jobs


def _final_strategy_param_names(recipe: Any, parent_stage_id: str) -> set[str]:
    names = {entry.param for entry in recipe.base_matrix}
    for stage in recipe.stages:
        if stage.stage_id == parent_stage_id:
            names.update(stage.optimize_inside_template.keys())
            names.update(stage.refine_around_parent_result.keys())
            names.update(stage.add_optimize.keys())
            break
        names.update(stage.optimize_inside_template.keys())
        names.update(stage.refine_around_parent_result.keys())
        names.update(stage.add_optimize.keys())
    return names


def _final_backtest_source_rows(
    session: OptimizerSession,
    *,
    recipe: Any,
    parent_stage_id: str,
    finalists_per_bucket: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve the rows a final fixed-backtest stage should validate.

    A **risk-knob** refinement parent (``prepare_refinement``: pins every
    structural param and sweeps only ProfitStop/LossStop/MaxTrades via
    ``optimize_inside_template``, no ``refine_around_parent_result``) carries the
    hand-picked winners we must validate verbatim — one final template per
    refined row. Re-pooling it through ``_final_rows_by_initial_bucket`` would
    mix in the original stage_1 coverage finalists (default risk knobs) and
    bucket by the initial grid, so the swept winners get washed out.

    A **structural** refinement parent (``refine_around_parent_result``, e.g. the
    Decision-Dashboard "refine structure" flow) still wants full initial-bucket
    coverage in the final — every lane represented, best across stages — so it
    keeps the initial-bucket path. The initial coverage stage (``stage_1``, no
    ``from_ref``) also uses initial bucketing.
    """
    parent_stage = _find_recipe_stage(recipe.stages, parent_stage_id)
    parent_uses_refine_around = parent_stage is not None and bool(
        getattr(parent_stage, "refine_around_parent_result", None)
    )
    parent_is_pick_refinement = (
        parent_stage is not None
        and bool(getattr(parent_stage, "from_ref", None))
        and not parent_uses_refine_around
    )
    if not parent_is_pick_refinement:
        return _final_rows_by_initial_bucket(
            session,
            recipe=recipe,
            parent_stage_id=parent_stage_id,
            finalists_per_bucket=finalists_per_bucket,
        )

    selected_rows = [row for row in _load_stage_selected_rows(session, parent_stage_id) if isinstance(row, dict)]
    bucket_report = [
        {
            "bucket_key": str(row.get("parent_candidate_id") or row.get("candidate_id") or f"row_{idx}"),
            "bucket_values": {},
            "candidate_count": 1,
            "selected_count": 1,
            "target_count": 1,
            "status": "selected",
            "selected_candidate_ids": [str(row.get("candidate_id") or "")],
        }
        for idx, row in enumerate(selected_rows, start=1)
    ]
    return selected_rows, bucket_report


def _final_rows_by_initial_bucket(
    session: OptimizerSession,
    *,
    recipe: Any,
    parent_stage_id: str,
    finalists_per_bucket: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    finalists_per_bucket = max(1, finalists_per_bucket)
    stage_ids = _optimizer_stage_ids_through_parent(recipe, parent_stage_id)
    if not stage_ids:
        return [], []

    stage_rows_by_id: dict[str, list[dict[str, Any]]] = {}
    rows_by_candidate: dict[str, dict[str, Any]] = {}
    for current_stage_id in stage_ids:
        rows = _load_stage_selected_rows(session, current_stage_id)
        stage_rows_by_id[current_stage_id] = rows
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id and candidate_id not in rows_by_candidate:
                rows_by_candidate[candidate_id] = row

    buckets = _initial_bucket_specs(session, recipe)
    bucketed: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in buckets}
    for current_stage_id in stage_ids:
        for row in stage_rows_by_id.get(current_stage_id, []):
            row_copy = dict(row)
            bucket_key, bucket_values = _initial_bucket_for_row(
                row_copy,
                recipe=recipe,
                rows_by_candidate=rows_by_candidate,
            )
            if not bucket_key:
                continue
            row_copy["initial_bucket_key"] = bucket_key
            row_copy["initial_bucket_values"] = bucket_values
            bucketed.setdefault(bucket_key, []).append(row_copy)

    selected: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    order = {key: idx for idx, (key, _) in enumerate(buckets)}
    for bucket_key in sorted(bucketed.keys(), key=lambda key: order.get(key, len(order))):
        candidates = _dedupe_candidate_rows(bucketed[bucket_key])
        ranked = sorted(candidates, key=_final_candidate_sort_key)
        winners = ranked[:finalists_per_bucket]
        selected.extend(winners)
        spec_values = dict(next((values for key, values in buckets if key == bucket_key), {}))
        if winners:
            reason = "selected"
        elif candidates:
            reason = "all_candidates_filtered_or_invalid"
        else:
            reason = "no_selected_candidates_for_initial_bucket"
        report.append({
            "bucket_key": bucket_key,
            "bucket_values": spec_values or (winners[0].get("initial_bucket_values") if winners else {}),
            "candidate_count": len(candidates),
            "selected_count": len(winners),
            "target_count": finalists_per_bucket,
            "status": reason,
            "selected_candidate_ids": [str(row.get("candidate_id") or "") for row in winners],
        })
    return selected, report


def _optimizer_stage_ids_through_parent(recipe: Any, parent_stage_id: str) -> list[str]:
    out: list[str] = []
    for stage in recipe.stages:
        if stage.stage_type == "optimizer":
            out.append(stage.stage_id)
        if stage.stage_id == parent_stage_id:
            break
    return out


def _load_stage_selected_rows(session: OptimizerSession, stage_id: str) -> list[dict[str, Any]]:
    stage_dir = session.directory / PARSED_RESULTS_DIRNAME / stage_id
    paths = [stage_dir / "selected.json", stage_dir / "final_selected.json"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id") or "")
            key = candidate_id or json.dumps(item, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(item))
    return rows


def _initial_bucket_specs(session: OptimizerSession, recipe: Any) -> list[tuple[str, dict[str, Any]]]:
    plan = load_recipe_plan(session) or {}
    for stage in plan.get("stages") or []:
        if str(stage.get("stage_id") or "") != "stage_1":
            continue
        seen: set[str] = set()
        buckets: list[tuple[str, dict[str, Any]]] = []
        for job in stage.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            values = dict(job.get("matrix_values") or {})
            key = _bucket_key_from_values(values, recipe)
            if key and key not in seen:
                seen.add(key)
                buckets.append((key, values))
        if buckets:
            return buckets

    axis_names = [entry.param for entry in recipe.base_matrix if entry.role == "matrix_axis"]
    if not axis_names:
        return [("base", {})]
    return []


def _initial_bucket_for_row(
    row: dict[str, Any],
    *,
    recipe: Any,
    rows_by_candidate: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    axis_names = [entry.param for entry in recipe.base_matrix if entry.role == "matrix_axis"]
    values: dict[str, Any] = {}
    for name in axis_names:
        value = _row_value(row, name)
        if value is not None:
            values[name] = value
    if len(values) == len(axis_names):
        return _bucket_key_from_values(values, recipe), values

    parent_id = str(row.get("parent_candidate_id") or "")
    if parent_id and parent_id in rows_by_candidate:
        return _initial_bucket_for_row(rows_by_candidate[parent_id], recipe=recipe, rows_by_candidate=rows_by_candidate)

    if not axis_names:
        return "base", {}
    return "", {}


def _bucket_key_from_values(values: dict[str, Any], recipe: Any) -> str:
    axis_names = [entry.param for entry in recipe.base_matrix if entry.role == "matrix_axis"]
    if not axis_names:
        return "base"
    parts: list[str] = []
    for name in axis_names:
        if name not in values:
            return ""
        parts.append(f"{_slug_value(name)}_{_slug_value(values.get(name))}")
    return "__".join(parts)


def _dedupe_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "")
        key = candidate_id or json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _final_candidate_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, str]:
    score = _float_for_sort(row.get("portfolio_score"))
    if score == 0:
        score = (
            _float_for_sort(row.get("profit_factor")) * 1000
            + _float_for_sort(row.get("total_net_profit")) / 10
            - _float_for_sort(row.get("drawdown_abs") or abs(_float_for_sort(row.get("max_drawdown")))) / 2
            + _float_for_sort(row.get("total_trades"))
        )
    return (
        -score,
        -_float_for_sort(row.get("profit_factor")),
        -_float_for_sort(row.get("total_net_profit")),
        _float_for_sort(row.get("drawdown_abs") or abs(_float_for_sort(row.get("max_drawdown")))),
        str(row.get("candidate_id") or ""),
    )


def _float_for_sort(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _strategy_values_from_row(row: dict[str, Any], *, allowed_names: set[str] | None = None) -> dict[str, Any]:
    skip = {
        "recipe_id",
        "stage_id",
        "template_id",
        "bucket_id",
        "parent_candidate_id",
        "optimizer_csv_file",
        "optimizer_row_id",
        "candidate_id",
        "selection_status",
        "selection_reason",
        "rejection_reason",
        "source_file",
        "batch_id",
        "raw_parameters",
        "parse_ok",
        "parse_warnings",
    }
    metric_prefixes = (
        "total_",
        "gross_",
        "profit_factor",
        "max_drawdown",
        "drawdown_abs",
        "percent_",
        "portfolio_score",
        "performance",
    )
    values: dict[str, Any] = {}
    if allowed_names is not None:
        for name in sorted(allowed_names):
            value = _row_value(row, name)
            if value is not None:
                values[name] = value
    for key, value in row.items():
        if value is None or key in skip:
            continue
        if key.startswith("param_"):
            name = key.removeprefix("param_")
            if allowed_names is not None and name not in allowed_names:
                continue
            if name not in values:
                values[name] = value
            continue
        if key.startswith(metric_prefixes):
            continue
        if key in {"Instrument", "instrument"}:
            continue
        if allowed_names is not None and key not in allowed_names:
            continue
        if isinstance(value, (str, int, float, bool)):
            values[key] = value
    return values


def _find_recipe_stage(stages: tuple[RecipeStage, ...], stage_id: str) -> RecipeStage | None:
    for stage in stages:
        if stage.stage_id == stage_id:
            return stage
    return None


def _parent_stage_id(from_ref: str | None) -> str | None:
    if not from_ref:
        return None
    parent, _, suffix = from_ref.partition(".")
    if parent and suffix == "selected_rows":
        return parent
    return None


def _strategy_param_default_value(strategy_id: str | None, param_name: str) -> Any:
    params = _strategy_parameters(strategy_id)
    for p in params:
        if p.name == param_name:
            return p.default
    wanted = _canonical_recipe_column_name(param_name)
    for p in params:
        if _canonical_recipe_column_name(p.name) == wanted:
            return p.default
    return None


def _child_stage_sweeps(stage: RecipeStage, row: dict[str, Any], strategy_id: str | None = None) -> list[RecipeSweep]:
    sweeps: list[RecipeSweep] = []
    for name, config in sorted(stage.optimize_inside_template.items()):
        sweeps.append(
            RecipeSweep(
                name=name,
                minimum=config.get("min"),
                maximum=config.get("max"),
                increment=config.get("step", config.get("increment")),
                step_count=_range_step_count(config.get("min"), config.get("max"), config.get("step", config.get("increment"))),
            )
        )
    for name, config in sorted(stage.refine_around_parent_result.items()):
        parent_value = _row_value(row, str(config.get("source") or name))
        if parent_value is None and str(config.get("source") or "") == "parent":
            parent_value = _row_value(row, name)

        # Fallback 1: Strategy catalog C# default value
        if parent_value is None and strategy_id:
            parent_value = _strategy_param_default_value(strategy_id, name)

        # Fallback 2: Allowed min/max midpoint
        if parent_value is None:
            min_val = config.get("min")
            max_val = config.get("max")
            if min_val is not None and max_val is not None:
                parent_value = (float(min_val) + float(max_val)) / 2

        # Fallback 3: Hard-coded default
        if parent_value is None:
            parent_value = 1

        minimum, maximum, increment = _refined_range(parent_value, config)

        # Safety clamp to guarantee non-None bounds
        if minimum is None or maximum is None:
            minimum = config.get("min", parent_value)
            maximum = config.get("max", parent_value)
            if minimum is None:
                minimum = 1
            if maximum is None:
                maximum = 1

        sweeps.append(
            RecipeSweep(
                name=name,
                minimum=minimum,
                maximum=maximum,
                increment=increment,
                step_count=_range_step_count(minimum, maximum, increment),
            )
        )
    for name, values in sorted(stage.add_optimize.items()):
        if not values:
            continue
        sweeps.append(
            RecipeSweep(
                name=name,
                minimum=values[0],
                maximum=values[-1],
                increment=1,
                step_count=len(values),
            )
        )
    return sweeps



def _refined_range(parent_value: Any, config: dict[str, Any]) -> tuple[Any, Any, Any]:
    increment = config.get("step", config.get("increment", 1))
    radius = config.get("radius", 0)
    parent = _to_decimal(parent_value)
    radius_d = _to_decimal(radius)
    if parent is None or radius_d is None:
        return parent_value, parent_value, increment
    minimum = parent - radius_d
    maximum = parent + radius_d
    floor = _to_decimal(config.get("min"))
    ceiling = _to_decimal(config.get("max"))
    if floor is not None and minimum < floor:
        minimum = floor
    if ceiling is not None and maximum > ceiling:
        maximum = ceiling
    return _decimal_to_number(minimum), _decimal_to_number(maximum), increment


def _row_value(row: dict[str, Any], name: str) -> Any:
    if name == "parent":
        return None
    if name in row and row[name] is not None:
        return row[name]
    param_name = f"param_{name}"
    if param_name in row and row[param_name] is not None:
        return row[param_name]
    wanted = _canonical_recipe_column_name(name)
    param_wanted = _canonical_recipe_column_name(param_name)
    for key, value in row.items():
        if value is None:
            continue
        canonical = _canonical_recipe_column_name(str(key))
        if canonical in {wanted, param_wanted}:
            return value
    return None


def _canonical_recipe_column_name(value: str) -> str:
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


def _range_step_count(minimum: Any, maximum: Any, increment: Any) -> int:
    if isinstance(minimum, bool) or isinstance(maximum, bool):
        return 1 if minimum == maximum else 2
    min_d = _to_decimal(minimum)
    max_d = _to_decimal(maximum)
    inc_d = _to_decimal(increment)
    if min_d is None or max_d is None:
        return 1
    if max_d < min_d:
        return 0
    if inc_d is None or inc_d <= 0:
        return 1 if max_d == min_d else 0
    return int((max_d - min_d) // inc_d) + 1


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _slug_value(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9.-]+", "_", str(value)).strip("_").lower()
    return text.replace(".", "p")[:120] or "value"


def _job_from_dict(data: dict[str, Any]) -> RecipeJobPlan:
    return RecipeJobPlan(
        stage_id=str(data.get("stage_id") or ""),
        template_id=str(data.get("template_id") or ""),
        bucket_id=str(data.get("bucket_id") or ""),
        combination_count=int(data.get("combination_count") or 0),
        matrix_values=dict(data.get("matrix_values") or {}),
        fixed_values=dict(data.get("fixed_values") or {}),
        optimized=tuple(
            RecipeSweep(
                name=str(sweep.get("name") or ""),
                minimum=sweep.get("minimum"),
                maximum=sweep.get("maximum"),
                increment=sweep.get("increment"),
                step_count=int(sweep.get("step_count") or 1),
            )
            for sweep in (data.get("optimized") or [])
        ),
        parent_candidate_id=data.get("parent_candidate_id"),
    )
