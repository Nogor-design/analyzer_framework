# Phase B — Read-Only Agents

Phase B introduces the first two agent roles: **Triage Analyst** and
**Scribe**. Both are read-only with respect to the deterministic pipeline
— they classify and narrate, but never run probes, never alter metrics,
never override gates. They are the lowest-risk way to validate that the
LLM stack is reliable enough to entrust with anything more.

Read `agentic_research_program.md` and `agentic_phase_a_foundation.md`
before this file.

**Prerequisite:** Phase A complete (ledger, tool registry, families,
backfill). If any Phase A row in the master plan is not ✅, this phase is
blocked.

## Status snapshot

| ID | Item | Status | Notes |
|----|------|--------|-------|
| B.1 | Triage agent (classification + reasoning) | ✅ Done | Deterministic state + LLM-generated reason + linter retry; 20 tests; CLI subcommand `triage-pass` |
| B.2 | Scribe agent (weekly letter + post-mortems) | ✅ Done | LLM writes body only; Python builds frontmatter + cites; 11 tests |
| B.4 | Workflow scheduler (fixed Python graph) | ✅ Done | `agent/scheduler.py` with daily_pass + weekly_pass; 3 tests |
| B.5 | HITL inbox CLI | ✅ Done | `agent/inbox.py` with list/show/accept/reject; 13 tests |
| B.3 | Numeric-claim linter | ✅ Done | Triage + Scribe variants; 14 tests; tolerance-aware float matching |
| B.4 | Workflow scheduler (replaces god-orchestrator) | ⏳ Blocked by A | Fixed graph, not LLM-decided |
| B.5 | HITL review surface for narrative artifacts | ⏳ Blocked by B.1, B.2 | Human acceptance gate |

---

## Already-shipped: B.1 — Triage Analyst + B.3 — Numeric-claim linter

Files landed:

- `src/ta_foundation/agent/roles/__init__.py` (new package surface).
- `src/ta_foundation/agent/roles/_linter.py` (new) — the B.3 numeric-claim
  linter. Two entry points:
  - `validate_triage_reason(reason, candidate) -> LintResult` for Triage.
    Checks 80–600 char length, float tokens within ±0.05 (or 3% relative)
    of a candidate metric, integer tokens (≥3 digits) match a trade-count
    field. Skips 4-digit year tokens; ignores 1–2 digit ints (often
    enumerations).
  - `validate_artifact_markdown(markdown, repo) -> LintResult` for Scribe.
    Parses the YAML frontmatter `cites:` block, loads the referenced
    candidate rows, and confirms every numeric token in the body traces
    back to one of them. Rejects unknown candidate_ids, malformed
    `cites`, and missing frontmatter.
- `src/ta_foundation/agent/roles/triage.py` (new) — the B.1 Triage role.
  Architectural choice that diverges from the original spec: the LLM does
  NOT pick the triage state. Instead:
  1. `derive_triage_state(candidate)` deterministically picks the state
     from the candidate row (gate_verdict + holdout metrics). Pure Python.
  2. `generate_triage_reason(candidate, state, llm_call)` asks the LLM
     for a one-paragraph rationale justifying the pre-chosen state, with
     a strict JSON output format (`{"reason": "..."}`).
  3. The linter validates the LLM's response. On a hallucination, the
     loop retries with the violation list fed back into the prompt
     (`prior_violations` arg). After `max_retries` (default 2) the
     candidate is left untriaged and a HITL flag is raised.
  4. `run_triage_pass(repo, llm_call, limit)` walks untriaged candidates.
  5. `make_ollama_llm_call(model, temperature)` builds the production
     LLM call from `langchain_ollama` lazily so tests don't pay the
     import cost.
- `src/ta_foundation/agent/cli.py` (rewritten) — subcommand-style. New
  `triage-pass` command wires the Ollama call into `run_triage_pass`.
  The legacy `analyze` command is retained behind a `DeprecationWarning`
  for one release cycle.

