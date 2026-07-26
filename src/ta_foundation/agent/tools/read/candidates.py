"""Read tools over the candidates and graveyard tables."""

from __future__ import annotations

import json
from typing import Optional

from ta_foundation.agent.tools._decorators import journaled_tool
from ta_foundation.research_ledger.repository import Repository

_VALID_TRIAGE = ["graveyard", "research", "hardening_queue", "shadow", "decayed"]


def _candidate_to_summary(c) -> dict:
    return {
        "candidate_id": c.candidate_id,
        "run_id": c.run_id,
        "hypothesis_id": c.hypothesis_id,
        "rank_in_run": c.rank_in_run,
        "n_trades_dev": c.n_trades_dev,
        "pf_dev": c.pf_dev,
        "expectancy_dev": c.expectancy_dev,
        "n_trades_oos": c.n_trades_oos,
        "pf_oos": c.pf_oos,
        "n_trades_holdout": c.n_trades_holdout,
        "pf_holdout": c.pf_holdout,
        "gate_verdict": c.gate_verdict,
        "triage_state": c.triage_state,
        "triage_reason": c.triage_reason,
        "holdout_attempted": c.holdout_attempted,
    }


@journaled_tool(
    name="list_candidates",
    role="agent:read",
    description=(
        "List candidate rows from the research ledger. Filter by family, "
        "instrument, triage state, or gate verdict. Returns at most `limit` rows "
        "ordered by rank_in_run."
    ),
    schema={
        "family": {"type": "str", "required": False},
        "instrument": {"type": "str", "required": False},
        "triage_state": {"type": "enum", "values": _VALID_TRIAGE, "required": False},
        "gate_verdict": {
            "type": "enum",
            "values": ["survivor", "rejected", "pending"],
            "required": False,
        },
        "untriaged_only": {"type": "bool", "required": False, "default": False},
        "limit": {"type": "int", "required": False, "default": 25, "min": 1, "max": 50},
    },
)
def list_candidates(
    repo: Repository,
    *,
    family: Optional[str] = None,
    instrument: Optional[str] = None,
    triage_state: Optional[str] = None,
    gate_verdict: Optional[str] = None,
    untriaged_only: bool = False,
    limit: int = 25,
) -> list[dict]:
    rows = repo.list_candidates(
        family=family,
        instrument=instrument,
        triage_state=triage_state,
        gate_verdict=gate_verdict,
        untriaged_only=untriaged_only,
        limit=limit,
    )
    return [_candidate_to_summary(c) for c in rows]


@journaled_tool(
    name="get_candidate",
    role="agent:read",
    description="Fetch the full ledger row for a single candidate, plus its sidecar artifact path.",
    schema={"candidate_id": {"type": "str", "min_length": 1}},
)
def get_candidate(repo: Repository, *, candidate_id: str) -> dict:
    c = repo.get_candidate(candidate_id)
    if c is None:
        return {"found": False, "candidate_id": candidate_id}
    run = repo.get_run(c.run_id)
    return {
        "found": True,
        **_candidate_to_summary(c),
        "params": json.loads(c.params_json),
        "gate_reasons": json.loads(c.gate_reasons_json) if c.gate_reasons_json else None,
        "slippage_stress_pass": c.slippage_stress_pass,
        "folds_distribution": json.loads(c.folds_distribution) if c.folds_distribution else None,
        "artifact_dir": run.artifact_dir if run else None,
        "yaml_path": run.yaml_path if run else None,
    }


@journaled_tool(
    name="list_graveyard",
    role="agent:read",
    description=(
        "List rejected candidates with their rejection reasons. Use before "
        "proposing a new hypothesis to confirm it has not already been tested and rejected."
    ),
    schema={
        "family": {"type": "str", "required": False},
        "instrument": {"type": "str", "required": False},
        "since": {"type": "str", "required": False},
        "limit": {"type": "int", "required": False, "default": 25, "min": 1, "max": 50},
    },
)
def list_graveyard(
    repo: Repository,
    *,
    family: Optional[str] = None,
    instrument: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    rows = repo.list_graveyard(family=family, instrument=instrument, since=since, limit=limit)
    return [
        {
            **_candidate_to_summary(c),
            "triaged_at": c.triaged_at,
            "triaged_by": c.triaged_by,
        }
        for c in rows
    ]


@journaled_tool(
    name="find_similar_hypotheses",
    role="agent:read",
    description=(
        "Find existing hypotheses similar to a proposed one (same family + instrument, "
        "Jaccard similarity on params ≥ threshold). Use to deduplicate before authoring."
    ),
    schema={
        "family": {"type": "str", "min_length": 1},
        "instrument": {"type": "str", "min_length": 1},
        "params": {"type": "dict"},
        "threshold": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.7, "required": False},
        "limit": {"type": "int", "required": False, "default": 10, "min": 1, "max": 25},
    },
)
def find_similar_hypotheses(
    repo: Repository,
    *,
    family: str,
    instrument: str,
    params: dict,
    threshold: float = 0.7,
    limit: int = 10,
) -> list[dict]:
    matches = repo.find_similar_hypotheses(
        family=family, instrument=instrument, params=params,
        threshold=threshold, limit=limit,
    )
    return [
        {
            "hypothesis_id": h.hypothesis_id,
            "status": h.status,
            "similarity": round(sim, 4),
            "registered_at": h.registered_at,
            "registered_by": h.registered_by,
        }
        for h, sim in matches
    ]
