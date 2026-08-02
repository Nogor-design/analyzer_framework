from __future__ import annotations

"""Dynamic Phase 3 causal specialist fixed-share selector.

The selector consumes an immutable Dynamic Phase 1 outcome cube.  It never
uses the Phase 2 oracle to make a decision: the oracle is accepted only as a
hash-bound evaluation label for daily-frozen dynamic regret.
"""

from collections import Counter, defaultdict
import hashlib
import json
from math import exp, fsum, isfinite, log
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION = "dynamic_fixed_share_selector.v1"
DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION = (
    "dynamic_carried_fixed_share_selector.v1"
)
SELECTOR_STATES = ("OFF", "WATCH", "ON", "DECAYING")
SELECTOR_PROTOCOLS = ("daily_frozen", "event_updated")

DEFAULT_DYNAMIC_FIXED_SHARE_PROFILES: Dict[str, Dict[str, float]] = {
    "Fast": {"reward_half_life_sessions": 3.0, "fixed_share_rate": 0.10},
    "Balanced": {"reward_half_life_sessions": 7.0, "fixed_share_rate": 0.05},
    "Slow": {"reward_half_life_sessions": 15.0, "fixed_share_rate": 0.02},
}

DEFAULT_DYNAMIC_FIXED_SHARE_CONFIG: Dict[str, Any] = {
    "profiles": DEFAULT_DYNAMIC_FIXED_SHARE_PROFILES,
    "primary_profile": "Balanced",
    "initial_risk_ticks": 150.0,
    "reward_clip_r": 1.0,
    "learning_rate": 1.0,
    "minimum_effective_outcomes_on": 3.0,
    "absolute_evidence_floor_r": 0.25,
    "maximum_concurrent_per_direction": 3,
    "switch_penalty_ticks": 15.0,
    "strict_evidence_rule": "exit_known_dt < decision_asof",
    "new_expert_confirmation_boundaries": 1,
    "decaying_boundaries": 1,
    "expert_update_policy": "capacity_eligible_paper_outcomes_only",
    "physical_conflict_tiebreak": [
        "descending_selector_probability",
        "descending_discounted_evidence_r",
        "ascending_expert_id",
        "ascending_outcome_id",
    ],
}

EVIDENCE_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "context_cell",
    "signal_side",
    "time_bucket",
    "trend_state",
    "expert_id",
    "expert_lane_id",
    "mode",
    "trade_direction",
    "discounted_evidence_r",
    "discounted_effective_outcomes",
    "known_outcomes",
    "selector_probability",
    "rank",
    "candidate_winner",
    "selected_policy_expert",
    "evidence_through",
)

STATE_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "context_cell",
    "signal_side",
    "time_bucket",
    "trend_state",
    "previous_state",
    "target_state",
    "state",
    "candidate_expert_id",
    "selected_expert_id",
    "expert_lane_id",
    "mode",
    "trade_direction",
    "selector_probability",
    "discounted_evidence_r",
    "discounted_effective_outcomes",
    "known_outcomes",
    "evidence_through",
    "transition",
    "reason_code",
    "executable",
)

SWITCH_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "context_cell",
    "previous_state",
    "state",
    "previous_expert_id",
    "selected_expert_id",
    "transition",
    "reason_code",
)

WINDOW_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "window_id",
    "signal_side",
    "trend_state",
    "start_time",
    "end_time",
    "time_buckets",
    "expert_ids",
    "expert_lane_id",
    "timeframe",
    "lookback",
    "basis",
    "multiplier",
    "mode",
    "trade_direction",
)

EXECUTION_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "physical_opportunity_id",
    "signal_dt",
    "entry_dt",
    "candidate_context_cells",
    "on_context_cells",
    "selected_candidate_count",
    "selected_expert_id",
    "selected_outcome_id",
    "selected_lane_event_id",
    "expert_lane_id",
    "mode",
    "trade_direction",
    "exit_dt",
    "exit_known_dt",
    "selector_probability",
    "discounted_evidence_r",
    "deduplication_conflicts",
    "capacity_eligible_in_source_lane",
    "executed",
    "capacity_skipped",
    "reason_code",
    "net_pnl_ticks",
)


class DynamicSelectorError(ValueError):
    """Raised when the frozen Dynamic Phase 3 contract cannot be honored."""


