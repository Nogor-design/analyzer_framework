"""Tool framework: schema validation, precondition checks, journaling, output truncation.

Every agent-facing tool is wrapped with `@journaled_tool`. The wrapper:

    1. Validates inputs against a small dict-based schema.
    2. Runs precondition functions; each returns None on pass or a structured
       failure dict.
    3. Calls the underlying function with timing.
    4. Truncates large outputs to a path + summary, journaling the artifact path.
    5. Records a row in `tool_journal` regardless of success or failure.
    6. Returns a stable shape: {"ok": True, "result": ...} or
       {"ok": False, "error": str, "code": str, "violations"?: [...]}.

The shape is uniform so a small local LLM can recover deterministically from
errors without re-prompting.

Schema dict format (one entry per parameter):

    {"type": "str",   "required": True, "min_length": int, "max_length": int}
    {"type": "int",   "required": True, "min": int, "max": int, "default": int}
    {"type": "float", "required": True, "min": float, "max": float}
    {"type": "bool",  "required": False, "default": True}
    {"type": "enum",  "values": [...], "required": True}
    {"type": "list",  "items": "str" | "int", "max_length": int}
    {"type": "dict",  "required": False}

Top-level `repo` is passed by the caller, never serialized through inputs.
"""

from __future__ import annotations

import functools
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ta_foundation.research_ledger.repository import Repository

# Output above this size (in bytes of canonical JSON) is written to disk and
# the tool returns a path + summary instead of the full payload.
MAX_INLINE_OUTPUT_BYTES = 2048

# Where truncated outputs are persisted. Mirrors `.ta_artifacts/` convention.
TRUNCATED_OUTPUT_DIR = Path(".ta_artifacts/tool_outputs")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


@dataclass(frozen=True)
class ToolFailure:
    code: str
    message: str
    violations: tuple[dict, ...] = ()


# A precondition is a callable: (repo, validated_inputs) -> ToolFailure | None
Precondition = Callable[[Repository, dict], Optional[ToolFailure]]


