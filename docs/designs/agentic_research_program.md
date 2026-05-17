# Agentic Research Program — Master Plan

This is the operating constitution and roadmap for adding AI agents to
`ta_foundation`. It supersedes `BUILDPLAN_AGENTIC.md`, which was scoped
before the Real Edge framing and the discovery-hardening work shipped.

The companion documents below carry the per-phase implementation specs and
status tables. **This master plan is the entry point for any session
working on the agentic system.**

| Phase | Doc | What it ships |
|---|---|---|
| A | [agentic_phase_a_foundation.md](agentic_phase_a_foundation.md) | Candidate ledger, tool registry refactor, pre-registration + family registry |
| B | [agentic_phase_b_read_only_agents.md](agentic_phase_b_read_only_agents.md) | Triage agent + Scribe agent (read-only, no pipeline control) |
| C | [agentic_phase_c_authoring_agents.md](agentic_phase_c_authoring_agents.md) | Hypothesis Author + Sweep Operator (write tools, with HITL ramp) |
| D | [agentic_phase_d_forward_observation.md](agentic_phase_d_forward_observation.md) | Shadow trade loop, daily health report, edge-decay detection |

Related upstream docs that this plan defers to:

- `Real Edge In Day Trading.md` — the constitution of *why* this program is
  shaped the way it is. If anything below conflicts with it, the Real Edge
  doc wins.
- `docs/designs/real_edge_discovery_program.md` — the deterministic research
  pipeline this agent layer wraps. Agents do not replace any of it.
- `docs/designs/discovery_hardening_plan.md` — the validation gates the
  agents must respect. Agents propose; gates dispose.

---

## 1. Constitution

These rules are load-bearing. Every design decision below derives from them.

1. **No LLM in the signal, risk, or execution path. Ever.** Including as a
   sanity-check override, including in NinjaTrader execution bridge code,
   including for "what does the model think about the tape." The pipeline
   that decides whether a candidate is real, and the code that eventually
   sends an order, are deterministic, reproducible, and auditable.
2. **Agents propose; code disposes.** Promotion thresholds, gate evaluation,
   walk-forward validation, holdout scoring, slippage stress, and
   multiple-comparison correction are all code-side. An agent can ask for a
   candidate to be promoted, but `evaluate_hard_gates` decides.
3. **Pre-registration is mandatory.** Every hypothesis carries a written
   mechanism paragraph and a parameter range *before* the run. After-the-fact
   tweak-and-retry is forbidden — the agent must register a new hypothesis,
   which contributes to the multiple-testing denominator.
4. **One holdout attempt per candidate.** Code-enforced. The locked holdout
   is touched exactly once per candidate ID, and the result lands in the
   ledger whether it passes or fails.
5. **Every agent action is journaled.** Tool inputs, outputs, and timestamps
   land in append-only logs. Agents are stochastic; the audit trail is not.
6. **Agents do not read HTML reports.** HTML is the human surface. Agents
   read structured artifacts (ledger rows, sidecar JSON, parquet). If an
   agent needs information that is only in HTML today, the fix is to expose
   it as structured data, not to scrape.
7. **The graveyard is part of the record.** Rejected candidates, their
   reasons, and post-mortems are first-class ledger rows. The agent must be
   able to query "have we tested this before?" before proposing a probe.

If any of those rules feels inconvenient mid-implementation, re-read the
Real Edge doc. The inconvenience is the discipline working.

---

## 2. Boundary architecture

```
┌──────────────────────────────────────┐    ┌──────────────────────────────────────┐
│  AGENT ZONE (stochastic OK)          │    │  PIPELINE ZONE (deterministic)       │
│                                      │    │                                      │
│  - hypothesis authoring              │    │  - parsers / ingest                  │
│  - YAML probe drafting               │    │  - MarketDataStore                   │
│  - reading ledger / sidecars         │ ──▶│  - pattern_engine                    │
│  - triage + classification           │    │  - strategy_discovery                │
│  - narrative reporting               │    │  - hardening (WF, holdout, slippage) │
│  - planning next sweeps              │    │  - evaluate_hard_gates               │
│                                      │    │  - report rendering                  │
└──────────────────────────────────────┘    └──────────────────────────────────────┘
                  ▲                                          │
                  └──────────  structured artifacts  ────────┘
                              (ledger rows, JSON, parquet)
```

The interface between the zones is the **tool registry** (Phase A). Agents
only ever touch the pipeline through that registry. The registry is small,
typed, journaled, and code-reviewed. New tools require the same review as
any other code change.

---

## 3. Roles (only four)

This is a hard cap. Adding a fifth role requires a written justification
that names a specific decision the existing four cannot make.

| Role | Reads | Writes | Cannot |
|---|---|---|---|
| **Hypothesis Author** | family registry, ledger, graveyard | new pre-registration records, new YAML probes in `discovery/generated/` | Invent indicators outside the family registry; tweak parameters after seeing results; re-test a graveyarded hypothesis without a written reason |
| **Sweep Operator** | pre-registration records, ledger | run records, candidate rows, artifact paths | Re-run a probe with modified params after seeing its result; skip pre-registration; bypass `evaluate_hard_gates` |
| **Triage Analyst** | new candidates, gate verdicts, sidecars | classification labels, rejection reasons, mechanism statements | Override gate verdicts; promote candidates the gates rejected; modify ledger metrics |
| **Scribe** | ledger, shadow log | weekly research letters, post-mortems, daily health reports | Cite numbers not in the ledger; render anything that wasn't computed by the pipeline |

Roles **explicitly removed** from the original `BUILDPLAN_AGENTIC.md`:

- *Risk Analyst* — risk is code in front of execution. An LLM "risk
  analyst" launders guesses as authority.
