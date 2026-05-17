"""Write tool: author a new pre-registered hypothesis and emit its YAML probe.

Preconditions (code-side):
    - family must be in the registry
    - all params must satisfy the family's whitelist
    - mechanism must be ≥ 50 chars
    - no existing hypothesis with the same dedupe_hash
    - if any graveyarded candidate has Jaccard ≥ 0.7 against these params,
      a `revival_reason` ≥ 30 chars is required.

The tool writes a hypothesis row to the ledger and a YAML file to
`discovery/generated/<hypothesis_id>.yaml` with a `pre_registration:` block
the CLI drift check will validate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml as _yaml

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger import (
    DuplicateHypothesisError,
    get_family_spec,
    validate_params,
)
from ta_foundation.research_ledger.repository import Repository

GENERATED_PROBE_DIR = Path("discovery/generated")


def _pc_family_exists(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    if get_family_spec(repo, inputs["family"]) is None:
        return ToolFailure(
            code="unknown_family",
            message=f"family {inputs['family']!r} is not in the registry; call list_probe_families first",
        )
    return None


def _pc_params_in_whitelist(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    violations = validate_params(repo, inputs["family"], inputs["params"])
    if violations:
        return ToolFailure(
            code="params_not_in_whitelist",
            message="one or more params are outside the family whitelist",
            violations=tuple(
                {"param": v.param_name, "code": v.code, "message": v.message}
                for v in violations
            ),
        )
    return None


def _pc_revival_reason_when_graveyarded(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    similar = repo.find_similar_hypotheses(
        family=inputs["family"],
        instrument=inputs["instrument"],
        params=inputs["params"],
        threshold=0.7,
    )
    # Any similar hypothesis whose candidates are graveyarded?
    for hypothesis, _sim in similar:
        candidates = repo.list_candidates(
            family=hypothesis.family,
            instrument=hypothesis.instrument,
            triage_state="graveyard",
            limit=5,
        )
        # Only block when at least one of those is the actual graveyarded match.
        if any(c.hypothesis_id == hypothesis.hypothesis_id for c in candidates):
            revival = inputs.get("revival_reason") or ""
            if len(revival.strip()) < 30:
                return ToolFailure(
                    code="graveyard_collision_requires_revival_reason",
                    message=(
                        f"hypothesis is similar to graveyarded {hypothesis.hypothesis_id}; "
                        "supply revival_reason ≥ 30 chars explaining what is materially "
                        "different now (e.g., new structural filter, new data window)"
                    ),
                )
    return None


_PRECONDITIONS: tuple[Precondition, ...] = (
    _pc_family_exists,
    _pc_params_in_whitelist,
    _pc_revival_reason_when_graveyarded,
)


@journaled_tool(
    name="author_probe",
    role="agent:hypothesis_author",
    description=(
        "Pre-register a new hypothesis and emit its YAML probe. The hypothesis "
        "is locked at the moment of registration: tweak-and-retry afterward is "
        "forbidden (Real Edge §5). Returns the hypothesis_id and the path of "
        "the generated YAML."
    ),
    schema={
        "hypothesis_id": {"type": "str", "min_length": 5, "max_length": 80},
        "family": {"type": "str", "min_length": 1},
        "instrument": {"type": "str", "min_length": 1, "max_length": 6},
        "timeframe": {"type": "str", "min_length": 1, "max_length": 8},
        "session_window": {"type": "str", "required": False},
        "direction": {
            "type": "enum", "values": ["long", "short", "both"], "required": False
        },
        "params": {"type": "dict"},
        "mechanism": {"type": "str", "min_length": 50, "max_length": 2000},
        "revival_reason": {"type": "str", "required": False, "max_length": 800},
        "registered_by": {"type": "str", "min_length": 1, "max_length": 80},
        "parent_id": {"type": "str", "required": False},
    },
    preconditions=_PRECONDITIONS,
)
def author_probe(
    repo: Repository,
    *,
    hypothesis_id: str,
    family: str,
    instrument: str,
    timeframe: str,
    params: dict,
    mechanism: str,
    registered_by: str,
    session_window: Optional[str] = None,
    direction: Optional[str] = None,
    revival_reason: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    try:
        h = repo.register_hypothesis(
            hypothesis_id=hypothesis_id,
            family=family,
            instrument=instrument,
            timeframe=timeframe,
            session_window=session_window,
            direction=direction,
            params=params,
            mechanism=mechanism,
            registered_by=registered_by,
            parent_id=parent_id,
        )
    except DuplicateHypothesisError as exc:
        return {
            "registered": False,
            "code": "duplicate_hypothesis",
            "existing_hypothesis_id": exc.existing_id,
            "dedupe_hash": exc.dedupe_hash,
        }

    yaml_path = _emit_probe_yaml(h, params=params, mechanism=mechanism,
                                 revival_reason=revival_reason)
    return {
        "registered": True,
        "hypothesis_id": h.hypothesis_id,
        "dedupe_hash": h.dedupe_hash,
        "yaml_path": str(yaml_path),
    }


def _emit_probe_yaml(hypothesis, *, params: dict, mechanism: str,
                     revival_reason: Optional[str]) -> Path:
    GENERATED_PROBE_DIR.mkdir(parents=True, exist_ok=True)
    target = GENERATED_PROBE_DIR / f"{hypothesis.hypothesis_id}.yaml"
    block: dict = {
        "pre_registration": {
            "hypothesis_id": hypothesis.hypothesis_id,
            "family": hypothesis.family,
            "instrument": hypothesis.instrument,
            "timeframe": hypothesis.timeframe,
            "session_window": hypothesis.session_window,
            "direction": hypothesis.direction,
            "params": params,
            "pre_reg_mechanism": mechanism,
        }
    }
    if revival_reason:
        block["pre_registration"]["revival_reason"] = revival_reason
    target.write_text(_yaml.safe_dump(block, sort_keys=True), encoding="utf-8")
    return target
