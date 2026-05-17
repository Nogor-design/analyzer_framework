"""Fixed Python scheduler that replaces the deprecated Lead Strategist graph.

There is no LLM in this module. The scheduler is a small set of functions
that call the role passes in a known order and return structured reports.
The agent CLI subcommands call into these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from ta_foundation.agent.roles.hypothesis_author import (
    AuthorReport,
    DEFAULT_SESSION_QUOTA,
    DEFAULT_WEEKLY_QUOTA,
    propose_hypotheses,
)
from ta_foundation.agent.roles.scribe import (
    PostMortemReport,
    WeeklyLetterReport,
    run_post_mortem_pass,
    write_weekly_letter_draft,
)
from ta_foundation.agent.roles.sweep_operator import (
    DEFAULT_OPERATOR_LIMIT,
    OperatorReport,
    resolve_yaml_path_via_author_probe,
    run_operator_pass,
)
from ta_foundation.agent.roles.triage import (
    LLMCall,
    TriagePassReport,
    run_triage_pass,
)
from ta_foundation.research_ledger.repository import Repository


@dataclass
class CombinedReport:
    triage: Optional[TriagePassReport] = None
    post_mortem: Optional[PostMortemReport] = None
    weekly_letter: Optional[WeeklyLetterReport] = None
    authoring: Optional[AuthorReport] = None
    operator: Optional[OperatorReport] = None
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "triage": self.triage.to_dict() if self.triage else None,
            "post_mortem": self.post_mortem.to_dict() if self.post_mortem else None,
            "weekly_letter": self.weekly_letter.to_dict() if self.weekly_letter else None,
            "authoring": self.authoring.to_dict() if self.authoring else None,
            "operator": self.operator.to_dict() if self.operator else None,
            "n_errors": len(self.errors),
            "errors": self.errors[:20],
        }


def daily_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    triage_limit: int = 25,
    post_mortem_limit: int = 25,
    max_retries: int = 2,
) -> CombinedReport:
    """Triage → post-mortem pass. Run nightly (or whenever the operator
    wants the inbox refreshed). The two are sequenced because post-mortems
    only fire for candidates whose `triage_state` is already graveyard.
    """
    report = CombinedReport()
    try:
        report.triage = run_triage_pass(
            repo, llm_call=llm_call, limit=triage_limit, max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "triage",
                                "error": f"{type(exc).__name__}: {exc}"})
    try:
        report.post_mortem = run_post_mortem_pass(
            repo, llm_call=llm_call, limit=post_mortem_limit, max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "post_mortem",
                                "error": f"{type(exc).__name__}: {exc}"})
    return report


def weekly_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    week_start: Optional[date] = None,
    max_retries: int = 2,
) -> CombinedReport:
    """Compose the weekly research letter for the trailing ISO week."""
    report = CombinedReport()
    try:
        report.weekly_letter = write_weekly_letter_draft(
            repo, llm_call=llm_call, week_start=week_start, max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "weekly_letter",
                                "error": f"{type(exc).__name__}: {exc}"})
    return report


def authoring_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    n_proposals: int = DEFAULT_SESSION_QUOTA,
    session_quota: int = DEFAULT_SESSION_QUOTA,
    weekly_quota: int = DEFAULT_WEEKLY_QUOTA,
    max_retries: int = 1,
) -> CombinedReport:
    """Run a Hypothesis Author session — propose, validate, register, draft."""
    report = CombinedReport()
    try:
        report.authoring = propose_hypotheses(
            repo, llm_call=llm_call,
            n_proposals=n_proposals,
            session_quota=session_quota,
            weekly_quota=weekly_quota,
            max_retries=max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "authoring",
                                "error": f"{type(exc).__name__}: {exc}"})
    return report


def operator_pass(
    repo: Repository,
    *,
    input_dir: str,
    output_dir: str,
    run_probe_call: Optional[Callable[..., dict]] = None,
    yaml_path_resolver: Optional[Callable[[str], Optional[str]]] = None,
    accepted_dir: Optional[Path] = None,
    market_data_root: Optional[str] = None,
    ledger_db: Optional[str] = None,
    limit: int = DEFAULT_OPERATOR_LIMIT,
    timeout_seconds: int = 1800,
) -> CombinedReport:
    """Drain the accepted-hypothesis queue (C.2 Sweep Operator).

    `run_probe_call` defaults to the journaled `run_probe` write tool. Tests
    inject a stub; production wiring lets the default subprocess invocation
    drive the existing discovery CLI.
    """
    report = CombinedReport()
    if run_probe_call is None:
        from ta_foundation.agent.tools.write.run_probe import run_probe as _rp
        run_probe_call = _rp
    if yaml_path_resolver is None:
        yaml_path_resolver = resolve_yaml_path_via_author_probe
    try:
        report.operator = run_operator_pass(
            repo,
            yaml_path_resolver=yaml_path_resolver,
            input_dir=input_dir,
            output_dir=output_dir,
            run_probe_call=run_probe_call,
            accepted_dir=accepted_dir,
            market_data_root=market_data_root,
            ledger_db=ledger_db,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "operator",
                                "error": f"{type(exc).__name__}: {exc}"})
    return report


def weekly_authoring_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    input_dir: str,
    output_dir: str,
    n_proposals: int = DEFAULT_SESSION_QUOTA,
    session_quota: int = DEFAULT_SESSION_QUOTA,
    weekly_quota: int = DEFAULT_WEEKLY_QUOTA,
    author_max_retries: int = 1,
    run_probe_call: Optional[Callable[..., dict]] = None,
    yaml_path_resolver: Optional[Callable[[str], Optional[str]]] = None,
    accepted_dir: Optional[Path] = None,
    market_data_root: Optional[str] = None,
    ledger_db: Optional[str] = None,
    operator_limit: int = DEFAULT_OPERATOR_LIMIT,
    timeout_seconds: int = 1800,
) -> CombinedReport:
    """C.4 — Author session, then drain whatever the operator can pick up.

    The graph (Author → inbox HITL → Operator → Triage) is encoded in
    Python, not in an LLM prompt. The inbox accept step is the only human
    intervention; this function performs (1) the Author proposal pass and
    (2) the Operator drain over any hypotheses already accepted via
    `inbox accept`. Proposals authored this session that have not yet been
    accepted simply wait in the inbox for the next pass.
    """
    report = CombinedReport()

    # 1) Author session.
    try:
        report.authoring = propose_hypotheses(
            repo, llm_call=llm_call,
            n_proposals=n_proposals,
            session_quota=session_quota,
            weekly_quota=weekly_quota,
            max_retries=author_max_retries,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "authoring",
                                "error": f"{type(exc).__name__}: {exc}"})

    # 2) Operator drain over accepted-but-not-yet-run hypotheses.
    if run_probe_call is None:
        from ta_foundation.agent.tools.write.run_probe import run_probe as _rp
        run_probe_call = _rp
    if yaml_path_resolver is None:
        yaml_path_resolver = resolve_yaml_path_via_author_probe
    try:
        report.operator = run_operator_pass(
            repo,
            yaml_path_resolver=yaml_path_resolver,
            input_dir=input_dir,
            output_dir=output_dir,
            run_probe_call=run_probe_call,
            accepted_dir=accepted_dir,
            market_data_root=market_data_root,
            ledger_db=ledger_db,
            limit=operator_limit,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append({"stage": "operator",
                                "error": f"{type(exc).__name__}: {exc}"})

    return report
