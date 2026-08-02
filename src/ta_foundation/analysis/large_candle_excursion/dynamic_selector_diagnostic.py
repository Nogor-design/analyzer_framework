from __future__ import annotations

"""Post-replay diagnostics for the frozen Dynamic Phase 3 selector.

The module consumes existing outcome, state, execution, and summary ledgers.
It never reruns or changes the selector.  Hindsight best-static comparisons
are kept separate from causal execution and are labeled diagnostic only.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION = "dynamic_selector_diagnostic.v1"
PRIMARY_PROFILE = "Balanced"
PRIMARY_PROTOCOL = "daily_frozen"

GAP_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "cellwise_paper_reward_ticks",
    "switch_penalty_ticks",
    "penalized_selector_objective_ticks",
    "deduplicated_candidate_reward_ticks",
    "deduplication_impact_ticks",
    "capacity_impact_ticks",
    "executed_net_ticks",
    "execution_minus_penalized_objective_ticks",
    "accounting_reconciled",
)

TURNOVER_SUMMARY_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "policy_transitions",
    "first_activations",
    "same_expert_reactivations",
    "off_gap_opens",
    "direct_expert_replacements",
    "expert_replacements_after_off_gap",
    "expert_replacements_total",
    "same_session_policy_transitions",
    "same_session_event_updated_churn",
)

TURNOVER_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "context_cell",
    "decision_index",
    "decision_asof",
    "session_id",
    "previous_policy",
    "current_policy",
    "last_active_expert",
    "transition_category",
    "same_session_as_previous_boundary",
    "event_updated_intraday_churn",
)

DIVERGENCE_SUMMARY_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "divergence_class",
    "physical_opportunities",
    "daily_executed_trades",
    "event_executed_trades",
    "daily_net_ticks",
    "event_net_ticks",
    "event_minus_daily_ticks",
)

DIVERGENCE_LEDGER_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "physical_opportunity_id",
    "session_id",
    "signal_dt",
    "daily_selected_expert_id",
    "event_selected_expert_id",
    "daily_executed",
    "event_executed",
    "daily_net_ticks",
    "event_net_ticks",
    "event_minus_daily_ticks",
    "divergence_class",
)

STATIC_CELL_COLUMNS = (
    "schema_version",
    "sequence",
    "context_cell",
    "static_expert_id",
    "static_raw_net_ticks",
    "selector_raw_net_ticks",
    "selector_policy_changes",
    "selector_switch_penalty_ticks",
    "selector_penalized_objective_ticks",
    "static_relative_regret_ticks",
)

STATIC_SEQUENCE_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "context_cells",
    "best_static_per_cell_raw_net_ticks",
    "selector_raw_net_ticks",
    "selector_switch_penalty_ticks",
    "selector_penalized_objective_ticks",
    "static_relative_regret_ticks",
    "selector_beats_static",
)


class DynamicSelectorDiagnosticError(ValueError):
    """Raised when frozen replay ledgers cannot be reconciled."""


def analyze_sequence(
    outcome_rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    state_rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    execution_rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    selector_summaries: Sequence[Mapping[str, Any]],
    *,
    switch_penalty_ticks: float,
) -> Dict[str, List[Dict[str, Any]]]:
    """Build deterministic post-replay diagnostics for one sequence."""
    outcomes = _canonical_outcomes(outcome_rows)
    states = _canonical_states(state_rows)
    executions = _canonical_executions(execution_rows)
    summaries = [dict(row) for row in selector_summaries]
    sequences = set(outcomes["sequence"]) | set(states["sequence"]) | set(
        executions["sequence"]
    )
    if len(sequences) != 1:
        raise DynamicSelectorDiagnosticError(
            "diagnostic inputs must contain exactly one matching sequence"
        )
    sequence = str(next(iter(sequences)))
    expected_matrix = {
        (str(row["profile"]), str(row["protocol"])) for row in summaries
    }
    actual_matrix = set(
        states[["profile", "protocol"]].drop_duplicates().itertuples(
            index=False, name=None
        )
    )
    if expected_matrix != actual_matrix:
        raise DynamicSelectorDiagnosticError(
            "selector summary/state profile-protocol matrices do not match"
        )

    outcome_pnl = outcomes.set_index("outcome_id")["net_pnl_ticks"].to_dict()
    gap_rows: List[Dict[str, Any]] = []
    turnover_rows: List[Dict[str, Any]] = []
    turnover_summaries: List[Dict[str, Any]] = []
    for summary in summaries:
        profile = str(summary["profile"])
        protocol = str(summary["protocol"])
        subset_states = states.loc[
            (states["profile"] == profile) & (states["protocol"] == protocol)
        ].copy()
        subset_executions = executions.loc[
            (executions["profile"] == profile)
            & (executions["protocol"] == protocol)
        ].copy()
        transitions, transition_summary = classify_policy_turnover(subset_states)
        turnover_rows.extend(transitions)
        turnover_summaries.append(transition_summary)
        if protocol == "daily_frozen":
            gap_rows.append(
                build_gap_attribution(
                    outcomes,
                    subset_states,
                    subset_executions,
                    summary,
                    outcome_pnl=outcome_pnl,
                )
            )

    divergence_rows, divergence_summaries = build_live_divergence(executions)
    primary_states = states.loc[
        (states["profile"] == PRIMARY_PROFILE)
        & (states["protocol"] == PRIMARY_PROTOCOL)
    ].copy()
    primary_summary = next(
        (
            row
            for row in summaries
            if str(row["profile"]) == PRIMARY_PROFILE
            and str(row["protocol"]) == PRIMARY_PROTOCOL
        ),
        None,
    )
    if primary_summary is None:
        raise DynamicSelectorDiagnosticError(
            "primary Balanced daily-frozen selector summary is missing"
        )
    static_cells, static_sequence = build_static_cell_benchmark(
        outcomes,
        primary_states,
        switch_penalty_ticks=float(switch_penalty_ticks),
    )
    _validate_summary_reconciliation(
        primary_summary,
        next(
            row
            for row in gap_rows
            if row["profile"] == PRIMARY_PROFILE
            and row["protocol"] == PRIMARY_PROTOCOL
        ),
        static_sequence,
    )
    return {
        "gap_rows": _json_safe(gap_rows),
        "turnover_summary_rows": _json_safe(turnover_summaries),
        "turnover_rows": _json_safe(turnover_rows),
        "divergence_summary_rows": _json_safe(divergence_summaries),
        "divergence_rows": _json_safe(divergence_rows),
        "static_cell_rows": _json_safe(static_cells),
        "static_sequence_rows": _json_safe([static_sequence]),
    }


def build_gap_attribution(
    outcomes: pd.DataFrame,
    states: pd.DataFrame,
    executions: pd.DataFrame,
    summary: Mapping[str, Any],
    *,
    outcome_pnl: Mapping[str, float],
) -> Dict[str, Any]:
    """Reconcile paper reward, deduplication, capacity, and execution."""
    regret = dict(summary["dynamic_regret"])
    paper = float(regret["selector_paper_reward_ticks"])
    penalty = float(regret["selector_switch_penalties_ticks"])
    objective = float(regret["selector_penalized_objective_ticks"])
    chosen = executions.loc[executions["selected_outcome_id"].notna()].copy()
    unknown = sorted(set(chosen["selected_outcome_id"]) - set(outcome_pnl))
    if unknown:
        raise DynamicSelectorDiagnosticError(
            "execution ledger references outcomes absent from the outcome cube"
        )
    deduplicated = sum(
        float(outcome_pnl[str(outcome_id)])
        for outcome_id in chosen["selected_outcome_id"]
    )
    executed = float(
        executions.loc[executions["executed"], "net_pnl_ticks"].sum()
    )
    dedup_impact = deduplicated - paper
    capacity_impact = executed - deduplicated
    reconciled = bool(
        np.isclose(objective, paper - penalty, atol=1e-7)
        and np.isclose(
            executed,
            objective + penalty + dedup_impact + capacity_impact,
            atol=1e-7,
        )
    )
    if not reconciled:
        raise DynamicSelectorDiagnosticError(
            "selector gap attribution did not reconcile"
        )
    return {
        "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
        "sequence": str(summary["sequence"]),
        "profile": str(summary["profile"]),
        "protocol": str(summary["protocol"]),
        "cellwise_paper_reward_ticks": paper,
        "switch_penalty_ticks": penalty,
        "penalized_selector_objective_ticks": objective,
        "deduplicated_candidate_reward_ticks": deduplicated,
        "deduplication_impact_ticks": dedup_impact,
        "capacity_impact_ticks": capacity_impact,
        "executed_net_ticks": executed,
        "execution_minus_penalized_objective_ticks": executed - objective,
        "accounting_reconciled": reconciled,
    }


def classify_policy_turnover(
    states: pd.DataFrame,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Classify causal policy changes, including event-updated intraday churn."""
    if states.empty:
        raise DynamicSelectorDiagnosticError("selector state ledger is empty")
    sequence = str(states["sequence"].iloc[0])
    profile = str(states["profile"].iloc[0])
    protocol = str(states["protocol"].iloc[0])
    output: List[Dict[str, Any]] = []
    for cell, group in states.groupby("context_cell", sort=True):
        prior_policy = "OFF"
        prior_session: str | None = None
        last_active: str | None = None
        seen_active = False
        for row in group.sort_values(
            ["decision_index", "decision_asof"], kind="stable"
        ).to_dict("records"):
            current = (
                str(row["selected_expert_id"])
                if str(row["state"]) == "ON" and row["selected_expert_id"]
                else "OFF"
            )
            if current == prior_policy:
                prior_session = str(row["session_id"])
                continue
            same_session = prior_session == str(row["session_id"])
            if prior_policy == "OFF" and current != "OFF":
                if not seen_active:
                    category = "FIRST_ACTIVATION"
                elif last_active == current:
                    category = "SAME_EXPERT_REACTIVATION"
                else:
                    category = "EXPERT_REPLACEMENT_AFTER_OFF_GAP"
                seen_active = True
                last_active = current
            elif prior_policy != "OFF" and current == "OFF":
                category = "OFF_GAP_OPEN"
                last_active = prior_policy
            else:
                category = "DIRECT_EXPERT_REPLACEMENT"
                last_active = current
                seen_active = True
            output.append(
                {
                    "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
                    "sequence": sequence,
                    "profile": profile,
                    "protocol": protocol,
                    "context_cell": str(cell),
                    "decision_index": int(row["decision_index"]),
                    "decision_asof": row["decision_asof"],
                    "session_id": str(row["session_id"]),
                    "previous_policy": prior_policy,
                    "current_policy": current,
                    "last_active_expert": last_active,
                    "transition_category": category,
                    "same_session_as_previous_boundary": same_session,
                    "event_updated_intraday_churn": (
                        protocol == "event_updated" and same_session
                    ),
                }
            )
            prior_policy = current
            prior_session = str(row["session_id"])
    counts = Counter(str(row["transition_category"]) for row in output)
    same_session = sum(bool(row["same_session_as_previous_boundary"]) for row in output)
    churn = sum(bool(row["event_updated_intraday_churn"]) for row in output)
    summary = {
        "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
        "sequence": sequence,
        "profile": profile,
        "protocol": protocol,
        "policy_transitions": len(output),
        "first_activations": counts["FIRST_ACTIVATION"],
        "same_expert_reactivations": counts["SAME_EXPERT_REACTIVATION"],
        "off_gap_opens": counts["OFF_GAP_OPEN"],
        "direct_expert_replacements": counts["DIRECT_EXPERT_REPLACEMENT"],
        "expert_replacements_after_off_gap": counts[
            "EXPERT_REPLACEMENT_AFTER_OFF_GAP"
        ],
        "expert_replacements_total": counts["DIRECT_EXPERT_REPLACEMENT"]
        + counts["EXPERT_REPLACEMENT_AFTER_OFF_GAP"],
        "same_session_policy_transitions": same_session,
        "same_session_event_updated_churn": churn,
    }
    return output, summary


