"""
Acceptance spec evaluator and EvidenceBundle.

EvidenceBundle   -- collects log records, outbox events, health, checkpoints
AcceptanceEvaluator.evaluate(spec, bundle) -> AcceptanceResult

The evaluator is stateless and deterministic: given the same spec and bundle
it always produces the same result.  It does NOT do any waiting or I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from bridge_test_harness.acceptance_spec_models import (
    AcceptanceSpec, EventMatcher, StateAssertion,
    CheckpointRule, DowngradeRule,
)
from bridge_test_harness.acceptance_result import (
    AcceptanceResult, AcceptanceStatus, ProofStrength,
    EvidenceMatch, UnmetRequirement,
)
from bridge_test_harness.checkpoint import CheckpointResult, CheckpointOutcome

if TYPE_CHECKING:
    from bridge_test_harness.log_parser import LogRecord
    from bridge_test_harness.outbox_parser import OutboxEvent
    from bridge_test_harness.health_parser import HealthSnapshot
    from bridge_test_harness.state_parser import ShellState


# ---------------------------------------------------------------------------
# Evidence bundle
# ---------------------------------------------------------------------------

@dataclass
class EvidenceBundle:
    """
    All observable evidence collected during one Phase 2 test run window.

    Collect via EvidenceBundle.collect() after all evidence-gathering waits
    have completed, BEFORE any cleanup signals are sent.
    """
    log_records: list = field(default_factory=list)      # list[LogRecord]
    outbox_events: list = field(default_factory=list)    # list[OutboxEvent]
    health_snapshots: list = field(default_factory=list) # list[HealthSnapshot]
    checkpoints: list = field(default_factory=list)      # list[CheckpointResult]
    state: Optional[Any] = None                          # ShellState | None

    @classmethod
    def collect(
        cls,
        ctx: Any,          # TestContext
        lb: int,           # log baseline (line number before test)
        ob: int,           # outbox baseline (event count before test)
        checkpoints: Optional[list] = None,
        include_health: bool = True,
        include_state: bool = False,
    ) -> "EvidenceBundle":
        """
        Snapshot all evidence in the test window [lb, ob] → current.
        Call this BEFORE sending any cleanup signals (EXIT_ALL, etc.).
        """
        from bridge_test_harness import log_parser, outbox_parser
        from bridge_test_harness.health_parser import (
            get_latest_health, get_current_position_from_log,
        )

        log_records = log_parser.read_records(ctx.log_file, lb)
        outbox_events = outbox_parser.events_since(ctx.outbox, ob)

        health_snaps = []
        if include_health:
            health = get_latest_health(ctx.log_file, since_line=lb)
            if health is not None:
                health_snaps = [health]

        state = None
        if include_state:
            try:
                from bridge_test_harness import state_parser
                state = state_parser.read_state(ctx.state_file)
            except Exception:
                pass

        return cls(
            log_records=log_records,
            outbox_events=outbox_events,
            health_snapshots=health_snaps,
            checkpoints=checkpoints or [],
            state=state,
        )


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class AcceptanceEvaluator:
    """
    Static evaluation engine.  All methods are class-level; no instance state.
    """

    @classmethod
    def evaluate(cls, spec: AcceptanceSpec, bundle: EvidenceBundle) -> AcceptanceResult:
        """
        Evaluate spec against bundle and return a fully populated AcceptanceResult.
        """
        # -- manual_only: always MANUAL_REQUIRED, no evaluation needed
        if spec.pass_mode == "manual_only":
            return AcceptanceResult(
                test_id=spec.test_id,
                status=AcceptanceStatus.MANUAL_REQUIRED,
                proof_strength=ProofStrength.NONE,
                automation_level=spec.automation_level,
                blocking_for_sim_readiness=spec.blocking_for_sim_readiness,
                notes=spec.notes,
            )

        # -- 1. Evaluate required events
        matched: list[EvidenceMatch] = []
        unmet: list[UnmetRequirement] = []

        for matcher in spec.required_events:
            desc = cls._match_event(matcher, bundle)
            if desc is not None:
                matched.append(EvidenceMatch(
                    requirement_id=matcher.id,
                    source=matcher.source,
                    description=desc,
                    proof_weight=matcher.proof_weight,
                ))
            elif not matcher.optional:
                unmet.append(UnmetRequirement(
                    requirement_id=matcher.id,
                    description=cls._describe_matcher(matcher),
                    optional=False,
                ))

        # -- 2. Check forbidden events
        forbidden_matches: list[EvidenceMatch] = []
        for matcher in spec.forbidden_events:
            desc = cls._match_event(matcher, bundle)
            if desc is not None:
                forbidden_matches.append(EvidenceMatch(
                    requirement_id=matcher.id,
                    source=matcher.source,
                    description=f"FORBIDDEN event present: {desc}",
                    proof_weight=matcher.proof_weight,
                ))

        # -- 3. Evaluate required_state
        state_assertions: list[tuple[str, bool, str]] = []
        for assertion in spec.required_state:
            passed, detail = cls._check_state(assertion, bundle)
            state_assertions.append((assertion.id, passed, detail))
            if not passed and not assertion.optional:
                unmet.append(UnmetRequirement(
                    requirement_id=assertion.id,
                    description=f"state {assertion.source}.{assertion.field}: {detail}",
                    optional=False,
                ))

        # -- 4. Evaluate checkpoint rules
        human_confirmation_used = any(
            c.outcome in (CheckpointOutcome.PASSED, CheckpointOutcome.AUTO)
            for c in bundle.checkpoints
        )
        cp_downgrade_labels: list[str] = []  # checkpoint labels needing downgrade
        for cp_rule in spec.checkpoint_rules:
            if cp_rule.if_missing == "ignore":
                continue
            matched_cp = cls._find_checkpoint(cp_rule.checkpoint_label, bundle.checkpoints)
            cp_confirmed = (
                matched_cp is not None
                and matched_cp.outcome in (CheckpointOutcome.PASSED, CheckpointOutcome.AUTO)
            )
            if not cp_confirmed and cp_rule.required:
                if cp_rule.if_missing == "fail":
                    unmet.append(UnmetRequirement(
                        requirement_id=f"cp:{cp_rule.checkpoint_label}",
                        description=f"Required checkpoint {cp_rule.checkpoint_label!r} not confirmed",
                        optional=False,
                    ))
                else:  # pass_unverified
                    cp_downgrade_labels.append(cp_rule.checkpoint_label)

        # -- 5. Apply downgrade rules
        downgrade_reasons: list[str] = []

        for dr in spec.downgrade_rules:
            if dr.condition_type == "event_not_matched":
                targets = [u for u in unmet if u.requirement_id == dr.ref and not u.optional]
                for u in targets:
                    u.downgraded = True
                    downgrade_reasons.append(dr.reason)

            elif dr.condition_type == "state_not_matched":
                targets = [u for u in unmet if u.requirement_id == dr.ref and not u.optional]
                for u in targets:
                    u.downgraded = True
                    downgrade_reasons.append(dr.reason)

            elif dr.condition_type == "checkpoint_missing":
                if dr.ref in cp_downgrade_labels:
                    downgrade_reasons.append(dr.reason)
                    cp_downgrade_labels.remove(dr.ref)

        # Unresolved cp_downgrade_labels (if_missing=pass_unverified but no matching downgrade_rule)
        if cp_downgrade_labels:
            downgrade_reasons.append(
                f"Unconfirmed checkpoints: {cp_downgrade_labels}"
            )

        downgrade_reason = "; ".join(downgrade_reasons) if downgrade_reasons else None

        # -- 6. Determine status
        blocking_unmet = [u for u in unmet if not u.optional and not u.downgraded]
        has_forbidden   = bool(forbidden_matches)
        has_downgraded  = any(u.downgraded for u in unmet) or bool(cp_downgrade_labels)

        if has_forbidden or blocking_unmet:
            status = AcceptanceStatus.FAIL
        elif spec.pass_mode == "any_required" and not matched:
            status = AcceptanceStatus.FAIL
        elif has_downgraded or downgrade_reason:
            status = AcceptanceStatus.PASS_UNVERIFIED
        else:
            status = AcceptanceStatus.PASS

        # -- 7. Compute proof_strength
        if status in (AcceptanceStatus.FAIL, AcceptanceStatus.MANUAL_REQUIRED):
            proof_strength = ProofStrength.NONE
        elif status == AcceptanceStatus.SKIP:
            proof_strength = ProofStrength.NONE
        else:
            weights = [m.proof_weight for m in matched]
            for (aid, apassed, _) in state_assertions:
                if apassed:
                    sa = next((s for s in spec.required_state if s.id == aid), None)
                    if sa:
                        weights.append(sa.proof_weight)
            if not weights:
                proof_strength = ProofStrength.WEAK
            elif all(w == "strong" for w in weights):
                proof_strength = ProofStrength.STRONG
            elif all(w in ("strong", "moderate") for w in weights):
                proof_strength = ProofStrength.MODERATE
            else:
                proof_strength = ProofStrength.WEAK

        return AcceptanceResult(
            test_id=spec.test_id,
            status=status,
            proof_strength=proof_strength,
            automation_level=spec.automation_level,
            blocking_for_sim_readiness=spec.blocking_for_sim_readiness,
            matched_requirements=matched,
            unmet_requirements=unmet,
            forbidden_matches=forbidden_matches,
            state_assertions=state_assertions,
            human_confirmation_used=human_confirmation_used,
            downgrade_reason=downgrade_reason,
            notes=spec.notes,
        )

    # ------------------------------------------------------------------
    # Event matching
    # ------------------------------------------------------------------

    @classmethod
    def _match_event(cls, matcher: EventMatcher, bundle: EvidenceBundle) -> Optional[str]:
        """Return a human-readable match description, or None if not matched."""
        if matcher.source == "log":
            rec = cls._match_log(matcher, bundle.log_records)
            if rec is not None:
                return f"log[{rec.line_number}] {rec.event_type}"
        elif matcher.source == "outbox":
            evt = cls._match_outbox(matcher, bundle.outbox_events)
            if evt is not None:
                return f"outbox {evt.status} ({evt.file_path.name})"
        return None

    @classmethod
    def _match_log(cls, matcher: EventMatcher, records: list) -> Optional[Any]:
        for rec in records:
            if matcher.event_type and rec.event_type != matcher.event_type:
                continue
            if matcher.contains and matcher.contains not in rec.raw:
                continue
            if matcher.regex and not re.search(matcher.regex, rec.raw):
                continue
            if matcher.field and matcher.op is not None:
                val = getattr(rec, matcher.field, None)
                if not _apply_op(val, matcher.op, matcher.value):
                    continue
            return rec
        return None

    @classmethod
    def _match_outbox(cls, matcher: EventMatcher, events: list) -> Optional[Any]:
        for evt in events:
            if matcher.status and evt.status != matcher.status:
                continue
            if matcher.contains:
                haystack = (evt.detail or "") + (evt.instruction_id or "")
                if matcher.contains not in haystack:
                    continue
            if matcher.regex and not re.search(matcher.regex, evt.detail or ""):
                continue
            if matcher.field and matcher.op is not None:
                val = _get_outbox_field(evt, matcher.field)
                if not _apply_op(val, matcher.op, matcher.value):
                    continue
            return evt
        return None

    # ------------------------------------------------------------------
    # State checking
    # ------------------------------------------------------------------

    @classmethod
    def _check_state(
        cls,
        assertion: StateAssertion,
        bundle: EvidenceBundle,
    ) -> tuple[bool, str]:
        """Returns (passed, detail_string)."""
        if assertion.source == "health":
            if not bundle.health_snapshots:
                return False, "no health snapshot in bundle"
            health = bundle.health_snapshots[-1]
            val = getattr(health, assertion.field, None)
            passed = _apply_op(val, assertion.op, assertion.value)
            return passed, f"{assertion.field}={val!r} op={assertion.op} expected={assertion.value!r}"

        if assertion.source == "state_file":
            if bundle.state is None:
                return False, "no state file in bundle"
            val = getattr(bundle.state, assertion.field, None)
            passed = _apply_op(val, assertion.op, assertion.value)
            return passed, f"{assertion.field}={val!r} op={assertion.op} expected={assertion.value!r}"

        return False, f"unknown state source {assertion.source!r}"

    # ------------------------------------------------------------------
    # Checkpoint lookup
    # ------------------------------------------------------------------

    @classmethod
    def _find_checkpoint(
        cls,
        label: str,
        checkpoints: list[CheckpointResult],
    ) -> Optional[CheckpointResult]:
        """Find checkpoint by exact label or label containment."""
        for cp in checkpoints:
            if cp.label == label or label in cp.label:
                return cp
        return None

    # ------------------------------------------------------------------
    # Description helpers
    # ------------------------------------------------------------------

    @classmethod
    def _describe_matcher(cls, m: EventMatcher) -> str:
        parts = [f"source={m.source}"]
        if m.event_type:
            parts.append(f"event_type={m.event_type!r}")
        if m.status:
            parts.append(f"status={m.status!r}")
        if m.contains:
            parts.append(f"contains={m.contains!r}")
        if m.field and m.op:
            parts.append(f"{m.field} {m.op} {m.value!r}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _get_outbox_field(evt: Any, field_name: str) -> Any:
    """Try top-level OutboxEvent attribute first, then parse detail."""
    if hasattr(evt, field_name):
        return getattr(evt, field_name)
    # Parse detail as semicolon-separated key=value pairs
    from bridge_test_harness.health_parser import parse_detail
    detail_dict = parse_detail(evt.detail or "")
    return detail_dict.get(field_name)


def _apply_op(actual: Any, op: str, expected: Any) -> bool:
    """Apply comparison operator. Type-tolerant."""
    if op == "exists":
        return actual is not None and str(actual) not in ("", "<null>", "None")
    if op == "missing":
        return actual is None or str(actual) in ("", "<null>", "None")
    if actual is None:
        return False

    # String-level comparisons
    if op == "eq":
        return actual == expected or str(actual) == str(expected)
    if op == "ne":
        return actual != expected and str(actual) != str(expected)
    if op == "contains":
        return str(expected) in str(actual)
    if op == "not_contains":
        return str(expected) not in str(actual)
    if op == "in":
        return actual in expected or str(actual) in [str(v) for v in (expected or [])]
    if op == "not_in":
        exp_list = expected or []
        return actual not in exp_list and str(actual) not in [str(v) for v in exp_list]

    # Numeric comparisons
    try:
        a = float(actual)
        e = float(expected)
        if op == "gt":  return a > e
        if op == "gte": return a >= e
        if op == "lt":  return a < e
        if op == "lte": return a <= e
    except (ValueError, TypeError):
        pass

    return False
