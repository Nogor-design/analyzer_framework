from __future__ import annotations

"""Row-level refinement: spawn a new optimizer session from selected rows
of a prior session's final review.

The operator picks specific candidate `run_id`s from the source session's
`final_backtest_review/evaluated_candidates.json`. This module:

- Reads those rows.
- Computes a tightened parameter config (min/max bracketing the observed
  values per param, optional widen factor) for numeric params and a
  fixed majority value for bool params.
- Clones the source session config and overwrites its parameter list
  with the refined sweep.
- Returns the new session.

The refined session starts fresh — no plan, generated templates, or
NT outputs are copied. The operator drives it through preflight,
RunBatch, and the multi-phase deployment package as normal.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ta_foundation.web.optimizer_session import (
    ChunkingConfig,
    Guardrails,
    OptimizerSession,
    OptimizerSessionDocument,
    ParameterConfig,
    clone_session,
)


class OptimizerRefineError(Exception):
    pass


# Map evaluated_candidates row keys -> NinjaScript parameter names.
_PARAM_NAME_MAP: dict[str, str] = {
    "start_hour": "StartTimeH",
    "duration_hours": "DurationTimeH",
    "average_fast": "averageFast",
    "average_slow": "averageSlow",
    "max_stop": "MaxStop",
    "max_tp_ratio": "MaxTPRatio",
    "profit_stop": "ProfitStop",
    "loss_stop": "LossStop",
    "max_trades": "MaxTrades",
    "use_trend": "UseTrend",
    "use_trend_reverse": "UseTrendReverse",
}

# Reasonable widening factors per parameter so the refined sweep doesn't
# collapse to a single value when all selected rows had the same setting.
# `widen` is how far on each side to extend the sweep from the observed
# min/max. `increment` is the step. `min_floor` clamps the lower bound so
# widening doesn't push the sweep into nonsensical territory (e.g. a
# negative ProfitStop). All numeric params default to a floor of 0.
_NUMERIC_PARAM_HINTS: dict[str, dict[str, float]] = {
    "averageFast":   {"widen": 2,    "increment": 1,   "min_floor": 2},
    "averageSlow":   {"widen": 20,   "increment": 10,  "min_floor": 10},
    "MaxStop":       {"widen": 30,   "increment": 10,  "min_floor": 10},
    "MaxTPRatio":    {"widen": 0.3,  "increment": 0.1, "min_floor": 0.1},
    "ProfitStop":    {"widen": 200,  "increment": 50,  "min_floor": 1},
    "LossStop":      {"widen": 200,  "increment": 50,  "min_floor": 1},
    "MaxTrades":     {"widen": 1,    "increment": 1,   "min_floor": 1},
    "StartTimeH":    {"widen": 0,    "increment": 1,   "min_floor": 0},
    "DurationTimeH": {"widen": 0,    "increment": 1,   "min_floor": 1},
}

_BOOL_PARAMS = {"UseTrend", "UseTrendReverse", "Reverse", "Long", "Short"}


@dataclass(frozen=True)
class RefineSummary:
    new_session_id: str
    source_session_id: str
    selected_run_ids: list[str]
    parameter_changes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_session_id": self.new_session_id,
            "source_session_id": self.source_session_id,
            "selected_run_ids": self.selected_run_ids,
            "parameter_changes": self.parameter_changes,
        }


def refine_from_rows(
    source: OptimizerSession,
    selected_run_ids: Iterable[str],
    *,
    label: str | None = None,
) -> tuple[OptimizerSession, RefineSummary]:
    selected = [str(s).strip() for s in selected_run_ids if str(s).strip()]
    if not selected:
        raise OptimizerRefineError("No run_ids supplied for refinement.")

    rows = _load_evaluated_rows(source)
    if not rows:
        raise OptimizerRefineError(
            "No evaluated_candidates.json found under "
            f"{source.directory}/deployment_package/final_backtest_handoff/final_backtest_review/. "
            "Rebuild the deployment package after final Backtests return."
        )

    selected_rows = [row for row in rows if str(row.get("run_id") or "") in selected]
    missing = sorted(set(selected) - {str(r.get("run_id") or "") for r in selected_rows})
    if missing:
        raise OptimizerRefineError(
            f"Unknown run_ids: {missing}. Available: "
            f"{[r.get('run_id') for r in rows]}"
        )

    source_doc = source.load_document()
    new_params, changes = _build_refined_parameters(source_doc.parameters, selected_rows)

    new_label = label or f"{source_doc.label or source.id} (refined from {len(selected_rows)} rows)"
    new_session = clone_session(source, label=new_label)
    new_session.update(parameters=[p.to_dict() for p in new_params])

    return new_session, RefineSummary(
        new_session_id=new_session.id,
        source_session_id=source.id,
        selected_run_ids=[str(r.get("run_id")) for r in selected_rows],
        parameter_changes=changes,
    )


def _load_evaluated_rows(source: OptimizerSession) -> list[dict[str, Any]]:
    candidate = (
        source.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
        / "evaluated_candidates.json"
    )
    if not candidate.exists():
        return []
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def _build_refined_parameters(
    source_params: list[ParameterConfig],
    selected_rows: list[Mapping[str, Any]],
) -> tuple[list[ParameterConfig], list[dict[str, Any]]]:
    """For each parameter on the source session, derive a refined config
    based on the observed values in the selected rows.

    - Numeric param, all rows agree -> pin as fixed at that value.
    - Numeric param, rows differ   -> sweep min..max with widening + the
      param's default increment hint.
    - Bool param -> majority value as fixed (so the operator can flip
      either copy via clone & refine if both modes are desired).
    - Param missing from the rows -> kept verbatim from the source.
    """
    refined: list[ParameterConfig] = []
    changes: list[dict[str, Any]] = []

    # Invert the param-name map so we can pull a NinjaScript name from
    # the source param into the evaluated_candidates row dict.
    name_to_row_key = {v: k for k, v in _PARAM_NAME_MAP.items()}

    for p in source_params:
        row_key = name_to_row_key.get(p.name)
        if row_key is None and p.name not in _BOOL_PARAMS:
            # No row mapping — keep the source config unchanged.
            refined.append(_clone_param(p))
            continue

        observed = [r.get(row_key) for r in selected_rows] if row_key else []
        # Drop None
        observed = [v for v in observed if v is not None and v != ""]

        if p.name in _BOOL_PARAMS:
            # Bool: row exposes "True"/"False" strings from settings parser.
            bool_values = [_as_bool_optional(v) for v in observed]
            bool_values = [v for v in bool_values if v is not None]
            if not bool_values:
                refined.append(_clone_param(p))
                continue
            majority = Counter(bool_values).most_common(1)[0][0]
            refined.append(ParameterConfig(
                name=p.name,
                type_name=p.type_name or "bool",
                mode="fixed",
                fixed_value=majority,
                minimum=None,
                maximum=None,
                increment=None,
            ))
            changes.append({
                "name": p.name,
                "decision": "fixed (bool majority)",
                "observed": list(set(bool_values)),
                "value": majority,
            })
            continue

        # Numeric path
        numeric_values = [_as_float_optional(v) for v in observed]
        numeric_values = [v for v in numeric_values if v is not None]
        if not numeric_values:
            refined.append(_clone_param(p))
            continue

        lo = min(numeric_values)
        hi = max(numeric_values)
        hint = _NUMERIC_PARAM_HINTS.get(p.name, {"widen": 0, "increment": 1})
        widen = float(hint.get("widen") or 0)
        increment = hint.get("increment") or 1

        if lo == hi and widen <= 0:
            refined.append(ParameterConfig(
                name=p.name,
                type_name=p.type_name or "int",
                mode="fixed",
                fixed_value=_cast_for_type(lo, p.type_name),
                minimum=None,
                maximum=None,
                increment=None,
            ))
            changes.append({
                "name": p.name,
                "decision": "fixed (unanimous)",
                "observed": list(set(numeric_values)),
                "value": _cast_for_type(lo, p.type_name),
            })
            continue

        floor = float(hint.get("min_floor", 0))
        new_min = max(lo - widen, floor)
        new_max = hi + widen
        # Ensure max >= min after clamping (can happen if widen pushes min
        # up to the floor and the observed range was empty above the floor).
        if new_max < new_min:
            new_max = new_min
        # For integer-typed params we floor/ceil so the sweep stays legal.
        if (p.type_name or "").lower() == "int":
            new_min = int(round(new_min))
            new_max = int(round(new_max))
        refined.append(ParameterConfig(
            name=p.name,
            type_name=p.type_name or "int",
            mode="optimize",
            fixed_value=None,
            minimum=_cast_for_type(new_min, p.type_name),
            maximum=_cast_for_type(new_max, p.type_name),
            increment=increment,
        ))
        changes.append({
            "name": p.name,
            "decision": "swept (tightened)",
            "observed": sorted(set(numeric_values)),
            "minimum": _cast_for_type(new_min, p.type_name),
            "maximum": _cast_for_type(new_max, p.type_name),
            "increment": increment,
        })

    return refined, changes


def _clone_param(p: ParameterConfig) -> ParameterConfig:
    return ParameterConfig(
        name=p.name,
        type_name=p.type_name,
        mode=p.mode,
        fixed_value=p.fixed_value,
        minimum=p.minimum,
        maximum=p.maximum,
        increment=p.increment,
    )


def _as_bool_optional(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _as_float_optional(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cast_for_type(value: Any, type_name: str | None) -> Any:
    t = (type_name or "").lower()
    if t == "int":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return value
    if t == "double":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value
