from __future__ import annotations

"""Recipe/Matrix optimizer schema and persistence helpers.

Recipe mode is intentionally stored beside the existing optimizer session
document instead of inside it. That keeps standard sessions backward-compatible
while giving the new autonomous planner a versioned config of its own.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


RECIPE_SCHEMA_VERSION = 1
RECIPE_FILENAME = "recipe.json"


class OptimizerRecipeError(Exception):
    pass


class OptimizerRecipeNotFoundError(OptimizerRecipeError):
    pass


class OptimizerRecipeSchemaError(OptimizerRecipeError):
    pass


@dataclass(frozen=True)
class RecipeSafetyCaps:
    max_total_combinations: int | None = None
    max_total_runtime_minutes: int | None = None
    max_templates_per_stage: int | None = None
    pause_on_boundary_winners: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RecipeSafetyCaps":
        data = data or {}
        return cls(
            max_total_combinations=_optional_int(data.get("max_total_combinations")),
            max_total_runtime_minutes=_optional_int(data.get("max_total_runtime_minutes")),
            max_templates_per_stage=_optional_int(data.get("max_templates_per_stage")),
            pause_on_boundary_winners=bool(data.get("pause_on_boundary_winners") or False),
        )


@dataclass(frozen=True)
class RecipeMatrixEntry:
    param: str
    role: str
    values: tuple[Any, ...] = ()
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "param": self.param,
            "role": self.role,
        }
        if self.role in {"matrix_axis", "matrix_bundle_axis"}:
            payload["values"] = list(self.values)
        else:
            payload["value"] = self.value
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeMatrixEntry":
        param = str(data.get("param") or "").strip()
        role = str(data.get("role") or "").strip()
        if not param:
            raise OptimizerRecipeSchemaError("Recipe matrix entry is missing param.")
        if role not in {"matrix_axis", "fixed", "matrix_bundle_axis"}:
            raise OptimizerRecipeSchemaError(
                f"Invalid recipe matrix role for {param}: {role!r}"
            )
        values = tuple(data.get("values") or ())
        if role == "matrix_axis" and not values:
            raise OptimizerRecipeSchemaError(
                f"Matrix axis {param} must define at least one value."
            )
        if role == "matrix_bundle_axis":
            if not values or not all(isinstance(value, dict) for value in values):
                raise OptimizerRecipeSchemaError(
                    f"Matrix bundle axis {param} must define at least one dict value."
                )
            values = tuple(dict(value) for value in values)
        return cls(
            param=param,
            role=role,
            values=values,
            value=data.get("value"),
        )


@dataclass(frozen=True)
class RecipeStage:
    stage_id: str
    stage_type: str
    description: str = ""
    from_ref: str | None = None
    pin: tuple[str, ...] = ()
    optimize_inside_template: dict[str, dict[str, Any]] = field(default_factory=dict)
    refine_around_parent_result: dict[str, dict[str, Any]] = field(default_factory=dict)
    add_optimize: dict[str, list[Any]] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.raw)
        payload.update({
            "stage_id": self.stage_id,
            "stage_type": self.stage_type,
            "description": self.description,
        })
        if self.from_ref is not None:
            payload["from"] = self.from_ref
        if self.pin:
            payload["pin"] = list(self.pin)
        if self.optimize_inside_template:
            payload["optimize_inside_template"] = self.optimize_inside_template
        if self.refine_around_parent_result:
            payload["refine_around_parent_result"] = self.refine_around_parent_result
        if self.add_optimize:
            payload["add_optimize"] = self.add_optimize
        if self.selection:
            payload["selection"] = self.selection
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeStage":
        stage_id = str(data.get("stage_id") or "").strip()
        stage_type = str(data.get("stage_type") or "").strip()
        if not stage_id:
            raise OptimizerRecipeSchemaError("Recipe stage is missing stage_id.")
        if stage_type not in {"optimizer", "fixed_backtest"}:
            raise OptimizerRecipeSchemaError(
                f"Invalid recipe stage_type for {stage_id}: {stage_type!r}"
            )
        return cls(
            stage_id=stage_id,
            stage_type=stage_type,
            description=str(data.get("description") or ""),
            from_ref=data.get("from"),
            pin=tuple(str(p) for p in (data.get("pin") or [])),
            optimize_inside_template=dict(data.get("optimize_inside_template") or {}),
            refine_around_parent_result=dict(data.get("refine_around_parent_result") or {}),
            add_optimize={
                str(k): list(v if isinstance(v, list) else [v])
                for k, v in dict(data.get("add_optimize") or {}).items()
            },
            selection=dict(data.get("selection") or {}),
            raw=dict(data),
        )


@dataclass(frozen=True)
class OptimizerRecipeDocument:
    recipe_version: int
    mode: str
    recipe_id: str
    recipe_name: str
    strategy_id: str
    target_final_candidates: int | None = None
    entries_per_direction: int = 1
    safety_caps: RecipeSafetyCaps = field(default_factory=RecipeSafetyCaps)
    base_matrix: tuple[RecipeMatrixEntry, ...] = ()
    stages: tuple[RecipeStage, ...] = ()
    optimizer_type: str = "Default"
    keep_best_results: int = 1000
    active_targets: tuple[str, ...] = ("MaxProfitFactor",)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_version": self.recipe_version,
            "mode": self.mode,
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "strategy_id": self.strategy_id,
            "target_final_candidates": self.target_final_candidates,
            "entries_per_direction": self.entries_per_direction,
            "safety_caps": self.safety_caps.to_dict(),
            "base_matrix": [entry.to_dict() for entry in self.base_matrix],
            "stages": [stage.to_dict() for stage in self.stages],
            "optimizer_type": self.optimizer_type,
            "keep_best_results": self.keep_best_results,
            "active_targets": list(self.active_targets),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptimizerRecipeDocument":
        version = data.get("recipe_version")
        if version != RECIPE_SCHEMA_VERSION:
            raise OptimizerRecipeSchemaError(
                f"Recipe has recipe_version {version!r}, expected {RECIPE_SCHEMA_VERSION}"
            )
        mode = str(data.get("mode") or "").strip()
        if mode != "matrix_sequence":
            raise OptimizerRecipeSchemaError(f"Unsupported recipe mode: {mode!r}")
        recipe_id = str(data.get("recipe_id") or "").strip()
        if not recipe_id:
            raise OptimizerRecipeSchemaError("Recipe is missing recipe_id.")
        return cls(
            recipe_version=int(version),
            mode=mode,
            recipe_id=recipe_id,
            recipe_name=str(data.get("recipe_name") or ""),
            strategy_id=str(data.get("strategy_id") or ""),
            target_final_candidates=_optional_int(data.get("target_final_candidates")),
            entries_per_direction=int(data.get("entries_per_direction") or 1),
            safety_caps=RecipeSafetyCaps.from_dict(data.get("safety_caps")),
            base_matrix=tuple(
                RecipeMatrixEntry.from_dict(entry)
                for entry in (data.get("base_matrix") or [])
            ),
            stages=tuple(
                RecipeStage.from_dict(stage)
                for stage in (data.get("stages") or [])
            ),
            optimizer_type=str(data.get("optimizer_type") or "Default"),
            keep_best_results=int(data.get("keep_best_results") or 1000),
            active_targets=tuple(str(t) for t in (data.get("active_targets") or ["MaxProfitFactor"])),
        )


def save_recipe(session: OptimizerSession, recipe: OptimizerRecipeDocument | dict[str, Any]) -> None:
    document = recipe if isinstance(recipe, OptimizerRecipeDocument) else OptimizerRecipeDocument.from_dict(recipe)
    _atomic_write_json(session.directory / RECIPE_FILENAME, document.to_dict())


def load_recipe(session: OptimizerSession) -> OptimizerRecipeDocument:
    path = session.directory / RECIPE_FILENAME
    if not path.exists():
        raise OptimizerRecipeNotFoundError(f"No recipe at {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise OptimizerRecipeError(f"Corrupt JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OptimizerRecipeError(f"Expected JSON object at {path}, got {type(data).__name__}")
    return OptimizerRecipeDocument.from_dict(data)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
