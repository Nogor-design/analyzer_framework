"""Read-side helpers and parameter validation for the families table.

The repository owns storage; this module owns the *policy* over the family
whitelist:

- `list_probe_families` / `get_family_spec` — convenience reads returning
  parsed FamilySpec dataclasses.
- `validate_params(repo, family_id, params)` — returns a list of structured
  violations. The agent's `author_probe` write tool calls this before
  delegating to `repo.register_hypothesis`. The repository itself stays
  permissive on purpose: domain validation is a layer above storage.

The whitelist format (per param) is:

    {"type": "int",   "min": <int>,    "max": <int>}
    {"type": "float", "min": <float>,  "max": <float>}
    {"type": "str"}
    {"type": "enum",  "values": [<allowed>, ...]}
    {"type": "bool"}

A param value may be either a scalar or a list (representing a sweep grid);
lists are validated element-wise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from ta_foundation.research_ledger.repository import Repository


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[tuple] = None


@dataclass(frozen=True)
class FamilySpec:
    family_id: str
    description: str
    mechanism_template: Optional[str]
    params: tuple[ParamSpec, ...]

    def param(self, name: str) -> Optional[ParamSpec]:
        for p in self.params:
            if p.name == name:
                return p
        return None


@dataclass(frozen=True)
class ParamViolation:
    param_name: str
    code: str
    message: str
    offending_value: Any = None


def list_probe_families(repo: Repository) -> list[FamilySpec]:
    return [_to_family_spec(f.family_id, f.description, f.mechanism_template,
                            f.legitimate_params_json)
            for f in repo.list_families()]


def get_family_spec(repo: Repository, family_id: str) -> Optional[FamilySpec]:
    fam = repo.get_family(family_id)
    if fam is None:
        return None
    return _to_family_spec(
        fam.family_id, fam.description, fam.mechanism_template, fam.legitimate_params_json
    )


def validate_params(
    repo: Repository,
    family_id: str,
    params: dict,
) -> list[ParamViolation]:
    """Return a list of violations. Empty list = valid."""
    spec = get_family_spec(repo, family_id)
    if spec is None:
        return [
            ParamViolation(
                param_name="<family>",
                code="unknown_family",
                message=f"Family '{family_id}' is not in the registry",
            )
        ]
    violations: list[ParamViolation] = []
    known_names = {p.name for p in spec.params}
    for name, value in params.items():
        if name not in known_names:
            violations.append(
                ParamViolation(
                    param_name=name,
                    code="unknown_param",
                    message=f"Param '{name}' is not in the {family_id} whitelist",
                    offending_value=value,
                )
            )
            continue
        ps = spec.param(name)
        assert ps is not None
        # Lists represent sweep grids — validate element-wise.
        elements = value if isinstance(value, list) else [value]
        if not elements:
            violations.append(
                ParamViolation(
                    param_name=name,
                    code="empty_grid",
                    message=f"Param '{name}' was given an empty list",
                    offending_value=value,
                )
            )
            continue
        for element in elements:
            v = _check_element(ps, element)
            if v is not None:
                violations.append(v)
    return violations


# ---------- Internals -------------------------------------------------------


def _to_family_spec(
    family_id: str,
    description: str,
    mechanism_template: Optional[str],
    legitimate_params_json: str,
) -> FamilySpec:
    raw = json.loads(legitimate_params_json)
    params = tuple(_to_param_spec(name, body) for name, body in sorted(raw.items()))
    return FamilySpec(
        family_id=family_id,
        description=description,
        mechanism_template=mechanism_template,
        params=params,
    )


def _to_param_spec(name: str, body: Any) -> ParamSpec:
    if not isinstance(body, dict) or "type" not in body:
        raise ValueError(
            f"Invalid param spec for '{name}': expected dict with 'type', got {body!r}"
        )
    return ParamSpec(
        name=name,
        type=body["type"],
        min=body.get("min"),
        max=body.get("max"),
        values=tuple(body["values"]) if "values" in body else None,
    )


def _check_element(ps: ParamSpec, value: Any) -> Optional[ParamViolation]:
    t = ps.type
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return ParamViolation(
                ps.name, "type_mismatch",
                f"Param '{ps.name}' expects int, got {type(value).__name__}",
                value,
            )
        if ps.min is not None and value < ps.min:
            return ParamViolation(
                ps.name, "below_min",
                f"Param '{ps.name}' = {value} below min {ps.min}", value,
            )
        if ps.max is not None and value > ps.max:
            return ParamViolation(
                ps.name, "above_max",
                f"Param '{ps.name}' = {value} above max {ps.max}", value,
            )
        return None
    if t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ParamViolation(
                ps.name, "type_mismatch",
                f"Param '{ps.name}' expects float, got {type(value).__name__}",
                value,
            )
        fv = float(value)
        if ps.min is not None and fv < ps.min:
            return ParamViolation(
                ps.name, "below_min",
                f"Param '{ps.name}' = {fv} below min {ps.min}", value,
            )
        if ps.max is not None and fv > ps.max:
            return ParamViolation(
                ps.name, "above_max",
                f"Param '{ps.name}' = {fv} above max {ps.max}", value,
            )
        return None
    if t == "str":
        if not isinstance(value, str):
            return ParamViolation(
                ps.name, "type_mismatch",
                f"Param '{ps.name}' expects str, got {type(value).__name__}",
                value,
            )
        return None
    if t == "enum":
        allowed = ps.values or ()
        if value not in allowed:
            return ParamViolation(
                ps.name, "not_in_enum",
                f"Param '{ps.name}' = {value!r}; allowed: {list(allowed)}",
                value,
            )
        return None
    if t == "bool":
        if not isinstance(value, bool):
            return ParamViolation(
                ps.name, "type_mismatch",
                f"Param '{ps.name}' expects bool, got {type(value).__name__}",
                value,
            )
        return None
    return ParamViolation(
        ps.name, "unknown_type",
        f"Param spec for '{ps.name}' has unknown type {t!r}",
        value,
    )
