from __future__ import annotations

"""Dynamic Phase 2 bounded-switching hindsight diagnostic.

This module segments an immutable Dynamic Phase 1 outcome cube.  It is an
oracle diagnostic, never a causal selector or a reported trading strategy.
"""

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION = "dynamic_opportunity_oracle.v1"
OFF_STATE = "OFF"

DEFAULT_DYNAMIC_ORACLE_CONFIG: Dict[str, Any] = {
    "minimum_dwell_sessions": 5,
    "switch_penalty_ticks": 15.0,
    "initial_risk_ticks": 150.0,
    "switch_penalty_risk_fraction": 0.10,
    "maximum_concurrent_per_direction": 3,
    "switch_penalty_scope": "every_post_initial_state_change_including_off",
    "initial_state_penalized": False,
    "terminal_short_regime_policy": "allow_and_mark_right_censored",
    "cell_definition": [
        "signal_side",
        "time_bucket",
        "trend_state",
    ],
    "session_clock": "all_observed_sequence_sessions",
    "paper_reward": "sum_capacity_eligible_net_pnl_ticks",
    "execution_conflict_tiebreak": [
        "descending_selected_cell_session_reward",
        "ascending_expert_id",
        "ascending_outcome_id",
    ],
}


SESSION_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "session_index",
    "session_id",
    "context_cell",
    "signal_side",
    "time_bucket",
    "trend_state",
    "oracle_state",
    "selected_expert_id",
    "state_age_sessions",
    "transition",
    "transition_from",
    "switch_penalty_ticks",
    "selected_event_rows",
    "selected_physical_opportunities",
    "raw_net_ticks",
    "penalized_objective_ticks",
)


REGIME_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "oracle_regime_id",
    "context_cell",
    "signal_side",
    "time_bucket",
    "trend_state",
    "oracle_state",
    "selected_expert_id",
    "start_session",
    "end_session",
    "start_session_index",
    "end_session_index",
    "dwell_sessions",
    "minimum_dwell_satisfied",
    "right_censored",
    "switch_penalty_ticks",
    "selected_event_rows",
    "selected_physical_opportunities",
    "raw_net_ticks",
    "penalized_objective_ticks",
)


OPPORTUNITY_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "physical_opportunity_id",
    "session_id",
    "entry_dt",
    "signal_side",
    "candidate_context_cells",
    "selected_candidate_count",
    "selected_expert_id",
    "selected_outcome_id",
    "selected_lane_event_id",
    "mode",
    "trade_direction",
    "exit_dt",
    "exit_known_dt",
    "deduplication_conflicts",
    "capacity_eligible_in_source_lane",
    "executed",
    "capacity_skipped",
    "reason_code",
    "net_pnl_ticks",
)


class DynamicOracleError(ValueError):
    """Raised when the Phase 2 diagnostic contract cannot be honored."""


@dataclass(frozen=True)
class _Node:
    objective: float
    switches: int
    path: Tuple[str, ...]


