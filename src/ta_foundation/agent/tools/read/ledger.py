"""Read tools over hypotheses, runs, and journal aggregates."""

from __future__ import annotations

from typing import Optional

from ta_foundation.agent.tools._decorators import journaled_tool
from ta_foundation.research_ledger.repository import Repository


@journaled_tool(
    name="count_hypotheses_tested",
    role="agent:read",
    description=(
        "Return the number of hypotheses with at least one completed run. "
        "This is the multiple-testing denominator referenced by T12 in the "
        "discovery hardening plan; the Hypothesis Author should consult it "
        "before proposing new probes so it sees the cost it adds."
    ),
    schema={
        "since": {"type": "str", "required": False},
        "until": {"type": "str", "required": False},
        "family": {"type": "str", "required": False},
    },
)
def count_hypotheses_tested(
    repo: Repository,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    family: Optional[str] = None,
) -> dict:
    total = repo.count_hypotheses_tested(since=since, until=until, family=family)
    return {"count": total, "since": since, "until": until, "family": family}


@journaled_tool(
    name="get_family_coverage",
    role="agent:read",
    description=(
        "Per-family count of hypotheses registered within a window. Used by "
        "the Hypothesis Author to enforce the C.3 coverage cap (max 40% of a "
        "session's quota in a single family) and to diversify proposals away "
        "from over-represented families."
    ),
    schema={
        "since": {"type": "str", "required": False},
        "until": {"type": "str", "required": False},
        "registered_by": {"type": "str", "required": False},
    },
)
def get_family_coverage(
    repo: Repository,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    registered_by: Optional[str] = None,
) -> dict:
    counts = repo.count_hypotheses_by_family(
        since=since, until=until, registered_by=registered_by,
    )
    return {
        "since": since, "until": until, "registered_by": registered_by,
        "total": sum(counts.values()),
        "by_family": dict(sorted(counts.items())),
    }


@journaled_tool(
    name="get_hypothesis",
    role="agent:read",
    description="Fetch a hypothesis row by id (status, mechanism, params, registration metadata).",
    schema={"hypothesis_id": {"type": "str", "min_length": 1}},
)
def get_hypothesis(repo: Repository, *, hypothesis_id: str) -> dict:
    h = repo.get_hypothesis(hypothesis_id)
    if h is None:
        return {"found": False, "hypothesis_id": hypothesis_id}
    import json as _json
    return {
        "found": True,
        "hypothesis_id": h.hypothesis_id,
        "family": h.family,
        "instrument": h.instrument,
        "timeframe": h.timeframe,
        "session_window": h.session_window,
        "direction": h.direction,
        "params": _json.loads(h.params_json),
        "mechanism": h.mechanism,
        "status": h.status,
        "registered_at": h.registered_at,
        "registered_by": h.registered_by,
        "parent_id": h.parent_id,
    }