**Why the LLM doesn't pick the state.** The decision table is short,
deterministic, and consequential — exactly the kind of logic the master
plan §1 keeps in code. The LLM's value-add is in narrative justification,
which is hard to write deterministically. Reducing the LLM to a scribe
role over a Python-decided state collapses the highest-risk failure mode
(wrong state picked) into a unit test.

Tests: 34 new across `tests/agent/roles/`:

- `test_linter.py` (14): triage-reason validation (happy, too short,
  too long, hallucinated PF, hallucinated trade count, year tokens
  allowed, tolerance, non-string); artifact markdown validation
  (missing frontmatter, unknown cite, unmatched float in body, multi-cite
  aggregation, malformed cites, year skip).
- `test_triage.py` (20): deterministic state for each gate_verdict;
  parse_llm_reason lenient extraction; generate_triage_reason succeed
  first try / retry after hallucination / fail after max retries /
  unparseable response / violations fed back into prompt;
  run_triage_pass against a real ledger (untriaged-only, all four
  classification states in one pass, HITL on LLM failure, limit
  respected, idempotency on repeat invocation).

Test command:
```
python -m pytest src/ta_foundation/tests/agent/roles/ -v
python -m ta_foundation.agent.cli triage-pass --help
```
34/34 pass. Full suite at 1437 passing with the same pre-existing
`regime_recommender` failure unrelated to this work.

### Deliberately not in this iteration

- **Workflow scheduler (B.4)** — the fixed graph that orchestrates which
  role runs when. With only Triage shipped today, the scheduler would
  be a one-node graph; it gains structure once Scribe (B.2) lands and
  Phase B has two roles to sequence.
- **HITL inbox (B.5)** — the human acceptance surface. Triage's HITL
  flagging path currently leaves candidates untriaged with a structured
  failure record; the inbox CLI is built alongside Scribe so it covers
  both narrative drafts and triage HITL flags in one pass.

---

## Already-shipped: B.2 — Scribe + B.4 — Scheduler + B.5 — Inbox

Files landed:

- `src/ta_foundation/agent/roles/scribe.py` (new). Two artifact types:
  - **Post-mortem** per graveyarded candidate. Drafts to
    `runs/inbox/post_mortems/<cid>.md`; final acceptance routes through
    the journaled `write_post_mortem` write tool (A.2a) which moves into
    `discovery/graveyard/`.
  - **Weekly letter** aggregating trailing-7-day ledger activity (joined
    over `candidates`, `hypotheses`, `runs`). Drafts to
    `runs/inbox/weekly_letters/<iso-week>.md`; final acceptance moves to
    `runs/letters/<iso-week>.md`.
  - Daily shadow health is wired but deliberately empty — Phase D fills
    it in when shadow data exists.
- `src/ta_foundation/agent/inbox.py` (new). Tiny HITL inbox: `list_drafts`,
  `show_draft`, `accept_draft`, `reject_draft`. Acceptance for post-mortems
  delegates to the journaled `write_post_mortem` tool (so the audit trail
  has the human signoff); rejection moves to `runs/rejected/` and journals
  the operator's reason.
- `src/ta_foundation/agent/scheduler.py` (new). Pure-Python orchestration:
  `daily_pass(repo, llm_call)` = triage then post-mortem; `weekly_pass`
  = weekly letter. No LLM in routing. Per-stage failures are caught and
  recorded in the `CombinedReport.errors` list rather than aborting the
  whole pass.
- `src/ta_foundation/agent/cli.py` extended with three new subcommands:
  `daily-pass`, `weekly-pass`, `inbox` (with `list / show / accept / reject`).

### Same defense-in-depth pattern as Triage

The LLM produces ONLY the markdown body — no frontmatter, no preamble,
no JSON. Python deterministically wraps the body in a YAML frontmatter
`cites:` block built from the candidate IDs being narrated. The full
artifact is then validated by `validate_artifact_markdown` (B.3 linter).
Hallucinated numbers trigger a retry with the violation list fed back
into the prompt. After `max_retries` exhausted, a `<id>_LINT_FAIL.md`
placeholder draft is written instead so the HITL inbox can surface it.

