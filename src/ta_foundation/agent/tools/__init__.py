"""Agent tool registry.

Exposes:
    - READ_TOOLS: tuple of read callables
    - WRITE_TOOLS: tuple of write callables
    - ALL_TOOLS: union of both
    - TOOLS_BY_NAME: dict[name -> callable]
    - TOOLS_BY_ROLE: dict[role -> tuple of callables]

Each callable accepts (repo: Repository, **inputs) and returns a dict with
shape {"ok": bool, "result": ..., "code": ..., "error": ...}. Inputs are
validated against the tool's schema, preconditions are enforced in code,
and every call is journaled.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from ta_foundation.agent.tools.read.candidates import (
    find_similar_hypotheses,
    get_candidate,
    list_candidates,
    list_graveyard,
)
from ta_foundation.agent.tools.read.families import (
    get_family_spec,
    list_probe_families,
)
from ta_foundation.agent.tools.read.ledger import (
    count_hypotheses_tested,
    get_family_coverage,
    get_hypothesis,
)
from ta_foundation.agent.tools.read.market import get_market_data_coverage
from ta_foundation.agent.tools.read.sidecars import read_sidecar
from ta_foundation.agent.tools.write.author_probe import author_probe
from ta_foundation.agent.tools.write.post_mortem import write_post_mortem
from ta_foundation.agent.tools.write.promote import (
    promote_to_hardening,
    request_locked_holdout,
)
from ta_foundation.agent.tools.write.run_probe import (
    record_candidates_for_run,
    run_probe,
)
from ta_foundation.agent.tools.write.shadow import enroll_shadow_trader
from ta_foundation.agent.tools.write.triage import set_triage_state

READ_TOOLS: tuple[Callable, ...] = (
    list_candidates,
    get_candidate,
    list_graveyard,
    find_similar_hypotheses,
    list_probe_families,
    get_family_spec,
    count_hypotheses_tested,
    get_family_coverage,
    get_hypothesis,
    get_market_data_coverage,
    read_sidecar,
)

WRITE_TOOLS: tuple[Callable, ...] = (
    author_probe,
    set_triage_state,
    promote_to_hardening,
    request_locked_holdout,
    write_post_mortem,
    enroll_shadow_trader,
    run_probe,
    record_candidates_for_run,
)

ALL_TOOLS: tuple[Callable, ...] = READ_TOOLS + WRITE_TOOLS

TOOLS_BY_NAME: dict[str, Callable] = {
    t._tool_name: t  # type: ignore[attr-defined]
    for t in ALL_TOOLS
}


def _build_role_index() -> dict[str, tuple[Callable, ...]]:
    by_role: dict[str, list[Callable]] = defaultdict(list)
    for t in ALL_TOOLS:
        by_role[t._tool_role].append(t)  # type: ignore[attr-defined]
    return {role: tuple(tools) for role, tools in by_role.items()}


TOOLS_BY_ROLE: dict[str, tuple[Callable, ...]] = _build_role_index()


__all__ = [
    "ALL_TOOLS",
    "READ_TOOLS",
    "TOOLS_BY_NAME",
    "TOOLS_BY_ROLE",
    "WRITE_TOOLS",
]
