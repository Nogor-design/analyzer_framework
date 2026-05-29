from __future__ import annotations

"""Matrix planner for Recipe/Matrix optimizer mode."""

import hashlib
import itertools
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_recipe import (
    OptimizerRecipeDocument,
    OptimizerRecipeError,
    RecipeMatrixEntry,
    RecipeStage,
    load_recipe,
)
from ta_foundation.web.optimizer_session import OptimizerSession


RECIPE_PLAN_FILENAME = "recipe_plan.json"


class RecipePlanError(OptimizerRecipeError):
    pass


@dataclass(frozen=True)
class RecipeSweep:
    name: str
    minimum: Any
    maximum: Any
    increment: Any
    step_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecipeJobPlan:
    stage_id: str
    template_id: str
    bucket_id: str
    combination_count: int
    matrix_values: dict[str, Any]
    fixed_values: dict[str, Any]
    optimized: tuple[RecipeSweep, ...]
    parent_candidate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "template_id": self.template_id,
            "bucket_id": self.bucket_id,
            "combination_count": self.combination_count,
            "matrix_values": self.matrix_values,
            "fixed_values": self.fixed_values,
            "optimized": [sweep.to_dict() for sweep in self.optimized],
            "parent_candidate_id": self.parent_candidate_id,
        }


@dataclass(frozen=True)
class RecipeStagePlan:
    stage_id: str
    stage_type: str
    template_count: int
    combination_count: int
    jobs: tuple[RecipeJobPlan, ...]
    deferred: bool = False
    from_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage_type": self.stage_type,
            "template_count": self.template_count,
            "combination_count": self.combination_count,
            "jobs": [job.to_dict() for job in self.jobs],
            "deferred": self.deferred,
            "from": self.from_ref,
        }


@dataclass(frozen=True)
class RecipePlanPreview:
    recipe_id: str
    recipe_name: str
    strategy_id: str
    template_count: int
    combination_estimate: int
    stages: tuple[RecipeStagePlan, ...]
    plan_hash: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "strategy_id": self.strategy_id,
            "template_count": self.template_count,
            "combination_estimate": self.combination_estimate,
            "stages": [stage.to_dict() for stage in self.stages],
            "plan_hash": self.plan_hash,
            "warnings": list(self.warnings),
        }


def build_recipe_plan_preview(recipe: OptimizerRecipeDocument) -> RecipePlanPreview:
    warnings: list[str] = []
    if not recipe.strategy_id:
        warnings.append("missing_strategy_id")
    if not recipe.stages:
        warnings.append("no_stages_configured")

    fixed_values = {
        entry.param: entry.value
        for entry in recipe.base_matrix
        if entry.role == "fixed"
    }
    matrix_axes = [
        entry for entry in recipe.base_matrix
        if entry.role == "matrix_axis"
    ]
    if not matrix_axes:
        warnings.append("no_matrix_axes_configured")

    stages: list[RecipeStagePlan] = []
    for stage in recipe.stages:
        if stage.from_ref:
            stages.append(_deferred_stage_plan(stage))
            continue
        if stage.stage_type == "fixed_backtest":
            stages.append(_deferred_stage_plan(stage))
            continue
        stages.append(_root_stage_plan(stage, matrix_axes, fixed_values))

    template_count = sum(stage.template_count for stage in stages)
    combination_estimate = sum(stage.combination_count for stage in stages)

    caps = recipe.safety_caps
    if caps.max_templates_per_stage is not None:
        for stage in stages:
            if stage.template_count > caps.max_templates_per_stage:
                warnings.append(f"stage_template_cap_exceeded:{stage.stage_id}:{stage.template_count}")
    if caps.max_total_combinations is not None and combination_estimate > caps.max_total_combinations:
        warnings.append(f"total_combination_cap_exceeded:{combination_estimate}")

    plan_hash = _hash_recipe_plan(recipe, stages)
    return RecipePlanPreview(
        recipe_id=recipe.recipe_id,
        recipe_name=recipe.recipe_name,
        strategy_id=recipe.strategy_id,
        template_count=template_count,
        combination_estimate=combination_estimate,
        stages=tuple(stages),
        plan_hash=plan_hash,
        warnings=tuple(warnings),
    )


def build_and_save_recipe_plan(session: OptimizerSession) -> RecipePlanPreview:
    recipe = load_recipe(session)
    plan = build_recipe_plan_preview(recipe)
    _write_json(session.directory / RECIPE_PLAN_FILENAME, plan.to_dict())
    return plan


def load_recipe_plan(session: OptimizerSession) -> dict[str, Any] | None:
    path = session.directory / RECIPE_PLAN_FILENAME
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _root_stage_plan(
    stage: RecipeStage,
    matrix_axes: list[RecipeMatrixEntry],
    fixed_values: dict[str, Any],
) -> RecipeStagePlan:
    optimized = _stage_sweeps(stage)
    per_template_combinations = 1
    for sweep in optimized:
        per_template_combinations *= max(1, sweep.step_count)

    jobs: list[RecipeJobPlan] = []
    axis_names = [axis.param for axis in matrix_axes]
    axis_values = [axis.values for axis in matrix_axes]
    for values in itertools.product(*axis_values):
        matrix_values = dict(zip(axis_names, values))
        bucket_id = _bucket_id(matrix_values)
        template_id = f"{stage.stage_id}__{bucket_id}"
        jobs.append(
            RecipeJobPlan(
                stage_id=stage.stage_id,
                template_id=template_id,
                bucket_id=bucket_id,
                combination_count=per_template_combinations,
                matrix_values=matrix_values,
                fixed_values=dict(fixed_values),
                optimized=optimized,
            )
        )

    return RecipeStagePlan(
        stage_id=stage.stage_id,
        stage_type=stage.stage_type,
        template_count=len(jobs),
        combination_count=sum(job.combination_count for job in jobs),
        jobs=tuple(jobs),
    )


def _deferred_stage_plan(stage: RecipeStage) -> RecipeStagePlan:
    return RecipeStagePlan(
        stage_id=stage.stage_id,
        stage_type=stage.stage_type,
        template_count=0,
        combination_count=0,
        jobs=(),
        deferred=True,
        from_ref=stage.from_ref,
    )


def _stage_sweeps(stage: RecipeStage) -> tuple[RecipeSweep, ...]:
    sweeps: list[RecipeSweep] = []
    for name, config in sorted(stage.optimize_inside_template.items()):
        minimum = config.get("min")
        maximum = config.get("max")
        increment = config.get("step", config.get("increment"))
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
    return tuple(sweeps)


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


def _bucket_id(values: dict[str, Any]) -> str:
    parts = [
        f"{_slug_name(name)}_{_slug_value(value)}"
        for name, value in values.items()
    ]
    return "__".join(parts) if parts else "base"


def _slug_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()
    return text or "param"


def _slug_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:02d}"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):02d}"
    text = re.sub(r"[^a-zA-Z0-9.-]+", "_", str(value)).strip("_").lower()
    return text.replace(".", "p") or "value"


def _hash_recipe_plan(recipe: OptimizerRecipeDocument, stages: list[RecipeStagePlan]) -> str:
    payload = {
        "recipe": recipe.to_dict(),
        "stages": [stage.to_dict() for stage in stages],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

