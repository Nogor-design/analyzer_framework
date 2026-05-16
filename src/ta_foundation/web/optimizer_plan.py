from __future__ import annotations

"""
Plan preview and chunker for /optimizer sessions.

Given a session's parameter config + chunking config, compute:

- per-parameter step count
- total combination estimate
- a deterministic chunk plan that respects ``max_combinations_per_chunk``
- a stable plan hash (so future runs can detect "we already ran this")

The chunker walks parameters in declaration order and slices the *first*
swept parameter whose remaining range still puts the chunk over the cap.
That keeps chunk boundaries human-readable (e.g. ``averageSlow 50..200``
in chunk 1, ``averageSlow 250..500`` in chunk 2) instead of carving the
combinatorial space in some opaque way.

No NinjaTrader XML is written here. That belongs to a later phase.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ta_foundation.web.optimizer_session import (
    OptimizerSessionDocument,
    ParameterConfig,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParameterSweep:
    """A single parameter's slice within a chunk."""
    name: str
    type_name: str
    minimum: Any
    maximum: Any
    increment: Any
    step_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FixedParameter:
    name: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkPlan:
    chunk_id: str
    combination_count: int
    optimized: tuple[ParameterSweep, ...]
    fixed: tuple[FixedParameter, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "combination_count": self.combination_count,
            "optimized": [p.to_dict() for p in self.optimized],
            "fixed": [p.to_dict() for p in self.fixed],
        }


@dataclass(frozen=True)
class PlanPreview:
    """Top-level result returned by ``build_plan_preview``."""
    strategy_id: str
    seed_template_path: str
    combination_estimate: int
    max_combinations_per_chunk: int
    keep_best_results: int
    chunks: tuple[ChunkPlan, ...]
    plan_hash: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "seed_template_path": self.seed_template_path,
            "combination_estimate": self.combination_estimate,
            "max_combinations_per_chunk": self.max_combinations_per_chunk,
            "keep_best_results": self.keep_best_results,
            "chunks": [c.to_dict() for c in self.chunks],
            "plan_hash": self.plan_hash,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PlanBuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# Step-count math
# ---------------------------------------------------------------------------

