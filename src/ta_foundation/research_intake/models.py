"""Typed records for external research intake."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ResearchSource:
    """Provenance for an external research artifact."""

    tool: str
    run_id: str
    report_path: str
    imported_at: str = field(default_factory=utc_now_iso)
    predecessor_run_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    extracted_text_path: Optional[str] = None


@dataclass(frozen=True)
class IntakeCandidate:
    """A reviewable candidate, not a registered hypothesis."""

    title: str
    family: str
    instrument: str
    timeframe: str
    session_window: Optional[str]
    direction: Optional[str]
    params: dict[str, Any]
    mechanism: str
    falsifiable_claim: str
    adverse_tests: list[str]
    source_confidence: str
    source_notes: list[str]
    status: str = "draft"
    rationale: str = ""


@dataclass(frozen=True)
class CandidateValidation:
    """Registry/authoring readiness checks for an intake candidate."""

    title: str
    family: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntakeBundle:
    """Complete output of an intake pass."""

    source: ResearchSource
    candidates: list[IntakeCandidate]
    validations: list[CandidateValidation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": asdict(self.source),
            "summary": self.summary,
            "warnings": list(self.warnings),
            "validations": [asdict(v) for v in self.validations],
            "candidates": [asdict(c) for c in self.candidates],
        }


def as_posixish(path: Path | str) -> str:
    """Render a path consistently without requiring it to exist."""
    return str(Path(path))
