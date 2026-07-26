"""Account-risk layer (Phase 2) — per-firm trailing-drawdown engine.

The business differentiator: keep each prop/funded account alive under its firm's
exact trailing-drawdown rules. ``account_state`` holds the verified versioned
firm config + live state; ``dd_engine`` is the firm-agnostic state machine
(cushion / lock / violation / daily risk budget). See
``docs/designs/account_risk_engine.md``. Gated on a real-account replay before live.
"""
from __future__ import annotations

from .account_state import (
    AccountState,
    FirmProfile,
    SizeRules,
    load_firm_profile,
)
from .allocator import (
    AccountSpec,
    Allocation,
    AllocatorConfig,
    DailyPlan,
    allocate_account,
    allocate_roster,
    estimate_per_contract_risk,
)
from .dd_engine import DdEngine, RiskReadout

__all__ = [
    "AccountSpec",
    "AccountState",
    "Allocation",
    "AllocatorConfig",
    "DailyPlan",
    "DdEngine",
    "FirmProfile",
    "RiskReadout",
    "SizeRules",
    "allocate_account",
    "allocate_roster",
    "estimate_per_contract_risk",
    "load_firm_profile",
]