def journaled_tool(
    *,
    name: str,
    role: str,
    schema: dict,
    description: str,
    preconditions: tuple[Precondition, ...] = (),
):
    """Decorator factory. The wrapped function must accept (repo, **inputs) and
    return any JSON-serialisable value (which becomes `result`)."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., dict]:
        @functools.wraps(fn)
        def wrapped(repo: Repository, **raw_inputs: Any) -> dict:
            t0 = time.monotonic()
            artifact_path: Optional[str] = None

            # 1. Schema validation (fills in defaults).
            validated, violations = _validate_inputs(schema, raw_inputs)
            if violations:
                err_payload = _err("schema_validation_failed",
                                   "one or more inputs failed schema validation",
                                   tuple(violations))
                _journal(repo, role, name, raw_inputs, err_payload, t0,
                         artifact_path=None, error_code="schema_validation_failed")
                return err_payload

            # 2. Preconditions.
            for pc in preconditions:
                failure = pc(repo, validated)
                if failure is not None:
                    err_payload = _err(failure.code, failure.message, failure.violations)
                    _journal(repo, role, name, raw_inputs, err_payload, t0,
                             artifact_path=None, error_code=failure.code)
                    return err_payload

            # 3. Execute.
            try:
                result = fn(repo, **validated)
            except Exception as exc:  # noqa: BLE001
                err_payload = _err("tool_exception", f"{type(exc).__name__}: {exc}")
                _journal(repo, role, name, raw_inputs, err_payload, t0,
                         artifact_path=None, error_code="tool_exception")
                return err_payload

            # 4. Output truncation.
            payload: dict = {"ok": True, "result": result}
            blob = _canonical_json(payload)
            if len(blob.encode("utf-8")) > MAX_INLINE_OUTPUT_BYTES:
                artifact_path = _spill_to_disk(name, blob)
                payload = {
                    "ok": True,
                    "truncated": True,
                    "artifact_path": artifact_path,
                    "summary": _summarise(result),
                }

            _journal(repo, role, name, raw_inputs, payload, t0,
                     artifact_path=artifact_path, error_code=None)
            return payload

        wrapped._tool_name = name           # type: ignore[attr-defined]
        wrapped._tool_role = role           # type: ignore[attr-defined]
        wrapped._tool_schema = schema       # type: ignore[attr-defined]
        wrapped._tool_description = description  # type: ignore[attr-defined]
        return wrapped

    return decorate


# ---- Internals -------------------------------------------------------------


def _err(code: str, message: str, violations: tuple = ()) -> dict:
    out: dict = {"ok": False, "error": message, "code": code}
    if violations:
        out["violations"] = list(violations)
    return out


def _journal(
    repo: Repository,
    role: str,
    name: str,
    raw_inputs: dict,
    payload: dict,
    t0: float,
    *,
    artifact_path: Optional[str],
    error_code: Optional[str],
) -> None:
    duration_ms = int((time.monotonic() - t0) * 1000)
    summary = _short_summary(payload)
    repo.journal(
        role=role,
        tool_name=name,
        inputs={k: _safe_for_json(v) for k, v in raw_inputs.items()},
        output_summary=summary,
        duration_ms=duration_ms,
        output_artifact_path=artifact_path,
        error=error_code,
    )


def _safe_for_json(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _short_summary(payload: dict) -> str:
    if not payload.get("ok", False):
        return f"ERR {payload.get('code', 'unknown')}: {payload.get('error', '')[:200]}"
    if payload.get("truncated"):
        return f"ok truncated → {payload.get('artifact_path')}"
    blob = _canonical_json(payload.get("result"))
    return blob[:240]


def _spill_to_disk(tool_name: str, blob: str) -> str:
    TRUNCATED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{tool_name}_{uuid.uuid4().hex[:12]}.json"
    target = TRUNCATED_OUTPUT_DIR / fname
    target.write_text(blob, encoding="utf-8")
    return str(target)


def _summarise(result: Any) -> dict:
    if isinstance(result, list):
        return {"kind": "list", "count": len(result),
                "first": _shallow_repr(result[0]) if result else None}
    if isinstance(result, dict):
        return {"kind": "dict", "keys": sorted(list(result.keys()))[:20]}
    return {"kind": type(result).__name__, "repr": _shallow_repr(result)}


def _shallow_repr(obj: Any) -> str:
    s = repr(obj)
    return s if len(s) <= 200 else s[:200] + "..."


# ---- Schema validator ------------------------------------------------------


def _validate_inputs(schema: dict, raw: dict) -> tuple[dict, list[dict]]:
    validated: dict = {}
    violations: list[dict] = []

    # Reject unknown keys outright.
    for key in raw:
        if key not in schema:
            violations.append({
                "param": key,
                "code": "unknown_param",
                "message": f"Unknown input parameter '{key}'",
            })

    for key, spec in schema.items():
        if not isinstance(spec, dict) or "type" not in spec:
            violations.append({
                "param": key, "code": "schema_error",
                "message": f"Schema entry for '{key}' missing 'type'",
            })
            continue

        required = spec.get("required", True)
        if key not in raw:
            if "default" in spec:
                validated[key] = spec["default"]
                continue
            if required:
                violations.append({
                    "param": key, "code": "required",
                    "message": f"Missing required parameter '{key}'",
                })
            continue

        v = raw[key]
        v_violation = _validate_one(key, spec, v)
        if v_violation:
            violations.append(v_violation)
        else:
            validated[key] = v

    return validated, violations


def _validate_one(key: str, spec: dict, value: Any) -> Optional[dict]:
    t = spec["type"]
    if t == "str":
        if not isinstance(value, str):
            return _v(key, "type_mismatch", f"'{key}' expects str, got {type(value).__name__}")
        if "min_length" in spec and len(value) < spec["min_length"]:
            return _v(key, "too_short", f"'{key}' length {len(value)} < min {spec['min_length']}")
        if "max_length" in spec and len(value) > spec["max_length"]:
            return _v(key, "too_long", f"'{key}' length {len(value)} > max {spec['max_length']}")
        return None
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return _v(key, "type_mismatch", f"'{key}' expects int, got {type(value).__name__}")
        if "min" in spec and value < spec["min"]:
            return _v(key, "below_min", f"'{key}' = {value} below min {spec['min']}")
        if "max" in spec and value > spec["max"]:
            return _v(key, "above_max", f"'{key}' = {value} above max {spec['max']}")
        return None
    if t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _v(key, "type_mismatch", f"'{key}' expects float, got {type(value).__name__}")
        fv = float(value)
        if "min" in spec and fv < spec["min"]:
            return _v(key, "below_min", f"'{key}' = {fv} below min {spec['min']}")
        if "max" in spec and fv > spec["max"]:
            return _v(key, "above_max", f"'{key}' = {fv} above max {spec['max']}")
        return None
    if t == "bool":
        if not isinstance(value, bool):
            return _v(key, "type_mismatch", f"'{key}' expects bool, got {type(value).__name__}")
        return None
    if t == "enum":
        allowed = spec.get("values") or []
        if value not in allowed:
            return _v(key, "not_in_enum",
                      f"'{key}' = {value!r}; allowed: {list(allowed)}")
        return None
    if t == "list":
        if not isinstance(value, list):
            return _v(key, "type_mismatch", f"'{key}' expects list, got {type(value).__name__}")
        max_len = spec.get("max_length")
        if max_len is not None and len(value) > max_len:
            return _v(key, "too_long", f"'{key}' length {len(value)} > max {max_len}")
        item_type = spec.get("items")
        if item_type:
            for i, elt in enumerate(value):
                if item_type == "str" and not isinstance(elt, str):
                    return _v(key, "type_mismatch", f"'{key}[{i}]' expects str")
                if item_type == "int" and (isinstance(elt, bool) or not isinstance(elt, int)):
                    return _v(key, "type_mismatch", f"'{key}[{i}]' expects int")
        return None
    if t == "dict":
        if not isinstance(value, dict):
            return _v(key, "type_mismatch", f"'{key}' expects dict, got {type(value).__name__}")
        return None
    return _v(key, "schema_error", f"Unknown schema type {t!r} for '{key}'")


def _v(param: str, code: str, message: str) -> dict:
    return {"param": param, "code": code, "message": message}