def _is_bool_type(type_name: str) -> bool:
    return type_name.lower() in {"bool", "boolean", "system.boolean"}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parameter_step_count(parameter: ParameterConfig) -> int:
    """Number of values this parameter takes in a sweep. Returns 1 for fixed."""
    if parameter.mode == "fixed":
        return 1
    if _is_bool_type(parameter.type_name):
        # Booleans either fixed at one value or sweep {false, true}.
        if parameter.minimum is None and parameter.maximum is None:
            return 2
        try:
            if bool(parameter.minimum) == bool(parameter.maximum):
                return 1
        except (TypeError, ValueError):
            return 2
        return 2

    minimum = _to_decimal(parameter.minimum)
    maximum = _to_decimal(parameter.maximum)
    increment = _to_decimal(parameter.increment)

    if minimum is None or maximum is None:
        return 1
    if maximum < minimum:
        return 0
    if increment is None or increment <= 0:
        return 1 if maximum == minimum else 0
    if maximum == minimum:
        return 1
    return int((maximum - minimum) // increment) + 1


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _split_numeric_parameter(
    parameter: ParameterConfig,
    *,
    max_slice_steps: int,
) -> list[ParameterSweep]:
    """Slice a single numeric sweep so each slice has <= max_slice_steps values.

    The slice boundaries align to the increment so values stay legal.
    """
    minimum = _to_decimal(parameter.minimum)
    maximum = _to_decimal(parameter.maximum)
    increment = _to_decimal(parameter.increment)
    if minimum is None or maximum is None or increment is None or increment <= 0:
        return [
            ParameterSweep(
                name=parameter.name,
                type_name=parameter.type_name,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                increment=parameter.increment,
                step_count=parameter_step_count(parameter),
            )
        ]

    slices: list[ParameterSweep] = []
    cursor = minimum
    while cursor <= maximum:
        slice_steps = min(max_slice_steps, int((maximum - cursor) // increment) + 1)
        slice_max = cursor + increment * (slice_steps - 1)
        slices.append(
            ParameterSweep(
                name=parameter.name,
                type_name=parameter.type_name,
                minimum=_format_decimal(cursor),
                maximum=_format_decimal(slice_max),
                increment=_format_decimal(increment),
                step_count=slice_steps,
            )
        )
        cursor = slice_max + increment
    return slices


def _format_decimal(value: Decimal) -> Any:
    """Render a Decimal as int when it has no fractional part, else float."""
    if value == value.to_integral():
        return int(value)
    return float(value)


def _full_sweep(parameter: ParameterConfig) -> ParameterSweep:
    return ParameterSweep(
        name=parameter.name,
        type_name=parameter.type_name,
        minimum=parameter.minimum,
        maximum=parameter.maximum,
        increment=parameter.increment,
        step_count=parameter_step_count(parameter),
    )


def _chunk_parameters(
    parameters: list[ParameterConfig],
    *,
    max_combinations_per_chunk: int,
) -> list[list[ParameterSweep]]:
    """Greedy slicer: accumulate sweeps until adding the next one would
    exceed the cap; then slice the offending parameter and emit chunks."""
    optimized = [p for p in parameters if p.mode == "optimize" and parameter_step_count(p) >= 1]
    if not optimized:
        return [[]]

    # Compute step count of each sweep
    sweeps_full = [(_full_sweep(p), p) for p in optimized]

    chunks: list[list[ParameterSweep]] = []
    current: list[ParameterSweep] = []
    current_product = 1

    def flush() -> None:
        nonlocal current, current_product
        if current:
            chunks.append(current)
        current = []
        current_product = 1

    for sweep, original in sweeps_full:
        steps = sweep.step_count
        if steps <= 0:
            continue
        projected = current_product * steps

        if projected <= max_combinations_per_chunk:
            current.append(sweep)
            current_product = projected
            continue

        # Need to split somewhere. Strategy: split this parameter into slices
        # such that each slice * current_product <= cap. If current_product is
        # already too big on its own, flush first.
        if current_product > max_combinations_per_chunk:
            flush()

        per_chunk_capacity = max(1, max_combinations_per_chunk // max(1, current_product))
        if _is_bool_type(sweep.type_name) or _to_decimal(sweep.increment) is None:
            # Can't slice; keep whole. Will cause an oversized chunk, flagged later.
            current.append(sweep)
            current_product *= steps
            flush()
            continue

        slices = _split_numeric_parameter(original, max_slice_steps=per_chunk_capacity)
        for piece in slices:
            piece_chunk = list(current) + [piece]
            chunks.append(piece_chunk)
        current = []
        current_product = 1

    flush()

    if not chunks:
        chunks.append([])
    return chunks


# ---------------------------------------------------------------------------
# Plan preview entry point
# ---------------------------------------------------------------------------

def build_plan_preview(doc: OptimizerSessionDocument) -> PlanPreview:
    warnings: list[str] = []

    if not doc.strategy_id:
        warnings.append("missing_strategy_id")
    if not doc.seed_template_path:
        warnings.append("missing_seed_template_path")

    parameters = list(doc.parameters)
    if not parameters:
        warnings.append("no_parameters_configured")

    fixed_entries = tuple(
        FixedParameter(name=p.name, value=p.fixed_value)
        for p in parameters
        if p.mode == "fixed"
    )

    total_estimate = 1
    for p in parameters:
        steps = parameter_step_count(p)
        if steps == 0:
            warnings.append(f"invalid_sweep:{p.name}")
            steps = 1
        if p.mode == "optimize":
            total_estimate *= steps

    cap = max(1, int(doc.chunking.max_combinations_per_chunk))
    sliced = _chunk_parameters(parameters, max_combinations_per_chunk=cap)

    chunks: list[ChunkPlan] = []
    for index, optimized in enumerate(sliced, start=1):
        combo = 1
        for sweep in optimized:
            combo *= max(1, sweep.step_count)
        if combo > cap:
            warnings.append(f"chunk_over_cap:chunk_{index:03d}:{combo}")
        chunks.append(
            ChunkPlan(
                chunk_id=f"chunk_{index:03d}",
                combination_count=combo,
                optimized=tuple(optimized),
                fixed=fixed_entries,
            )
        )

    plan_hash = _hash_plan(doc, chunks)

    return PlanPreview(
        strategy_id=doc.strategy_id,
        seed_template_path=doc.seed_template_path,
        combination_estimate=total_estimate,
        max_combinations_per_chunk=cap,
        keep_best_results=doc.chunking.keep_best_results,
        chunks=tuple(chunks),
        plan_hash=plan_hash,
        warnings=tuple(warnings),
    )


def _hash_plan(doc: OptimizerSessionDocument, chunks: list[ChunkPlan]) -> str:
    payload = {
        "strategy_id": doc.strategy_id,
        "seed_template_path": doc.seed_template_path,
        "instrument": doc.instrument,
        "market_suffix": doc.market_suffix,
        "parameters": [p.to_dict() for p in doc.parameters],
        "guardrails": doc.guardrails.to_dict(),
        "chunking": doc.chunking.to_dict(),
        "chunks": [c.to_dict() for c in chunks],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
