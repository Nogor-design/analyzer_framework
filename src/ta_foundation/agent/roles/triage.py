"""Triage Analyst — classifies untriaged candidates.

Design (master plan §3): the Triage Analyst is read-only with respect to
the deterministic pipeline. It cannot override `gate_verdict`, modify
metrics, or promote to shadow without a passed locked holdout.

Implementation split (defense in depth):

    1. `derive_triage_state(candidate)` — pure Python. Decides graveyard /
       research / hardening_queue / shadow / decayed strictly from the
       candidate row's columns. No LLM involvement.

    2. `generate_triage_reason(candidate, state, llm_call)` — calls the
       injected LLM to write a one-paragraph rationale justifying the
       chosen state. The LLM is reduced to a scribe role; it never picks
       the state itself.

    3. `validate_triage_reason(reason, candidate)` — the linter from
       `_linter.py` confirms the reason cites only metrics that exist on
       the candidate. Hallucinations trigger a retry with the linter
       errors fed back into the prompt; after `max_retries` we leave the
       candidate untriaged and surface a HITL flag.

`run_triage_pass(repo, llm_call, limit)` walks every untriaged candidate
(up to limit) through this pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ta_foundation.agent.roles._linter import (
    LintResult,
    validate_triage_reason,
)
from ta_foundation.research_ledger.models import Candidate
from ta_foundation.research_ledger.repository import Repository

# (system, user) -> raw model response text
LLMCall = Callable[[str, str], str]

DEFAULT_MAX_RETRIES = 2

SYSTEM_PROMPT = (
    "You are the Triage Analyst for the ta_foundation research ledger. "
    "Your one job is to write a short rationale paragraph justifying a "
    "pre-determined triage state for a single candidate.\n\n"
    "Hard rules:\n"
    "- You DO NOT choose the state. The state is given to you.\n"
    "- You DO NOT invent metrics. Every number you write must appear on the "
    "candidate row exactly (rounding to one decimal is fine).\n"
    "- You DO NOT comment on whether the gate verdict was correct.\n"
    "- You MUST keep the rationale between 80 and 600 characters.\n"
    "- You MUST respond with a single JSON object: {\"reason\": \"...\"}. "
    "No markdown fences, no prose around it.\n"
)


# ---- Deterministic classification ----------------------------------------


def derive_triage_state(candidate: Candidate) -> str:
    """Return one of: 'graveyard', 'research', 'hardening_queue', 'shadow',
    'decayed'. Strictly a function of the candidate row.

    Decision table:
        gate_verdict == 'rejected'                                 → graveyard
        gate_verdict == 'pending'                                  → research
        gate_verdict == 'survivor', no holdout evaluated           → hardening_queue
        gate_verdict == 'survivor', holdout pf > 1.0               → shadow
        gate_verdict == 'survivor', holdout pf <= 1.0              → graveyard
        gate_verdict == 'survivor', holdout attempted but not run  → hardening_queue
    """
    verdict = (candidate.gate_verdict or "").lower()
    if verdict == "rejected":
        return "graveyard"
    if verdict == "pending":
        return "research"
    if verdict == "survivor":
        if candidate.pf_holdout is None or candidate.n_trades_holdout is None:
            return "hardening_queue"
        if candidate.pf_holdout > 1.0:
            return "shadow"
        return "graveyard"
    # Unknown verdict — keep it visible for human review.
    return "research"


# ---- LLM reason generation -----------------------------------------------


def _build_user_prompt(candidate: Candidate, state: str,
                        prior_violations: Optional[tuple[dict, ...]] = None) -> str:
    parts: list[str] = [
        f"candidate_id: {candidate.candidate_id}",
        f"chosen_state: {state}",
        f"gate_verdict: {candidate.gate_verdict}",
        f"pf_dev: {_fmt(candidate.pf_dev)}",
        f"n_trades_dev: {_fmt(candidate.n_trades_dev)}",
        f"pf_oos: {_fmt(candidate.pf_oos)}",
        f"n_trades_oos: {_fmt(candidate.n_trades_oos)}",
        f"pf_holdout: {_fmt(candidate.pf_holdout)}",
        f"n_trades_holdout: {_fmt(candidate.n_trades_holdout)}",
    ]
    if candidate.gate_reasons_json:
        parts.append(f"gate_reasons: {candidate.gate_reasons_json[:400]}")
    if candidate.notes_json:
        notes = json.loads(candidate.notes_json)
        compact = {k: notes[k] for k in
                   ("signal", "discovery_family", "tier_id", "tier_verdict")
                   if k in notes}
        if compact:
            parts.append(f"notes: {json.dumps(compact)[:300]}")

    parts.append("")
    parts.append("Write a one-paragraph rationale (80–600 chars) for the chosen_state.")
    parts.append("Only mention metric values that are listed above.")

    if prior_violations:
        parts.append("")
        parts.append("Your previous attempt was rejected for these reasons:")
        for v in prior_violations[:5]:
            parts.append(f"  - {v.get('code')}: {v.get('message')}")
        parts.append("Fix every issue. Do not cite metrics that are not in the row above.")

    parts.append("")
    parts.append("Respond with: {\"reason\": \"...\"}")
    return "\n".join(parts)


_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def parse_llm_reason(raw: str) -> Optional[str]:
    """Lenient parse: extract the first {"reason": "..."} JSON object."""
    if not isinstance(raw, str):
        return None
    # Try direct JSON parse first.
    text = raw.strip()
    for blob in (text, *_JSON_OBJ_RE.findall(text)):
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("reason"), str):
            return data["reason"].strip()
    return None


def generate_triage_reason(
    candidate: Candidate,
    state: str,
    llm_call: LLMCall,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[Optional[str], list[dict]]:
    """Ask the LLM for a rationale, validate it, retry up to `max_retries`
    feeding violations back into the prompt. Returns (reason, all_violations)
    where reason is None if no attempt produced a clean result."""
    prior: tuple[dict, ...] = ()
    accumulated: list[dict] = []
    for attempt in range(max_retries + 1):
        user = _build_user_prompt(candidate, state, prior_violations=prior)
        raw = llm_call(SYSTEM_PROMPT, user)
        reason = parse_llm_reason(raw)
        if reason is None:
            prior = ({"code": "unparseable_response",
                       "message": "LLM did not return valid JSON with a 'reason' field"},)
            accumulated.extend(prior)
            continue
        result = validate_triage_reason(reason, candidate)
        if result.ok:
            return reason, accumulated
        prior = result.violations
        accumulated.extend(prior)
    return None, accumulated


# ---- Run pass ------------------------------------------------------------


@dataclass
class TriagePassReport:
    scanned: int = 0
    triaged: int = 0
    hitl_flagged: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "triaged": self.triaged,
            "hitl_flagged": self.hitl_flagged,
            "by_state": dict(self.by_state),
            "n_failures": len(self.failures),
            "failures": self.failures[:20],
        }


def run_triage_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    limit: int = 25,
    max_retries: int = DEFAULT_MAX_RETRIES,
    triaged_by: str = "agent:triage",
) -> TriagePassReport:
    """Triage up to `limit` untriaged candidates. Idempotent: candidates
    that are already triaged are not re-processed.
    """
    report = TriagePassReport()
    candidates = repo.list_candidates(untriaged_only=True, limit=limit)
    for c in candidates:
        report.scanned += 1
        state = derive_triage_state(c)
        try:
            reason, all_violations = generate_triage_reason(
                c, state, llm_call, max_retries=max_retries,
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-candidate failures
            report.hitl_flagged += 1
            report.failures.append({
                "candidate_id": c.candidate_id,
                "derived_state": state,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if reason is None:
            report.hitl_flagged += 1
            report.failures.append({
                "candidate_id": c.candidate_id,
                "derived_state": state,
                "violations": all_violations[:6],
            })
            continue
        try:
            repo.set_triage(
                candidate_id=c.candidate_id,
                state=state,
                reason=reason,
                triaged_by=triaged_by,
            )
        except Exception as exc:  # noqa: BLE001
            report.failures.append({
                "candidate_id": c.candidate_id,
                "derived_state": state,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        report.triaged += 1
        report.by_state[state] = report.by_state.get(state, 0) + 1
    return report


# ---- Helpers -------------------------------------------------------------


def _fmt(v) -> str:
    if v is None:
        return "<none>"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


# ---- Production LLM wiring (lazy) ----------------------------------------


def make_ollama_llm_call(model: str = "llama3.1", temperature: float = 0.0) -> LLMCall:
    """Build an LLMCall backed by langchain-ollama. Imported lazily so unit
    tests don't pay the import cost.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama

    chat = ChatOllama(model=model, temperature=temperature)

    def llm_call(system: str, user: str) -> str:
        resp = chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        content = resp.content
        if isinstance(content, list):
            # ChatOllama can return list-of-content-parts; flatten to text.
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                              for part in content)
        return str(content)

    return llm_call
