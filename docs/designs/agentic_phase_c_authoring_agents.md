# Phase C — Authoring Agents

Phase C is where the agents start *originating* work: proposing
hypotheses and running probes. This is the highest-risk phase from a
research-integrity standpoint, because every agent action that authors a
new hypothesis contributes to the multiple-testing denominator. The
guardrails are accordingly heavy.

Read `agentic_research_program.md`, `agentic_phase_a_foundation.md`, and
`agentic_phase_b_read_only_agents.md` before this file.

**Prerequisite:** Phase B complete and stable for ≥ 4 weeks. If the
linter has caught hallucinations recently, do not start Phase C — the
LLM stack isn't reliable enough yet.

## Status snapshot

| ID | Item | Status | Notes |
|----|------|--------|-------|
| C.1 | Hypothesis Author with HITL inbox draft | ✅ Done | Deterministic guardrails (forbidden_family, coverage cap, quota, dedupe, graveyard) + LLM JSON proposals; 26 tests |
| C.2 | Sweep Operator with no-retry discipline | ✅ Done | Python-driven fast→hardened workflow; no-trades retires (not graveyards); locked_holdout never invoked; 16 tests |
| C.3 | Family-coverage tracking + 40% cap | ✅ Done | `get_family_coverage` read tool + cap enforced in author; 3 read-tool tests + author tests cover it |
| C.4 | Author/Operator workflow integration | ✅ Done | `weekly_authoring_pass` in `scheduler.py` + `weekly-authoring-pass` / `operator-pass` CLI subcommands; graph encoded in Python; 2 scheduler tests |
| C.5 | Multiple-testing accounting wired to ledger | ⏳ Blocked by C.4 | Connects to T12 in hardening plan |

---

## Already-shipped: C.1 — Hypothesis Author + C.3 — Family-coverage cap

Files landed:

- `src/ta_foundation/research_ledger/repository.py` — new
  `count_hypotheses_by_family(since, until, registered_by)` query for
  coverage and quota tracking.
- `src/ta_foundation/agent/tools/read/ledger.py` — new
  `get_family_coverage` journaled read tool.
- `src/ta_foundation/agent/roles/hypothesis_author.py` (new) — the C.1
  Author role.
- `src/ta_foundation/agent/inbox.py` — proposal-type handling. Accept
  moves the draft to `runs/proposals_accepted/`; reject moves to
  `runs/rejected/proposals/` AND retires the hypothesis (status='retired')
  so it stops contributing to the multiple-testing denominator.
- `src/ta_foundation/agent/scheduler.py` — `authoring_pass(repo, llm_call, …)`.
- `src/ta_foundation/agent/cli.py` — `authoring-pass` subcommand
  (defaults to temperature=0.2 — slightly creative since authoring is
  generative work).

### Defense-in-depth pattern

The Author's only LLM responsibility is producing a JSON object listing
proposed hypotheses. Every other concern is enforced in Python:

1. **Forbidden families**: `legacy_imported` and anything outside the
   registry are rejected at validation, before the LLM's proposal even
   reaches `author_probe`.
2. **Shape validation**: required fields present, params is a dict,
   mechanism ≥ 50 chars, direction in {long, short, both, null}.
3. **Coverage cap (C.3)**: ≤ 40% of session_quota in any single family.
   Computed live during the validation loop; the (N+1)-th proposal in a
   family is rejected with `coverage_cap_exceeded`.
4. **Quota**: session_quota (default 5) caps per-session output;
   weekly_quota (default 25) is checked against ledger history of
   `registered_by='agent:hypothesis_author'` registrations in the trailing
   7 days. An exhausted weekly quota aborts the session immediately.
5. **Whitelist + dedupe + graveyard collision**: delegated to the
   existing `author_probe` write tool from A.2a, which carries the
   schema-validated preconditions and dedupe_hash UNIQUE.
