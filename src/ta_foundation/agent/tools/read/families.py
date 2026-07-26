"""Read tools over the families table (the finite hypothesis-family registry)."""

from __future__ import annotations

from ta_foundation.agent.tools._decorators import journaled_tool
from ta_foundation.research_ledger import (
    get_family_spec as _get_spec,
    list_probe_families as _list_specs,
)
from ta_foundation.research_ledger.repository import Repository


@journaled_tool(
    name="list_probe_families",
    role="agent:read",
    description=(
        "List the legitimate hypothesis families the Hypothesis Author may use. "
        "Returns thin {family_id, description} rows; call get_family_spec for "
        "the parameter whitelist and mechanism template."
    ),
    schema={},
)
def list_probe_families(repo: Repository) -> list[dict]:
    return [
        {"family_id": f.family_id, "description": f.description}
        for f in _list_specs(repo)
    ]


@journaled_tool(
    name="get_family_spec",
    role="agent:read",
    description=(
        "Get the full whitelist + mechanism template for a hypothesis family. "
        "Use when authoring a probe to confirm legal parameter ranges."
    ),
    schema={"family_id": {"type": "str", "min_length": 1}},
)
def get_family_spec(repo: Repository, *, family_id: str) -> dict:
    spec = _get_spec(repo, family_id)
    if spec is None:
        return {"found": False, "family_id": family_id}
    return {
        "found": True,
        "family_id": spec.family_id,
        "description": spec.description,
        "mechanism_template": spec.mechanism_template,
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "min": p.min,
                "max": p.max,
                "values": list(p.values) if p.values else None,
            }
            for p in spec.params
        ],
    }
