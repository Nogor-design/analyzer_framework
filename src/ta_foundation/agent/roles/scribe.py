"""Scribe role — produces narrative artifacts from ledger data.

Two artifact types ship in B.2:

    Post-mortem        per graveyarded candidate, narrative explaining the
                       rejection. Drafts land at:
                         runs/inbox/post_mortems/<candidate_id>.md
                       Final acceptance moves to:
                         discovery/graveyard/<candidate_id>.md (via the
                         write_post_mortem write tool, journaled).

    Weekly letter      aggregate prose summarizing the trailing 7-day
                       ledger activity. Drafts at:
                         runs/inbox/weekly_letters/<iso-week>.md
                       Final: runs/letters/<iso-week>.md.

Phase D.3 adds a third artifact type:

    Shadow health letter  per Denver session day, narrative summarising the
                          ``ShadowHealthReport`` produced by ``shadow.health``.
                          Drafts at:
                            runs/inbox/shadow_health/<YYYY-MM-DD>.md
                          Final acceptance is not currently routed anywhere
                          on disk — acceptance just journals to the ledger.
                          The numbers consumed are aggregated from
                          ``shadow_signals`` rather than from candidate rows,
                          so the linter is widened via ``extra_allowed_*``.

Design (master plan §3 + §6 §"Why the LLM doesn't pick the state"):
the LLM generates ONLY the body markdown — no frontmatter, no preamble.
Python deterministically wraps the body in a YAML frontmatter `cites:`
block built from the known candidate IDs being narrated. The full artifact
is then validated by `validate_artifact_markdown` (B.3 linter); any
hallucinated number triggers a retry with the violation list fed back into
the prompt. After `max_retries` we leave a `_LINT_FAIL` marker draft for
HITL review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from ta_foundation.agent.roles._linter import (
    LintResult,
    validate_artifact_markdown,
)
from ta_foundation.research_ledger.models import Candidate
from ta_foundation.research_ledger.repository import Repository

LLMCall = Callable[[str, str], str]

DEFAULT_MAX_RETRIES = 2

# Output roots. Module-level so tests can monkeypatch with tmp_path.
INBOX_ROOT = Path("runs/inbox")
LETTERS_FINAL_DIR = Path("runs/letters")

# Body length bounds — matching B.5 / write_post_mortem schema.
POST_MORTEM_MIN_BODY_CHARS = 200
POST_MORTEM_MAX_BODY_CHARS = 8000
WEEKLY_LETTER_MIN_BODY_CHARS = 400
WEEKLY_LETTER_MAX_BODY_CHARS = 8000
SHADOW_HEALTH_MIN_BODY_CHARS = 300
SHADOW_HEALTH_MAX_BODY_CHARS = 8000


POST_MORTEM_SYSTEM_PROMPT = (
    "You are the Scribe writing a post-mortem for a graveyarded research "
    "candidate. Output ONLY the markdown body — no frontmatter, no preamble, "
    "no JSON, no code fences, no apologies.\n\n"
    "Hard rules:\n"
    "- Every metric value you cite (PF, trade counts, expectancy) must appear "
    "in the data block below. Rounding to one decimal is fine.\n"
    "- Reference the candidate by its candidate_id at least once.\n"
    "- Keep the body between 200 and 8000 characters.\n"
    "- Focus on the structural reason for rejection (gate failure, sample "
    "size, mechanism plausibility). Do not speculate beyond the data."
)


SHADOW_HEALTH_SYSTEM_PROMPT = (
    "You are the Scribe writing the daily shadow-health letter. The "
    "research program is in forward-observation mode: a small basket of "
    "candidates is firing simulated signals against current bars. Your job "
    "is to translate today's numbers into an operator-ready briefing. "
    "Output ONLY the markdown body — no frontmatter, no preamble, no JSON, "
    "no code fences, no apologies.\n\n"
    "Hard rules:\n"
    "- Every metric value you cite (PF, win rate, expectancy, net PnL, "
    "signal counts) must appear in the DATA BLOCK below. Rounding to one "
    "decimal is fine.\n"
    "- Reference at least one candidate by its candidate_id. The cites "
    "block Python adds for you will only allow the candidates listed in "
    "the DATA BLOCK.\n"
    "- Keep the body between 300 and 8000 characters.\n"
    "- Cover: today's signal activity, today's resolved PnL, trailing "
    "edge (per candidate or aggregated), open positions, and any "
    "anomalies. Call out divergence between trailing PF and today's "
    "outcomes if material. Suggest a next step only if the data "
    "supports it — do not speculate."
)


WEEKLY_LETTER_SYSTEM_PROMPT = (
    "You are the Scribe writing the weekly research letter summarising the "
    "trailing 7-day ledger activity. Output ONLY the markdown body — no "
    "frontmatter, no preamble, no JSON, no code fences, no apologies.\n\n"
    "Hard rules:\n"
    "- Every metric value you cite must appear in the data block below.\n"
    "- Reference at least one candidate by its candidate_id; the cites block "
    "Python adds for you will only allow those you actually name.\n"
    "- Keep the body between 400 and 8000 characters.\n"
    "- Cover: how many hypotheses were tested, how many survived, which "
    "families saw the most activity, any notable graveyard entries."
)


# ===========================================================================
# Post-mortem
# ===========================================================================


@dataclass
class PostMortemReport:
    scanned: int = 0
    written: int = 0
    skipped_existing: int = 0
    hitl_flagged: int = 0
    by_candidate: dict[str, str] = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "written": self.written,
            "skipped_existing": self.skipped_existing,
            "hitl_flagged": self.hitl_flagged,
            "by_candidate": dict(self.by_candidate),
            "n_failures": len(self.failures),
            "failures": self.failures[:20],
        }


def _post_mortem_inbox_dir() -> Path:
    return INBOX_ROOT / "post_mortems"


def _post_mortem_final_dir() -> Path:
    return Path("discovery/graveyard")


def _post_mortem_draft_path(candidate_id: str) -> Path:
    return _post_mortem_inbox_dir() / f"{candidate_id}.md"


def _post_mortem_final_path(candidate_id: str) -> Path:
    return _post_mortem_final_dir() / f"{candidate_id}.md"


def has_post_mortem_draft_or_final(candidate_id: str) -> bool:
    return (_post_mortem_draft_path(candidate_id).exists()
            or _post_mortem_final_path(candidate_id).exists())


def _build_candidate_data_block(c: Candidate) -> str:
    parts = [
        f"candidate_id: {c.candidate_id}",
        f"hypothesis_id: {c.hypothesis_id}",
        f"gate_verdict: {c.gate_verdict}",
        f"pf_dev: {c.pf_dev}",
        f"n_trades_dev: {c.n_trades_dev}",
        f"pf_oos: {c.pf_oos}",
        f"n_trades_oos: {c.n_trades_oos}",
        f"pf_holdout: {c.pf_holdout}",
        f"n_trades_holdout: {c.n_trades_holdout}",
    ]
    if c.gate_reasons_json:
        parts.append(f"gate_reasons: {c.gate_reasons_json[:600]}")
    if c.notes_json:
        try:
            notes = json.loads(c.notes_json)
            compact = {k: notes[k] for k in
                       ("signal", "discovery_family", "tier_id", "tier_verdict",
                        "hardening_issues")
                       if k in notes}
            if compact:
                parts.append(f"notes: {json.dumps(compact)[:500]}")
        except json.JSONDecodeError:
            pass
    return "\n".join(parts)


def _frontmatter_for_candidates(
    artifact_type: str, candidate_ids: list[str],
    *, extra: Optional[dict] = None,
) -> str:
    lines = ["---", f"type: {artifact_type}",
             f"generated_at: {_now_iso()}",
             "generated_by: agent:scribe",
             "cites:"]
    for cid in candidate_ids:
        lines.append(f"  - candidate_id: {cid}")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _generate_body_with_retries(
    system_prompt: str,
    data_block: str,
    candidate_ids: list[str],
    artifact_type: str,
    repo: Repository,
    llm_call: LLMCall,
    *,
    max_retries: int,
    min_body: int,
    max_body: int,
    extra_frontmatter: Optional[dict] = None,
    extra_allowed_floats: Optional[set[float]] = None,
    extra_allowed_ints: Optional[set[int]] = None,
) -> tuple[Optional[str], list[dict]]:
    """Common loop: ask LLM for body → wrap with cites frontmatter → lint →
    retry on failure. Returns the FULL artifact text on success.

    ``extra_allowed_floats`` / ``extra_allowed_ints`` widen the linter's
    allow-list for artifacts whose numbers come from outside the cited
    candidate rows (e.g. Phase D.3 shadow-health letters).
    """
    prior: tuple[dict, ...] = ()
    accumulated: list[dict] = []
    for attempt in range(max_retries + 1):
        user = _build_user_prompt(data_block, prior, candidate_ids,
                                   min_body=min_body, max_body=max_body)
        raw_body = llm_call(system_prompt, user)
        body = _strip_codefences(raw_body).strip()
        if not body:
            prior = ({"code": "empty_body", "message": "LLM returned empty body"},)
            accumulated.extend(prior)
            continue
        if len(body) < min_body:
            prior = ({"code": "body_too_short",
                       "message": f"body length {len(body)} < min {min_body}"},)
            accumulated.extend(prior)
            continue
        if len(body) > max_body:
            prior = ({"code": "body_too_long",
                       "message": f"body length {len(body)} > max {max_body}"},)
            accumulated.extend(prior)
            continue

        artifact = _frontmatter_for_candidates(
            artifact_type, candidate_ids, extra=extra_frontmatter,
        ) + body + "\n"
        lint = validate_artifact_markdown(
            artifact,
            repo,
            extra_allowed_floats=extra_allowed_floats,
            extra_allowed_ints=extra_allowed_ints,
        )
        if lint.ok:
            return artifact, accumulated
        prior = lint.violations
        accumulated.extend(prior)
    return None, accumulated


def _build_user_prompt(
    data_block: str,
    prior_violations: tuple[dict, ...],
    candidate_ids: list[str],
    *,
    min_body: int,
    max_body: int,
) -> str:
    parts = [
        "DATA BLOCK (your only allowed source of numeric values):",
        data_block,
        "",
        "Candidate IDs you may reference in the body:",
    ]
    for cid in candidate_ids:
        parts.append(f"  - {cid}")
    parts.append("")
    parts.append(f"Write the markdown body. Length: {min_body}–{max_body} chars.")
    if prior_violations:
        parts.append("")
        parts.append("Your previous attempt was rejected for these reasons:")
        for v in prior_violations[:6]:
            code = v.get("code")
            msg = v.get("message")
            parts.append(f"  - {code}: {msg}")
        parts.append("Fix every issue. Cite only numbers from the DATA BLOCK above.")
    return "\n".join(parts)


def _write_draft(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_hitl_failure_draft(
    inbox_path: Path, artifact_type: str,
    candidate_ids: list[str],
    violations: list[dict],
    last_attempt: Optional[str],
) -> Path:
    # A clearly-labelled placeholder draft so the inbox surface can see
    # there's a failed attempt awaiting human attention.
    target = inbox_path.with_name(inbox_path.stem + "_LINT_FAIL.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = ["---",
            f"type: {artifact_type}_lint_failure",
            f"generated_at: {_now_iso()}",
            "generated_by: agent:scribe",
            "lint_violations:"]
    for v in violations[:20]:
        body.append(f"  - code: {v.get('code')}")
        body.append(f"    message: {json.dumps(v.get('message', ''))}")
    body.append("---")
    body.append("")
    body.append(f"# Scribe lint failure for {artifact_type}")
    body.append("")
    body.append(f"Candidate IDs: {', '.join(candidate_ids) or '<none>'}\n")
    if last_attempt:
        body.append("## Last LLM attempt (rejected)\n")
        body.append("```")
        body.append(last_attempt[:4000])
        body.append("```")
    target.write_text("\n".join(body), encoding="utf-8")
    return target


def write_post_mortem_draft(
    repo: Repository,
    candidate: Candidate,
    llm_call: LLMCall,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[Optional[Path], list[dict]]:
    """Generate, lint, and write a post-mortem draft. Returns the draft
    path on success or None if the LLM never produced a clean body."""
    data_block = _build_candidate_data_block(candidate)
    artifact, violations = _generate_body_with_retries(
        POST_MORTEM_SYSTEM_PROMPT, data_block,
        candidate_ids=[candidate.candidate_id],
        artifact_type="post_mortem",
        repo=repo, llm_call=llm_call, max_retries=max_retries,
        min_body=POST_MORTEM_MIN_BODY_CHARS,
        max_body=POST_MORTEM_MAX_BODY_CHARS,
    )
    draft_path = _post_mortem_draft_path(candidate.candidate_id)
    if artifact is None:
        _write_hitl_failure_draft(draft_path, "post_mortem",
                                    [candidate.candidate_id], violations, None)
        return None, violations
    _write_draft(draft_path, artifact)
    return draft_path, violations


def run_post_mortem_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    limit: int = 25,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> PostMortemReport:
    """Walk graveyarded candidates lacking a draft or final post-mortem;
    generate drafts in `runs/inbox/post_mortems/`.
    """
    report = PostMortemReport()
    candidates = repo.list_candidates(triage_state="graveyard", limit=limit)
    for c in candidates:
        report.scanned += 1
        if has_post_mortem_draft_or_final(c.candidate_id):
            report.skipped_existing += 1
            continue
        try:
            path, violations = write_post_mortem_draft(
                repo, c, llm_call, max_retries=max_retries,
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-candidate failures
            report.hitl_flagged += 1
            report.failures.append({
                "candidate_id": c.candidate_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if path is None:
            report.hitl_flagged += 1
            report.failures.append({
                "candidate_id": c.candidate_id,
                "violations": violations[:6],
            })
            continue
        report.written += 1
        report.by_candidate[c.candidate_id] = str(path)
    return report


# ===========================================================================
# Weekly letter
# ===========================================================================


@dataclass
class WeeklyLetterReport:
    week_iso: str = ""
    candidates_considered: int = 0
    draft_path: Optional[str] = None
    hitl_flagged: bool = False
    violations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "week_iso": self.week_iso,
            "candidates_considered": self.candidates_considered,
            "draft_path": self.draft_path,
            "hitl_flagged": self.hitl_flagged,
            "n_violations": len(self.violations),
            "violations": self.violations[:10],
        }


def _weekly_letter_inbox_path(week_iso: str) -> Path:
    return INBOX_ROOT / "weekly_letters" / f"{week_iso}.md"


def _weekly_letter_final_path(week_iso: str) -> Path:
    return LETTERS_FINAL_DIR / f"{week_iso}.md"


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _build_weekly_data_block(
    repo: Repository, week_start: date,
) -> tuple[str, list[str]]:
    """Return (data_block_text, candidate_ids_cited)."""
    # Pull all candidates inserted within the trailing 7 days. The runs
    # table carries started_at; we read through it.
    end_iso = (datetime.combine(week_start + timedelta(days=7),
                                  datetime.min.time(),
                                  tzinfo=timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ"))
    start_iso = (datetime.combine(week_start, datetime.min.time(),
                                    tzinfo=timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ"))
    rows = repo.conn.execute(
        """
        SELECT c.candidate_id, c.gate_verdict, c.pf_dev, c.n_trades_dev,
               c.pf_oos, c.n_trades_oos, c.pf_holdout, c.n_trades_holdout,
               c.triage_state, h.family, h.instrument, h.timeframe
        FROM candidates c
        JOIN hypotheses h ON h.hypothesis_id = c.hypothesis_id
        JOIN runs r ON r.run_id = c.run_id
        WHERE r.started_at >= ? AND r.started_at < ?
        ORDER BY c.candidate_id
        LIMIT 30
        """,
        (start_iso, end_iso),
    ).fetchall()

    if not rows:
        return ("No new candidates were recorded between {start} and {end}."
                .format(start=start_iso, end=end_iso)), []

    family_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    cited_candidate_ids: list[str] = []
    lines: list[str] = []
    survivors = []
    rejected_n = 0
    for r in rows:
        family_counts[r["family"]] = family_counts.get(r["family"], 0) + 1
        st = r["triage_state"] or "untriaged"
        state_counts[st] = state_counts.get(st, 0) + 1
        if r["gate_verdict"] == "survivor":
            survivors.append(r)
        if r["gate_verdict"] == "rejected":
            rejected_n += 1
        cited_candidate_ids.append(r["candidate_id"])
        lines.append(
            f"candidate_id={r['candidate_id']} family={r['family']} "
            f"instrument={r['instrument']} pf_dev={r['pf_dev']} "
            f"n_trades_dev={r['n_trades_dev']} pf_oos={r['pf_oos']} "
            f"n_trades_oos={r['n_trades_oos']} pf_holdout={r['pf_holdout']} "
            f"n_trades_holdout={r['n_trades_holdout']} verdict={r['gate_verdict']} "
            f"triage_state={st}"
        )

    summary = [
        f"week_start: {start_iso}",
        f"week_end: {end_iso}",
        f"hypotheses_tested_window: {repo.count_hypotheses_tested(since=start_iso, until=end_iso)}",
        f"candidates_in_window: {len(rows)}",
        f"survivors_in_window: {len(survivors)}",
        f"rejected_in_window: {rejected_n}",
        "families_in_window: " + ", ".join(
            f"{fam}={n}" for fam, n in sorted(family_counts.items())
        ),
        "triage_states_in_window: " + ", ".join(
            f"{st}={n}" for st, n in sorted(state_counts.items())
        ),
        "",
        "CANDIDATE DETAIL:",
        *lines,
    ]
    return "\n".join(summary), cited_candidate_ids


def write_weekly_letter_draft(
    repo: Repository,
    *,
    llm_call: LLMCall,
    week_start: Optional[date] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> WeeklyLetterReport:
    if week_start is None:
        # Default to the Monday of the current ISO week.
        today = date.today()
        week_start = today - timedelta(days=today.isoweekday() - 1)
    week_iso = _iso_week(week_start)
    report = WeeklyLetterReport(week_iso=week_iso)

    data_block, cited_ids = _build_weekly_data_block(repo, week_start)
    report.candidates_considered = len(cited_ids)
    draft_path = _weekly_letter_inbox_path(week_iso)

    if not cited_ids:
        # Empty week → write a tiny stub instead of calling the LLM.
        stub = _frontmatter_for_candidates(
            "weekly_letter", [],
            extra={"week_iso": week_iso, "candidates_in_window": 0},
        ) + (
            f"# Weekly research letter for {week_iso}\n\n"
            "No new candidates were recorded in this window. "
            "Backfill or new probes can populate next week's letter.\n"
        )
        _write_draft(draft_path, stub)
        report.draft_path = str(draft_path)
        return report

    artifact, violations = _generate_body_with_retries(
        WEEKLY_LETTER_SYSTEM_PROMPT, data_block,
        candidate_ids=cited_ids,
        artifact_type="weekly_letter",
        repo=repo, llm_call=llm_call, max_retries=max_retries,
        min_body=WEEKLY_LETTER_MIN_BODY_CHARS,
        max_body=WEEKLY_LETTER_MAX_BODY_CHARS,
        extra_frontmatter={"week_iso": week_iso},
    )
    if artifact is None:
        _write_hitl_failure_draft(draft_path, "weekly_letter",
                                    cited_ids, violations, None)
        report.hitl_flagged = True
        report.violations = violations
        return report
    _write_draft(draft_path, artifact)
    report.draft_path = str(draft_path)
    return report


# ===========================================================================
# Shadow health letter (Phase D.3)
# ===========================================================================


@dataclass
class ShadowHealthLetterReport:
    report_date: str = ""
    candidates_considered: int = 0
    anomalies_in_window: int = 0
    draft_path: Optional[str] = None
    hitl_flagged: bool = False
    violations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date,
            "candidates_considered": self.candidates_considered,
            "anomalies_in_window": self.anomalies_in_window,
            "draft_path": self.draft_path,
            "hitl_flagged": self.hitl_flagged,
            "n_violations": len(self.violations),
            "violations": self.violations[:10],
        }


def _shadow_health_inbox_path(report_date: str) -> Path:
    return INBOX_ROOT / "shadow_health" / f"{report_date}.md"


def _build_shadow_health_data_block(
    health_report,
) -> tuple[str, list[str], set[float], set[int]]:
    """Render the deterministic data block the LLM is allowed to cite.

    Returns ``(text, candidate_ids, allowed_floats, allowed_ints)``.
    Every numeric token surfaced in the text is also added to the
    allowed sets so the linter accepts the body when the LLM echoes
    it (with rounding).
    """
    from ta_foundation.shadow.health import ShadowHealthReport  # local import

    if not isinstance(health_report, ShadowHealthReport):
        raise TypeError(
            "expected ShadowHealthReport, got "
            f"{type(health_report).__name__}"
        )

    floats: set[float] = set()
    ints: set[int] = set()

    def _add_num(v):
        if v is None:
            return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        if f != f:  # NaN
            return
        floats.add(f)
        if abs(f - round(f)) < 1e-9:
            ints.add(int(round(f)))

    lines: list[str] = []
    lines.append(f"report_date: {health_report.report_date}")
    lines.append(f"trailing_window: {health_report.trailing_window}")
    _add_num(health_report.trailing_window)
    lines.append(f"candidates_active: {health_report.candidates_active}")
    _add_num(health_report.candidates_active)
    lines.append(f"total_signals_today: {health_report.total_signals_today}")
    _add_num(health_report.total_signals_today)
    lines.append(f"total_resolved_today: {health_report.total_resolved_today}")
    _add_num(health_report.total_resolved_today)
    lines.append(f"total_no_fill_today: {health_report.total_no_fill_today}")
    _add_num(health_report.total_no_fill_today)
    lines.append(f"total_open_positions: {health_report.total_open_positions}")
    _add_num(health_report.total_open_positions)
    lines.append(f"total_net_pnl_today: {health_report.total_net_pnl_today:.2f}")
    _add_num(health_report.total_net_pnl_today)
    lines.append(f"anomalies_in_window: {len(health_report.anomalies)}")
    _add_num(len(health_report.anomalies))
    lines.append("")

    candidate_ids: list[str] = []
    if health_report.candidates:
        lines.append("CANDIDATE DETAIL:")
        for c in health_report.candidates:
            candidate_ids.append(c.candidate_id)
            row_parts = [
                f"candidate_id={c.candidate_id}",
                f"family={c.family or '-'}",
                f"instrument={c.instrument or '-'}",
                f"n_signals_today={c.n_signals_today}",
                f"n_resolved_today={c.n_resolved_today}",
                f"n_no_fill_today={c.n_no_fill_today}",
                f"n_open_positions={c.n_open_positions}",
                f"net_pnl_today={c.net_pnl_today:.2f}",
                f"trailing_pf={_fmt_for_data_block(c.trailing_pf)}",
                f"trailing_win_rate={_fmt_for_data_block(c.trailing_win_rate)}",
                f"trailing_win_rate_pct="
                + (
                    f"{c.trailing_win_rate * 100:.1f}"
                    if c.trailing_win_rate is not None
                    else "None"
                ),
                f"trailing_expectancy_net={_fmt_for_data_block(c.trailing_expectancy_net)}",
            ]
            for v in (
                c.n_signals_today,
                c.n_resolved_today,
                c.n_no_fill_today,
                c.n_open_positions,
                c.net_pnl_today,
                c.trailing_pf,
                c.trailing_win_rate,
                (c.trailing_win_rate * 100) if c.trailing_win_rate is not None else None,
                c.trailing_expectancy_net,
            ):
                _add_num(v)
            lines.append(" ".join(row_parts))

    if health_report.anomalies:
        lines.append("")
        lines.append("ANOMALIES:")
        for a in health_report.anomalies:
            lines.append(f"  - {a.code} [{a.candidate_id}]: {a.message}")

    return "\n".join(lines), candidate_ids, floats, ints


def _fmt_for_data_block(v) -> str:
    if v is None:
        return "None"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def write_shadow_health_letter_draft(
    repo: Repository,
    *,
    health_report,
    llm_call: LLMCall,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> ShadowHealthLetterReport:
    """Generate, lint, and persist a shadow-health letter draft.

    Numbers in the body must be present in the data block we feed the
    LLM, which is itself a direct read of ``ShadowHealthReport``. So the
    full numerical chain is: ``shadow_signals`` rows → aggregator →
    data block → LLM body → linter check against the same allow-list.
    """
    data_block, cited_ids, allowed_floats, allowed_ints = (
        _build_shadow_health_data_block(health_report)
    )
    report = ShadowHealthLetterReport(
        report_date=health_report.report_date,
        candidates_considered=len(cited_ids),
        anomalies_in_window=len(health_report.anomalies),
    )
    draft_path = _shadow_health_inbox_path(health_report.report_date)

    if not cited_ids:
        # No enrolled candidates → write a deterministic stub instead of
        # calling the LLM. Matches the empty-week weekly letter pattern.
        stub = _frontmatter_for_candidates(
            "shadow_health_letter", [],
            extra={
                "report_date": health_report.report_date,
                "candidates_active": health_report.candidates_active,
            },
        ) + (
            f"# Shadow health letter for {health_report.report_date}\n\n"
            "No candidates were enrolled in shadow on this session day. "
            "Enrol a candidate via `set_triage --state shadow` to begin "
            "forward observation; the next pass will pick it up.\n"
        )
        _write_draft(draft_path, stub)
        report.draft_path = str(draft_path)
        return report

    artifact, violations = _generate_body_with_retries(
        SHADOW_HEALTH_SYSTEM_PROMPT, data_block,
        candidate_ids=cited_ids,
        artifact_type="shadow_health_letter",
        repo=repo, llm_call=llm_call, max_retries=max_retries,
        min_body=SHADOW_HEALTH_MIN_BODY_CHARS,
        max_body=SHADOW_HEALTH_MAX_BODY_CHARS,
        extra_frontmatter={
            "report_date": health_report.report_date,
            "trailing_window": health_report.trailing_window,
        },
        extra_allowed_floats=allowed_floats,
        extra_allowed_ints=allowed_ints,
    )
    if artifact is None:
        _write_hitl_failure_draft(draft_path, "shadow_health_letter",
                                    cited_ids, violations, None)
        report.hitl_flagged = True
        report.violations = violations
        return report
    _write_draft(draft_path, artifact)
    report.draft_path = str(draft_path)
    return report


def run_shadow_health_pass(
    repo: Repository,
    *,
    llm_call: LLMCall,
    for_date=None,
    trailing_window: int = 30,
    open_position_age_warn_hours: int = 24,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> ShadowHealthLetterReport:
    """End-to-end Phase D.3 pass: aggregate → narrate → persist draft.

    Re-uses ``shadow.health.compute_daily_health`` so the numbers narrated
    match the deterministic markdown report 1:1. The two artifacts are
    intended to ship side-by-side: a numbers file (D.2) and a prose file
    (D.3) for the same Denver session day.
    """
    from ta_foundation.shadow.health import compute_daily_health  # local import

    health_report = compute_daily_health(
        repo,
        for_date=for_date,
        trailing_window=trailing_window,
        open_position_age_warn_hours=open_position_age_warn_hours,
    )
    return write_shadow_health_letter_draft(
        repo,
        health_report=health_report,
        llm_call=llm_call,
        max_retries=max_retries,
    )


def _strip_codefences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # Find the first newline after ``` and the trailing ``` line.
        try:
            first_nl = t.index("\n")
        except ValueError:
            return ""
        rest = t[first_nl + 1:]
        if rest.endswith("```"):
            rest = rest[: -3]
        return rest
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