def run_bounded_switching_oracle(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Solve the frozen bounded-switching oracle for one outcome cube.

    Paths are solved independently per exact context cell.  The returned
    execution projection then deduplicates physical opportunities and reapplies
    a global per-direction capacity limit.  The projection is diagnostic and
    is not claimed to solve the globally coupled capacity-constrained problem.
    """
    resolved = _resolve_config(config)
    frame = _canonical_rows(rows)
    sequence_values = sorted(frame["sequence"].unique())
    if len(sequence_values) != 1:
        raise DynamicOracleError(
            "the opportunity oracle requires exactly one sequence"
        )
    sequence = str(sequence_values[0])
    sessions = sorted(frame["session_id"].unique())
    if not sessions:
        raise DynamicOracleError("the outcome cube has no observed sessions")

    rewards, event_counts, opportunity_counts = _reward_indexes(frame)
    cell_parts = _cell_metadata(frame)
    experts_by_cell = {
        cell: sorted(
            frame.loc[frame["context_cell"] == cell, "expert_id"].unique()
        )
        for cell in sorted(cell_parts)
    }

    session_rows: List[Dict[str, Any]] = []
    regime_rows: List[Dict[str, Any]] = []
    selected_state: Dict[Tuple[str, str], str] = {}
    best_static_raw = 0.0
    unconstrained_raw = 0.0
    for cell in sorted(experts_by_cell):
        experts = experts_by_cell[cell]
        path = solve_bounded_cell_path(
            sessions,
            experts,
            rewards,
            context_cell=cell,
            minimum_dwell_sessions=resolved["minimum_dwell_sessions"],
            switch_penalty_ticks=resolved["switch_penalty_ticks"],
        )
        cell_session_rows = _build_cell_session_rows(
            sequence,
            cell,
            cell_parts[cell],
            sessions,
            path,
            rewards,
            event_counts,
            opportunity_counts,
            resolved,
        )
        session_rows.extend(cell_session_rows)
        regime_rows.extend(
            _build_regime_rows(
                sequence,
                cell_session_rows,
                resolved["minimum_dwell_sessions"],
            )
        )
        selected_state.update(
            {
                (cell, session): state
                for session, state in zip(sessions, path)
            }
        )
        state_totals = [
            sum(rewards.get((cell, session, expert), 0.0) for session in sessions)
            for expert in experts
        ]
        best_static_raw += max([0.0, *state_totals])
        unconstrained_raw += sum(
            max(
                [
                    0.0,
                    *[
                        rewards.get((cell, session, expert), 0.0)
                        for expert in experts
                    ],
                ]
            )
            for session in sessions
        )

    opportunity_rows = _build_execution_projection(
        frame,
        selected_state,
        rewards,
        maximum_per_direction=resolved[
            "maximum_concurrent_per_direction"
        ],
    )
    session_rows = _json_safe(session_rows)
    regime_rows = _json_safe(regime_rows)
    opportunity_rows = _json_safe(opportunity_rows)
    summary = _build_summary(
        sequence,
        sessions,
        session_rows,
        regime_rows,
        opportunity_rows,
        best_static_raw=best_static_raw,
        unconstrained_raw=unconstrained_raw,
        config=resolved,
    )
    safe_source = _json_safe(dict(source_manifest or {}))
    source_hash = (
        safe_source.get("manifest_sha256")
        if isinstance(safe_source, Mapping)
        else None
    )
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_2",
        "research_classification": "hindsight_diagnostic_only",
        "sequence": sequence,
        "source_outcome_cube": {
            "manifest_sha256": source_hash,
            "outcome_cube_sha256": safe_source.get("outcome_cube_sha256"),
            "schema_version": safe_source.get("schema_version"),
        },
        "configuration": {
            "sha256": _sha256_json(resolved),
            "payload": _json_safe(resolved),
        },
        "contracts": {
            "causal_strategy": False,
            "may_inform_selector_parameters": False,
            "minimum_switch_spacing_sessions": resolved[
                "minimum_dwell_sessions"
            ],
            "off_is_a_state": True,
            "paper_objective_capacity_policy": (
                "use only source rows with capacity_eligible=true"
            ),
            "execution_projection": (
                "physical deduplication plus chronological per-direction "
                "capacity; diagnostic, not globally optimized"
            ),
        },
        "counts": {
            "sessions": len(sessions),
            "context_cells": len(experts_by_cell),
            "candidate_experts": int(frame["expert_id"].nunique()),
            "outcome_rows": len(frame),
            "session_ledger_rows": len(session_rows),
            "regimes": len(regime_rows),
            "physical_opportunities": len(opportunity_rows),
        },
        "summary_sha256": _sha256_json(summary),
        "session_ledger_sha256": _sha256_json(session_rows),
        "regime_ledger_sha256": _sha256_json(regime_rows),
        "opportunity_ledger_sha256": _sha256_json(opportunity_rows),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "session_rows": session_rows,
        "regime_rows": regime_rows,
        "opportunity_rows": opportunity_rows,
    }


def solve_bounded_cell_path(
    sessions: Sequence[str],
    experts: Sequence[str],
    rewards: Mapping[Tuple[str, str, str], float],
    *,
    context_cell: str,
    minimum_dwell_sessions: int,
    switch_penalty_ticks: float,
) -> List[str]:
    """Return the deterministic optimal state path for one specialist cell."""
    ordered_sessions = [str(value) for value in sessions]
    states = [OFF_STATE, *sorted({str(value) for value in experts})]
    if not ordered_sessions:
        return []
    dwell = int(minimum_dwell_sessions)
    penalty = float(switch_penalty_ticks)
    if dwell < 1:
        raise DynamicOracleError("minimum_dwell_sessions must be positive")
    if penalty < 0.0 or not isfinite(penalty):
        raise DynamicOracleError("switch_penalty_ticks must be finite and nonnegative")

    nodes: Dict[Tuple[str, int], _Node] = {}
    first_session = ordered_sessions[0]
    for state in states:
        reward = (
            0.0
            if state == OFF_STATE
            else float(rewards.get((context_cell, first_session, state), 0.0))
        )
        nodes[(state, 1)] = _Node(reward, 0, (state,))

    for session in ordered_sessions[1:]:
        next_nodes: Dict[Tuple[str, int], _Node] = {}
        for (state, age), node in nodes.items():
            stay_reward = (
                0.0
                if state == OFF_STATE
                else float(rewards.get((context_cell, session, state), 0.0))
            )
            _keep_better(
                next_nodes,
                (state, min(dwell, age + 1)),
                _Node(
                    node.objective + stay_reward,
                    node.switches,
                    node.path + (state,),
                ),
            )
            if age < dwell:
                continue
            for new_state in states:
                if new_state == state:
                    continue
                switch_reward = (
                    0.0
                    if new_state == OFF_STATE
                    else float(
                        rewards.get((context_cell, session, new_state), 0.0)
                    )
                )
                _keep_better(
                    next_nodes,
                    (new_state, 1),
                    _Node(
                        node.objective + switch_reward - penalty,
                        node.switches + 1,
                        node.path + (new_state,),
                    ),
                )
        nodes = next_nodes
    winner = sorted(nodes.values(), key=_node_sort_key)[0]
    return list(winner.path)


def write_dynamic_opportunity_oracle(
    output_dir: Path,
    result: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, str]:
    """Write stable, hash-bound Dynamic Phase 2 artifacts."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": root / "oracle_summary.json",
        "session_ledger": root / "oracle_session_ledger.csv",
        "regime_ledger": root / "oracle_regime_ledger.csv",
        "opportunity_ledger": root / "oracle_opportunity_ledger.csv",
        "manifest": root / "oracle_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite opportunity-oracle artifacts: "
            + ", ".join(str(path) for path in existing)
        )

    _write_json(paths["summary"], result.get("summary") or {})
    _write_csv(
        paths["session_ledger"],
        result.get("session_rows") or [],
        SESSION_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["regime_ledger"],
        result.get("regime_rows") or [],
        REGIME_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["opportunity_ledger"],
        result.get("opportunity_rows") or [],
        OPPORTUNITY_LEDGER_COLUMNS,
    )
    manifest = dict(result.get("manifest") or {})
    manifest.pop("manifest_sha256", None)
    manifest["artifacts"] = {
        name: {
            "filename": path.name,
            "bytes": int(path.stat().st_size),
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
        if name != "manifest"
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _write_json(paths["manifest"], manifest)
    return {name: str(path) for name, path in paths.items()}


def _resolve_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = dict(DEFAULT_DYNAMIC_ORACLE_CONFIG)
    resolved.update(dict(config or {}))
    dwell = int(resolved["minimum_dwell_sessions"])
    penalty = float(resolved["switch_penalty_ticks"])
    risk = float(resolved["initial_risk_ticks"])
    expected_penalty = risk * float(resolved["switch_penalty_risk_fraction"])
    capacity = int(resolved["maximum_concurrent_per_direction"])
    if dwell != 5:
        raise DynamicOracleError("Dynamic Phase 2 freezes minimum dwell at 5 sessions")
    if abs(penalty - 15.0) > 1e-12 or abs(expected_penalty - penalty) > 1e-12:
        raise DynamicOracleError("Dynamic Phase 2 freezes the switch penalty at 15 ticks (0.10R)")
    if capacity != 3:
        raise DynamicOracleError("Dynamic Phase 2 freezes capacity at 3 per direction")
    return _json_safe(resolved)


def _canonical_rows(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence",
        "physical_opportunity_id",
        "lane_event_id",
        "expert_id",
        "outcome_id",
        "signal_side",
        "time_bucket",
        "trend_state",
        "context_cell",
        "session_id",
        "mode",
        "trade_direction",
        "entry_dt",
        "exit_dt",
        "exit_known_dt",
        "net_pnl_ticks",
        "capacity_eligible",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise DynamicOracleError(
            "outcome cube is missing oracle columns: " + ", ".join(missing)
        )
    if frame.empty:
        raise DynamicOracleError("the opportunity oracle requires outcome rows")
    out = frame.loc[:, sorted(required)].copy()
    for column in (
        "sequence",
        "physical_opportunity_id",
        "lane_event_id",
        "expert_id",
        "outcome_id",
        "signal_side",
        "time_bucket",
        "trend_state",
        "context_cell",
        "session_id",
        "mode",
    ):
        out[column] = out[column].astype(str)
        if (out[column].str.len() == 0).any():
            raise DynamicOracleError(f"outcome cube contains blank {column}")
    for column in ("entry_dt", "exit_dt", "exit_known_dt"):
        out[column] = pd.to_datetime(
            out[column], errors="raise", utc=True
        ).dt.tz_convert("America/Denver")
        if out[column].dt.tz is None:
            raise DynamicOracleError(f"{column} must be timezone-aware")
    if not (out["exit_known_dt"] > out["exit_dt"]).all():
        raise DynamicOracleError("every exit_known_dt must be after exit_dt")
    out["net_pnl_ticks"] = pd.to_numeric(
        out["net_pnl_ticks"], errors="raise"
    ).astype(float)
    if not np.isfinite(out["net_pnl_ticks"]).all():
        raise DynamicOracleError("net_pnl_ticks must be finite")
    out["trade_direction"] = pd.to_numeric(
        out["trade_direction"], errors="raise"
    ).astype(int)
    if not set(out["trade_direction"].unique()) <= {-1, 1}:
        raise DynamicOracleError("trade_direction must be -1 or 1")
    out["capacity_eligible"] = out["capacity_eligible"].map(_truth_value)
    if out["outcome_id"].duplicated().any():
        raise DynamicOracleError("outcome cube contains duplicate outcome_id")
    if out.duplicated(["physical_opportunity_id", "expert_id"]).any():
        raise DynamicOracleError(
            "an expert may emit at most one row per physical opportunity"
        )
    per_expert_cells = out.groupby("expert_id")["context_cell"].nunique()
    if (per_expert_cells != 1).any():
        raise DynamicOracleError("every expert_id must belong to exactly one context cell")
    return out.sort_values(
        ["entry_dt", "physical_opportunity_id", "expert_id"]
    ).reset_index(drop=True)


def _reward_indexes(
    frame: pd.DataFrame,
) -> Tuple[
    Dict[Tuple[str, str, str], float],
    Dict[Tuple[str, str, str], int],
    Dict[Tuple[str, str, str], int],
]:
    eligible = frame.loc[frame["capacity_eligible"]].copy()
    keys = ["context_cell", "session_id", "expert_id"]
    rewards = eligible.groupby(keys)["net_pnl_ticks"].sum().to_dict()
    events = eligible.groupby(keys)["outcome_id"].count().to_dict()
    opportunities = (
        eligible.groupby(keys)["physical_opportunity_id"].nunique().to_dict()
    )
    return (
        {tuple(map(str, key)): float(value) for key, value in rewards.items()},
        {tuple(map(str, key)): int(value) for key, value in events.items()},
        {
            tuple(map(str, key)): int(value)
            for key, value in opportunities.items()
        },
    )


def _cell_metadata(frame: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    metadata: Dict[str, Dict[str, str]] = {}
    for cell, group in frame.groupby("context_cell", sort=True):
        values = {
            field: sorted(group[field].astype(str).unique())
            for field in ("signal_side", "time_bucket", "trend_state")
        }
        if any(len(items) != 1 for items in values.values()):
            raise DynamicOracleError(
                f"context cell {cell} has inconsistent component fields"
            )
        metadata[str(cell)] = {
            field: items[0] for field, items in values.items()
        }
    return metadata


def _build_cell_session_rows(
    sequence: str,
    cell: str,
    metadata: Mapping[str, str],
    sessions: Sequence[str],
    path: Sequence[str],
    rewards: Mapping[Tuple[str, str, str], float],
    event_counts: Mapping[Tuple[str, str, str], int],
    opportunity_counts: Mapping[Tuple[str, str, str], int],
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    prior: Optional[str] = None
    age = 0
    for index, (session, state) in enumerate(zip(sessions, path)):
        transition = prior is not None and state != prior
        age = 1 if transition or prior is None else age + 1
        key = (cell, str(session), state)
        raw = 0.0 if state == OFF_STATE else float(rewards.get(key, 0.0))
        penalty = (
            float(config["switch_penalty_ticks"]) if transition else 0.0
        )
        rows.append(
            {
                "schema_version": DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
                "sequence": sequence,
                "session_index": index,
                "session_id": str(session),
                "context_cell": cell,
                **dict(metadata),
                "oracle_state": OFF_STATE if state == OFF_STATE else "EXPERT",
                "selected_expert_id": (
                    None if state == OFF_STATE else state
                ),
                "state_age_sessions": age,
                "transition": transition,
                "transition_from": prior,
                "switch_penalty_ticks": penalty,
                "selected_event_rows": (
                    0 if state == OFF_STATE else event_counts.get(key, 0)
                ),
                "selected_physical_opportunities": (
                    0
                    if state == OFF_STATE
                    else opportunity_counts.get(key, 0)
                ),
                "raw_net_ticks": raw,
                "penalized_objective_ticks": raw - penalty,
            }
        )
        prior = state
    return rows


def _build_regime_rows(
    sequence: str,
    session_rows: Sequence[Mapping[str, Any]],
    minimum_dwell_sessions: int,
) -> List[Dict[str, Any]]:
    if not session_rows:
        return []
    regimes: List[List[Mapping[str, Any]]] = []
    current: List[Mapping[str, Any]] = []
    current_expert: Any = object()
    for row in session_rows:
        expert = row.get("selected_expert_id")
        state_key = expert if expert is not None else OFF_STATE
        if current and state_key != current_expert:
            regimes.append(current)
            current = []
        current.append(row)
        current_expert = state_key
    regimes.append(current)

    output: List[Dict[str, Any]] = []
    for index, regime in enumerate(regimes):
        first = regime[0]
        last = regime[-1]
        dwell = len(regime)
        right_censored = index == len(regimes) - 1 and dwell < minimum_dwell_sessions
        selected_expert = first.get("selected_expert_id")
        output.append(
            {
                "schema_version": DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
                "sequence": sequence,
                "oracle_regime_id": _stable_id(
                    "oracle_regime",
                    {
                        "sequence": sequence,
                        "context_cell": first["context_cell"],
                        "start_session": first["session_id"],
                        "state": selected_expert or OFF_STATE,
                    },
                ),
                "context_cell": first["context_cell"],
                "signal_side": first["signal_side"],
                "time_bucket": first["time_bucket"],
                "trend_state": first["trend_state"],
                "oracle_state": first["oracle_state"],
                "selected_expert_id": selected_expert,
                "start_session": first["session_id"],
                "end_session": last["session_id"],
                "start_session_index": first["session_index"],
                "end_session_index": last["session_index"],
                "dwell_sessions": dwell,
                "minimum_dwell_satisfied": dwell >= minimum_dwell_sessions,
                "right_censored": right_censored,
                "switch_penalty_ticks": sum(
                    float(row["switch_penalty_ticks"]) for row in regime
                ),
                "selected_event_rows": sum(
                    int(row["selected_event_rows"]) for row in regime
                ),
                "selected_physical_opportunities": sum(
                    int(row["selected_physical_opportunities"])
                    for row in regime
                ),
                "raw_net_ticks": sum(
                    float(row["raw_net_ticks"]) for row in regime
                ),
                "penalized_objective_ticks": sum(
                    float(row["penalized_objective_ticks"]) for row in regime
                ),
            }
        )
    return output


def _build_execution_projection(
    frame: pd.DataFrame,
    selected_state: Mapping[Tuple[str, str], str],
    rewards: Mapping[Tuple[str, str, str], float],
    *,
    maximum_per_direction: int,
) -> List[Dict[str, Any]]:
    candidate_by_physical: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in frame.to_dict("records"):
        state = selected_state.get(
            (str(row["context_cell"]), str(row["session_id"])),
            OFF_STATE,
        )
        if state != str(row["expert_id"]):
            continue
        enriched = dict(row)
        enriched["_cell_session_reward"] = rewards.get(
            (
                str(row["context_cell"]),
                str(row["session_id"]),
                str(row["expert_id"]),
            ),
            0.0,
        )
        candidate_by_physical[str(row["physical_opportunity_id"])].append(
            enriched
        )

    selected: Dict[str, Optional[Dict[str, Any]]] = {}
    for physical_id, group in frame.groupby(
        "physical_opportunity_id", sort=False
    ):
        candidates = [
            row
            for row in candidate_by_physical.get(str(physical_id), [])
            if bool(row["capacity_eligible"])
        ]
        candidates.sort(
            key=lambda row: (
                -float(row["_cell_session_reward"]),
                str(row["expert_id"]),
                str(row["outcome_id"]),
            )
        )
        selected[str(physical_id)] = candidates[0] if candidates else None

    active: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
    execution: Dict[str, Tuple[bool, bool, str]] = {}
    selected_items = [
        (physical_id, row)
        for physical_id, row in selected.items()
        if row is not None
    ]
    selected_items.sort(
        key=lambda item: (
            pd.Timestamp(item[1]["entry_dt"]),
            item[0],
        )
    )
    for physical_id, row in selected_items:
        direction = int(row["trade_direction"])
        entry_dt = pd.Timestamp(row["entry_dt"])
        active[direction] = [
            exit_dt for exit_dt in active[direction] if exit_dt > entry_dt
        ]
        if len(active[direction]) >= maximum_per_direction:
            execution[physical_id] = (False, True, "GLOBAL_CAPACITY_LIMIT")
            continue
        active[direction].append(pd.Timestamp(row["exit_dt"]))
        active[direction].sort()
        execution[physical_id] = (True, False, "ORACLE_EXPERT_EXECUTED")

    output: List[Dict[str, Any]] = []
    for physical_id, group in frame.groupby(
        "physical_opportunity_id", sort=False
    ):
        records = group.to_dict("records")
        first = records[0]
        all_candidates = candidate_by_physical.get(str(physical_id), [])
        eligible_candidates = [
            row for row in all_candidates if bool(row["capacity_eligible"])
        ]
        chosen = selected[str(physical_id)]
        if chosen is None:
            if all_candidates:
                reason = "SELECTED_EXPERT_SOURCE_CAPACITY_INELIGIBLE"
            else:
                reason = "ORACLE_OFF_OR_SELECTED_EXPERT_HAS_NO_SETUP"
            executed = False
            capacity_skipped = False
        else:
            executed, capacity_skipped, reason = execution[str(physical_id)]
        context_cells = sorted(
            {str(row["context_cell"]) for row in records}
        )
        output.append(
            {
                "schema_version": DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
                "sequence": str(first["sequence"]),
                "physical_opportunity_id": str(physical_id),
                "session_id": str(first["session_id"]),
                "entry_dt": first["entry_dt"],
                "signal_side": str(first["signal_side"]),
                "candidate_context_cells": context_cells,
                "selected_candidate_count": len(eligible_candidates),
                "selected_expert_id": (
                    str(chosen["expert_id"]) if chosen is not None else None
                ),
                "selected_outcome_id": (
                    str(chosen["outcome_id"]) if chosen is not None else None
                ),
                "selected_lane_event_id": (
                    str(chosen["lane_event_id"]) if chosen is not None else None
                ),
                "mode": str(chosen["mode"]) if chosen is not None else None,
                "trade_direction": (
                    int(chosen["trade_direction"])
                    if chosen is not None
                    else None
                ),
                "exit_dt": chosen["exit_dt"] if chosen is not None else None,
                "exit_known_dt": (
                    chosen["exit_known_dt"] if chosen is not None else None
                ),
                "deduplication_conflicts": max(0, len(eligible_candidates) - 1),
                "capacity_eligible_in_source_lane": (
                    bool(chosen["capacity_eligible"])
                    if chosen is not None
                    else False
                ),
                "executed": executed,
                "capacity_skipped": capacity_skipped,
                "reason_code": reason,
                "net_pnl_ticks": (
                    float(chosen["net_pnl_ticks"]) if executed else None
                ),
            }
        )
    return sorted(
        output,
        key=lambda row: (
            str(row["entry_dt"]),
            str(row["physical_opportunity_id"]),
        ),
    )


def _build_summary(
    sequence: str,
    sessions: Sequence[str],
    session_rows: Sequence[Mapping[str, Any]],
    regime_rows: Sequence[Mapping[str, Any]],
    opportunity_rows: Sequence[Mapping[str, Any]],
    *,
    best_static_raw: float,
    unconstrained_raw: float,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    bounded_raw = sum(float(row["raw_net_ticks"]) for row in session_rows)
    penalties = sum(
        float(row["switch_penalty_ticks"]) for row in session_rows
    )
    bounded_penalized = bounded_raw - penalties
    executed = [row for row in opportunity_rows if row["executed"]]
    pnls = [float(row["net_pnl_ticks"]) for row in executed]
    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    equity = 0.0
    high_water = 0.0
    max_drawdown = 0.0
    by_session: Dict[str, float] = defaultdict(float)
    for row, pnl in zip(executed, pnls):
        equity += pnl
        high_water = max(high_water, equity)
        max_drawdown = max(max_drawdown, high_water - equity)
        by_session[str(row["session_id"])] += pnl
    profitable_sessions = [value for value in by_session.values() if value > 0]
    largest_share = (
        100.0 * max(profitable_sessions) / sum(profitable_sessions)
        if profitable_sessions
        else None
    )
    active_regimes = [
        row for row in regime_rows if row["oracle_state"] == "EXPERT"
    ]
    active_experts = {
        str(row["selected_expert_id"]) for row in active_regimes
    }
    adaptation_value = bounded_penalized - float(best_static_raw)
    projection_pf = (
        gross_profit / gross_loss
        if gross_loss > 0.0
        else (float("inf") if gross_profit > 0.0 else None)
    )
    if adaptation_value <= 0.0:
        conclusion = "NO_BOUNDED_DYNAMIC_VALUE_OVER_STATIC"
    elif sum(pnls) <= 0.0 or (projection_pf is not None and projection_pf <= 1.0):
        conclusion = "SEGMENTATION_VALUE_NOT_POSITIVE_IN_EXECUTION_PROJECTION"
    else:
        conclusion = "TRACKABLE_IN_HINDSIGHT_DIAGNOSTIC_ONLY"
    opportunity_increment = unconstrained_raw - best_static_raw
    return _json_safe(
        {
            "schema_version": DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
            "research_classification": "hindsight_diagnostic_only",
            "sequence": sequence,
            "sessions": len(sessions),
            "session_start": sessions[0],
            "session_end": sessions[-1],
            "configuration": dict(config),
            "detectability_ceiling": {
                "best_static_per_cell_raw_net_ticks": best_static_raw,
                "bounded_oracle_raw_net_ticks": bounded_raw,
                "bounded_oracle_switch_penalties_ticks": penalties,
                "bounded_oracle_penalized_objective_ticks": bounded_penalized,
                "unconstrained_per_session_raw_net_ticks": unconstrained_raw,
                "bounded_adaptation_value_over_static_ticks": adaptation_value,
                "bounded_incremental_opportunity_capture_pct": (
                    100.0 * adaptation_value / opportunity_increment
                    if opportunity_increment > 0.0
                    else None
                ),
            },
            "regimes": {
                "total": len(regime_rows),
                "active": len(active_regimes),
                "off": len(regime_rows) - len(active_regimes),
                "distinct_active_experts": len(active_experts),
                "state_changes": sum(
                    bool(row["transition"]) for row in session_rows
                ),
                "expert_to_expert_changes": sum(
                    row["transition"]
                    and row["selected_expert_id"] is not None
                    and row["transition_from"] not in (None, OFF_STATE)
                    for row in session_rows
                ),
                "median_active_dwell_sessions": (
                    float(
                        np.median(
                            [int(row["dwell_sessions"]) for row in active_regimes]
                        )
                    )
                    if active_regimes
                    else None
                ),
                "terminal_right_censored_regimes": sum(
                    bool(row["right_censored"]) for row in regime_rows
                ),
            },
            "execution_projection": {
                "physical_opportunities": len(opportunity_rows),
                "selected_candidates": sum(
                    row["selected_expert_id"] is not None
                    for row in opportunity_rows
                ),
                "deduplication_conflicts": sum(
                    int(row["deduplication_conflicts"])
                    for row in opportunity_rows
                ),
                "capacity_skips": sum(
                    bool(row["capacity_skipped"]) for row in opportunity_rows
                ),
                "trades": len(executed),
                "unique_sessions": len(by_session),
                "wins": sum(value > 0.0 for value in pnls),
                "win_rate_pct": (
                    100.0 * sum(value > 0.0 for value in pnls) / len(pnls)
                    if pnls
                    else None
                ),
                "net_ticks": sum(pnls),
                "average_trade_ticks": (
                    sum(pnls) / len(pnls) if pnls else None
                ),
                "profit_factor": projection_pf,
                "maximum_drawdown_ticks": max_drawdown,
                "largest_profitable_session_share_pct": largest_share,
            },
            "diagnostic_conclusion": conclusion,
            "causal_selector_authorized": False,
            "selector_parameter_choice_authorized": False,
        }
    )


def _truth_value(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value in (1, 1.0):
        return True
    if value in (0, 0.0):
        return False
    normalized = str(value).strip().lower()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0"}:
        return False
    raise DynamicOracleError(f"invalid capacity_eligible value: {value!r}")


def _keep_better(
    nodes: Dict[Tuple[str, int], _Node],
    key: Tuple[str, int],
    candidate: _Node,
) -> None:
    current = nodes.get(key)
    if current is None or _node_sort_key(candidate) < _node_sort_key(current):
        nodes[key] = candidate


def _node_sort_key(node: _Node) -> Tuple[float, int, Tuple[str, ...]]:
    return (-round(float(node.objective), 12), node.switches, node.path)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}:v1:{_sha256_json(payload)[:24]}"


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise DynamicOracleError(
                f"oracle row {index} violates stable schema; "
                f"missing={sorted(expected - set(row))}, "
                f"extra={sorted(set(row) - expected)}"
            )
    frame = pd.DataFrame(rows, columns=columns)
    for column in frame.columns:
        frame[column] = frame[column].map(_csv_scalar)
    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            _json_safe(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _csv_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _json_safe(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return _json_safe(value)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return value
