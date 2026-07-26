"""
YAML spec loader and structural validator.

Loads acceptance_specs/phase2/*.yaml into typed AcceptanceSpec models.
Raises SpecLoadError with a descriptive message on schema violations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bridge_test_harness.acceptance_spec_models import (
    AcceptanceSpec, EventMatcher, StateAssertion,
    CheckpointRule, DowngradeRule, SpecGroup,
)


class SpecLoadError(ValueError):
    """Raised when a spec file fails structural validation."""


# ---------------------------------------------------------------------------
# Valid values for enum-like fields
# ---------------------------------------------------------------------------

_VALID_SOURCES      = {"log", "outbox"}
_VALID_STATE_SRC    = {"health", "state_file"}
_VALID_OPS          = {"eq", "ne", "in", "not_in", "contains", "not_contains",
                       "exists", "missing", "gt", "gte", "lt", "lte"}
_VALID_WEIGHTS      = {"strong", "moderate", "weak"}
_VALID_SEVERITY     = {"critical", "high", "normal", "low"}
_VALID_AUTOMATION   = {"FULLY_AUTOMATED", "SEMI_AUTOMATED", "MANUAL_REQUIRED"}
_VALID_PASS_MODES   = {"all_required", "any_required", "manual_only"}
_VALID_DOWNGRADE_CT = {"event_not_matched", "state_not_matched", "checkpoint_missing"}
_VALID_IF_MISSING   = {"pass_unverified", "fail", "ignore"}

_DEFAULT_WEIGHT     = {"outbox": "strong", "log": "moderate",
                       "health": "weak", "state_file": "moderate"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_spec_group(yaml_path: Path) -> SpecGroup:
    """Load one .yaml file into a SpecGroup. Raises SpecLoadError on problems."""
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecLoadError(f"{yaml_path}: YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise SpecLoadError(f"{yaml_path}: root must be a YAML mapping")

    group = str(raw.get("group", "?"))
    label = str(raw.get("label", group))
    tests_raw = raw.get("tests", [])

    if not isinstance(tests_raw, list):
        raise SpecLoadError(f"{yaml_path}: 'tests' must be a list")

    specs: dict[str, AcceptanceSpec] = {}
    for i, t in enumerate(tests_raw):
        if not isinstance(t, dict):
            raise SpecLoadError(f"{yaml_path}[{i}]: each test must be a mapping")
        spec = _parse_spec(t, context=f"{yaml_path.name}[{i}]")
        if spec.test_id in specs:
            raise SpecLoadError(f"{yaml_path}: duplicate test_id {spec.test_id!r}")
        specs[spec.test_id] = spec

    return SpecGroup(group=group, label=label, specs=specs)


def load_all_specs(specs_dir: Path) -> dict[str, AcceptanceSpec]:
    """
    Load all .yaml files under specs_dir/phase2/.
    Returns a flat dict keyed by test_id.
    Raises SpecLoadError on any schema problem.
    """
    result: dict[str, AcceptanceSpec] = {}
    phase2_dir = specs_dir / "phase2"
    if not phase2_dir.exists():
        return result
    for yaml_file in sorted(phase2_dir.glob("*.yaml")):
        group = load_spec_group(yaml_file)
        for tid, spec in group.specs.items():
            if tid in result:
                raise SpecLoadError(f"Duplicate test_id {tid!r} across spec files")
            result[tid] = spec
    return result


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_spec(raw: dict, context: str) -> AcceptanceSpec:
    _req(raw, "test_id", context)
    _req(raw, "title", context)
    tid = raw["test_id"]
    ctx = f"{context}({tid})"

    severity = _str(raw, "severity", "normal", ctx)
    _enum(severity, _VALID_SEVERITY, "severity", ctx)

    auto_level = _str(raw, "automation_level", "FULLY_AUTOMATED", ctx)
    _enum(auto_level, _VALID_AUTOMATION, "automation_level", ctx)

    pass_mode = _str(raw, "pass_mode", "all_required", ctx)
    _enum(pass_mode, _VALID_PASS_MODES, "pass_mode", ctx)

    return AcceptanceSpec(
        test_id=tid,
        title=raw["title"],
        severity=severity,
        automation_level=auto_level,
        blocking_for_sim_readiness=bool(raw.get("blocking_for_sim_readiness", False)),
        pass_mode=pass_mode,
        required_events=[
            _parse_event_matcher(e, f"{ctx}.required_events[{i}]")
            for i, e in enumerate(raw.get("required_events", []))
        ],
        forbidden_events=[
            _parse_event_matcher(e, f"{ctx}.forbidden_events[{i}]")
            for i, e in enumerate(raw.get("forbidden_events", []))
        ],
        required_state=[
            _parse_state_assertion(s, f"{ctx}.required_state[{i}]")
            for i, s in enumerate(raw.get("required_state", []))
        ],
        checkpoint_rules=[
            _parse_checkpoint_rule(c, f"{ctx}.checkpoint_rules[{i}]")
            for i, c in enumerate(raw.get("checkpoint_rules", []))
        ],
        downgrade_rules=[
            _parse_downgrade_rule(d, f"{ctx}.downgrade_rules[{i}]")
            for i, d in enumerate(raw.get("downgrade_rules", []))
        ],
        timeouts=dict(raw.get("timeouts", {})),
        notes=str(raw.get("notes", "")),
    )


def _parse_event_matcher(raw: dict, context: str) -> EventMatcher:
    _req(raw, "id", context)
    _req(raw, "source", context)
    source = raw["source"]
    _enum(source, _VALID_SOURCES, "source", context)

    pw = _str(raw, "proof_weight", _DEFAULT_WEIGHT.get(source, "moderate"), context)
    _enum(pw, _VALID_WEIGHTS, "proof_weight", context)

    op = raw.get("op")
    if op is not None:
        _enum(op, _VALID_OPS, "op", context)

    return EventMatcher(
        id=raw["id"],
        source=source,
        event_type=raw.get("event_type"),
        status=raw.get("status"),
        contains=raw.get("contains"),
        regex=raw.get("regex"),
        field=raw.get("field"),
        op=op,
        value=raw.get("value"),
        proof_weight=pw,
        optional=bool(raw.get("optional", False)),
    )


def _parse_state_assertion(raw: dict, context: str) -> StateAssertion:
    _req(raw, "id", context)
    _req(raw, "source", context)
    _req(raw, "field", context)
    source = raw["source"]
    _enum(source, _VALID_STATE_SRC, "source", context)

    op = _str(raw, "op", "eq", context)
    _enum(op, _VALID_OPS, "op", context)

    pw = _str(raw, "proof_weight", _DEFAULT_WEIGHT.get(source, "weak"), context)
    _enum(pw, _VALID_WEIGHTS, "proof_weight", context)

    return StateAssertion(
        id=raw["id"],
        source=source,
        field=raw["field"],
        op=op,
        value=raw.get("value"),
        proof_weight=pw,
        optional=bool(raw.get("optional", False)),
    )


def _parse_checkpoint_rule(raw: dict, context: str) -> CheckpointRule:
    _req(raw, "checkpoint_label", context)
    if_missing = _str(raw, "if_missing", "pass_unverified", context)
    _enum(if_missing, _VALID_IF_MISSING, "if_missing", context)
    return CheckpointRule(
        checkpoint_label=raw["checkpoint_label"],
        required=bool(raw.get("required", True)),
        if_missing=if_missing,
    )


def _parse_downgrade_rule(raw: dict, context: str) -> DowngradeRule:
    _req(raw, "condition_type", context)
    _req(raw, "ref", context)
    _req(raw, "action", context)
    _req(raw, "reason", context)
    _enum(raw["condition_type"], _VALID_DOWNGRADE_CT, "condition_type", context)
    return DowngradeRule(
        condition_type=raw["condition_type"],
        ref=raw["ref"],
        action=raw["action"],
        reason=raw["reason"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(d: dict, key: str, ctx: str) -> None:
    if key not in d:
        raise SpecLoadError(f"{ctx}: required field {key!r} is missing")


def _str(d: dict, key: str, default: str, ctx: str) -> str:
    val = d.get(key, default)
    if not isinstance(val, str):
        raise SpecLoadError(f"{ctx}: field {key!r} must be a string, got {type(val).__name__}")
    return val


def _enum(value: Any, valid: set, field: str, ctx: str) -> None:
    if value not in valid:
        raise SpecLoadError(
            f"{ctx}: {field}={value!r} is not valid; "
            f"must be one of {sorted(valid)}"
        )
