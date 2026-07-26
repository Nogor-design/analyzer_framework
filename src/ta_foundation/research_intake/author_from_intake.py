"""Bridge reviewed intake proposals into the guarded Hypothesis Author flow."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from ta_foundation.agent.roles.hypothesis_author import propose_hypotheses
from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository


def load_author_json(path: Path, *, max_proposals: Optional[int] = None) -> str:
    """Load and optionally truncate an author-proposals JSON artifact."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        raise ValueError("author JSON must contain a 'proposals' list")
    if max_proposals is not None:
        proposals = proposals[:max_proposals]
    return json.dumps({"proposals": proposals}, sort_keys=True)


def run_author_from_intake(
    *,
    author_json: Path,
    db_path: Path = DEFAULT_DB_PATH,
    max_proposals: int = 3,
    dry_run: bool = True,
    session_quota: Optional[int] = None,
    weekly_quota: int = 25,
) -> dict:
    """Run the existing Hypothesis Author against intake proposals.

    Dry-run mode copies the ledger into a temporary directory and changes the
    working directory there, so ledger writes, generated YAML, and inbox drafts
    are all discarded after the report is built.
    """
    raw = load_author_json(author_json, max_proposals=max_proposals)
    effective_session_quota = session_quota or max_proposals

    def llm_call(_system: str, _user: str) -> str:
        return raw

    if dry_run:
        return _run_dry(
            llm_call=llm_call,
            source_db=db_path,
            n_proposals=max_proposals,
            session_quota=effective_session_quota,
            weekly_quota=weekly_quota,
        )

    repo = get_repository(db_path)
    try:
        report = propose_hypotheses(
            repo,
            llm_call=llm_call,
            n_proposals=max_proposals,
            session_quota=effective_session_quota,
            weekly_quota=weekly_quota,
            max_retries=0,
        )
        return {"dry_run": False, "db_path": str(db_path), "report": report.to_dict()}
    finally:
        repo.close()


def _run_dry(
    *,
    llm_call,
    source_db: Path,
    n_proposals: int,
    session_quota: int,
    weekly_quota: int,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="ta_intake_author_dry_") as tmp:
        tmp_root = Path(tmp)
        dry_db = tmp_root / "research_ledger.db"
        if source_db.exists():
            shutil.copy2(source_db, dry_db)
        repo = get_repository(dry_db)
        old_cwd = Path.cwd()
        try:
            # Keep generated YAML and inbox drafts out of the real workspace.
            import os

            os.chdir(tmp_root)
            report = propose_hypotheses(
                repo,
                llm_call=llm_call,
                n_proposals=n_proposals,
                session_quota=session_quota,
                weekly_quota=weekly_quota,
                max_retries=0,
            )
            return {
                "dry_run": True,
                "db_path": str(source_db),
                "temp_db_path": str(dry_db),
                "report": report.to_dict(),
            }
        finally:
            import os

            os.chdir(old_cwd)
            repo.close()


def format_report(result: dict) -> str:
    report = result["report"]
    lines = [
        "Intake authoring report",
        f"dry_run={result['dry_run']}",
        f"db_path={result['db_path']}",
        (
            f"requested={report['requested']} parsed={report['parsed']} "
            f"accepted={report['accepted']} rejected={report['rejected']}"
        ),
        (
            "quota_remaining_session="
            f"{report['quota_remaining_session']} "
            f"quota_remaining_week={report['quota_remaining_week']}"
        ),
    ]
    for proposal in report["proposals"]:
        status = "accepted" if proposal["accepted"] else "rejected"
        lines.append(
            f"- {status}: family={proposal['family']} "
            f"instrument={proposal['instrument']} "
            f"hypothesis_id={proposal['hypothesis_id'] or ''}"
        )
        if proposal.get("draft_path"):
            lines.append(f"  draft_path={proposal['draft_path']}")
        if proposal.get("rejection_code"):
            lines.append(
                f"  rejection={proposal['rejection_code']}: "
                f"{proposal.get('rejection_message') or ''}"
            )
    if report["failures"]:
        lines.append("failures:")
        for failure in report["failures"]:
            lines.append(f"- {failure.get('code')}: {failure.get('message')}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ta_foundation.research_intake.author_from_intake",
        description=(
            "Run intake proposals through the guarded Hypothesis Author flow."
        ),
    )
    parser.add_argument("--author-json", required=True, type=Path)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, type=Path)
    parser.add_argument("--max-proposals", default=3, type=int)
    parser.add_argument("--session-quota", default=None, type=int)
    parser.add_argument("--weekly-quota", default=25, type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a temporary copied ledger and discard writes.",
    )
    parser.add_argument(
        "--write-report-json",
        default=None,
        type=Path,
        help="Optional path for the authoring report JSON.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_author_from_intake(
        author_json=args.author_json,
        db_path=args.db_path,
        max_proposals=args.max_proposals,
        dry_run=args.dry_run,
        session_quota=args.session_quota,
        weekly_quota=args.weekly_quota,
    )
    if args.write_report_json:
        args.write_report_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_report_json.write_text(
            json.dumps(result, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
