"""Numeric-claim and structural linters for LLM-generated narrative.

Two entry points, used by different roles:

    validate_triage_reason(reason, candidate) -> LintResult
        Used by the Triage Analyst (B.1). Confirms the reason text:
        - is 80–600 characters
        - any metric-shaped number it cites matches a real value on the
          candidate row, within rounding tolerance.

    validate_artifact_markdown(markdown, repo) -> LintResult
        Used by the Scribe (B.2). Parses the YAML frontmatter `cites:` block,
        loads the referenced ledger rows, and confirms:
        - every numeric token in the body traces back to a value on a cited
          row (or is one of the "prose" exceptions: year, single digit, ordinal).
        - every cited candidate_id exists in the ledger.

Both linters return a `LintResult(ok, violations)` so the caller can decide
whether to retry, surface to HITL, or accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml as _yaml

from ta_foundation.research_ledger import Repository
from ta_foundation.research_ledger.models import Candidate

# Float comparison tolerance (PF / expectancy values are usually quoted to 1
# or 2 decimal places). Tight enough to catch hallucinations like "PF=2.8"
# when reality is "PF=1.62".
FLOAT_ABS_TOLERANCE = 0.05
FLOAT_REL_TOLERANCE = 0.03  # 3% — for larger expectancy / net values
INT_TOLERANCE = 0  # trade counts must match exactly

MIN_TRIAGE_REASON_CHARS = 80
MAX_TRIAGE_REASON_CHARS = 600

# Tokens that look like numeric claims about strategy metrics:
#   - 1.62, 2.80, .85, 0.5
#   - 47, 120, 9999 (3+ digits → trade count / sample size)
# We deliberately ignore 1- and 2-digit ints (often enumerations, "N/2",
# session hours) and 4-digit years (2024–2030).
_FLOAT_RE = re.compile(r"(?<![A-Za-z_\d])-?\d*\.\d+")
_INT_RE = re.compile(r"(?<![A-Za-z_\d\.])\d{3,}(?!\.\d)")

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass(frozen=True)
class LintResult:
    ok: bool
    violations: tuple[dict, ...] = field(default_factory=tuple)


# ---- Public entrypoints ----------------------------------------------------


def validate_triage_reason(reason: str, candidate: Candidate) -> LintResult:
    if not isinstance(reason, str):
        return LintResult(False, ({"code": "not_a_string",
                                     "message": "reason must be a string"},))
    text = reason.strip()
    n = len(text)
    violations: list[dict] = []

    if n < MIN_TRIAGE_REASON_CHARS:
        violations.append({
            "code": "too_short",
            "message": f"reason length {n} < min {MIN_TRIAGE_REASON_CHARS}",
        })
    if n > MAX_TRIAGE_REASON_CHARS:
        violations.append({
            "code": "too_long",
            "message": f"reason length {n} > max {MAX_TRIAGE_REASON_CHARS}",
        })

    allowed_floats, allowed_ints = _candidate_metric_values(candidate)
    for tok in _FLOAT_RE.findall(text):
        try:
            v = float(tok)
        except ValueError:
            continue
        if not _matches_any_float(v, allowed_floats):
            violations.append({
                "code": "unmatched_float",
                "message": (f"reason cites {tok}; no candidate metric within "
                            f"±{FLOAT_ABS_TOLERANCE}"),
                "token": tok,
                "allowed": sorted(allowed_floats),
            })
    for tok in _INT_RE.findall(text):
        try:
            v = int(tok)
        except ValueError:
            continue
        if _YEAR_RE.match(tok):
            continue
        if v not in allowed_ints:
            violations.append({
                "code": "unmatched_int",
                "message": (f"reason cites {tok}; not in candidate trade counts "
                            f"{sorted(allowed_ints)}"),
                "token": tok,
                "allowed": sorted(allowed_ints),
            })

    return LintResult(ok=not violations, violations=tuple(violations))


def validate_artifact_markdown(
    markdown: str,
    repo: Repository,
    *,
    extra_allowed_floats: Optional[set[float]] = None,
    extra_allowed_ints: Optional[set[int]] = None,
) -> LintResult:
    """Lint a Scribe artifact: a markdown body with a YAML frontmatter
    `cites:` block listing the ledger rows the body is allowed to reference.

    ``extra_allowed_floats`` / ``extra_allowed_ints`` widen the allow-list
    beyond the cited candidates' own metric columns. Phase D.3 uses this for
    the daily shadow-health letter, whose numbers (trailing PF, expectancy,
    signal counts) come from aggregated ``shadow_signals`` rows rather than
    from the candidate row itself.
    """
    frontmatter, body = _split_frontmatter(markdown)
    if frontmatter is None:
        return LintResult(False, ({"code": "missing_frontmatter",
                                     "message": "artifact lacks YAML frontmatter"},))
    cites = frontmatter.get("cites") or []
    if not isinstance(cites, list):
        return LintResult(False, ({"code": "malformed_cites",
                                     "message": "frontmatter 'cites' must be a list"},))

    violations: list[dict] = []
    cited_candidates: list[Candidate] = []
    for entry in cites:
        if not isinstance(entry, dict):
            violations.append({"code": "cite_not_dict",
                                "message": f"cite entry must be a dict, got {type(entry).__name__}"})
            continue
        cid = entry.get("candidate_id")
        if cid is not None:
            c = repo.get_candidate(cid)
            if c is None:
                violations.append({"code": "unknown_candidate_cite",
                                    "message": f"cited candidate_id {cid!r} not in ledger",
                                    "candidate_id": cid})
            else:
                cited_candidates.append(c)

    # Aggregate the allowed metric values across every cited candidate.
    allowed_floats: set[float] = set()
    allowed_ints: set[int] = set()
    for c in cited_candidates:
        f, i = _candidate_metric_values(c)
        allowed_floats |= f
        allowed_ints |= i
    if extra_allowed_floats:
        allowed_floats |= {float(v) for v in extra_allowed_floats}
    if extra_allowed_ints:
        allowed_ints |= {int(v) for v in extra_allowed_ints}

    for tok in _FLOAT_RE.findall(body):
        try:
            v = float(tok)
        except ValueError:
            continue
        if not _matches_any_float(v, allowed_floats):
            violations.append({
                "code": "unmatched_float",
                "message": f"body cites float {tok} not covered by any cited candidate",
                "token": tok,
            })
    for tok in _INT_RE.findall(body):
        try:
            v = int(tok)
        except ValueError:
            continue
        if _YEAR_RE.match(tok):
            continue
        if v not in allowed_ints:
            violations.append({
                "code": "unmatched_int",
                "message": f"body cites int {tok} not covered by any cited candidate",
                "token": tok,
            })

    return LintResult(ok=not violations, violations=tuple(violations))


# ---- Internals -------------------------------------------------------------


def _candidate_metric_values(c: Candidate) -> tuple[set[float], set[int]]:
    floats: set[float] = set()
    ints: set[int] = set()
    for v in (c.pf_dev, c.pf_oos, c.pf_holdout,
              c.expectancy_dev, c.expectancy_oos, c.expectancy_holdout):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            floats.add(float(v))
    for v in (c.n_trades_dev, c.n_trades_oos, c.n_trades_holdout):
        if isinstance(v, int) and not isinstance(v, bool):
            ints.add(v)
    return floats, ints


def _matches_any_float(v: float, allowed: set[float]) -> bool:
    for a in allowed:
        if abs(v - a) <= FLOAT_ABS_TOLERANCE:
            return True
        if a != 0 and abs(v - a) / abs(a) <= FLOAT_REL_TOLERANCE:
            return True
    return False


def _split_frontmatter(markdown: str) -> tuple[Optional[dict], str]:
    if not markdown.startswith("---"):
        return None, markdown
    # Find the closing '---' on its own line.
    parts = markdown.split("\n", 1)
    if len(parts) < 2:
        return None, markdown
    rest = parts[1]
    end = rest.find("\n---")
    if end < 0:
        return None, markdown
    fm_text = rest[:end]
    body = rest[end + len("\n---"):].lstrip("\n")
    try:
        fm = _yaml.safe_load(fm_text) or {}
    except _yaml.YAMLError:
        return None, markdown
    if not isinstance(fm, dict):
        return None, markdown
    return fm, body
