"""Write tool: write_post_mortem — Scribe-authored markdown for graveyard candidates.

Preconditions:
    - candidate exists
    - candidate.triage_state == 'graveyard'
    - markdown frontmatter cites the candidate_id (Phase B will lint citations
      more strictly; A.2 only requires presence of the candidate_id mention)

Side effects:
    - writes <discovery/graveyard/<candidate_id>.md>
    - journals the path
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger.repository import Repository

GRAVEYARD_DIR = Path("discovery/graveyard")


def _pc_candidate_graveyarded(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    c = repo.get_candidate(inputs["candidate_id"])
    if c is None:
        return ToolFailure(
            code="unknown_candidate",
            message=f"candidate_id {inputs['candidate_id']!r} not found",
        )
    if c.triage_state != "graveyard":
        return ToolFailure(
            code="not_graveyarded",
            message=(
                f"candidate triage_state={c.triage_state!r}; post-mortems are "
                "only written for graveyard candidates"
            ),
        )
    return None


def _pc_markdown_cites_candidate(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    if inputs["candidate_id"] not in inputs["markdown"]:
        return ToolFailure(
            code="markdown_missing_citation",
            message=(
                "markdown body must mention the candidate_id at least once "
                "(Phase B will enforce stricter citation rules via the linter)"
            ),
        )
    return None


@journaled_tool(
    name="write_post_mortem",
    role="agent:scribe",
    description=(
        "Write a graveyard post-mortem for a triaged-graveyard candidate. "
        "The markdown body is required to mention the candidate_id at least "
        "once; Phase B's numeric-claim linter will tighten the requirement to "
        "ledger-traceable numbers."
    ),
    schema={
        "candidate_id": {"type": "str", "min_length": 1},
        "markdown": {"type": "str", "min_length": 200, "max_length": 12000},
        "authored_by": {"type": "str", "min_length": 1, "max_length": 80},
    },
    preconditions=(_pc_candidate_graveyarded, _pc_markdown_cites_candidate),
)
def write_post_mortem(
    repo: Repository,
    *,
    candidate_id: str,
    markdown: str,
    authored_by: str,
) -> dict:
    GRAVEYARD_DIR.mkdir(parents=True, exist_ok=True)
    target = GRAVEYARD_DIR / f"{candidate_id}.md"
    target.write_text(markdown, encoding="utf-8")
    return {
        "written": True,
        "candidate_id": candidate_id,
        "path": str(target),
        "authored_by": authored_by,
        "byte_count": len(markdown.encode("utf-8")),
    }
