"""Write tool: set a candidate's triage state.

Preconditions:
    - candidate must exist
    - state must be in the valid set
    - reason must be ≥ 20 chars
    - shadow state requires gate_verdict='survivor' AND a passed locked holdout
"""

from __future__ import annotations

from typing import Optional

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger.repository import Repository

_VALID_TRIAGE_STATES = ["graveyard", "research", "hardening_queue", "shadow", "decayed"]


def _pc_candidate_exists(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    if repo.get_candidate(inputs["candidate_id"]) is None:
        return ToolFailure(
            code="unknown_candidate",
            message=f"candidate_id {inputs['candidate_id']!r} not found",
        )
    return None


def _pc_shadow_requires_holdout_pass(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    if inputs["state"] != "shadow":
        return None
    c = repo.get_candidate(inputs["candidate_id"])
    if c is None:  # already caught upstream, but be defensive
        return None
    if c.gate_verdict != "survivor":
        return ToolFailure(
            code="shadow_requires_survivor",
            message=(
                f"candidate gate_verdict={c.gate_verdict!r}; only survivors may be enrolled in shadow"
            ),
        )
    if c.n_trades_holdout is None or c.pf_holdout is None:
        return ToolFailure(
            code="shadow_requires_locked_holdout",
            message="cannot enroll in shadow until a locked-holdout evaluation has been recorded",
        )
    if c.pf_holdout <= 1.0:
        return ToolFailure(
            code="shadow_requires_passing_holdout",
            message=f"locked-holdout PF={c.pf_holdout} did not exceed 1.0",
        )
    return None


@journaled_tool(
    name="set_triage_state",
    role="agent:triage",
    description=(
        "Set the triage state for a candidate (graveyard | research | "
        "hardening_queue | shadow | decayed) with a written reason. "
        "Cannot override gate_verdict; cannot promote to shadow without a "
        "passing locked holdout."
    ),
    schema={
        "candidate_id": {"type": "str", "min_length": 1},
        "state": {"type": "enum", "values": _VALID_TRIAGE_STATES},
        "reason": {"type": "str", "min_length": 20, "max_length": 800},
        "triaged_by": {"type": "str", "min_length": 1, "max_length": 80},
    },
    preconditions=(_pc_candidate_exists, _pc_shadow_requires_holdout_pass),
)
def set_triage_state(
    repo: Repository,
    *,
    candidate_id: str,
    state: str,
    reason: str,
    triaged_by: str,
) -> dict:
    repo.set_triage(
        candidate_id=candidate_id, state=state, reason=reason, triaged_by=triaged_by
    )
    return {"triaged": True, "candidate_id": candidate_id, "state": state}
