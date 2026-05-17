"""External research intake helpers.

This package keeps outside research tools upstream of the deterministic
research ledger. Importers may turn a report into reviewable candidate drafts,
but they do not register hypotheses or mutate the ledger.
"""

from __future__ import annotations

from ta_foundation.research_intake.ldr import (
    LdrImportOptions,
    import_ldr_report,
)
from ta_foundation.research_intake.models import (
    CandidateValidation,
    IntakeBundle,
    IntakeCandidate,
    ResearchSource,
)

__all__ = [
    "CandidateValidation",
    "IntakeBundle",
    "IntakeCandidate",
    "LdrImportOptions",
    "ResearchSource",
    "import_ldr_report",
]
