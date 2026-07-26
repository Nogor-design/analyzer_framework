"""
Normalized result types for the acceptance spec engine.

These are the output types produced by AcceptanceEvaluator.evaluate().
They carry richer metadata than Phase2TestResult and feed the Phase 2
acceptance summary report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AcceptanceStatus(str, Enum):
    PASS             = "PASS"
    FAIL             = "FAIL"
    SKIP             = "SKIP"
    PASS_UNVERIFIED  = "PASS_UNVERIFIED"   # passed but with reduced proof
    MANUAL_REQUIRED  = "MANUAL_REQUIRED"   # spec says manual_only


class ProofStrength(str, Enum):
    STRONG   = "strong"    # all matched evidence is outbox/log strong-weight
    MODERATE = "moderate"  # at least one moderate-weight match; no weak
    WEAK     = "weak"      # some or all evidence is weak (health/state indirect)
    NONE     = "none"      # FAIL or SKIP — proof does not apply


@dataclass
class EvidenceMatch:
    """A single requirement that was successfully matched in the evidence bundle."""
    requirement_id: str
    source: str           # log | outbox | health | state_file | checkpoint
    description: str      # human-readable match description
    proof_weight: str     # strong | moderate | weak

    def __str__(self) -> str:
        return f"[{self.proof_weight}] {self.requirement_id}: {self.description}"


@dataclass
class UnmetRequirement:
    """A required event or state assertion that was NOT satisfied."""
    requirement_id: str
    description: str
    optional: bool = False    # optional matchers don't fail the test
    downgraded: bool = False  # True when a DowngradeRule covered this miss

    def __str__(self) -> str:
        tags: list[str] = []
        if self.optional:
            tags.append("optional")
        if self.downgraded:
            tags.append("downgraded->PASS_UNVERIFIED")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        return f"{self.requirement_id}{tag_str}: {self.description}"


@dataclass
class AcceptanceResult:
    """
    The complete result of evaluating one acceptance spec against an evidence bundle.

    Produced by AcceptanceEvaluator.evaluate(spec, bundle).
    Embedded in Phase2TestResult.acceptance_result.
    """
    test_id: str
    status: AcceptanceStatus
    proof_strength: ProofStrength
    automation_level: str           # FULLY_AUTOMATED | SEMI_AUTOMATED | MANUAL_REQUIRED
    blocking_for_sim_readiness: bool

    matched_requirements: list[EvidenceMatch] = field(default_factory=list)
    unmet_requirements: list[UnmetRequirement] = field(default_factory=list)
    forbidden_matches: list[EvidenceMatch] = field(default_factory=list)
    state_assertions: list[tuple[str, bool, str]] = field(default_factory=list)  # (id, passed, detail)

    human_confirmation_used: bool = False
    downgrade_reason: Optional[str] = None
    notes: str = ""
    elapsed_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status in (AcceptanceStatus.PASS, AcceptanceStatus.PASS_UNVERIFIED)

    @property
    def skipped(self) -> bool:
        return self.status in (AcceptanceStatus.SKIP, AcceptanceStatus.MANUAL_REQUIRED)

    def summary_line(self) -> str:
        """One-line console-friendly summary."""
        strength = f" [{self.proof_strength.value}]" if self.passed else ""
        blocking = " [BLOCKING]" if self.blocking_for_sim_readiness and not self.passed else ""
        return f"{self.status.value}{strength}{blocking} — {self.test_id}"