6. **Idempotent hypothesis IDs**: each accepted proposal gets a
   deterministic id `h_auth_<ts>_<family_prefix>_<hash>_<rand>` —
   timestamp + sha256(family+params+mechanism) so re-proposing the same
   thing collides predictably.

The LLM is given context (available families with whitelists, trailing
30-day coverage, recent graveyard rejections, current quota state) and
asked to respond with `{"proposals": [...]}`. On any structural failure,
the violation list is fed back into the prompt for a single retry.

### Inbox integration

Every successfully-registered hypothesis writes a draft markdown to
`runs/inbox/proposals/<hypothesis_id>.md` describing family / instrument
/ params / mechanism / yaml path. The operator reviews via:

```
python -m ta_foundation.agent.cli inbox list
python -m ta_foundation.agent.cli inbox show proposals/<hypothesis_id>
python -m ta_foundation.agent.cli inbox accept proposals/<hypothesis_id>
python -m ta_foundation.agent.cli inbox reject proposals/<hypothesis_id> --reason "..."
```

`accept` is a signoff — the hypothesis stays open in the ledger.
`reject` retires the hypothesis so it stops counting against the
multiple-testing denominator AND removes it from family-coverage going
forward.

### Tests (29 new)

- `tests/agent/roles/test_hypothesis_author.py` (26): proposal parsing
  (direct JSON, code fences, embedded JSON, missing list, invalid JSON,
  no JSON anywhere, non-string); happy path (single + multi-family);
  validation rejections (forbidden_family, unknown_family,
  mechanism_too_short, params_not_in_whitelist, missing_field,
  bad_direction, duplicate_hypothesis); coverage cap (blocks
  over-concentration, allows diverse mix); quota (session quota fills,
  weekly quota exhausts the session); LLM failures (unparseable retries,
  exception recorded, always-unparseable failure record); inbox
  integration (draft content includes metadata, reject retires
  hypothesis, accept moves draft).
- `tests/agent/tools/test_read_tools.py` (3 new): `get_family_coverage`
  empty / aggregates / filters by registered_by.

Test command:
```
python -m pytest src/ta_foundation/tests/agent/roles/test_hypothesis_author.py -v
python -m ta_foundation.agent.cli authoring-pass --help
```
29/29 new pass. Full suite at 1493 passing with the same pre-existing
`regime_recommender` failure unrelated to this work.

### Deliberately not in C.1

- **HITL "ramp" automation** (review-all → 1-in-5 → 1-in-20 spot-checks
  as the Author proves itself). The mechanism is shipped — every
  proposal lands in the inbox — but ramp adjustments are operational
  posture, not code. They become a config flag once the Author has run
  end-to-end against real data for a couple of weeks.

---

## C.1 — Hypothesis Author (original spec, kept for reference)

### Job

Once per scheduled session (start: weekly; ramp to daily later), propose
N (default 5) new hypotheses for the operator to consider. Each proposal
includes:
- A choice of `family` from the registry.
- An `instrument`, `timeframe`, `session_window`, `direction`.
- A param grid where every key is in the family's
  `legitimate_params_json` whitelist.
- A `mechanism` paragraph (≥ 50 chars) describing the structural
  counterparty story — *who* is on the other side, *why* they trade
  this way, *what* would cause the edge to decay.

### What it can read

- `list_probe_families`, `get_family_spec`
- `list_graveyard` — to avoid re-proposing dead ideas
- `find_similar_hypotheses` — to deduplicate
- `count_hypotheses_tested` — so the agent sees the multiple-testing cost
  it's adding to
- `get_market_data_coverage` — so it doesn't propose probes against
  missing data

### What it cannot do