def build_live_divergence(
    executions: pd.DataFrame,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pair daily-frozen and event-updated opportunity decisions."""
    output: List[Dict[str, Any]] = []
    for (sequence, profile), group in executions.groupby(
        ["sequence", "profile"], sort=True
    ):
        daily = group.loc[group["protocol"] == "daily_frozen"].copy()
        event = group.loc[group["protocol"] == "event_updated"].copy()
        if daily["physical_opportunity_id"].duplicated().any() or event[
            "physical_opportunity_id"
        ].duplicated().any():
            raise DynamicSelectorDiagnosticError(
                "execution ledger has duplicate protocol/opportunity rows"
            )
        paired = daily.merge(
            event,
            on="physical_opportunity_id",
            how="outer",
            suffixes=("_daily", "_event"),
            validate="one_to_one",
            indicator=True,
        )
        if set(paired["_merge"]) != {"both"}:
            raise DynamicSelectorDiagnosticError(
                "daily-frozen and event-updated opportunity sets differ"
            )
        for row in paired.to_dict("records"):
            daily_expert = _optional_text(row.get("selected_expert_id_daily"))
            event_expert = _optional_text(row.get("selected_expert_id_event"))
            daily_executed = bool(row["executed_daily"])
            event_executed = bool(row["executed_event"])
            if daily_expert is None and event_expert is None:
                category = "MATCHED_INACTIVE"
            elif daily_expert is None:
                category = "EVENT_ACTIVATION"
            elif event_expert is None:
                category = "EVENT_DEACTIVATION"
            elif daily_expert != event_expert:
                category = "EVENT_EXPERT_REPLACEMENT"
            elif daily_executed != event_executed:
                category = "CAPACITY_EXECUTION_DIFFERENCE"
            else:
                category = "MATCHED_SAME_EXPERT"
            daily_pnl = (
                float(row["net_pnl_ticks_daily"]) if daily_executed else 0.0
            )
            event_pnl = (
                float(row["net_pnl_ticks_event"]) if event_executed else 0.0
            )
            output.append(
                {
                    "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
                    "sequence": str(sequence),
                    "profile": str(profile),
                    "physical_opportunity_id": str(
                        row["physical_opportunity_id"]
                    ),
                    "session_id": str(row["session_id_daily"]),
                    "signal_dt": row["signal_dt_daily"],
                    "daily_selected_expert_id": daily_expert,
                    "event_selected_expert_id": event_expert,
                    "daily_executed": daily_executed,
                    "event_executed": event_executed,
                    "daily_net_ticks": daily_pnl,
                    "event_net_ticks": event_pnl,
                    "event_minus_daily_ticks": event_pnl - daily_pnl,
                    "divergence_class": category,
                }
            )
    summary_rows: List[Dict[str, Any]] = []
    frame = pd.DataFrame(output)
    for (sequence, profile, category), group in frame.groupby(
        ["sequence", "profile", "divergence_class"], sort=True
    ):
        summary_rows.append(
            {
                "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
                "sequence": str(sequence),
                "profile": str(profile),
                "divergence_class": str(category),
                "physical_opportunities": len(group),
                "daily_executed_trades": int(group["daily_executed"].sum()),
                "event_executed_trades": int(group["event_executed"].sum()),
                "daily_net_ticks": float(group["daily_net_ticks"].sum()),
                "event_net_ticks": float(group["event_net_ticks"].sum()),
                "event_minus_daily_ticks": float(
                    group["event_minus_daily_ticks"].sum()
                ),
            }
        )
    return _json_safe(output), _json_safe(summary_rows)


def build_static_cell_benchmark(
    outcomes: pd.DataFrame,
    states: pd.DataFrame,
    *,
    switch_penalty_ticks: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compare the causal primary selector with best static expert per cell."""
    if states.empty:
        raise DynamicSelectorDiagnosticError("primary selector states are missing")
    eligible = outcomes.loc[outcomes["capacity_eligible"]].copy()
    expert_rewards = (
        eligible.groupby(["context_cell", "expert_id"], sort=True)[
            "net_pnl_ticks"
        ]
        .sum()
        .reset_index()
    )
    selected_by_session_cell = _latest_policy_by_session_cell(states)
    rows: List[Dict[str, Any]] = []
    for cell, cell_outcomes in eligible.groupby("context_cell", sort=True):
        candidates = expert_rewards.loc[
            expert_rewards["context_cell"] == cell
        ].sort_values(["net_pnl_ticks", "expert_id"], ascending=[False, True])
        best = candidates.iloc[0]
        if float(best["net_pnl_ticks"]) > 0.0:
            static_expert = str(best["expert_id"])
            static_reward = float(best["net_pnl_ticks"])
        else:
            static_expert = None
            static_reward = 0.0
        cell_states = states.loc[states["context_cell"] == cell].sort_values(
            ["decision_index", "decision_asof"], kind="stable"
        )
        policies = [
            (
                str(row["selected_expert_id"])
                if str(row["state"]) == "ON" and row["selected_expert_id"]
                else "OFF"
            )
            for row in cell_states.to_dict("records")
        ]
        policy_changes = sum(
            current != prior for prior, current in zip(policies, policies[1:])
        )
        selector_reward = 0.0
        for (session, selected_cell), expert in selected_by_session_cell.items():
            if selected_cell != str(cell) or expert == "OFF":
                continue
            selector_reward += float(
                cell_outcomes.loc[
                    (cell_outcomes["session_id"] == session)
                    & (cell_outcomes["expert_id"] == expert),
                    "net_pnl_ticks",
                ].sum()
            )
        penalty = policy_changes * float(switch_penalty_ticks)
        objective = selector_reward - penalty
        rows.append(
            {
                "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
                "sequence": str(outcomes["sequence"].iloc[0]),
                "context_cell": str(cell),
                "static_expert_id": static_expert,
                "static_raw_net_ticks": static_reward,
                "selector_raw_net_ticks": selector_reward,
                "selector_policy_changes": policy_changes,
                "selector_switch_penalty_ticks": penalty,
                "selector_penalized_objective_ticks": objective,
                "static_relative_regret_ticks": static_reward - objective,
            }
        )
    static_total = sum(float(row["static_raw_net_ticks"]) for row in rows)
    selector_raw = sum(float(row["selector_raw_net_ticks"]) for row in rows)
    penalties = sum(float(row["selector_switch_penalty_ticks"]) for row in rows)
    objective = selector_raw - penalties
    sequence_row = {
        "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
        "sequence": str(outcomes["sequence"].iloc[0]),
        "profile": PRIMARY_PROFILE,
        "protocol": PRIMARY_PROTOCOL,
        "context_cells": len(rows),
        "best_static_per_cell_raw_net_ticks": static_total,
        "selector_raw_net_ticks": selector_raw,
        "selector_switch_penalty_ticks": penalties,
        "selector_penalized_objective_ticks": objective,
        "static_relative_regret_ticks": static_total - objective,
        "selector_beats_static": objective > static_total,
    }
    return _json_safe(rows), _json_safe(sequence_row)


def build_panel_summary(
    diagnostics: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Summarize a complete seven-sequence diagnostic panel."""
    gap = pd.DataFrame(diagnostics["gap_rows"])
    turnover = pd.DataFrame(diagnostics["turnover_summary_rows"])
    divergence = pd.DataFrame(diagnostics["divergence_summary_rows"])
    static = pd.DataFrame(diagnostics["static_sequence_rows"])
    primary_gap = gap.loc[gap["profile"] == PRIMARY_PROFILE]
    primary_turnover = turnover.loc[turnover["profile"] == PRIMARY_PROFILE]
    primary_divergence = divergence.loc[
        divergence["profile"] == PRIMARY_PROFILE
    ]
    divergence_by_class = (
        primary_divergence.groupby("divergence_class")[
            ["physical_opportunities", "event_minus_daily_ticks"]
        ]
        .sum()
        .sort_values("event_minus_daily_ticks")
    )
    return _json_safe(
        {
            "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
            "research_phase": "dynamic_phase_4_post_replay_diagnostic",
            "research_classification": "frozen_replay_diagnostic_only",
            "primary_profile": PRIMARY_PROFILE,
            "primary_protocol": PRIMARY_PROTOCOL,
            "sequences": int(static["sequence"].nunique()),
            "primary_gap_attribution": {
                "cellwise_paper_reward_ticks": float(
                    primary_gap["cellwise_paper_reward_ticks"].sum()
                ),
                "switch_penalty_ticks": float(
                    primary_gap["switch_penalty_ticks"].sum()
                ),
                "deduplication_impact_ticks": float(
                    primary_gap["deduplication_impact_ticks"].sum()
                ),
                "capacity_impact_ticks": float(
                    primary_gap["capacity_impact_ticks"].sum()
                ),
                "executed_net_ticks": float(
                    primary_gap["executed_net_ticks"].sum()
                ),
            },
            "primary_static_comparison": {
                "best_static_per_cell_raw_net_ticks": float(
                    static["best_static_per_cell_raw_net_ticks"].sum()
                ),
                "selector_penalized_objective_ticks": float(
                    static["selector_penalized_objective_ticks"].sum()
                ),
                "static_relative_regret_ticks": float(
                    static["static_relative_regret_ticks"].sum()
                ),
                "sequences_beating_static": int(
                    static["selector_beats_static"].sum()
                ),
            },
            "primary_turnover": {
                protocol: {
                    column: int(group[column].sum())
                    for column in (
                        "policy_transitions",
                        "first_activations",
                        "same_expert_reactivations",
                        "off_gap_opens",
                        "expert_replacements_total",
                        "same_session_event_updated_churn",
                    )
                }
                for protocol, group in primary_turnover.groupby("protocol")
            },
            "primary_live_divergence": {
                "event_minus_daily_ticks": float(
                    primary_divergence["event_minus_daily_ticks"].sum()
                ),
                "by_class": [
                    {
                        "divergence_class": str(index),
                        "physical_opportunities": int(
                            row["physical_opportunities"]
                        ),
                        "event_minus_daily_ticks": float(
                            row["event_minus_daily_ticks"]
                        ),
                    }
                    for index, row in divergence_by_class.iterrows()
                ],
            },
            "selector_configuration_changed": False,
            "oracle_used_for_decisions": False,
            "family_b_exit_adaptation_unlocked": False,
            "forward_authorized": False,
        }
    )


def write_dynamic_selector_diagnostic(
    output_dir: Path,
    diagnostics: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    panel_summary: Mapping[str, Any],
    source_phase4_manifest: Mapping[str, Any],
    parity_record: Mapping[str, Any],
) -> Path:
    """Write an immutable, hash-bound post-replay diagnostic bundle."""
    root = Path(output_dir)
    if root.exists():
        raise FileExistsError(f"diagnostic output already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "gap_attribution": (
            "gap_attribution.csv",
            GAP_COLUMNS,
            diagnostics["gap_rows"],
        ),
        "turnover_summary": (
            "turnover_summary.csv",
            TURNOVER_SUMMARY_COLUMNS,
            diagnostics["turnover_summary_rows"],
        ),
        "turnover_ledger": (
            "turnover_transition_ledger.csv",
            TURNOVER_LEDGER_COLUMNS,
            diagnostics["turnover_rows"],
        ),
        "live_divergence_summary": (
            "live_divergence_summary.csv",
            DIVERGENCE_SUMMARY_COLUMNS,
            diagnostics["divergence_summary_rows"],
        ),
        "live_divergence_ledger": (
            "live_divergence_ledger.csv",
            DIVERGENCE_LEDGER_COLUMNS,
            diagnostics["divergence_rows"],
        ),
        "static_cell_benchmark": (
            "static_cell_benchmark.csv",
            STATIC_CELL_COLUMNS,
            diagnostics["static_cell_rows"],
        ),
        "static_sequence_summary": (
            "static_sequence_summary.csv",
            STATIC_SEQUENCE_COLUMNS,
            diagnostics["static_sequence_rows"],
        ),
    }
    records: Dict[str, Any] = {}
    for name, (filename, columns, rows) in artifacts.items():
        path = root / filename
        _write_csv(path, columns, rows)
        records[name] = _artifact_record(root, path)
    summary_path = root / "diagnostic_summary.json"
    _write_json(summary_path, panel_summary)
    records["summary"] = _artifact_record(root, summary_path)
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_SELECTOR_DIAGNOSTIC_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_4_post_replay_diagnostic",
        "research_classification": "frozen_replay_diagnostic_only",
        "source_phase4": {
            "schema_version": source_phase4_manifest.get("schema_version"),
            "manifest_sha256": source_phase4_manifest.get("manifest_sha256"),
        },
        "contracts": {
            "selector_replayed": False,
            "selector_configuration_changed": False,
            "oracle_used_for_decisions": False,
            "primary_profile": PRIMARY_PROFILE,
            "primary_protocol": PRIMARY_PROTOCOL,
            "family_b_exit_adaptation_unlocked": False,
            "forward_authorized": False,
        },
        "parity_gate": dict(parity_record),
        "counts": {
            key: len(list(diagnostics[value]))
            for key, value in (
                ("gap_rows", "gap_rows"),
                ("turnover_summary_rows", "turnover_summary_rows"),
                ("turnover_rows", "turnover_rows"),
                ("divergence_summary_rows", "divergence_summary_rows"),
                ("divergence_rows", "divergence_rows"),
                ("static_cell_rows", "static_cell_rows"),
                ("static_sequence_rows", "static_sequence_rows"),
            )
        },
        "summary_sha256": _sha256_json(panel_summary),
        "artifacts": records,
        "forward_authorized": False,
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _write_json(root / "diagnostic_manifest.json", manifest)
    return root


def _canonical_outcomes(
    value: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = _frame(value)
    required = {
        "sequence",
        "outcome_id",
        "physical_opportunity_id",
        "context_cell",
        "session_id",
        "expert_id",
        "net_pnl_ticks",
        "capacity_eligible",
    }
    _require_columns(frame, required, "outcome")
    frame["net_pnl_ticks"] = pd.to_numeric(
        frame["net_pnl_ticks"], errors="raise"
    ).astype(float)
    frame["capacity_eligible"] = frame["capacity_eligible"].map(_truth_value)
    if frame["outcome_id"].duplicated().any():
        raise DynamicSelectorDiagnosticError("outcome IDs must be unique")
    return frame


def _canonical_states(
    value: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = _frame(value)
    required = {
        "sequence",
        "profile",
        "protocol",
        "decision_index",
        "decision_asof",
        "session_id",
        "context_cell",
        "state",
        "selected_expert_id",
    }
    _require_columns(frame, required, "state")
    frame["decision_index"] = pd.to_numeric(
        frame["decision_index"], errors="raise"
    ).astype(int)
    frame["selected_expert_id"] = frame["selected_expert_id"].map(
        _optional_text
    )
    return frame


def _canonical_executions(
    value: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = _frame(value)
    required = {
        "sequence",
        "profile",
        "protocol",
        "physical_opportunity_id",
        "session_id",
        "signal_dt",
        "selected_expert_id",
        "selected_outcome_id",
        "executed",
        "net_pnl_ticks",
    }
    _require_columns(frame, required, "execution")
    frame["selected_expert_id"] = frame["selected_expert_id"].map(
        _optional_text
    )
    frame["selected_outcome_id"] = frame["selected_outcome_id"].map(
        _optional_text
    )
    frame["executed"] = frame["executed"].map(_truth_value)
    frame["net_pnl_ticks"] = pd.to_numeric(
        frame["net_pnl_ticks"], errors="coerce"
    )
    if frame.loc[frame["executed"], "net_pnl_ticks"].isna().any():
        raise DynamicSelectorDiagnosticError(
            "executed rows require finite net_pnl_ticks"
        )
    return frame


def _latest_policy_by_session_cell(
    states: pd.DataFrame,
) -> Dict[Tuple[str, str], str]:
    latest = states.sort_values(
        ["decision_index", "decision_asof"], kind="stable"
    ).drop_duplicates(["session_id", "context_cell"], keep="last")
    return {
        (str(row["session_id"]), str(row["context_cell"])): (
            str(row["selected_expert_id"])
            if str(row["state"]) == "ON" and row["selected_expert_id"]
            else "OFF"
        )
        for row in latest.to_dict("records")
    }


def _validate_summary_reconciliation(
    summary: Mapping[str, Any],
    gap: Mapping[str, Any],
    static: Mapping[str, Any],
) -> None:
    regret = summary["dynamic_regret"]
    execution = summary["execution"]
    checks = (
        (
            gap["cellwise_paper_reward_ticks"],
            regret["selector_paper_reward_ticks"],
        ),
        (
            gap["penalized_selector_objective_ticks"],
            regret["selector_penalized_objective_ticks"],
        ),
        (gap["executed_net_ticks"], execution["net_ticks"]),
        (
            static["selector_penalized_objective_ticks"],
            regret["selector_penalized_objective_ticks"],
        ),
    )
    if not all(
        np.isclose(float(left), float(right), atol=1e-7)
        for left, right in checks
    ):
        raise DynamicSelectorDiagnosticError(
            "reconstructed diagnostic values do not match selector summary"
        )


def _frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame([dict(row) for row in value])


def _require_columns(
    frame: pd.DataFrame, required: set[str], label: str
) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise DynamicSelectorDiagnosticError(
            f"{label} ledger is missing columns: {', '.join(missing)}"
        )
    if frame.empty:
        raise DynamicSelectorDiagnosticError(f"{label} ledger is empty")


def _optional_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _truth_value(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    frame = pd.DataFrame([dict(row) for row in rows])
    for column in columns:
        if column not in frame:
            frame[column] = None
    frame.loc[:, list(columns)].to_csv(path, index=False, lineterminator="\n")


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


def _artifact_record(root: Path, path: Path) -> Dict[str, Any]:
    return {
        "filename": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


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
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        if not np.isfinite(value):
            return None
        return float(value)
    return value
