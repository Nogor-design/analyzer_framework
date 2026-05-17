"""Write tool: enroll a candidate in the shadow forward-observation loop.

This is the entry point Phase D will exercise. The shadow runner itself
(D.1) does not exist yet; this tool just sets the candidate's triage_state
to 'shadow' under code-side preconditions identical to set_triage_state's
shadow guard.
"""

from __future__ import annotations

from typing import Optional

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger.repository import Repository


def _pc_passes_holdout(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    c = repo.get_candidate(inputs["candidate_id"])
    if c is None:
        return ToolFailure("unknown_candidate", f"candidate {inputs['candidate_id']!r} not found")
    if c.gate_verdict != "survivor":
        return ToolFailure(
            "not_a_survivor",
            "shadow enrollment requires gate_verdict='survivor'",
        )
    if c.n_trades_holdout is None or c.pf_holdout is None:
        return ToolFailure(
            "holdout_not_evaluated",
            "shadow enrollment requires a recorded locked-holdout evaluation",
        )
    if c.pf_holdout <= 1.0:
        return ToolFailure(
            "holdout_did_not_pass",
            f"locked-holdout PF={c.pf_holdout} ≤ 1.0 — candidate does not qualify for shadow",
        )
    return None


@journaled_tool(
    name="enroll_shadow_trader",
    role="agent:triage",
    description=(
        "Enroll a candidate in the forward-observation (shadow) loop. "
        "Requires a survivor verdict and a passed locked-holdout evaluation. "
        "Does not place orders or interact with any broker; the shadow simulator "
        "(Phase D.1) will pick up enrolled candidates and write to shadow_signals."
    ),
    schema={
        "candidate_id": {"type": "str", "min_length": 1},
        "reason": {"type": "str", "min_length": 20, "max_length": 800},
        "enrolled_by": {"type": "str", "min_length": 1, "max_length": 80},
    },
    preconditions=(_pc_passes_holdout,),
)
def enroll_shadow_trader(
    repo: Repository,
    *,
    candidate_id: str,
    reason: str,
    enrolled_by: str,
) -> dict:
    repo.set_triage(
        candidate_id=candidate_id,
        state="shadow",
        reason=reason,
        triaged_by=enrolled_by,
    )
    return {"enrolled": True, "candidate_id": candidate_id}
