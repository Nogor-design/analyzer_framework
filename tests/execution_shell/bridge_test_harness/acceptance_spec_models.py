"""
Typed models for the acceptance spec YAML schema.

Each YAML file in acceptance_specs/phase2/ maps to one SpecGroup.
Each SpecGroup contains one AcceptanceSpec per test_id.

Schema summary
--------------
EventMatcher   -- matches against log records or outbox events (and their fields)
StateAssertion -- checks a point-in-time field on health snapshot or state file
CheckpointRule -- governs how a human checkpoint affects the result
DowngradeRule  -- converts a specific failure to PASS_UNVERIFIED
AcceptanceSpec -- complete spec for one test case
SpecGroup      -- all specs in one YAML file (one per group A-G)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EventMatcher:
    """
    Matches one logical event in the evidence bundle.

    For source=log:   event_type, mode, pos, qty, message, contains, regex all apply.
    For source=outbox: status and any top-level or detail field apply.

    Field checks: if `field`, `op`, and `value` are all set, the evaluator checks
    `event.<field>` (or parsed detail for outbox) against `value` using `op`.

    proof_weight determines the matcher's contribution to overall proof_strength.
    Default weights by source: outbox=strong, log=moderate.
    """
    id: str
    source: str                         # log | outbox

    # -- log selectors
    event_type: Optional[str] = None    # LogRecord.event_type

    # -- outbox selectors
    status: Optional[str] = None        # OutboxEvent.status

    # -- shared substring/regex filters
    contains: Optional[str] = None      # substring in raw (log) or detail (outbox)
    regex: Optional[str] = None         # regex on raw (log) or detail (outbox)

    # -- field-level value assertion (applies to any source)
    field: Optional[str] = None
    op: Optional[str] = None            # eq|ne|in|not_in|contains|exists|missing|gt|gte|lt|lte
    value: Optional[Any] = None

    # -- classification
    proof_weight: str = "moderate"      # strong | moderate | weak
    optional: bool = False              # if True, no-match does not fail the test


@dataclass
class StateAssertion:
    """
    Checks a point-in-time field on a health snapshot or the state file.
    Does not match an event stream — always looks at the latest observed value.
    """
    id: str
    source: str                         # health | state_file
    field: str                          # attribute name on HealthSnapshot or ShellState
    op: str = "eq"                      # eq|ne|in|not_in|exists|missing|gt|gte|lt|lte
    value: Optional[Any] = None
    proof_weight: str = "weak"          # health/state are indirect — default weak
    optional: bool = False


@dataclass
class CheckpointRule:
    """
    Governs how a human checkpoint outcome affects the test result.
    Used only in SEMI_AUTOMATED tests.
    """
    checkpoint_label: str               # must match the label passed to checkpoint()
    required: bool = True
    if_missing: str = "pass_unverified" # pass_unverified | fail | ignore


@dataclass
class DowngradeRule:
    """
    When condition is met (an optional or expected event is missing), convert
    FAIL → PASS_UNVERIFIED instead.  Only `action: pass_unverified` is supported.
    """
    condition_type: str   # event_not_matched | state_not_matched | checkpoint_missing
    ref: str              # requirement_id (for events/state) or checkpoint_label
    action: str           # pass_unverified
    reason: str           # shown in report and downgrade_reason field


@dataclass
class AcceptanceSpec:
    """Complete acceptance specification for one Phase 2 test case."""
    test_id: str
    title: str
    severity: str                              # critical | high | normal | low
    automation_level: str                      # FULLY_AUTOMATED | SEMI_AUTOMATED | MANUAL_REQUIRED
    blocking_for_sim_readiness: bool
    pass_mode: str = "all_required"            # all_required | any_required | manual_only

    required_events: list[EventMatcher] = field(default_factory=list)
    forbidden_events: list[EventMatcher] = field(default_factory=list)
    required_state: list[StateAssertion] = field(default_factory=list)
    checkpoint_rules: list[CheckpointRule] = field(default_factory=list)
    downgrade_rules: list[DowngradeRule] = field(default_factory=list)

    timeouts: dict[str, float] = field(default_factory=dict)
    notes: str = ""


@dataclass
class SpecGroup:
    """All specs for one group (maps to one YAML file)."""
    group: str
    label: str
    specs: dict[str, AcceptanceSpec] = field(default_factory=dict)  # keyed by test_id