- *Strategy Engineer* / parameter-optimization agent — agent-driven parameter
  optimization is in-sample curve fitting with extra steps. Parameter sweeps
  are pre-registered grids run by the deterministic pipeline.
- *Lead Strategist* god-orchestrator — replaced by a thin scheduler that
  invokes the four narrow roles in sequence. No role decides what the others
  do; the workflow is a fixed graph (Phase B/C).

---

## 4. Tool design principles

These bind every tool added to the registry.

1. **Read tools are cheap, narrow, and many.** Each returns a small, typed
   payload. If a result would be large, it writes to disk and returns a path
   plus a summary.
2. **Write tools are few, heavily guarded, and journaled.** Each write tool
   has explicit code-side preconditions checked *before* the side effect.
3. **Inputs are JSON-schema-validated before execution.** Local LLMs
   hallucinate arguments; the schema catches it deterministically and
   returns a structured error the model can recover from.
4. **No tool composes other tools internally.** Composition happens in the
   workflow graph, not inside a tool. This keeps each tool auditable.
5. **No tool reads HTML.** Period.
6. **No tool returns more than ~2 KB to the agent.** Anything bigger is a
   path plus a summary.
7. **Idempotent or journaled.** A tool either has no observable side effects
   or appends to a content-addressed log. No silent mutation of state the
   agent can't see.

The current `src/ta_foundation/agent/tools/analysis_tools.py` violates most
of these (4 god-tools, no schema validation, no journaling, no
preconditions). Phase A.2 replaces it.

---

## 5. Roadmap and status snapshot

**Update this table whenever a phase milestone ships, not just when a chat
session ends.** Same convention as `discovery_hardening_plan.md`.

| ID | Phase | Milestone | Status | Notes |
|----|-------|-----------|--------|-------|
| A.1 | A | Candidate ledger schema + access layer | ✅ Done | `src/ta_foundation/research_ledger/`; 35 tests; see phase A doc |
| A.2a | A | Tool framework + read tools + simple write tools | ✅ Done | 10 read + 6 write tools, journaled, schema-validated; 79 new tests |
| A.2b | A | run_probe + record_candidates_for_run + graph.py deprecation | ✅ Done | Subprocess lifecycle + ledger ingest; 15 new tests |
| A.2c | A | Sidecar parser + backfill module | ✅ Done | One parser covers all 16 real sidecars; 25 new tests |
| A.3 | A | Pre-registration + family registry | ✅ Done | 13 families seeded; drift check in `cli/main.py`; 31 new tests |
| A.4 | A | Backfill ledger from existing sidecars | ✅ Done | `scripts/backfill_ledger.py`; 16 hyp / 251 candidates from real outputs/; idempotent |
| B.1 | B | Triage agent (deterministic state + LLM rationale + linter retry) | ✅ Done | 20 tests; CLI `triage-pass` subcommand wired to Ollama |
| B.2 | B | Scribe (post-mortem + weekly letter; LLM writes body, Python builds frontmatter) | ✅ Done | 11 tests; `runs/inbox/<type>/` drafts |
| B.4 | B | Workflow scheduler (fixed Python graph) | ✅ Done | `agent/scheduler.py`; daily_pass + weekly_pass; 3 tests |
| B.5 | B | HITL inbox CLI (list/show/accept/reject) | ✅ Done | `agent/inbox.py`; 13 tests; journals every human decision |
| B.3 | B | Numeric-claim linter (triage + artifact variants) | ✅ Done | 14 tests; tolerance-aware float matching, cites-frontmatter parsing |
| C.1 | C | Hypothesis Author (LLM proposes JSON; Python guardrails enforce) | ✅ Done | 26 tests; CLI `authoring-pass`; drafts to inbox `proposals/` |
| C.2 | C | Sweep Operator with no-retry discipline | ⏳ Next | |
| C.3 | C | Family-coverage tracking + 40% cap | ✅ Done | `get_family_coverage` + author enforcement; 3 + N tests |
| D.1 | D | Shadow signal log | ⏳ Blocked by C | |
| D.2 | D | Daily health report (Scribe-generated) | ⏳ Blocked by D.1 | |
| D.3 | D | Sequential edge-decay test (CUSUM/SPRT) | ⏳ Blocked by D.1 | Auto-disables candidates on divergence |

Phase D is **not** "agents trade." Phase D is "agents narrate forward
observation." Live trading decisions remain a human action until at least
12 months of forward shadow results exist; that policy lives in the Real
Edge doc and is not negotiable from this plan.

---

## 6. What this program is not

To prevent scope creep, the following are **explicitly out of scope** and
should be rejected if proposed:

- A multi-agent council that debates trades.
- Real-time LLM interpretation of the tape.
- LLM-generated features feeding the signal pipeline.
- Auto-retraining of strategy parameters in response to live performance.
- An "AI portfolio manager" the human defers to.
- An agent framework upgrade (LangGraph version bumps, Deep Agents
  migration) without a specific decision the new framework enables.
- Any tool that lets an agent edit a YAML in-place after seeing results.
- Cloud-native deployment of the agent stack. Local-first, single repo.

---

## 7. How to start a new session on this program

1. Read this file.
2. Read the phase doc for the lowest-numbered ⏳ entry in §5.
3. Confirm prerequisites: phase B requires phase A complete, etc.
4. Pick the next ⏳ task in that phase's status table.
5. Run the strategy_discovery test suite before and after every change:
   `python -m pytest src/ta_foundation/tests/analysis/strategy_discovery/test_strategy_discovery.py -x --tb=short`
6. When a task ships, mark it ✅ in both this master and the phase doc, with
   a one-line summary of files touched.

The pre-existing `regime_recommender` test failure is unrelated and exists
on `main` — ignore it.
