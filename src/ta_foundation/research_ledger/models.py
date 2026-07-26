"""Typed records for the research ledger.

These dataclasses are read-side projections: repository methods return them
populated from rows. Writes use kwargs on repository methods, not these
dataclasses, so callers cannot accidentally mutate state by editing a
returned object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Family:
    family_id: str
    description: str
    legitimate_params_json: str
    mechanism_template: Optional[str]
    seeded_at: str


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    family: str
    instrument: str
    timeframe: str
    session_window: Optional[str]
    direction: Optional[str]
    params_json: str
    mechanism: str
    dedupe_hash: str
    registered_at: str
    registered_by: str
    parent_id: Optional[str]
    status: str


@dataclass(frozen=True)
class Run:
    run_id: str
    hypothesis_id: str
    mode: str
    config_hash: str
    yaml_path: str
    artifact_dir: str
    started_at: str
    completed_at: Optional[str]
    status: str
    error: Optional[str]


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    run_id: str
    hypothesis_id: str
    rank_in_run: int
    params_json: str
    n_trades_dev: Optional[int]
    pf_dev: Optional[float]
    expectancy_dev: Optional[float]
    n_trades_oos: Optional[int]
    pf_oos: Optional[float]
    expectancy_oos: Optional[float]
    n_trades_holdout: Optional[int]
    pf_holdout: Optional[float]
    expectancy_holdout: Optional[float]
    gate_verdict: str
    gate_reasons_json: Optional[str]
    slippage_stress_pass: Optional[int]
    folds_distribution: Optional[str]
    triage_state: Optional[str]
    triage_reason: Optional[str]
    triaged_at: Optional[str]
    triaged_by: Optional[str]
    holdout_attempted: int
    notes_json: Optional[str] = None


@dataclass(frozen=True)
class JournalEntry:
    journal_id: int
    ts: str
    role: str
    tool_name: str
    inputs_json: str
    output_summary: str
    output_artifact_path: Optional[str]
    duration_ms: int
    error: Optional[str]


@dataclass(frozen=True)
class ShadowSignal:
    signal_id: int
    candidate_id: str
    ts: str
    instrument: str
    direction: str
    planned_entry: Optional[float]
    planned_stop: Optional[float]
    planned_target: Optional[float]
    realized_outcome_json: Optional[str]
