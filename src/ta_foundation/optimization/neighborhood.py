from __future__ import annotations

"""Parameter-neighborhood cell planning.

For each candidate, sweep small offsets around its numeric parameters and
ask: does the strategy stay profitable one increment away, or is this a
needle peak? This module is the pure-function planner — it produces a list
of "cells" (parameter override sets) given a candidate's parameter values
and a sweep spec. The web layer wraps it to drive per-cell NT template
generation.

Conventions
-----------

- Only numeric parameters are swept. Bool / enum / string params are
  skipped silently — sweep_spec entries for non-numeric params are
  ignored.
- The center value (the candidate's own value) is **excluded** from the
  generated cells: the original final Backtest already covered it, and
  re-running it adds no signal. The candidate's own result is included
  in the stability report separately as the "center" reference.
- ``steps`` controls how many off-center samples per parameter. A typical
  value is 4 — two on each side of center. The generator distributes
  samples evenly across ``[-pct, +pct]`` and drops the 0 sample.
- ``increment``, if supplied, snaps each generated value to the nearest
  multiple of that increment. Ints always snap to integer increments
  (default increment=1). This keeps generated values on the same grid
  the original optimizer used.
- Generated values are deduplicated; cells are zero-indexed in
  generation order.
"""

from dataclasses import asdict, dataclass, field
from typing import Any


class NeighborhoodError(Exception):
    pass


@dataclass(frozen=True)
class NeighborhoodCell:
    index: int
    overrides: dict[str, Any]
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterSweepSpec:
    name: str
    type_name: str       # "int" | "float"
    pct: float           # e.g. 0.10 for ±10%
    steps: int           # off-center sample count per axis (>= 2)
    increment: float | int | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_neighborhood_cells(
    *,
    candidate_params: dict[str, Any],
    sweep_specs: list[ParameterSweepSpec],
    mode: str = "one_at_a_time",
) -> list[NeighborhoodCell]:
    """Generate neighborhood cells around a candidate's parameter values.

    Parameters
    ----------
    candidate_params : dict
        The candidate's full parameter set. Only entries listed in
        ``sweep_specs`` are varied; the rest are held at the candidate's
        value (and are not included in the per-cell override dict).
    sweep_specs : list[ParameterSweepSpec]
        Per-parameter sweep configuration. Non-numeric specs are dropped
        with no error so the caller can pass the full optimized-param
        list without filtering first.
    mode : "one_at_a_time" | "full_cube"
        ``one_at_a_time`` varies one parameter at a time, holding all
        others at the candidate's value. Cell count: sum(steps_i).
        ``full_cube`` takes the Cartesian product of all per-param
        sample sets. Cell count: prod(steps_i). Use the cube sparingly.
    """
    if mode not in {"one_at_a_time", "full_cube"}:
        raise NeighborhoodError(f"Unknown mode: {mode!r}")

    numeric_specs: list[ParameterSweepSpec] = []
    for spec in sweep_specs:
        if spec.type_name not in {"int", "float"}:
            continue
        if spec.name not in candidate_params:
            raise NeighborhoodError(
                f"Sweep spec for {spec.name!r} but candidate has no such parameter"
            )
        if spec.steps < 2:
            raise NeighborhoodError(
                f"Sweep spec for {spec.name!r}: steps={spec.steps} (need >= 2)"
            )
        if spec.pct <= 0:
            raise NeighborhoodError(
                f"Sweep spec for {spec.name!r}: pct={spec.pct} (need > 0)"
            )
        numeric_specs.append(spec)

    if not numeric_specs:
        return []

    per_axis: list[tuple[ParameterSweepSpec, list[Any]]] = []
    for spec in numeric_specs:
        center = _coerce_number(candidate_params[spec.name], spec.type_name)
        if center is None:
            raise NeighborhoodError(
                f"Candidate value for {spec.name!r} is not numeric: "
                f"{candidate_params[spec.name]!r}"
            )
        samples = _samples_around(center, spec)
        if not samples:
            continue
        per_axis.append((spec, samples))

    if not per_axis:
        return []

    cells: list[NeighborhoodCell] = []
    if mode == "one_at_a_time":
        idx = 0
        for spec, samples in per_axis:
            for value in samples:
                cells.append(NeighborhoodCell(
                    index=idx,
                    overrides={spec.name: value},
                    label=f"{spec.name}={_fmt(value, spec.type_name)}",
                ))
                idx += 1
        return cells

    # full_cube
    combos: list[dict[str, Any]] = [{}]
    for spec, samples in per_axis:
        combos = [
            {**existing, spec.name: value}
            for existing in combos
            for value in samples
        ]
    for idx, combo in enumerate(combos):
        label = ", ".join(
            f"{name}={_fmt(value, _spec_type_for(name, per_axis))}"
            for name, value in combo.items()
        )
        cells.append(NeighborhoodCell(index=idx, overrides=combo, label=label))
    return cells


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _coerce_number(value: Any, type_name: str) -> float | int | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if type_name == "int":
        return int(round(f))
    return f


def _samples_around(center: float | int, spec: ParameterSweepSpec) -> list[float | int]:
    """Generate ``spec.steps`` off-center samples evenly distributed in
    ``[-pct, +pct]`` around ``center``, dropping the center itself."""
    steps = spec.steps
    pct = spec.pct
    # We want N off-center samples. Build offsets symmetrically around 0,
    # skipping 0. For steps=4 → offsets at -pct, -pct/2, +pct/2, +pct.
    # For steps=2 → offsets at -pct, +pct.
    if steps % 2 == 0:
        # Even: place samples symmetrically.
        half = steps // 2
        offsets = [-pct * (i + 1) / half for i in range(half - 1, -1, -1)]
        offsets += [pct * (i + 1) / half for i in range(half)]
    else:
        # Odd: include both endpoints and intermediate fractions, skip 0.
        half = (steps - 1) // 2
        denom = half + 1
        offsets = [-pct * (i + 1) / denom for i in range(half + 1 - 1, -1, -1)]
        offsets += [pct * (i + 1) / denom for i in range(half + 1)]
        # Trim to exactly ``steps`` samples (we built steps+1 with this
        # construction when steps is odd).
        offsets = offsets[: steps]

    raw_values = [center * (1.0 + off) for off in offsets]

    snap = spec.increment
    if spec.type_name == "int" and snap is None:
        snap = 1

    samples: list[float | int] = []
    seen: set[Any] = set()
    for raw in raw_values:
        v = _snap(raw, snap) if snap else raw
        if spec.type_name == "int":
            v = int(round(v))
        if spec.minimum is not None and v < spec.minimum:
            continue
        if spec.maximum is not None and v > spec.maximum:
            continue
        if v == center:
            # Snapping may have collapsed an offset back to the center.
            continue
        key = v if spec.type_name == "int" else round(float(v), 9)
        if key in seen:
            continue
        seen.add(key)
        samples.append(v)
    return samples


def _snap(value: float, increment: float | int) -> float:
    if increment is None or increment == 0:
        return value
    return round(value / increment) * increment


def _fmt(value: Any, type_name: str) -> str:
    if type_name == "int":
        return str(int(value))
    f = float(value)
    if f == int(f):
        return f"{f:.1f}"
    return f"{f:g}"


def _spec_type_for(name: str, per_axis: list[tuple[ParameterSweepSpec, list[Any]]]) -> str:
    for spec, _ in per_axis:
        if spec.name == name:
            return spec.type_name
    return "float"