- Invent a new family.
- Propose params outside a family whitelist.
- Re-propose a hypothesis whose params hash matches an existing `open` or
  `graveyarded` hypothesis (enforced by `author_probe`'s preconditions).
- Modify a previously authored hypothesis after results exist. *No
  tweak-and-retry. Ever.* This is the cardinal sin from Real Edge §5.

### Hard guardrails (code-side, not prompt-side)

1. **Family whitelist enforcement**: covered by A.3 family registry.
2. **Dedupe by mechanism + params**: `author_probe` rejects with
   `code='duplicate_hypothesis'`.
3. **Graveyard awareness**: if the proposed family/instrument/params
   match a graveyarded candidate within Jaccard ≥ 0.7, the tool requires
   the agent to include a `revival_reason` field explaining what's
   different now (e.g., "new structural OR-reclaim filter applied"). Without
   it, rejected.
4. **Quota**: max 5 new hypotheses per Author session, max 25 per week.
   Ledger-level enforcement, not prompt-level.

### HITL ramp

- **First 20 hypotheses**: every proposal goes to the inbox; the human
  reviews mechanism quality, dedupe correctness, and parameter sanity
  before `author_probe` is called.
- **20–100**: spot-check 1 in 5; auto-author the rest.
- **100+**: spot-check 1 in 20; revisit if rejection rate climbs.

The ramp is configured in `agent/config.yaml`, not hardcoded.

### Implementation shape

- `src/ta_foundation/agent/roles/hypothesis_author.py`
- Stronger model recommended here (cloud Claude Opus or Sonnet). This is
  a creative task where quality of proposals matters far more than
  throughput, and ~5 calls per week is cheap.
- Output goes through the inbox surface from B.5 with a special "author
  proposal" template.

### Files to touch

- `src/ta_foundation/agent/roles/hypothesis_author.py` (new)
- `src/ta_foundation/agent/roles/_prompts/hypothesis_author.md` (new)
- `src/ta_foundation/agent/config.yaml` (new — ramp settings, model
  selection per role)
- `src/ta_foundation/tests/agent/roles/test_hypothesis_author.py` (new)

### Done when

- Author session produces 5 well-formed proposals, each passing
  `author_probe` preconditions.
- A duplicate proposal in the same session is rejected with the
  structured error and the agent recovers (re-proposes a non-duplicate)
  within retry budget.
- 20 human-reviewed proposals, with ≥ 80% accepted without edit.

---

## Already-shipped: C.2 — Sweep Operator + C.4 — Workflow integration

Files landed:

- `src/ta_foundation/agent/roles/sweep_operator.py` — `run_one_hypothesis`
  (single-hypothesis fast → optional hardened), `run_operator_pass` (queue
  drainer), `discover_accepted_hypotheses` (walks
  `runs/proposals_accepted/` and skips hypotheses with an existing
  running/completed run).
- `src/ta_foundation/agent/roles/_prompts/sweep_operator.md` — human-readable
  diagnostics template only. There is no LLM in the Operator's hot path.
- `src/ta_foundation/research_ledger/repository.py` — `list_candidates`
  gained `run_id` / `hypothesis_id` filters so the Operator can read just
  the candidates produced by a single run.
- `src/ta_foundation/agent/scheduler.py` — `operator_pass` and
  `weekly_authoring_pass`. The latter is the C.4 graph encoded in Python:
  authoring → (HITL inbox accept happens between passes) → operator drain
  of whatever the human has already accepted.
- `src/ta_foundation/agent/cli.py` — `operator-pass` and
  `weekly-authoring-pass` subcommands.

### Discipline (Python-enforced)

1. `mode='locked_holdout'` is rejected by the Operator itself before
   `run_probe` is ever reached. (Belt-and-suspenders next to the journaled
   tool's enum schema.)
2. Hypotheses with status≠'open' or with an existing running/completed run
   are skipped, never re-run with different parameters.
3. Zero-candidate fast runs retire the hypothesis (`status='retired'`),
   they do NOT graveyard it. Graveyard implies tested-and-failed; no_trades
   means the signal never triggered.
4. `run_probe` failures are recorded on the per-hypothesis report and the
   Operator moves on — no retry with different inputs.

### Tests (18 new)

- `tests/agent/roles/test_sweep_operator.py` (16): fast→hardened happy
  path; fast-only when no dev survivor; zero-candidate retirement (and
  graveyard stays empty); subprocess failure stops; tool rejection records
  but starts no run; hardened-stage subprocess failure; locked_holdout
  never appears in any call path; already-completed-run skip; retired
  hypothesis skip; unknown hypothesis skip; queue discovery (skips retired
  + already-run); empty accepted dir; full pass over a queue with mixed
  outcomes; missing yaml resolver path; default resolver hit + miss.
- `tests/agent/roles/test_scheduler.py` (2 new): `operator_pass` drains a
  one-hypothesis queue; `weekly_authoring_pass` authors then drains.

Test command:
```
python -m pytest src/ta_foundation/tests/agent/roles/test_sweep_operator.py \
                  src/ta_foundation/tests/agent/roles/test_scheduler.py -v
```
21/21 pass.

### Deliberately not in C.2/C.4

- Automatic sidecar-to-candidate ingestion is still a CLI-side
  responsibility. In production the discovery CLI writes candidate rows
  to the ledger via `--ledger-db`; the Operator only reads them. Tests
  inject rows directly to exercise the gating logic.
- HITL accept remains a manual step between the Author pass and the
  Operator drain. `weekly_authoring_pass` runs both passes back-to-back
  but the operator can only ever pick up hypotheses the human has already
  accepted; proposals authored this session that sit unreviewed simply
  wait for the next pass.

---

## C.2 — Sweep Operator (original spec, kept for reference)

### Job

Take an authored hypothesis, run the appropriate probe(s), and record the
result. Discipline: **the Operator never modifies a hypothesis after
seeing its result.** If a result is interesting but underpowered, the
Operator's only legal action is to author a *new* hypothesis (which
contributes to the multiple-testing denominator).

### Workflow per hypothesis

1. Read the hypothesis row.
2. Call `run_probe(hypothesis_id, mode='fast_probe')`.
3. Wait for completion. Read the resulting candidate rows.
4. If any candidate has dev-stage gate_verdict='survivor':
   - Call `run_probe(hypothesis_id, mode='hardened')`.
5. If hardening verdict is 'survivor':
   - The Operator does NOT request the locked holdout. The human (or, in
     a later ramp, the Triage Analyst) decides whether to spend the
     one-shot holdout attempt.
6. Journal everything.

### What it can do

- Call `run_probe`.
- Read candidate results.

### What it cannot do

- Edit YAMLs.
- Re-run with modified params.
- Promote to hardening if the dev gates didn't pass (the tool refuses; the
  agent should not waste turns trying).