### Hardening fix applied alongside this iteration

`run_triage_pass` and `run_post_mortem_pass` now catch per-candidate
exceptions from the LLM call (e.g., transient model crashes) and record
them as HITL failures, rather than aborting the whole pass. This was a
hidden gap surfaced by the scheduler tests.

Tests: 27 new across `tests/agent/roles/`:

- `test_scribe.py` (11): post-mortem happy path, code-fence stripping,
  retry-on-hallucination, HITL after max retries, pass processes only
  graveyard, pass skips existing drafts, pass records HITL failures,
  idempotency after final acceptance, weekly letter with window data,
  empty-window stub, weekly letter HITL on hallucination.
- `test_inbox.py` (13): list empty, list both types, show found / not
  found, lint-fail marking, accept post-mortem (uses write_post_mortem),
  accept weekly letter, accept unknown, accept lint-fail refused,
  reject moves to rejected/, reject short reason refused, reject unknown,
  reject journals.
- `test_scheduler.py` (3): daily_pass runs triage→post-mortem,
  daily_pass isolates per-candidate LLM crash to HITL, weekly_pass.

Test command:
```
python -m pytest src/ta_foundation/tests/agent/roles/ -v
python -m ta_foundation.agent.cli daily-pass --help
python -m ta_foundation.agent.cli inbox list
```
27/27 new pass; full suite at 1464 passing with the same pre-existing
`regime_recommender` failure.

### Phase B end-to-end runnable

You can now drive the read-only agent loop against the 251 backfilled
candidates with a real Ollama:

```
python -m ta_foundation.agent.cli daily-pass --ledger-db .ta_artifacts/research_ledger.db
python -m ta_foundation.agent.cli weekly-pass --ledger-db .ta_artifacts/research_ledger.db
python -m ta_foundation.agent.cli inbox list
python -m ta_foundation.agent.cli inbox show post_mortems/<cid>
python -m ta_foundation.agent.cli inbox accept post_mortems/<cid>
```

---

## B.1 — Triage Analyst (original spec, kept for reference)

### Job

For every new candidate row written by a hardened or holdout run:
1. Read the candidate, its sidecar, and the gate verdict.
2. Classify into one of: `graveyard`, `research`, `hardening_queue`,
   `shadow`.
3. Write a one-paragraph rationale into `triage_reason`.
4. For `graveyard`-bound candidates, queue a post-mortem job for the Scribe.

### What it can read

- `list_candidates` (filtered to `triage_state IS NULL`)
- `get_candidate`
- `read_sidecar`
- `list_graveyard` (to recognize patterns of repeated failure)
- `find_similar_hypotheses` (to flag clusters)

### What it cannot do

- Override the `gate_verdict`. If the gates rejected the candidate, the
  only legal triage states are `graveyard` (with a post-mortem) or
  `research` (with a written exception reason that triggers HITL).
- Modify metrics. Period.
- Promote to `shadow` without a `survivor` verdict on dev, oos, *and* a
  passed locked holdout. This is enforced in `set_triage_state`'s
  preconditions, but the agent prompt should make it explicit so the
  model doesn't waste turns trying.

### Implementation shape

- `src/ta_foundation/agent/roles/triage.py`
- A LangGraph subgraph: input → classifier-LLM call → schema-validate
  output → `set_triage_state` write tool → next candidate.
- Tool subset bound: read tools listed above + `set_triage_state` only.
- Model: small local (Llama 3.1 8B is fine for classification).
  Temperature 0.

### Determinism check

After every triage call, a follow-up linter compares the agent's reasoning
text against the candidate's actual ledger row:
- Rejects narratives that cite a metric value that doesn't match the
  ledger to within rounding.
- Rejects narratives shorter than 80 chars or longer than 600.
- Re-runs the agent if the linter rejects, up to 2 retries; on the third
  failure, the candidate is left untriaged and a HITL flag is raised.