def run_dynamic_fixed_share_selector(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    profile: str,
    protocol: str = "daily_frozen",
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    oracle_summary: Optional[Mapping[str, Any]] = None,
    oracle_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the frozen Phase 3 discounted-score selector."""
    return _run_dynamic_selector(
        rows,
        profile=profile,
        protocol=protocol,
        config=config,
        source_manifest=source_manifest,
        oracle_summary=oracle_summary,
        oracle_manifest=oracle_manifest,
        selector_mechanic="discounted_score_mixture",
    )


def run_dynamic_carried_fixed_share_selector(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    profile: str,
    protocol: str = "daily_frozen",
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    oracle_summary: Optional[Mapping[str, Any]] = None,
    oracle_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the frozen Phase 4B carried specialist fixed-share selector."""
    return _run_dynamic_selector(
        rows,
        profile=profile,
        protocol=protocol,
        config=config,
        source_manifest=source_manifest,
        oracle_summary=oracle_summary,
        oracle_manifest=oracle_manifest,
        selector_mechanic="carried_fixed_share_posterior",
    )


def _run_dynamic_selector(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    profile: str,
    protocol: str = "daily_frozen",
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    oracle_summary: Optional[Mapping[str, Any]] = None,
    oracle_manifest: Optional[Mapping[str, Any]] = None,
    selector_mechanic: str,
) -> Dict[str, Any]:
    """Run one causal profile/protocol replay against a Phase 1 cube."""
    if selector_mechanic not in {
        "discounted_score_mixture",
        "carried_fixed_share_posterior",
    }:
        raise DynamicSelectorError(
            f"unknown selector mechanic: {selector_mechanic}"
        )
    carried_posterior = selector_mechanic == "carried_fixed_share_posterior"
    schema_version = (
        DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION
        if carried_posterior
        else DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION
    )
    resolved = _resolve_config(config)
    profile_name = str(profile)
    if profile_name not in resolved["profiles"]:
        raise DynamicSelectorError(f"unknown fixed-share profile: {profile_name}")
    protocol_name = str(protocol)
    if protocol_name not in SELECTOR_PROTOCOLS:
        raise DynamicSelectorError(f"unknown selector protocol: {protocol_name}")

    frame = _canonical_rows(rows)
    sequence_values = sorted(frame["sequence"].unique())
    if len(sequence_values) != 1:
        raise DynamicSelectorError("the selector requires exactly one sequence")
    sequence = str(sequence_values[0])
    experts, experts_by_cell, cell_parts = _expert_indexes(frame)
    sessions = _session_clock(frame)
    session_index = {session: index for index, session in enumerate(sessions)}
    boundaries = _decision_boundaries(
        frame,
        protocol_name,
        sessions=sessions,
        all_cells=tuple(sorted(experts_by_cell)),
    )
    groups_by_physical = {
        str(physical_id): group.copy()
        for physical_id, group in frame.groupby(
            "physical_opportunity_id", sort=False
        )
    }

    half_life = float(
        resolved["profiles"][profile_name]["reward_half_life_sessions"]
    )
    share_rate = float(resolved["profiles"][profile_name]["fixed_share_rate"])
    decay = 0.5 ** (1.0 / half_life)
    scores = {expert_id: 0.0 for expert_id in experts}
    effective_counts = {expert_id: 0.0 for expert_id in experts}
    posterior_weights = {
        expert_id: 1.0 / len(expert_ids)
        for expert_ids in experts_by_cell.values()
        for expert_id in expert_ids
    }
    known_counts = {expert_id: 0 for expert_id in experts}
    evidence_through: Dict[str, Optional[pd.Timestamp]] = {
        expert_id: None for expert_id in experts
    }
    states: Dict[str, Dict[str, Any]] = {
        cell: {
            "state": "OFF",
            "selected_expert_id": None,
            "candidate_expert_id": None,
            "reason_code": "NO_DECISION",
        }
        for cell in experts_by_cell
    }
    state_details: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    eligible_updates = frame.loc[frame["capacity_eligible"]].sort_values(
        ["exit_known_dt", "outcome_id"]
    )
    update_records = eligible_updates.to_dict("records")
    update_cursor = 0
    prior_session_index: Optional[int] = None
    active: Dict[int, List[pd.Timestamp]] = {-1: [], 1: []}
    evidence_rows: List[Dict[str, Any]] = []
    state_rows: List[Dict[str, Any]] = []
    switch_rows: List[Dict[str, Any]] = []
    window_rows: List[Dict[str, Any]] = []
    execution_rows: List[Dict[str, Any]] = []

    for decision_index, boundary in enumerate(boundaries):
        decision_asof = pd.Timestamp(boundary["decision_asof"])
        session_id = str(boundary["session_id"])
        current_session_index = session_index[session_id]
        session_advanced = (
            prior_session_index is None
            or current_session_index != prior_session_index
        )
        if prior_session_index is not None:
            gap = current_session_index - prior_session_index
            if gap < 0:
                raise DynamicSelectorError("decision boundaries are not chronological")
            if gap:
                multiplier = decay ** gap
                for expert_id in scores:
                    scores[expert_id] *= multiplier
                    effective_counts[expert_id] *= multiplier
                if carried_posterior:
                    for expert_ids in experts_by_cell.values():
                        tempered = _normalize_positive_weights(
                            {
                                expert_id: posterior_weights[expert_id]
                                ** multiplier
                                for expert_id in expert_ids
                            }
                        )
                        posterior_weights.update(tempered)
        prior_session_index = current_session_index

        changed_cells: set[str] = set()
        reward_batches: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        while update_cursor < len(update_records):
            update = update_records[update_cursor]
            known_dt = pd.Timestamp(update["exit_known_dt"])
            if not known_dt < decision_asof:
                break
            expert_id = str(update["expert_id"])
            reward_r = float(update["net_pnl_ticks"]) / float(
                resolved["initial_risk_ticks"]
            )
            clip = float(resolved["reward_clip_r"])
            clipped_reward = max(-clip, min(clip, reward_r))
            scores[expert_id] += clipped_reward
            effective_counts[expert_id] += 1.0
            known_counts[expert_id] += 1
            evidence_through[expert_id] = known_dt
            update_cell = str(update["context_cell"])
            changed_cells.add(update_cell)
            reward_batches[update_cell][expert_id].append(clipped_reward)
            update_cursor += 1

        if carried_posterior:
            learning_rate = float(resolved["learning_rate"])
            for cell in sorted(changed_cells):
                expert_ids = experts_by_cell[cell]
                logits = {
                    expert_id: log(max(posterior_weights[expert_id], 1e-300))
                    + learning_rate
                    * fsum(reward_batches[cell].get(expert_id, ()))
                    for expert_id in expert_ids
                }
                maximum = max(logits.values())
                posterior = _normalize_positive_weights(
                    {
                        expert_id: exp(value - maximum)
                        for expert_id, value in logits.items()
                    }
                )
                count = len(expert_ids)
                shared = _normalize_positive_weights(
                    {
                        expert_id: (1.0 - share_rate) * posterior[expert_id]
                        + share_rate / count
                        for expert_id in expert_ids
                    }
                )
                posterior_weights.update(shared)

        if protocol_name == "daily_frozen" or session_advanced:
            evaluated_cells = set(experts_by_cell)
        else:
            evaluated_cells = changed_cells | set(boundary["context_cells"])

        for cell in sorted(evaluated_cells):
            assessment, detail_rows = _assess_cell(
                cell,
                experts_by_cell[cell],
                experts,
                scores,
                effective_counts,
                known_counts,
                evidence_through,
                share_rate=share_rate,
                config=resolved,
                posterior_weights=(
                    posterior_weights if carried_posterior else None
                ),
            )
            previous = dict(states[cell])
            transition = _transition_state(previous, assessment)
            states[cell] = transition
            selected_id = transition["selected_expert_id"]
            state_details[cell] = {
                row["expert_id"]: row for row in detail_rows
            }
            for rank, detail in enumerate(detail_rows, start=1):
                expert = experts[detail["expert_id"]]
                evidence_rows.append(
                    {
                        "schema_version": schema_version,
                        "sequence": sequence,
                        "profile": profile_name,
                        "protocol": protocol_name,
                        "decision_index": decision_index,
                        "decision_asof": decision_asof,
                        "session_id": session_id,
                        "context_cell": cell,
                        **cell_parts[cell],
                        "expert_id": detail["expert_id"],
                        "expert_lane_id": expert["expert_lane_id"],
                        "mode": expert["mode"],
                        "trade_direction": expert["trade_direction"],
                        "discounted_evidence_r": detail["score"],
                        "discounted_effective_outcomes": detail["effective_count"],
                        "known_outcomes": detail["known_count"],
                        "selector_probability": detail["probability"],
                        "rank": rank,
                        "candidate_winner": detail["expert_id"]
                        == assessment["candidate_expert_id"],
                        "selected_policy_expert": detail["expert_id"] == selected_id,
                        "evidence_through": detail["evidence_through"],
                    }
                )

            selected_detail = (
                state_details[cell].get(selected_id) if selected_id else None
            )
            selected_expert = experts.get(selected_id) if selected_id else None
            state_row = {
                "schema_version": schema_version,
                "sequence": sequence,
                "profile": profile_name,
                "protocol": protocol_name,
                "decision_index": decision_index,
                "decision_asof": decision_asof,
                "session_id": session_id,
                "context_cell": cell,
                **cell_parts[cell],
                "previous_state": previous["state"],
                "target_state": assessment["target_state"],
                "state": transition["state"],
                "candidate_expert_id": assessment["candidate_expert_id"],
                "selected_expert_id": selected_id,
                "expert_lane_id": (
                    selected_expert["expert_lane_id"] if selected_expert else None
                ),
                "mode": selected_expert["mode"] if selected_expert else None,
                "trade_direction": (
                    selected_expert["trade_direction"] if selected_expert else None
                ),
                "selector_probability": (
                    selected_detail["probability"] if selected_detail else None
                ),
                "discounted_evidence_r": (
                    selected_detail["score"] if selected_detail else None
                ),
                "discounted_effective_outcomes": (
                    selected_detail["effective_count"] if selected_detail else None
                ),
                "known_outcomes": (
                    selected_detail["known_count"] if selected_detail else 0
                ),
                "evidence_through": (
                    selected_detail["evidence_through"] if selected_detail else None
                ),
                "transition": f"{previous['state']}->{transition['state']}",
                "reason_code": transition["reason_code"],
                "executable": transition["state"] == "ON",
            }
            state_rows.append(state_row)
            if (
                previous["state"] != transition["state"]
                or previous.get("selected_expert_id") != selected_id
            ):
                switch_rows.append(
                    {
                        "schema_version": schema_version,
                        "sequence": sequence,
                        "profile": profile_name,
                        "protocol": protocol_name,
                        "decision_index": decision_index,
                        "decision_asof": decision_asof,
                        "session_id": session_id,
                        "context_cell": cell,
                        "previous_state": previous["state"],
                        "state": transition["state"],
                        "previous_expert_id": previous.get("selected_expert_id"),
                        "selected_expert_id": selected_id,
                        "transition": state_row["transition"],
                        "reason_code": transition["reason_code"],
                    }
                )

        window_rows.extend(
            _build_windows(
                sequence,
                profile_name,
                protocol_name,
                decision_index,
                decision_asof,
                session_id,
                states,
                experts,
                cell_parts,
            )
        )
        for physical_id in boundary["physical_opportunity_ids"]:
            group = groups_by_physical[str(physical_id)]
            execution_rows.append(
                _project_opportunity(
                    group,
                    sequence=sequence,
                    profile=profile_name,
                    protocol=protocol_name,
                    decision_index=decision_index,
                    decision_asof=decision_asof,
                    states=states,
                    state_details=state_details,
                    active=active,
                    maximum_per_direction=int(
                        resolved["maximum_concurrent_per_direction"]
                    ),
                )
            )

    evidence_rows = _json_safe(evidence_rows)
    state_rows = _json_safe(state_rows)
    switch_rows = _json_safe(switch_rows)
    window_rows = _json_safe(window_rows)
    execution_rows = _json_safe(execution_rows)
    if carried_posterior:
        for rows_payload in (window_rows, execution_rows):
            for row in rows_payload:
                row["schema_version"] = schema_version
    physical_execution_ids = [
        str(row["physical_opportunity_id"]) for row in execution_rows
    ]
    if len(physical_execution_ids) != len(set(physical_execution_ids)):
        raise DynamicSelectorError(
            "a physical opportunity was projected more than once"
        )
    summary = _build_summary(
        sequence,
        profile_name,
        protocol_name,
        sessions,
        frame,
        state_rows,
        switch_rows,
        execution_rows,
        resolved,
        oracle_summary=oracle_summary,
    )
    summary["schema_version"] = schema_version
    safe_source = _json_safe(dict(source_manifest or {}))
    safe_oracle = _json_safe(dict(oracle_manifest or {}))
    manifest: Dict[str, Any] = {
        "schema_version": schema_version,
        "research_phase": (
            "dynamic_phase_4b" if carried_posterior else "dynamic_phase_3"
        ),
        "research_classification": "causal_development_replay",
        "sequence": sequence,
        "profile": profile_name,
        "protocol": protocol_name,
        "source_outcome_cube": {
            "manifest_sha256": safe_source.get("manifest_sha256"),
            "outcome_cube_sha256": safe_source.get("outcome_cube_sha256"),
            "schema_version": safe_source.get("schema_version"),
        },
        "evaluation_oracle": {
            "manifest_sha256": safe_oracle.get("manifest_sha256"),
            "schema_version": safe_oracle.get("schema_version"),
            "used_for_decisions": False,
        },
        "configuration": {
            "sha256": _sha256_json(resolved),
            "payload": _json_safe(resolved),
        },
        "contracts": {
            "causal_strategy": True,
            "selector_mechanic": selector_mechanic,
            "ranking_source": (
                "carried_posterior_weight"
                if carried_posterior
                else "discounted_raw_score"
            ),
            "discounted_raw_evidence_use": (
                "absolute_off_watch_floor"
                if carried_posterior
                else "ranking_and_absolute_off_watch_floor"
            ),
            "posterior_recurrence": (
                "session_temper_then_stable_reward_batch_then_uniform_fixed_share"
                if carried_posterior
                else None
            ),
            "strict_evidence_rule": resolved["strict_evidence_rule"],
            "daily_frozen_primary": True,
            "oracle_may_update_selector": False,
            "maximum_executable_experts_per_physical_opportunity": 1,
            "maximum_concurrent_per_direction": resolved[
                "maximum_concurrent_per_direction"
            ],
        },
        "counts": {
            "sessions": len(sessions),
            "context_cells": len(experts_by_cell),
            "candidate_experts": len(experts),
            "decision_boundaries": len(boundaries),
            "evidence_rows": len(evidence_rows),
            "state_rows": len(state_rows),
            "switch_rows": len(switch_rows),
            "window_rows": len(window_rows),
            "execution_rows": len(execution_rows),
        },
        "summary_sha256": _sha256_json(summary),
        "evidence_ledger_sha256": _sha256_json(evidence_rows),
        "state_ledger_sha256": _sha256_json(state_rows),
        "switch_ledger_sha256": _sha256_json(switch_rows),
        "window_ledger_sha256": _sha256_json(window_rows),
        "execution_ledger_sha256": _sha256_json(execution_rows),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "evidence_rows": evidence_rows,
        "state_rows": state_rows,
        "switch_rows": switch_rows,
        "window_rows": window_rows,
        "execution_rows": execution_rows,
    }


def run_dynamic_fixed_share_matrix(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    profiles: Sequence[str] = ("Fast", "Balanced", "Slow"),
    protocols: Sequence[str] = SELECTOR_PROTOCOLS,
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    oracle_summary: Optional[Mapping[str, Any]] = None,
    oracle_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the declared profile/protocol matrix and combine stable ledgers."""
    runs = []
    for profile in profiles:
        for protocol in protocols:
            runs.append(
                run_dynamic_fixed_share_selector(
                    rows,
                    profile=profile,
                    protocol=protocol,
                    config=config,
                    source_manifest=source_manifest,
                    oracle_summary=oracle_summary,
                    oracle_manifest=oracle_manifest,
                )
            )
    combined: Dict[str, Any] = {
        key: [
            row
            for run in runs
            for row in run[f"{key}_rows"]
        ]
        for key in ("evidence", "state", "switch", "window", "execution")
    }
    summaries = [run["summary"] for run in runs]
    resolved = _resolve_config(config)
    first = runs[0]["manifest"]
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_3",
        "research_classification": "causal_development_replay",
        "sequence": first["sequence"],
        "profiles": [str(value) for value in profiles],
        "protocols": [str(value) for value in protocols],
        "primary_profile": resolved["primary_profile"],
        "primary_protocol": "daily_frozen",
        "source_outcome_cube": first["source_outcome_cube"],
        "evaluation_oracle": first["evaluation_oracle"],
        "configuration": first["configuration"],
        "contracts": first["contracts"],
        "run_manifest_sha256": [
            run["manifest"]["manifest_sha256"] for run in runs
        ],
        "counts": {
            "runs": len(runs),
            **{f"{key}_rows": len(value) for key, value in combined.items()},
        },
        "summary_sha256": _sha256_json(summaries),
        **{
            f"{key}_ledger_sha256": _sha256_json(value)
            for key, value in combined.items()
        },
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summaries,
        **{f"{key}_rows": value for key, value in combined.items()},
    }


def run_dynamic_carried_fixed_share_matrix(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    profiles: Sequence[str] = ("Fast", "Balanced", "Slow"),
    protocols: Sequence[str] = SELECTOR_PROTOCOLS,
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    oracle_summary: Optional[Mapping[str, Any]] = None,
    oracle_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the complete frozen Phase 4B carried-posterior matrix."""
    runs = []
    for profile in profiles:
        for protocol in protocols:
            runs.append(
                run_dynamic_carried_fixed_share_selector(
                    rows,
                    profile=profile,
                    protocol=protocol,
                    config=config,
                    source_manifest=source_manifest,
                    oracle_summary=oracle_summary,
                    oracle_manifest=oracle_manifest,
                )
            )
    combined: Dict[str, Any] = {
        key: [
            row
            for run in runs
            for row in run[f"{key}_rows"]
        ]
        for key in ("evidence", "state", "switch", "window", "execution")
    }
    summaries = [run["summary"] for run in runs]
    resolved = _resolve_config(config)
    first = runs[0]["manifest"]
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_4b",
        "research_classification": "causal_development_replay",
        "sequence": first["sequence"],
        "profiles": [str(value) for value in profiles],
        "protocols": [str(value) for value in protocols],
        "primary_profile": resolved["primary_profile"],
        "primary_protocol": "daily_frozen",
        "source_outcome_cube": first["source_outcome_cube"],
        "evaluation_oracle": first["evaluation_oracle"],
        "configuration": first["configuration"],
        "contracts": first["contracts"],
        "run_manifest_sha256": [
            run["manifest"]["manifest_sha256"] for run in runs
        ],
        "counts": {
            "runs": len(runs),
            **{f"{key}_rows": len(value) for key, value in combined.items()},
        },
        "summary_sha256": _sha256_json(summaries),
        **{
            f"{key}_ledger_sha256": _sha256_json(value)
            for key, value in combined.items()
        },
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summaries,
        **{f"{key}_rows": value for key, value in combined.items()},
    }


def write_dynamic_fixed_share_selector(
    output_dir: Path,
    result: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, str]:
    """Write stable, hash-bound Dynamic Phase 3 artifacts."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": root / "selector_summary.json",
        "evidence_ledger": root / "selector_evidence_ledger.csv",
        "state_ledger": root / "selector_state_ledger.csv",
        "switch_ledger": root / "selector_switch_ledger.csv",
        "window_ledger": root / "selector_window_ledger.csv",
        "execution_ledger": root / "selector_execution_ledger.csv",
        "manifest": root / "selector_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite fixed-share selector artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    _write_json(paths["summary"], result.get("summary") or [])
    _write_csv(
        paths["evidence_ledger"],
        result.get("evidence_rows") or [],
        EVIDENCE_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["state_ledger"],
        result.get("state_rows") or [],
        STATE_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["switch_ledger"],
        result.get("switch_rows") or [],
        SWITCH_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["window_ledger"],
        result.get("window_rows") or [],
        WINDOW_LEDGER_COLUMNS,
    )
    _write_csv(
        paths["execution_ledger"],
        result.get("execution_rows") or [],
        EXECUTION_LEDGER_COLUMNS,
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
    resolved = json.loads(json.dumps(DEFAULT_DYNAMIC_FIXED_SHARE_CONFIG))
    override = dict(config or {})
    for key, value in override.items():
        if key == "profiles":
            profiles = resolved["profiles"]
            for name, payload in dict(value).items():
                profiles[str(name)] = dict(payload)
        else:
            resolved[key] = value
    if set(resolved["profiles"]) != {"Fast", "Balanced", "Slow"}:
        raise DynamicSelectorError("Phase 3 freezes the Fast/Balanced/Slow profiles")
    frozen = {
        "Fast": (3.0, 0.10),
        "Balanced": (7.0, 0.05),
        "Slow": (15.0, 0.02),
    }
    for name, (half_life, share) in frozen.items():
        payload = resolved["profiles"][name]
        if (
            abs(float(payload["reward_half_life_sessions"]) - half_life) > 1e-12
            or abs(float(payload["fixed_share_rate"]) - share) > 1e-12
        ):
            raise DynamicSelectorError(f"Phase 3 freezes the {name} profile")
    numeric_contract = {
        "initial_risk_ticks": 150.0,
        "reward_clip_r": 1.0,
        "learning_rate": 1.0,
        "minimum_effective_outcomes_on": 3.0,
        "absolute_evidence_floor_r": 0.25,
        "maximum_concurrent_per_direction": 3.0,
        "switch_penalty_ticks": 15.0,
        "new_expert_confirmation_boundaries": 1.0,
        "decaying_boundaries": 1.0,
    }
    for key, expected in numeric_contract.items():
        value = float(resolved[key])
        if not isfinite(value) or abs(value - expected) > 1e-12:
            raise DynamicSelectorError(
                f"Phase 3 freezes {key} at {expected:g}"
            )
    if resolved["primary_profile"] != "Balanced":
        raise DynamicSelectorError("Phase 3 freezes Balanced as primary")
    text_contract = {
        "strict_evidence_rule": "exit_known_dt < decision_asof",
        "expert_update_policy": "capacity_eligible_paper_outcomes_only",
    }
    for key, expected in text_contract.items():
        if resolved.get(key) != expected:
            raise DynamicSelectorError(f"Phase 3 freezes {key}")
    if resolved.get("physical_conflict_tiebreak") != (
        DEFAULT_DYNAMIC_FIXED_SHARE_CONFIG["physical_conflict_tiebreak"]
    ):
        raise DynamicSelectorError("Phase 3 freezes physical_conflict_tiebreak")
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
        "expert_lane_id",
        "signal_side",
        "timeframe",
        "lookback",
        "basis",
        "multiplier",
        "time_bucket",
        "trend_state",
        "context_cell",
        "session_id",
        "mode",
        "trade_direction",
        "signal_dt",
        "entry_dt",
        "exit_dt",
        "exit_known_dt",
        "net_pnl_ticks",
        "capacity_eligible",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise DynamicSelectorError(
            "outcome cube is missing selector columns: " + ", ".join(missing)
        )
    if frame.empty:
        raise DynamicSelectorError("the selector requires outcome rows")
    out = frame.loc[:, sorted(required)].copy()
    text_columns = required - {
        "timeframe",
        "lookback",
        "multiplier",
        "trade_direction",
        "signal_dt",
        "entry_dt",
        "exit_dt",
        "exit_known_dt",
        "net_pnl_ticks",
        "capacity_eligible",
    }
    for column in text_columns:
        out[column] = out[column].astype(str)
        if (out[column].str.len() == 0).any():
            raise DynamicSelectorError(f"outcome cube contains blank {column}")
    for column in ("signal_dt", "entry_dt", "exit_dt", "exit_known_dt"):
        out[column] = pd.to_datetime(
            out[column], errors="raise", utc=True
        ).dt.tz_convert("America/Denver")
        if out[column].dt.tz is None:
            raise DynamicSelectorError(f"{column} must be timezone-aware")
    if not (out["signal_dt"] < out["entry_dt"]).all():
        raise DynamicSelectorError("every signal_dt must be before entry_dt")
    if not (out["exit_known_dt"] > out["exit_dt"]).all():
        raise DynamicSelectorError("every exit_known_dt must be after exit_dt")
    for column in ("timeframe", "lookback", "trade_direction"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(int)
    out["multiplier"] = pd.to_numeric(out["multiplier"], errors="raise").astype(float)
    out["net_pnl_ticks"] = pd.to_numeric(
        out["net_pnl_ticks"], errors="raise"
    ).astype(float)
    if not np.isfinite(out[["multiplier", "net_pnl_ticks"]].to_numpy()).all():
        raise DynamicSelectorError("numeric selector inputs must be finite")
    if not set(out["trade_direction"].unique()) <= {-1, 1}:
        raise DynamicSelectorError("trade_direction must be -1 or 1")
    out["capacity_eligible"] = out["capacity_eligible"].map(_truth_value)
    if out["outcome_id"].duplicated().any():
        raise DynamicSelectorError("outcome cube contains duplicate outcome_id")
    if out.duplicated(["physical_opportunity_id", "expert_id"]).any():
        raise DynamicSelectorError(
            "an expert may emit at most one row per physical opportunity"
        )
    return out.sort_values(
        ["signal_dt", "physical_opportunity_id", "expert_id"]
    ).reset_index(drop=True)


def _expert_indexes(
    frame: pd.DataFrame,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, List[str]],
    Dict[str, Dict[str, str]],
]:
    fields = (
        "context_cell",
        "expert_lane_id",
        "signal_side",
        "time_bucket",
        "trend_state",
        "timeframe",
        "lookback",
        "basis",
        "multiplier",
        "mode",
        "trade_direction",
    )
    experts: Dict[str, Dict[str, Any]] = {}
    for expert_id, group in frame.groupby("expert_id", sort=True):
        payload: Dict[str, Any] = {}
        for field in fields:
            values = group[field].unique().tolist()
            if len(values) != 1:
                raise DynamicSelectorError(
                    f"expert {expert_id} has inconsistent {field}"
                )
            payload[field] = values[0]
        experts[str(expert_id)] = _json_safe(payload)
    experts_by_cell: Dict[str, List[str]] = defaultdict(list)
    for expert_id, payload in experts.items():
        experts_by_cell[str(payload["context_cell"])].append(expert_id)
    for cell in experts_by_cell:
        experts_by_cell[cell].sort()
    cell_parts: Dict[str, Dict[str, str]] = {}
    for cell, expert_ids in experts_by_cell.items():
        sample = experts[expert_ids[0]]
        cell_parts[cell] = {
            field: str(sample[field])
            for field in ("signal_side", "time_bucket", "trend_state")
        }
        if any(
            str(experts[expert_id][field]) != value
            for expert_id in expert_ids
            for field, value in cell_parts[cell].items()
        ):
            raise DynamicSelectorError(f"context cell {cell} is inconsistent")
    return experts, dict(experts_by_cell), cell_parts


def _session_clock(frame: pd.DataFrame) -> List[str]:
    first = (
        frame.groupby("session_id", sort=False)["signal_dt"].min().sort_values()
    )
    return [str(value) for value in first.index]


def _decision_boundaries(
    frame: pd.DataFrame,
    protocol: str,
    *,
    sessions: Sequence[str],
    all_cells: Sequence[str],
) -> List[Dict[str, Any]]:
    boundaries: List[Dict[str, Any]] = []
    if protocol == "daily_frozen":
        for session in sessions:
            group = frame.loc[frame["session_id"] == session]
            opportunities = (
                group.groupby("physical_opportunity_id", as_index=False)
                .agg(signal_dt=("signal_dt", "min"))
                .sort_values(["signal_dt", "physical_opportunity_id"])
            )
            boundaries.append(
                {
                    "session_id": session,
                    "decision_asof": opportunities["signal_dt"].min(),
                    "context_cells": tuple(all_cells),
                    "physical_opportunity_ids": opportunities[
                        "physical_opportunity_id"
                    ].astype(str).tolist(),
                }
            )
        return boundaries
    opportunities = (
        frame.groupby("physical_opportunity_id", sort=False)
        .agg(
            session_id=("session_id", "first"),
            decision_asof=("signal_dt", "min"),
            context_cells=("context_cell", lambda values: tuple(sorted(set(values)))),
        )
        .reset_index()
        .sort_values(["decision_asof", "physical_opportunity_id"])
    )
    for row in opportunities.to_dict("records"):
        boundaries.append(
            {
                "session_id": str(row["session_id"]),
                "decision_asof": row["decision_asof"],
                "context_cells": row["context_cells"],
                "physical_opportunity_ids": [str(row["physical_opportunity_id"])],
            }
        )
    return boundaries


def _assess_cell(
    cell: str,
    expert_ids: Sequence[str],
    experts: Mapping[str, Mapping[str, Any]],
    scores: Mapping[str, float],
    effective_counts: Mapping[str, float],
    known_counts: Mapping[str, int],
    evidence_through: Mapping[str, Optional[pd.Timestamp]],
    *,
    share_rate: float,
    config: Mapping[str, Any],
    posterior_weights: Optional[Mapping[str, float]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    count = len(expert_ids)
    if posterior_weights is None:
        logits = [
            float(config["learning_rate"]) * float(scores[expert_id])
            for expert_id in expert_ids
        ]
        maximum = max(logits)
        exponentials = [exp(value - maximum) for value in logits]
        denominator = sum(exponentials)
        probabilities = {
            expert_id: (
                (1.0 - share_rate) * raw_weight / denominator
                + share_rate / count
            )
            for expert_id, raw_weight in zip(expert_ids, exponentials)
        }
    else:
        probabilities = _normalize_positive_weights(
            {
                expert_id: float(posterior_weights[expert_id])
                for expert_id in expert_ids
            }
        )
    details = []
    for expert_id in expert_ids:
        details.append(
            {
                "expert_id": expert_id,
                "probability": probabilities[expert_id],
                "score": float(scores[expert_id]),
                "effective_count": float(effective_counts[expert_id]),
                "known_count": int(known_counts[expert_id]),
                "evidence_through": evidence_through[expert_id],
            }
        )
    details.sort(
        key=lambda row: (
            -round(float(row["probability"]), 15),
            -round(float(row["score"]), 15),
            str(row["expert_id"]),
        )
    )
    winner = details[0]
    if winner["score"] <= 0.0:
        target = "OFF"
        reason = (
            "INSUFFICIENT_EVIDENCE"
            if winner["known_count"] == 0
            else "NON_POSITIVE_ABSOLUTE_EVIDENCE"
        )
    elif (
        winner["effective_count"]
        < float(config["minimum_effective_outcomes_on"])
        or winner["score"] < float(config["absolute_evidence_floor_r"])
    ):
        target = "WATCH"
        reason = "ABSOLUTE_FLOOR_NOT_MET"
    else:
        target = "ON"
        reason = "ABSOLUTE_FLOOR_MET"
    return (
        {
            "context_cell": cell,
            "candidate_expert_id": winner["expert_id"],
            "target_state": target,
            "reason_code": reason,
        },
        details,
    )


def _normalize_positive_weights(
    weights: Mapping[str, float],
) -> Dict[str, float]:
    if not weights:
        raise DynamicSelectorError("a specialist cell requires experts")
    if any(not isfinite(float(value)) or float(value) <= 0.0 for value in weights.values()):
        raise DynamicSelectorError(
            "specialist posterior weights must remain finite and positive"
        )
    denominator = fsum(float(value) for value in weights.values())
    if not isfinite(denominator) or denominator <= 0.0:
        raise DynamicSelectorError("specialist posterior cannot be normalized")
    normalized = {
        str(expert_id): float(value) / denominator
        for expert_id, value in weights.items()
    }
    correction_key = min(normalized)
    normalized[correction_key] += 1.0 - fsum(normalized.values())
    return normalized


def _transition_state(
    previous: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> Dict[str, Any]:
    prior_state = str(previous.get("state") or "OFF")
    prior_expert = previous.get("selected_expert_id")
    candidate = str(assessment["candidate_expert_id"])
    target = str(assessment["target_state"])
    reason = str(assessment["reason_code"])
    if target == "ON":
        if prior_state == "OFF":
            state, selected, reason = "WATCH", candidate, "WATCH_CONFIRMATION_REQUIRED"
        elif prior_state == "WATCH":
            if prior_expert == candidate:
                state, selected = "ON", candidate
            else:
                state, selected, reason = (
                    "WATCH",
                    candidate,
                    "WATCH_CANDIDATE_CHANGED",
                )
        elif prior_state == "ON":
            if prior_expert == candidate:
                state, selected = "ON", candidate
            else:
                state, selected, reason = (
                    "DECAYING",
                    prior_expert,
                    "ACTIVE_EXPERT_REPLACEMENT_PENDING",
                )
        else:
            if prior_expert == candidate:
                state, selected, reason = "ON", candidate, "DECAYING_RECOVERED"
            else:
                state, selected, reason = (
                    "WATCH",
                    candidate,
                    "REPLACEMENT_CONFIRMATION_REQUIRED",
                )
    elif target == "WATCH":
        if prior_state == "ON":
            state, selected, reason = "DECAYING", prior_expert, "ACTIVE_EVIDENCE_WEAKENED"
        else:
            state, selected = "WATCH", candidate
    else:
        if prior_state == "ON":
            state, selected, reason = "DECAYING", prior_expert, "ACTIVE_EVIDENCE_FAILED"
        else:
            state, selected = "OFF", None
    return {
        "state": state,
        "selected_expert_id": selected,
        "candidate_expert_id": candidate,
        "reason_code": reason,
    }


def _project_opportunity(
    group: pd.DataFrame,
    *,
    sequence: str,
    profile: str,
    protocol: str,
    decision_index: int,
    decision_asof: pd.Timestamp,
    states: Mapping[str, Mapping[str, Any]],
    state_details: Mapping[str, Mapping[str, Mapping[str, Any]]],
    active: Dict[int, List[pd.Timestamp]],
    maximum_per_direction: int,
) -> Dict[str, Any]:
    records = group.sort_values(["expert_id", "outcome_id"]).to_dict("records")
    first = records[0]
    cells = sorted({str(row["context_cell"]) for row in records})
    on_cells = [cell for cell in cells if states[cell]["state"] == "ON"]
    matches = [
        row
        for row in records
        if states[str(row["context_cell"])]["state"] == "ON"
        and str(row["expert_id"])
        == states[str(row["context_cell"])]["selected_expert_id"]
    ]
    eligible = [row for row in matches if bool(row["capacity_eligible"])]
    eligible.sort(
        key=lambda row: (
            -round(
                float(
                    state_details[str(row["context_cell"])][str(row["expert_id"])][
                        "probability"
                    ]
                ),
                15,
            ),
            -round(
                float(
                    state_details[str(row["context_cell"])][str(row["expert_id"])][
                        "score"
                    ]
                ),
                15,
            ),
            str(row["expert_id"]),
            str(row["outcome_id"]),
        )
    )
    chosen = eligible[0] if eligible else None
    if chosen is None:
        if matches:
            reason = "SELECTED_EXPERT_SOURCE_CAPACITY_INELIGIBLE"
        elif on_cells:
            reason = "SELECTED_EXPERT_HAS_NO_SETUP"
        else:
            reason = "SELECTOR_NOT_ON"
        executed = False
        capacity_skipped = False
    else:
        direction = int(chosen["trade_direction"])
        entry_dt = pd.Timestamp(chosen["entry_dt"])
        active[direction] = [
            exit_dt for exit_dt in active[direction] if exit_dt > entry_dt
        ]
        if len(active[direction]) >= maximum_per_direction:
            executed = False
            capacity_skipped = True
            reason = "GLOBAL_DIRECTION_CAPACITY_LIMIT"
        else:
            executed = True
            capacity_skipped = False
            reason = "SELECTOR_EXPERT_EXECUTED"
            active[direction].append(pd.Timestamp(chosen["exit_dt"]))
            active[direction].sort()
    detail = (
        state_details[str(chosen["context_cell"])][str(chosen["expert_id"])]
        if chosen is not None
        else None
    )
    return {
        "schema_version": DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
        "sequence": sequence,
        "profile": profile,
        "protocol": protocol,
        "decision_index": decision_index,
        "decision_asof": decision_asof,
        "session_id": str(first["session_id"]),
        "physical_opportunity_id": str(first["physical_opportunity_id"]),
        "signal_dt": first["signal_dt"],
        "entry_dt": first["entry_dt"],
        "candidate_context_cells": cells,
        "on_context_cells": on_cells,
        "selected_candidate_count": len(eligible),
        "selected_expert_id": str(chosen["expert_id"]) if chosen is not None else None,
        "selected_outcome_id": str(chosen["outcome_id"]) if chosen is not None else None,
        "selected_lane_event_id": (
            str(chosen["lane_event_id"]) if chosen is not None else None
        ),
        "expert_lane_id": (
            str(chosen["expert_lane_id"]) if chosen is not None else None
        ),
        "mode": str(chosen["mode"]) if chosen is not None else None,
        "trade_direction": (
            int(chosen["trade_direction"]) if chosen is not None else None
        ),
        "exit_dt": chosen["exit_dt"] if chosen is not None else None,
        "exit_known_dt": chosen["exit_known_dt"] if chosen is not None else None,
        "selector_probability": detail["probability"] if detail else None,
        "discounted_evidence_r": detail["score"] if detail else None,
        "deduplication_conflicts": max(0, len(eligible) - 1),
        "capacity_eligible_in_source_lane": chosen is not None,
        "executed": executed,
        "capacity_skipped": capacity_skipped,
        "reason_code": reason,
        "net_pnl_ticks": (
            float(chosen["net_pnl_ticks"]) if chosen is not None and executed else None
        ),
    }


def _build_windows(
    sequence: str,
    profile: str,
    protocol: str,
    decision_index: int,
    decision_asof: pd.Timestamp,
    session_id: str,
    states: Mapping[str, Mapping[str, Any]],
    experts: Mapping[str, Mapping[str, Any]],
    cell_parts: Mapping[str, Mapping[str, str]],
) -> List[Dict[str, Any]]:
    active_rows = []
    for cell, state in states.items():
        if state["state"] != "ON" or not state.get("selected_expert_id"):
            continue
        expert_id = str(state["selected_expert_id"])
        expert = experts[expert_id]
        start, end = _bucket_bounds(cell_parts[cell]["time_bucket"])
        active_rows.append(
            {
                "cell": cell,
                "expert_id": expert_id,
                "start": start,
                "end": end,
                **expert,
            }
        )
    compatibility_fields = (
        "signal_side",
        "trend_state",
        "expert_lane_id",
        "timeframe",
        "lookback",
        "basis",
        "multiplier",
        "mode",
        "trade_direction",
    )
    grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in active_rows:
        grouped[tuple(row[field] for field in compatibility_fields)].append(row)
    output: List[Dict[str, Any]] = []
    for key in sorted(grouped, key=lambda value: tuple(map(str, value))):
        rows = sorted(grouped[key], key=lambda row: (row["start"], row["end"]))
        runs: List[List[Dict[str, Any]]] = []
        for row in rows:
            if runs and runs[-1][-1]["end"] == row["start"]:
                runs[-1].append(row)
            else:
                runs.append([row])
        for run in runs:
            sample = run[0]
            payload = {
                "sequence": sequence,
                "profile": profile,
                "protocol": protocol,
                "decision_index": decision_index,
                "signal_side": sample["signal_side"],
                "trend_state": sample["trend_state"],
                "start_time": run[0]["start"],
                "end_time": run[-1]["end"],
                "expert_lane_id": sample["expert_lane_id"],
                "mode": sample["mode"],
            }
            output.append(
                {
                    "schema_version": DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
                    "sequence": sequence,
                    "profile": profile,
                    "protocol": protocol,
                    "decision_index": decision_index,
                    "decision_asof": decision_asof,
                    "session_id": session_id,
                    "window_id": f"window:v1:{_sha256_json(payload)[:24]}",
                    "signal_side": sample["signal_side"],
                    "trend_state": sample["trend_state"],
                    "start_time": run[0]["start"],
                    "end_time": run[-1]["end"],
                    "time_buckets": [
                        cell_parts[row["cell"]]["time_bucket"] for row in run
                    ],
                    "expert_ids": [row["expert_id"] for row in run],
                    "expert_lane_id": sample["expert_lane_id"],
                    "timeframe": sample["timeframe"],
                    "lookback": sample["lookback"],
                    "basis": sample["basis"],
                    "multiplier": sample["multiplier"],
                    "mode": sample["mode"],
                    "trade_direction": sample["trade_direction"],
                }
            )
    return output


def _build_summary(
    sequence: str,
    profile: str,
    protocol: str,
    sessions: Sequence[str],
    frame: pd.DataFrame,
    state_rows: Sequence[Mapping[str, Any]],
    switch_rows: Sequence[Mapping[str, Any]],
    execution_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    oracle_summary: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    executed = [row for row in execution_rows if row["executed"]]
    pnls = [float(row["net_pnl_ticks"]) for row in executed]
    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    equity = high_water = max_drawdown = 0.0
    by_session: Dict[str, float] = defaultdict(float)
    for row, pnl in zip(executed, pnls):
        equity += pnl
        high_water = max(high_water, equity)
        max_drawdown = max(max_drawdown, high_water - equity)
        by_session[str(row["session_id"])] += pnl
    state_counts = Counter(str(row["state"]) for row in state_rows)
    missed_positive = 0.0
    by_physical = frame.loc[frame["capacity_eligible"]].groupby(
        "physical_opportunity_id"
    )["net_pnl_ticks"].max()
    for row in execution_rows:
        if not row["executed"]:
            missed_positive += max(
                0.0, float(by_physical.get(row["physical_opportunity_id"], 0.0))
            )

    regret: Dict[str, Any] = {
        "comparable_to_phase2_oracle": protocol == "daily_frozen"
        and bool(oracle_summary),
        "oracle_is_hindsight_only": True,
        "oracle_used_for_decisions": False,
        "selector_paper_reward_ticks": None,
        "selector_switch_penalties_ticks": None,
        "selector_penalized_objective_ticks": None,
        "bounded_oracle_penalized_objective_ticks": None,
        "dynamic_regret_ticks": None,
    }
    if protocol == "daily_frozen":
        latest = {
            (str(row["session_id"]), str(row["context_cell"])): row
            for row in state_rows
        }
        paper_reward = 0.0
        for (session, cell), row in latest.items():
            if row["state"] != "ON" or not row["selected_expert_id"]:
                continue
            selected = frame.loc[
                (frame["session_id"] == session)
                & (frame["context_cell"] == cell)
                & (frame["expert_id"] == row["selected_expert_id"])
                & frame["capacity_eligible"]
            ]
            paper_reward += float(selected["net_pnl_ticks"].sum())
        policy_changes = 0
        for _, group in pd.DataFrame(state_rows).groupby("context_cell"):
            prior: Optional[str] = None
            for row in group.sort_values("decision_index").to_dict("records"):
                policy = (
                    str(row["selected_expert_id"])
                    if row["state"] == "ON" and row["selected_expert_id"]
                    else "OFF"
                )
                if prior is not None and policy != prior:
                    policy_changes += 1
                prior = policy
        penalties = policy_changes * float(config["switch_penalty_ticks"])
        selector_objective = paper_reward - penalties
        regret.update(
            {
                "selector_paper_reward_ticks": paper_reward,
                "selector_switch_penalties_ticks": penalties,
                "selector_penalized_objective_ticks": selector_objective,
            }
        )
        if oracle_summary:
            oracle_value = float(
                oracle_summary["detectability_ceiling"][
                    "bounded_oracle_penalized_objective_ticks"
                ]
            )
            regret.update(
                {
                    "bounded_oracle_penalized_objective_ticks": oracle_value,
                    "dynamic_regret_ticks": oracle_value - selector_objective,
                }
            )
    activation_days = len(by_session)
    profitable_days = sum(value > 0.0 for value in by_session.values())
    return _json_safe(
        {
            "schema_version": DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
            "research_classification": "causal_development_replay",
            "sequence": sequence,
            "profile": profile,
            "protocol": protocol,
            "primary_row": profile == config["primary_profile"]
            and protocol == "daily_frozen",
            "sessions": len(sessions),
            "session_start": sessions[0],
            "session_end": sessions[-1],
            "configuration": {
                "profile": config["profiles"][profile],
                "initial_risk_ticks": config["initial_risk_ticks"],
                "reward_clip_r": config["reward_clip_r"],
                "learning_rate": config["learning_rate"],
                "minimum_effective_outcomes_on": config[
                    "minimum_effective_outcomes_on"
                ],
                "absolute_evidence_floor_r": config[
                    "absolute_evidence_floor_r"
                ],
            },
            "states": {
                "rows": len(state_rows),
                **{state.lower(): state_counts[state] for state in SELECTOR_STATES},
                "switch_rows": len(switch_rows),
                "distinct_selected_experts": len(
                    {
                        str(row["selected_expert_id"])
                        for row in state_rows
                        if row["selected_expert_id"]
                    }
                ),
            },
            "execution": {
                "physical_opportunities": len(execution_rows),
                "selected_candidates": sum(
                    row["selected_expert_id"] is not None for row in execution_rows
                ),
                "deduplication_conflicts": sum(
                    int(row["deduplication_conflicts"]) for row in execution_rows
                ),
                "capacity_skips": sum(
                    bool(row["capacity_skipped"]) for row in execution_rows
                ),
                "trades": len(executed),
                "activation_days": activation_days,
                "profitable_activation_days": profitable_days,
                "activation_day_precision_pct": (
                    100.0 * profitable_days / activation_days
                    if activation_days
                    else None
                ),
                "wins": sum(value > 0.0 for value in pnls),
                "win_rate_pct": (
                    100.0 * sum(value > 0.0 for value in pnls) / len(pnls)
                    if pnls
                    else None
                ),
                "net_ticks": sum(pnls),
                "average_trade_ticks": sum(pnls) / len(pnls) if pnls else None,
                "profit_factor": (
                    gross_profit / gross_loss
                    if gross_loss > 0.0
                    else (float("inf") if gross_profit > 0.0 else None)
                ),
                "maximum_drawdown_ticks": max_drawdown,
                "positive_paper_opportunity_missed_while_inactive_ticks": (
                    missed_positive
                ),
            },
            "dynamic_regret": regret,
            "causal_result": True,
            "forward_authorized": False,
        }
    )


def _bucket_bounds(value: str) -> Tuple[str, str]:
    parts = str(value).split("-", 1)
    if len(parts) != 2:
        raise DynamicSelectorError(f"invalid time bucket: {value}")
    return parts[0], parts[1]


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
    raise DynamicSelectorError(f"invalid capacity_eligible value: {value!r}")


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise DynamicSelectorError(
                f"selector row {index} violates stable schema; "
                f"missing={sorted(expected - set(row))}, "
                f"extra={sorted(set(row) - expected)}"
            )
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


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
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return value
    if value is pd.NA:
        return None
    return value