- Touch the locked holdout. Period.

### Failure handling

- If `run_probe` fails (CLI error, missing market data), the Operator
  reads the error, decides whether to surface to the inbox or retry with
  the same inputs. Never with different inputs.
- If a probe runs but produces zero candidates (no signal triggered any
  trades), the hypothesis is marked `status='retired'` with reason
  `no_trades` — *not* graveyard, because graveyard implies tested-and-failed.

### Files to touch

- `src/ta_foundation/agent/roles/sweep_operator.py` (new)
- `src/ta_foundation/agent/roles/_prompts/sweep_operator.md` (new)
- `src/ta_foundation/tests/agent/roles/test_sweep_operator.py` (new)

### Done when

- Operator runs a queue of 5 hypotheses end-to-end (fast → hardened) on
  the post-backfill ledger without human intervention, journaling every
  step.
- Operator correctly retires a no-signal hypothesis without graveyarding it.
- Operator does not call `request_locked_holdout` under any test scenario.

---

## C.3 — Hypothesis-family coverage tracking

### Why

A common LLM failure mode is fixating on one family ("I just keep
proposing VWAP fade variants"). To detect this, the Author needs visibility
into family coverage and the ledger needs to enforce diversity.

### Approach

Add a read tool: `get_family_coverage(window) → {family_id: count}`.

Add a soft enforcement in the Author prompt: the system prompt template
includes the current family-count distribution and asks the model to
prefer underrepresented families.

Add a hard enforcement: the Author cannot propose more than 40% of a
session's quota in a single family.

### Files to touch

- `src/ta_foundation/agent/tools/read/ledger.py` — add `get_family_coverage`
- `src/ta_foundation/agent/roles/_prompts/hypothesis_author.md` — render
  coverage stats into prompt
- `src/ta_foundation/agent/roles/hypothesis_author.py` — quota check

### Done when

- A test where the Author has just authored 4 VWAP-fade hypotheses in a
  session of 5 forces the 5th to a different family.
- Coverage stats are queryable and rendered into the prompt.

---

## C.4 — Author/Operator workflow

### Fixed graph

```
   scheduler (cron weekly) ──► hypothesis_author ──► inbox (HITL ramp gate)
                                                          │
                                          ┌───────────────┴───────────────┐
                                          ▼                               ▼
                                  rejected (back to author)       accepted
                                                                          │
                                                                          ▼
                                                            ┌──────────────────────┐
                                                            │ sweep_operator queue │
                                                            └──────────┬───────────┘
                                                                       ▼
                                                              run_probe(fast)
                                                                       ▼
                                                       gates pass? ─── no ──► triage (Phase B)
                                                                yes
                                                                       ▼
                                                             run_probe(hardened)
                                                                       ▼
                                                                   triage (Phase B)
```

This graph is **encoded in Python**, not in an LLM prompt. The
`scheduler.py` from Phase B grows a new entry point `weekly_authoring_pass`
that runs this sequence.

### Files to touch

- `src/ta_foundation/agent/scheduler.py` — add `weekly_authoring_pass`
- `src/ta_foundation/agent/cli.py` — add `weekly-authoring-pass`
  subcommand

### Done when

- One full pass: Author proposes 5 → human accepts 3 → Operator runs 3 →
  Triage classifies → Scribe post-mortems the graveyarded ones. Zero
  manual intervention beyond the inbox accept step.

---

## C.5 — Multiple-testing accounting wired to ledger

### Why

T12 in `discovery_hardening_plan.md` is queued: Romano-Wolf / Deflated
Sharpe family-wise correction. Once the Author is generating hypotheses
at scale, the *correct denominator* for that correction is "every
hypothesis ever registered in this program," not "every candidate in
this run."

### Approach

- The Deflated Sharpe Ratio computation in
  `analysis/strategy_discovery/` reads `count_hypotheses_tested(window)`
  from the ledger as its `n_trials` argument by default, with an override
  for unit tests.
- The hardening plan's T12 implementation should be coordinated with this
  change so the gate uses the right denominator from day one.

### Files to touch

- `src/ta_foundation/analysis/strategy_discovery/validation.py` — DSR
  call site reads from ledger
- `docs/designs/discovery_hardening_plan.md` — note the dependency in
  T12's spec

### Done when

- DSR uses the ledger's hypothesis count by default.
- Adding 50 hypotheses to the ledger raises the t-stat threshold for
  passing DSR, observably, in a synthetic test.

---

## Phase C exit criteria

1. ✅ Author session has produced ≥ 50 well-formed hypotheses, ≥ 80%
   accepted on first review.
2. ✅ Operator has run ≥ 50 probes end-to-end without violating
   no-retry discipline (verified by journal audit).
3. ✅ Family coverage shows no single family above 40% over the trailing
   30 days.
4. ✅ Hypothesis survival rate is in the 0–10% range. **If it is higher,
   the validation gates are leaking, not the agent getting smart**;
   pause Phase C and audit `evaluate_hard_gates` before continuing.
5. ✅ DSR uses the ledger denominator.

When all hold, Phase D may begin.