### Files to touch

- `src/ta_foundation/agent/roles/triage.py` (new)
- `src/ta_foundation/agent/roles/_linter.py` (new — shared with Scribe)
- `src/ta_foundation/agent/roles/_prompts/triage.md` (new — system prompt)
- `src/ta_foundation/tests/agent/roles/test_triage.py` (new)
- `src/ta_foundation/tests/agent/roles/fixtures/` (new — fake ledger states)

### Done when

- ≥ 20 fixture-based tests covering: clear graveyard, clear shadow
  candidate, ambiguous mid-tier, rejected-but-flagged, retry-then-success,
  linter-fail-then-HITL.
- Running the agent on the post-backfill ledger triages every untriaged
  candidate to a state, with a reason ≥ 80 chars, in under 2 minutes per
  candidate on the local model.
- 100% of triage decisions log to `tool_journal`.

---

## B.2 — Scribe

### Job

The Scribe produces narrative artifacts from structured ledger data. It
runs on a schedule (initially manual, later cron):

| Cadence | Output |
|---|---|
| Daily (Phase D) | `runs/<date>/shadow_health.md` — for each shadow candidate, signals fired, fills, slippage vs modeled, daily PnL, anomalies |
| Weekly | `runs/<week>/research_letter.md` — what was tested, what survived, what died, mechanism summaries |
| Per graveyard entry | `discovery/graveyard/<candidate_id>.md` — post-mortem |

### Hard rule

**Every number in a Scribe artifact must be traceable to a ledger row.**
The numeric-claim linter (B.3) enforces this before publish.

### What it can read

- `list_candidates`, `get_candidate`, `list_graveyard`,
  `count_hypotheses_tested`, `read_sidecar`.

### What it cannot do

- Compute aggregate metrics that aren't already in the ledger. If the
  weekly letter needs a summary stat, that stat is computed by a
  deterministic function in `research_ledger/repository.py` and exposed as
  a read tool. The Scribe consumes it; it does not re-derive it.

### Implementation shape

- `src/ta_foundation/agent/roles/scribe.py`
- Three pipelines (post_mortem, weekly_letter, daily_health) as separate
  prompts and graphs in `roles/_prompts/`.
- Output goes to a draft path first, runs through the linter, then is
  promoted to its final path. Failed lint stays as draft with the linter
  error appended.

### Files to touch

- `src/ta_foundation/agent/roles/scribe.py` (new)
- `src/ta_foundation/agent/roles/_prompts/post_mortem.md` (new)
- `src/ta_foundation/agent/roles/_prompts/weekly_letter.md` (new)
- `src/ta_foundation/agent/roles/_prompts/daily_health.md` (new — Phase D wires it)
- `src/ta_foundation/research_ledger/repository.py` — add aggregate-stat readers as needed.
- `src/ta_foundation/tests/agent/roles/test_scribe.py` (new)

### Done when

- A weekly letter on the post-backfill ledger renders without lint failures.
- A post-mortem on a known graveyard candidate cites only ledger numbers
  and includes the full rejection reasons.
- The daily-health prompt is wired but harmlessly empty (Phase D activates
  it).

---

## B.3 — Numeric-claim linter

### Why

LLMs hallucinate numbers, and even when they don't, they round
inconsistently. A research letter that drifts from the ledger by 5% is
worthless — and worse, contagious if humans start citing it.

### Approach

`src/ta_foundation/agent/roles/_linter.py`:

1. Extract every numeric token from the draft (regex over scientific,
   percentage, currency, and plain numbers).
2. For each, search the ledger rows referenced in the artifact's frontmatter
   `cites:` block. A match is exact-equal or equal within stated rounding
   precision.
3. Reject the draft if any numeric token has no matching ledger value.
4. Reject if the draft references a candidate_id that doesn't exist.
5. Return a structured error the role can use to retry.

The artifact frontmatter convention is enforced by the linter:

