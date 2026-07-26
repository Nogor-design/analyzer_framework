"""Hypothesis Author — proposes new pre-registered hypotheses.

The Author is the only role allowed to originate hypotheses. It is bound
by code-side guardrails layered exactly the way the master plan §1 and
phase C doc require:

    1. Schema validation (LLM must produce well-formed JSON).
    2. Family whitelist: family must be in the registry; legacy_imported
       is forbidden for forward authoring.
    3. Coverage cap: ≤ 40% of session_quota in any one family.
    4. Param whitelist: every param must satisfy the family spec (handled
       inside the existing author_probe write tool).
    5. Dedupe: same (family, instrument, timeframe, session_window,
       direction, params, mechanism) → DuplicateHypothesisError caught
       by author_probe and surfaced as a per-proposal failure.
    6. Graveyard collision: similar hypothesis already graveyarded →
       requires `revival_reason ≥ 30 chars` (handled by author_probe).
    7. Quota: max `session_quota` new hypotheses per session; max
       `weekly_quota` across the trailing 7 days (counted from the ledger).

If the LLM session exhausts retries without producing any valid proposal,
the call returns a HITL failure record. Successful registrations always
write one proposal draft per hypothesis to `runs/inbox/proposals/`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ta_foundation.agent.tools.write.author_probe import author_probe
from ta_foundation.research_ledger import (
    get_family_spec,
    list_probe_families,
)
from ta_foundation.research_ledger.repository import Repository

LLMCall = Callable[[str, str], str]

DEFAULT_SESSION_QUOTA = 5
DEFAULT_WEEKLY_QUOTA = 25
COVERAGE_CAP_FRACTION = 0.40
DEFAULT_MAX_RETRIES = 1

INBOX_ROOT = Path("runs/inbox")
PROPOSAL_TYPE_DIR = "proposals"

FORBIDDEN_FAMILIES = frozenset({"legacy_imported"})

REGISTERED_BY = "agent:hypothesis_author"

SYSTEM_PROMPT = (
    "You are the Hypothesis Author for the ta_foundation research ledger. "
    "Your job: propose N new pre-registered hypotheses drawn STRICTLY from "
    "the family registry provided below.\n\n"
    "Hard rules (the system will reject violations):\n"
    "- Choose `family` only from the AVAILABLE FAMILIES list. "
    "Do not invent families. Do not propose the `legacy_imported` family.\n"
    "- Every `params` key MUST appear in the family's whitelist. Use values "
    "inside the documented ranges. Lists (e.g. [5, 15, 30]) are allowed when "
    "the underlying type permits.\n"
    "- `mechanism` must be ≥ 50 characters and describe a structural "
    "counterparty story — who is on the other side, why they trade that way, "
    "what would cause the edge to decay.\n"
    "- No family may receive more than 40% of this session's proposals. "
    "Prefer families that are underrepresented in the recent COVERAGE block "
    "below.\n"
    "- If a similar hypothesis is in the GRAVEYARD block, you may revive it "
    "only by including a `revival_reason` field (≥ 30 chars) naming what is "
    "materially different now.\n"
    "- Respond with a single JSON object:\n"
    "  {\"proposals\": [\n"
    "    {\"family\": str, \"instrument\": str, \"timeframe\": str, "
    "\"session_window\": str|null, \"direction\": \"long\"|\"short\"|\"both\"|null, "
    "\"params\": {...}, \"mechanism\": str, "
    "\"revival_reason\": str|null}\n"
    "  ]}\n"
    "  No prose, no markdown fences, no apologies."
)


# ---- Reports ---------------------------------------------------------------


@dataclass
class ProposalRecord:
    hypothesis_id: Optional[str]
    family: Optional[str]
    instrument: Optional[str]
    accepted: bool
    rejection_code: Optional[str] = None
    rejection_message: Optional[str] = None
    draft_path: Optional[str] = None


@dataclass
class AuthorReport:
    requested: int = 0
    parsed: int = 0
    accepted: int = 0
    rejected: int = 0
    quota_remaining_session: int = 0
    quota_remaining_week: int = 0
    coverage_pre: dict[str, int] = field(default_factory=dict)
    proposals: list[ProposalRecord] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "parsed": self.parsed,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "quota_remaining_session": self.quota_remaining_session,
            "quota_remaining_week": self.quota_remaining_week,
            "coverage_pre": dict(self.coverage_pre),
            "proposals": [
                {
                    "hypothesis_id": p.hypothesis_id,
                    "family": p.family,
                    "instrument": p.instrument,
                    "accepted": p.accepted,
                    "rejection_code": p.rejection_code,
                    "rejection_message": p.rejection_message,
                    "draft_path": p.draft_path,
                }
                for p in self.proposals
            ],
            "n_failures": len(self.failures),
            "failures": self.failures[:20],
        }


# ---- Context gathering ---------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _week_ago_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_family_block(repo: Repository) -> str:
    parts: list[str] = []
    for fam in list_probe_families(repo):
        if fam.family_id in FORBIDDEN_FAMILIES:
            continue
        whitelist: list[str] = []
        for p in fam.params:
            if p.type == "enum":
                whitelist.append(f"{p.name}({p.type}={list(p.values or ())})")
            elif p.min is not None or p.max is not None:
                whitelist.append(f"{p.name}({p.type} {p.min}..{p.max})")
            else:
                whitelist.append(f"{p.name}({p.type})")
        parts.append(
            f"- {fam.family_id}: {fam.description}\n"
            f"  params: {', '.join(whitelist)}"
        )
        if fam.mechanism_template:
            parts.append(f"  template: {fam.mechanism_template[:240]}")
    return "\n".join(parts)


def _build_coverage_block(coverage: dict[str, int]) -> str:
    if not coverage:
        return "(no prior agent-authored hypotheses; coverage is uniformly empty)"
    rows = sorted(coverage.items(), key=lambda kv: -kv[1])
    return "\n".join(f"  - {fam}: {n}" for fam, n in rows)


def _build_graveyard_block(repo: Repository, limit: int = 10) -> str:
    candidates = repo.list_graveyard(limit=limit)
    if not candidates:
        return "(graveyard is empty for the queried window)"
    lines: list[str] = []
    for c in candidates:
        h = repo.get_hypothesis(c.hypothesis_id)
        family = h.family if h else "?"
        instrument = h.instrument if h else "?"
        reason = (c.triage_reason or "").strip().replace("\n", " ")[:160]
        lines.append(f"  - {c.candidate_id}  family={family} instr={instrument}  "
                       f"reason={reason}")
    return "\n".join(lines)


def _build_user_prompt(
    n_proposals: int,
    family_block: str,
    coverage_block: str,
    graveyard_block: str,
    quota_session: int,
    quota_week: int,
    prior_violations: tuple[dict, ...] = (),
) -> str:
    parts = [
        f"Propose up to {n_proposals} new hypotheses.",
        f"Session quota remaining: {quota_session}.",
        f"Weekly quota remaining: {quota_week}.",
        "",
        "AVAILABLE FAMILIES (you must pick `family` from this list):",
        family_block,
        "",
        "COVERAGE (per-family count over the trailing 30 days, agent-authored):",
        coverage_block,
        "",
        "GRAVEYARD (recent rejections, do not propose near-duplicates without a "
        "revival_reason):",
        graveyard_block,
    ]
    if prior_violations:
        parts.append("")
        parts.append("Your previous attempt was rejected for these reasons:")
        for v in prior_violations[:8]:
            parts.append(f"  - {v.get('code')}: {v.get('message', '')}")
        parts.append("Fix every issue and try again with valid JSON.")
    parts.append("")
    parts.append("Respond with the JSON object described in the system message.")
    return "\n".join(parts)


# ---- LLM response parsing ------------------------------------------------


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_proposals(raw: str) -> tuple[Optional[list[dict]], Optional[dict]]:
    """Return (proposals, error). On success error is None."""
    if not isinstance(raw, str):
        return None, {"code": "non_string_response",
                       "message": "LLM returned a non-string response"}
    text = raw.strip()
    if text.startswith("```"):
        # Strip code fences.
        try:
            text = text.split("\n", 1)[1]
        except IndexError:
            return None, {"code": "empty_fence", "message": "code fence with no body"}
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Try direct, then any embedded {...} block.
    blob = text if text.startswith("{") else None
    if blob is None:
        m = _JSON_OBJ_RE.search(text)
        if m:
            blob = m.group(0)
    if blob is None:
        return None, {"code": "no_json_found",
                       "message": "no JSON object in response"}
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return None, {"code": "invalid_json", "message": str(exc)}
    if not isinstance(data, dict):
        return None, {"code": "not_an_object", "message": "JSON root must be an object"}
    proposals = data.get("proposals")
    if not isinstance(proposals, list):
        return None, {"code": "no_proposals_list",
                       "message": "JSON object must include a 'proposals' list"}
    return proposals, None


# ---- Validation passes ---------------------------------------------------


_REQUIRED_PROPOSAL_FIELDS = ("family", "instrument", "timeframe",
                             "params", "mechanism")


def _validate_basic_shape(p: dict) -> Optional[dict]:
    if not isinstance(p, dict):
        return {"code": "not_a_dict",
                "message": f"proposal must be a dict, got {type(p).__name__}"}
    for field in _REQUIRED_PROPOSAL_FIELDS:
        if field not in p or p[field] in (None, ""):
            return {"code": "missing_field",
                    "message": f"missing required field {field!r}"}
    if not isinstance(p["params"], dict):
        return {"code": "params_not_dict",
                "message": "params must be a dict"}
    if not isinstance(p["mechanism"], str) or len(p["mechanism"].strip()) < 50:
        return {"code": "mechanism_too_short",
                "message": "mechanism must be a string ≥ 50 chars"}
    fam = p["family"]
    if not isinstance(fam, str) or fam in FORBIDDEN_FAMILIES:
        return {"code": "forbidden_family",
                "message": f"family {fam!r} is forbidden for forward authoring"}
    direction = p.get("direction")
    if direction is not None and direction not in {"long", "short", "both"}:
        return {"code": "bad_direction",
                "message": f"direction must be long/short/both, got {direction!r}"}
    return None


def _check_coverage_cap(
    family: str,
    session_counter: Counter,
    session_quota: int,
) -> Optional[dict]:
    cap = max(1, int(session_quota * COVERAGE_CAP_FRACTION))
    if session_counter[family] >= cap:
        return {"code": "coverage_cap_exceeded",
                "message": (f"family {family!r} already has "
                            f"{session_counter[family]} proposals this session; "
                            f"cap is {cap} (= {int(COVERAGE_CAP_FRACTION * 100)}% "
                            f"of session_quota={session_quota})")}
    return None


# ---- Hypothesis ID generation --------------------------------------------


def _generate_hypothesis_id(family: str, params: dict, mechanism: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    blob = json.dumps({"family": family, "params": params,
                        "mechanism": mechanism.strip()},
                       sort_keys=True, ensure_ascii=True).encode("utf-8")
    short = hashlib.sha256(blob).hexdigest()[:8]
    fam_prefix = family[:16].replace("_", "")
    rand = uuid.uuid4().hex[:4]
    return f"h_auth_{ts}_{fam_prefix}_{short}_{rand}"


# ---- Inbox draft writer --------------------------------------------------


def _proposal_draft_dir() -> Path:
    return INBOX_ROOT / PROPOSAL_TYPE_DIR


def _write_proposal_draft(
    hypothesis_id: str,
    family: str,
    instrument: str,
    timeframe: str,
    session_window: Optional[str],
    direction: Optional[str],
    params: dict,
    mechanism: str,
    revival_reason: Optional[str],
    yaml_path: Optional[str],
) -> Path:
    target = _proposal_draft_dir() / f"{hypothesis_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---",
             "type: hypothesis_proposal",
             f"hypothesis_id: {hypothesis_id}",
             f"generated_at: {_now_iso()}",
             "generated_by: agent:hypothesis_author",
             "cites: []",
             "---",
             "",
             f"# Proposed hypothesis: {hypothesis_id}",
             "",
             f"- **Family:** {family}",
             f"- **Instrument / timeframe:** {instrument} / {timeframe}",
             f"- **Session window:** {session_window or '(any)'}",
             f"- **Direction:** {direction or '(any)'}",
             f"- **YAML draft:** {yaml_path or '(not emitted)'}",
             "",
             "## Mechanism",
             "",
             mechanism.strip(),
             ""]
    if revival_reason:
        lines.extend(["## Revival reason", "", revival_reason.strip(), ""])
    lines.extend(["## Params", "",
                  "```json",
                  json.dumps(params, indent=2, sort_keys=True),
                  "```",
                  ""])
    lines.extend([
        "---",
        "",
        "Accept this proposal in the inbox to keep the hypothesis open in the "
        "ledger. Reject to retire the hypothesis (status='retired') and move "
        "this draft to runs/rejected/proposals/.",
        "",
    ])
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ---- Main entry point ----------------------------------------------------


def propose_hypotheses(
    repo: Repository,
    *,
    llm_call: LLMCall,
    n_proposals: int = DEFAULT_SESSION_QUOTA,
    session_quota: int = DEFAULT_SESSION_QUOTA,
    weekly_quota: int = DEFAULT_WEEKLY_QUOTA,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AuthorReport:
    """Run one authoring session — propose, validate, register, draft."""
    report = AuthorReport(requested=n_proposals)
    report.coverage_pre = repo.count_hypotheses_by_family(
        since=(datetime.now(timezone.utc) - timedelta(days=30))
              .strftime("%Y-%m-%dT%H:%M:%SZ"),
        registered_by=REGISTERED_BY,
    )

    week_count = sum(repo.count_hypotheses_by_family(
        since=_week_ago_iso(), registered_by=REGISTERED_BY,
    ).values())
    report.quota_remaining_week = max(0, weekly_quota - week_count)
    if report.quota_remaining_week <= 0:
        report.failures.append({
            "code": "weekly_quota_exhausted",
            "message": (f"weekly quota of {weekly_quota} agent-authored "
                        "hypotheses is exhausted; no proposals this session"),
        })
        report.quota_remaining_session = 0
        return report

    effective_n = min(n_proposals, session_quota, report.quota_remaining_week)
    report.requested = effective_n
    report.quota_remaining_session = effective_n

    family_block = _build_family_block(repo)
    coverage_block = _build_coverage_block(report.coverage_pre)
    graveyard_block = _build_graveyard_block(repo)

    prior_violations: tuple[dict, ...] = ()
    parsed_proposals: list[dict] = []
    parse_error: Optional[dict] = None

    for _attempt in range(max_retries + 1):
        prompt = _build_user_prompt(
            effective_n, family_block, coverage_block, graveyard_block,
            quota_session=report.quota_remaining_session,
            quota_week=report.quota_remaining_week,
            prior_violations=prior_violations,
        )
        try:
            raw = llm_call(SYSTEM_PROMPT, prompt)
        except Exception as exc:  # noqa: BLE001
            report.failures.append({"code": "llm_exception",
                                      "message": f"{type(exc).__name__}: {exc}"})
            continue
        proposals, parse_error = parse_proposals(raw)
        if proposals is None:
            prior_violations = (parse_error,)
            continue
        parsed_proposals = proposals
        break

    if not parsed_proposals:
        if parse_error and parse_error not in report.failures:
            report.failures.append(parse_error)
        return report

    report.parsed = len(parsed_proposals)

    session_counter: Counter = Counter()
    quota_used = 0
    for p in parsed_proposals:
        if quota_used >= effective_n:
            report.proposals.append(ProposalRecord(
                hypothesis_id=None, family=p.get("family") if isinstance(p, dict) else None,
                instrument=p.get("instrument") if isinstance(p, dict) else None,
                accepted=False, rejection_code="session_quota_full",
                rejection_message=(f"session quota of {effective_n} already "
                                    "filled by earlier accepted proposals"),
            ))
            report.rejected += 1
            continue

        shape_violation = _validate_basic_shape(p)
        if shape_violation:
            report.proposals.append(ProposalRecord(
                hypothesis_id=None, family=p.get("family") if isinstance(p, dict) else None,
                instrument=p.get("instrument") if isinstance(p, dict) else None,
                accepted=False, rejection_code=shape_violation["code"],
                rejection_message=shape_violation["message"],
            ))
            report.rejected += 1
            continue

        family = p["family"]
        cap_violation = _check_coverage_cap(family, session_counter, effective_n)
        if cap_violation:
            report.proposals.append(ProposalRecord(
                hypothesis_id=None, family=family, instrument=p["instrument"],
                accepted=False, rejection_code=cap_violation["code"],
                rejection_message=cap_violation["message"],
            ))
            report.rejected += 1
            continue

        hypothesis_id = _generate_hypothesis_id(family, p["params"], p["mechanism"])
        tool_input = {
            "hypothesis_id": hypothesis_id,
            "family": family,
            "instrument": p["instrument"],
            "timeframe": p["timeframe"],
            "session_window": p.get("session_window"),
            "direction": p.get("direction"),
            "params": p["params"],
            "mechanism": p["mechanism"],
            "registered_by": REGISTERED_BY,
        }
        revival = p.get("revival_reason")
        if isinstance(revival, str) and revival.strip():
            tool_input["revival_reason"] = revival.strip()

        result = author_probe(repo, **tool_input)
        if not result.get("ok"):
            report.proposals.append(ProposalRecord(
                hypothesis_id=hypothesis_id, family=family,
                instrument=p["instrument"], accepted=False,
                rejection_code=result.get("code"),
                rejection_message=str(result.get("error") or
                                      result.get("violations")),
            ))
            report.rejected += 1
            continue

        body = result.get("result") or {}
        if not body.get("registered"):
            report.proposals.append(ProposalRecord(
                hypothesis_id=hypothesis_id, family=family,
                instrument=p["instrument"], accepted=False,
                rejection_code=body.get("code", "duplicate_hypothesis"),
                rejection_message=("duplicate of existing "
                                    f"{body.get('existing_hypothesis_id')}"),
            ))
            report.rejected += 1
            continue

        draft_path = _write_proposal_draft(
            hypothesis_id=hypothesis_id, family=family,
            instrument=p["instrument"], timeframe=p["timeframe"],
            session_window=p.get("session_window"),
            direction=p.get("direction"),
            params=p["params"], mechanism=p["mechanism"],
            revival_reason=revival if isinstance(revival, str) else None,
            yaml_path=body.get("yaml_path"),
        )
        report.proposals.append(ProposalRecord(
            hypothesis_id=hypothesis_id, family=family,
            instrument=p["instrument"], accepted=True,
            draft_path=str(draft_path),
        ))
        report.accepted += 1
        session_counter[family] += 1
        quota_used += 1

    report.quota_remaining_session = max(0, effective_n - quota_used)
    report.quota_remaining_week = max(0, report.quota_remaining_week - quota_used)
    return report
