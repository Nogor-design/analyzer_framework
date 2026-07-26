"""Human-in-the-loop inbox for Scribe-authored drafts (Phase B.5).

The Scribe writes drafts to `runs/inbox/<type>/<id>.md`. The operator
reviews them via the CLI and either:

    accept   moves the draft to its final destination (for post-mortems,
             via the journaled `write_post_mortem` write tool so the
             acceptance lands in tool_journal; for weekly letters, a
             direct move into `runs/letters/` plus a manual journal entry).

    reject   moves the draft to `runs/rejected/<type>/<id>.md` with a
             reason recorded in the tool_journal so the Scribe can avoid
             repeating the issue.

The CLI is intentionally tiny: list, show, accept, reject. No web UI;
running this on the operator's laptop is the entire model for now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ta_foundation.agent.roles import scribe as scribe_mod
from ta_foundation.agent.tools.write.post_mortem import write_post_mortem
from ta_foundation.research_ledger.repository import Repository

INBOX_ROOT = Path("runs/inbox")
REJECTED_ROOT = Path("runs/rejected")
LETTERS_FINAL_DIR = Path("runs/letters")

VALID_TYPES = ("post_mortems", "weekly_letters", "proposals")


@dataclass(frozen=True)
class Draft:
    draft_id: str            # e.g. "post_mortems/c_abc_001"
    artifact_type: str       # "post_mortem" | "weekly_letter" | "*_lint_failure"
    inbox_path: Path
    is_lint_failure: bool


def _root(custom: Optional[Path] = None) -> Path:
    return custom or INBOX_ROOT


def _rejected_root(custom: Optional[Path] = None) -> Path:
    return custom or REJECTED_ROOT


def list_drafts(
    *,
    inbox_root: Optional[Path] = None,
    artifact_type: Optional[str] = None,
) -> list[Draft]:
    root = _root(inbox_root)
    if not root.exists():
        return []
    out: list[Draft] = []
    for type_dir in sorted(root.iterdir()):
        if not type_dir.is_dir():
            continue
        if artifact_type and type_dir.name != artifact_type:
            continue
        for p in sorted(type_dir.glob("*.md")):
            stem = p.stem
            is_fail = stem.endswith("_LINT_FAIL")
            id_part = stem[: -len("_LINT_FAIL")] if is_fail else stem
            inferred_type = (type_dir.name.rstrip("s") or type_dir.name)  # post_mortems -> post_mortem
            artifact_kind = inferred_type if not is_fail else f"{inferred_type}_lint_failure"
            out.append(Draft(
                draft_id=f"{type_dir.name}/{id_part}",
                artifact_type=artifact_kind,
                inbox_path=p,
                is_lint_failure=is_fail,
            ))
    return out


def _find_draft(draft_id: str, inbox_root: Optional[Path] = None) -> Optional[Draft]:
    for d in list_drafts(inbox_root=inbox_root):
        if d.draft_id == draft_id:
            return d
    return None


def accept_draft(
    repo: Repository,
    draft_id: str,
    *,
    accepted_by: str,
    inbox_root: Optional[Path] = None,
    letters_final_dir: Optional[Path] = None,
) -> dict:
    """Move a draft from inbox to its final destination."""
    draft = _find_draft(draft_id, inbox_root=inbox_root)
    if draft is None:
        return {"ok": False, "code": "unknown_draft", "draft_id": draft_id}
    if draft.is_lint_failure:
        return {"ok": False, "code": "cannot_accept_lint_failure",
                "message": "lint_failure drafts must be deleted or fixed by the operator"}

    if draft.artifact_type == "post_mortem":
        return _accept_post_mortem(repo, draft, accepted_by=accepted_by)
    if draft.artifact_type == "weekly_letter":
        return _accept_weekly_letter(repo, draft, accepted_by=accepted_by,
                                      final_dir=letters_final_dir or LETTERS_FINAL_DIR)
    if draft.artifact_type == "proposal":
        return _accept_proposal(repo, draft, accepted_by=accepted_by)
    return {"ok": False, "code": "unknown_artifact_type",
            "artifact_type": draft.artifact_type}


def _accept_post_mortem(repo: Repository, draft: Draft, *, accepted_by: str) -> dict:
    candidate_id = draft.inbox_path.stem
    markdown = draft.inbox_path.read_text(encoding="utf-8")
    # Use the journaled write_post_mortem tool — it enforces the
    # candidate-graveyarded precondition and journals the acceptance.
    tool_result = write_post_mortem(
        repo,
        candidate_id=candidate_id,
        markdown=markdown,
        authored_by=accepted_by,
    )
    if not tool_result.get("ok"):
        return {"ok": False, "code": "write_tool_failed", "tool_result": tool_result}
    # Tool wrote to discovery/graveyard/; remove the inbox copy.
    draft.inbox_path.unlink()
    return {"ok": True, "draft_id": draft.draft_id,
            "final_path": tool_result["result"]["path"]}


def _accept_weekly_letter(
    repo: Repository, draft: Draft, *, accepted_by: str, final_dir: Path,
) -> dict:
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / draft.inbox_path.name
    final_path.write_text(draft.inbox_path.read_text(encoding="utf-8"),
                            encoding="utf-8")
    repo.journal(
        role="human",
        tool_name="inbox.accept_weekly_letter",
        inputs={"draft_id": draft.draft_id, "accepted_by": accepted_by},
        output_summary=f"moved to {final_path}",
        duration_ms=0,
    )
    draft.inbox_path.unlink()
    return {"ok": True, "draft_id": draft.draft_id,
            "final_path": str(final_path)}


def _accept_proposal(repo: Repository, draft: Draft, *, accepted_by: str) -> dict:
    """Accepting a hypothesis proposal is a no-op on the ledger (the
    hypothesis was already registered when the Author proposed it). We move
    the draft to runs/proposals_accepted/ and journal the human signoff.
    """
    target_dir = Path("runs") / "proposals_accepted"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / draft.inbox_path.name
    target.write_text(draft.inbox_path.read_text(encoding="utf-8"),
                       encoding="utf-8")
    hypothesis_id = draft.inbox_path.stem
    repo.journal(
        role="human",
        tool_name="inbox.accept_proposal",
        inputs={"draft_id": draft.draft_id, "accepted_by": accepted_by,
                "hypothesis_id": hypothesis_id},
        output_summary=f"proposal accepted → {target}",
        duration_ms=0,
    )
    draft.inbox_path.unlink()
    return {"ok": True, "draft_id": draft.draft_id,
            "final_path": str(target), "hypothesis_id": hypothesis_id}


def reject_draft(
    repo: Repository,
    draft_id: str,
    *,
    reason: str,
    rejected_by: str,
    inbox_root: Optional[Path] = None,
    rejected_root: Optional[Path] = None,
) -> dict:
    if len(reason.strip()) < 10:
        return {"ok": False, "code": "reason_too_short",
                "message": "rejection reason must be at least 10 characters"}
    draft = _find_draft(draft_id, inbox_root=inbox_root)
    if draft is None:
        return {"ok": False, "code": "unknown_draft", "draft_id": draft_id}
    rejected = _rejected_root(rejected_root) / draft.draft_id.split("/")[0]
    rejected.mkdir(parents=True, exist_ok=True)
    target = rejected / draft.inbox_path.name
    target.write_text(draft.inbox_path.read_text(encoding="utf-8"),
                       encoding="utf-8")

    # Side effect specific to hypothesis proposals: retire the registered
    # hypothesis so it doesn't continue to count against the multiple-testing
    # denominator. Other draft types have no ledger row to update.
    retired_hypothesis_id: Optional[str] = None
    if draft.artifact_type == "proposal":
        hid = draft.inbox_path.stem
        if repo.get_hypothesis(hid) is not None:
            try:
                repo.set_hypothesis_status(hid, "retired")
                retired_hypothesis_id = hid
            except Exception:  # noqa: BLE001 — never block the rejection move
                pass

    repo.journal(
        role="human",
        tool_name="inbox.reject",
        inputs={"draft_id": draft.draft_id, "rejected_by": rejected_by,
                "reason": reason,
                "retired_hypothesis_id": retired_hypothesis_id},
        output_summary=f"rejected → {target}",
        duration_ms=0,
    )
    draft.inbox_path.unlink()
    return {"ok": True, "draft_id": draft.draft_id, "rejected_path": str(target),
            "retired_hypothesis_id": retired_hypothesis_id}


def show_draft(draft_id: str, inbox_root: Optional[Path] = None) -> Optional[str]:
    draft = _find_draft(draft_id, inbox_root=inbox_root)
    if draft is None:
        return None
    return draft.inbox_path.read_text(encoding="utf-8")