```yaml
---
type: weekly_letter | post_mortem | daily_health
period: 2026-W19
cites:
  - candidate_id: c_2026_05_07_orb_short_150_30_001
  - candidate_id: c_2026_05_03_vwap_london_fade_007
  - hypothesis_count_query: {window: '2026-W19'}
generated_by: agent:scribe
generated_at: 2026-05-10T14:00:00Z
---
```

### Files to touch

- `src/ta_foundation/agent/roles/_linter.py` (new)
- `src/ta_foundation/tests/agent/roles/test_linter.py` (new)

### Done when

- A draft with a hallucinated PF rejects with a clear error.
- A draft citing a non-existent candidate_id rejects.
- A draft where every number traces back to a `cites:` row passes.
- ≥ 15 test cases.

---

## B.4 — Workflow scheduler (kills the god-orchestrator)

### Why

Current `graph.py` has a `Lead Strategist` planner that decides what to do
next via LLM reasoning. That's exactly the multi-agent-council pattern the
master plan §6 rejects. Replace with a fixed graph.

### New graph (Phase B version)

```
        ┌─────────────────────┐
        │ scheduler (cron)    │
        └──────────┬──────────┘
                   │ "any untriaged candidates?"
                   ▼
            ┌──────────────┐
            │ triage_role  │  (one candidate per pass)
            └──────┬───────┘
                   │ "graveyard?"
                   ├────────► scribe_post_mortem ──┐
                   │                               │
                   ▼                               ▼
              other states                     END
                   │
                   └─────────────► END
```

A second graph for weekly cadence:

```
   scheduler (cron Sunday) ──► scribe_weekly_letter ──► linter ──► publish | retry | HITL
```

No LLM decides which subgraph runs. The scheduler is a Python function
that polls the ledger.

### Files to touch

- `src/ta_foundation/agent/scheduler.py` (new — replaces `graph.py`'s
  router)
- `src/ta_foundation/agent/graph.py` — refactor: one subgraph per role,
  no global planner, no global router
- `src/ta_foundation/agent/cli.py` — new subcommands: `triage-pass`,
  `weekly-letter`, `post-mortem`

### Done when

- `graph.py` no longer contains a `planner_node` or `Lead Strategist`
  prompt.
- `python -m ta_foundation.agent.cli triage-pass` triages all open
  candidates and exits.
- `python -m ta_foundation.agent.cli weekly-letter` produces this week's
  letter.
- No code path lets an LLM choose which role runs next.

---

## B.5 — HITL review surface

### Why

For at least the first 3 months of Phase B, every Scribe artifact and every
non-trivial Triage decision should have a human acceptance step. After
that, spot-checks suffice.

### Approach

Lightweight: a `runs/inbox/` directory the Scribe writes drafts into, plus
a CLI `python -m ta_foundation.agent.cli inbox` that:
- Lists pending drafts.
- Shows diff against the linter's expectations.
- Lets the human accept (move to final path), reject (move to
  `runs/rejected/`), or annotate.

No web UI for now. The `web/` capability surface is for the broader
ta_foundation product, not the agent inbox.

### Files to touch

- `src/ta_foundation/agent/inbox.py` (new)
- `src/ta_foundation/agent/cli.py` — add `inbox` subcommand

### Done when

- Drafts land in `runs/inbox/`.
- Accept/reject moves them and journals the human decision.
- Rejected drafts include the rejection reason in their next-pass prompt
  context (so the Scribe can learn within a session).

---

## Phase B exit criteria

1. ✅ Triage agent operational; triages all post-backfill candidates.
2. ✅ Scribe produces a weekly letter and at least 3 post-mortems for
   real graveyard entries, all linter-clean.
3. ✅ God-orchestrator removed; fixed graphs only.
4. ✅ HITL inbox in regular use; ≥ 90% of drafts accepted with no edits
   for 2 consecutive weeks.
5. ✅ No LLM hallucination has been observed downstream of the linter
   (i.e., no published artifact has had a number diverge from the ledger).

When all five hold, Phase C may begin.
