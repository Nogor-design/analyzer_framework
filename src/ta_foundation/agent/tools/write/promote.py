"""Write tools: promote_to_hardening, request_locked_holdout.

These do not run probes themselves (that's `run_probe`, deferred to A.2b).
They mark the candidate as eligible and acquire the necessary locks.
"""

from __future__ import annotations

from typing import Optional

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger.repository import Repository


def _pc_candidate_survivor(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    c = repo.get_candidate(inputs["candidate_id"])
    if c is None:
        return ToolFailure("unknown_candidate", f"candidate {inputs['candidate_id']!r} not found")
    if c.gate_verdict != "survivor":
        return ToolFailure(
            "not_a_survivor",
            f"candidate gate_verdict={c.gate_verdict!r}; only survivors may be promoted",
        )
    return None


def _pc_holdout_requires_dev_oos_pass(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    c = repo.get_candidate(inputs["candidate_id"])
    if c is None:
        return ToolFailure("unknown_candidate", f"candidate {inputs['candidate_id']!r} not found")
    if c.gate_verdict != "survivor":
        return ToolFailure(
            "not_a_survivor",
            "locked holdout is only available for survivor-verdict candidates",
        )
    # Require dev and OOS metrics to exist (i.e., hardening was completed).
    if c.n_trades_dev is None or c.n_trades_oos is None:
        return ToolFailure(
            "hardening_incomplete",
            "candidate has no dev/oos metrics on record; complete hardening first",
        )
    return None


@journaled_tool(
    name="promote_to_hardening",
    role="agent:triage",
    description=(
        "Mark a survivor candidate as eligible for hardened validation "
        "(rolling walk-forward, slippage stress, parameter neighborhood). "
        "Sets triage_state='hardening_queue'. Does not itself run hardening."
    ),
    schema={
        "candidate_id": {"type": "str", "min_length": 1},
        "reason": {"type": "str", "min_length": 20, "max_length": 800},
        "promoted_by": {"type": "str", "min_length": 1, "max_length": 80},
    },
    preconditions=(_pc_candidate_survivor,),
)
def promote_to_hardening(
    repo: Repository,
    *,
    candidate_id: str,
    reason: str,
    promoted_by: str,
) -> dict:
    repo.set_triage(
        candidate_id=candidate_id,
        state="hardening_queue",
        reason=reason,
        triaged_by=promoted_by,
    )
    return {"promoted": True, "candidate_id": candidate_id}


@journaled_tool(
    name="request_locked_holdout",
    role="agent:triage",
    description=(
        "Acquire the one-shot locked-holdout lock for a candidate. "
        "Atomic: a candidate can only ever pass through this tool once. "
        "Returns ok=False on the second attempt without journaling a state change. "
        "After the lock is acquired, the actual holdout run is performed by "
        "run_probe(mode='locked_holdout')."
    ),
    schema={
        "candidate_id": {"type": "str", "min_length": 1},
        "requested_by": {"type": "str", "min_length": 1, "max_length": 80},
    },
    preconditions=(_pc_holdout_requires_dev_oos_pass,),
)
def request_locked_holdout(
    repo: Repository,
    *,
    candidate_id: str,
    requested_by: str,
) -> dict:
    acquired = repo.lock_holdout_attempt(candidate_id)
    return {
        "lock_acquired": acquired,
        "candidate_id": candidate_id,
        "requested_by": requested_by,
        "note": (
            "if lock_acquired=False, the locked holdout has already been used "
            "for this candidate and cannot be retried"
        ),
    }
