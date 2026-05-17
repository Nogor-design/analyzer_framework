"""Research ledger — structured persistence for hypotheses, runs, and candidates.

This package is the substrate the agentic research program (see
docs/designs/agentic_research_program.md) reads and writes through. It is
deliberately not LLM-aware: it is a typed data layer over a SQLite database
with forward-only migrations.

Public surface:
    - get_repository(db_path) -> Repository
    - Repository methods (see repository.py docstrings)
    - models: Hypothesis, Run, Candidate, ShadowSignal, JournalEntry, Family
    - exceptions: DuplicateHypothesisError, HoldoutAlreadyAttemptedError,
      LedgerIntegrityError
"""

from __future__ import annotations

from ta_foundation.research_ledger.db import (
    DEFAULT_DB_PATH,
    LedgerIntegrityError,
    connect,
    init_db,
)
from ta_foundation.research_ledger.models import (
    Candidate,
    Family,
    Hypothesis,
    JournalEntry,
    Run,
    ShadowSignal,
)
from ta_foundation.research_ledger.family_registry import (
    FamilySpec,
    ParamSpec,
    ParamViolation,
    get_family_spec,
    list_probe_families,
    validate_params,
)
from ta_foundation.research_ledger.pre_registration import (
    DriftReport,
    PreRegistrationBlock,
    check_drift,
    check_yaml_file,
    extract_pre_registration_block,
)
from ta_foundation.research_ledger.repository import (
    DuplicateHypothesisError,
    HoldoutAlreadyAttemptedError,
    Repository,
    get_repository,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "Candidate",
    "DriftReport",
    "DuplicateHypothesisError",
    "Family",
    "FamilySpec",
    "HoldoutAlreadyAttemptedError",
    "Hypothesis",
    "JournalEntry",
    "LedgerIntegrityError",
    "ParamSpec",
    "ParamViolation",
    "PreRegistrationBlock",
    "Repository",
    "Run",
    "ShadowSignal",
    "check_drift",
    "check_yaml_file",
    "connect",
    "extract_pre_registration_block",
    "get_family_spec",
    "get_repository",
    "init_db",
    "list_probe_families",
    "validate_params",
]
