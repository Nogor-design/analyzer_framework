# Autonomous Research-To-Paper-Trade Loop Build Plan

This is the build plan for turning the existing agentic research, NinjaTrader
validation, shadow, and Sim101 execution pieces into one autonomous loop. The
goal is continuous progress without human babysitting, while preserving the
project constitution: agents may propose and supervise, but deterministic code
owns gates, risk, validation, execution, and promotion.

Read first:

- `docs/designs/agentic_nt_strategy_knowledge_base.md`
- `docs/designs/agentic_research_program.md`
- `docs/designs/real_edge_discovery_program.md`
- `docs/designs/autonomous_ninjatrader_strategy_loop.md`
- `docs/ideas/IMPROVEMENT_IDEAS.md`
- `docs/ideas/LDR_HYPOTHESIS_BACKLOG_2026-05-14.md`
- `docs/ideas/LDR_INTAKE_06bd51f1.md`

## Operating Model

The loop should run as a deterministic state machine with agent-assisted
research and scribing around it.

```text
Hypothesis intake
  -> duplicate/graveyard/test-budget checks
  -> fast probe
  -> hardening
  -> NT validation
  -> locked holdout
  -> shadow observation
  -> Sim101 paper trade
  -> manager decision
  -> refine, promote, retire, or ask for live-capital approval
```

Humans should not be required for routine discovery, NT compile/repair,
optimizer runs, shadow enrollment, Sim101 paper-trading, or retirement. Humans
are required for live-capital approval, account-level risk changes, and any
policy change that weakens gates.

## Existing Components To Reuse

Do not rebuild these:

- Research agents: `src/ta_foundation/agent/`
- Hypothesis Author: `src/ta_foundation/agent/roles/hypothesis_author.py`
- Sweep Operator: `src/ta_foundation/agent/roles/sweep_operator.py`
- Promotion/holdout tools: `src/ta_foundation/agent/tools/write/promote.py`
- Shadow tools: `src/ta_foundation/agent/tools/write/shadow.py`
- Research ledger: `src/ta_foundation/research_ledger/`
- Shadow runner/health/decay: `src/ta_foundation/shadow/`
- NT compile/optimizer loop: `src/ta_foundation/nt_strategy_loop/`
- Quick NT validation harness:
  `src/ta_foundation/strategies/StrategyDiscoveryFilter/`
- Execution bridge compatibility:
  `src/ta_foundation/strategies/TaFoundationExecutionBridge/`
- Strategy Factory:
  `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\strategy_factory`
- Optimizer AddOn: `D:\ninjatraderOptimizer`
- Sim/runtime account monitor and direct strategy API: `D:\NinjaAccountManager`

## Key Ideas Incorporated

From `docs/ideas/IMPROVEMENT_IDEAS.md`:

- Add cumulative multiple-testing accounting across probes and families.
- Add structural duplicate/graveyard similarity checks before new runs.
- Push cost awareness earlier than hardening where possible.
- Make volatility/session/regime tagging first-class enough for discovery and
  manager decisions.
- Defer portfolio allocation until there are enough simultaneous shadow or
  paper candidates, but start collecting overlap/correlation evidence.
- Make Sweep Operator enforcement strict: registered params, datasets, and
  feature definitions must match the run being executed.
- Use the graveyard actively to reject near-duplicate failed ideas.

From the Local Deep Research intake/backlog:

- Treat Deep Research output as conservative hypothesis intake, not evidence.
- Rewrite performance claims as falsifiable hypotheses before registration.
- Prefer registry-compatible families already identified by the backlog:
  `orb_failure_reclaim`, `overnight_high_low_sweep_reclaim`,
  `prior_high_low_failed_breakout`, `initial_balance_reversal`,
  `vwap_reclaim_continuation`, `vwap_reject_fade`, and
  `compression_then_expansion`.
- Prioritize ORB/reference-level families before low-confidence VWAP variants.
- Keep `large_candle_origin_retest` low priority unless a revival reason
  explains why it differs from prior slippage-fragile failures.

## Target State Machine

Each candidate should have exactly one current state and append-only evidence.

```text
DRAFT
ACCEPTED
FAST_PROBE_RUNNING
FAST_PROBE_FAILED
FAST_PROBE_PASSED
HARDENING_RUNNING
HARDENING_FAILED
HARDENING_PASSED
NT_FILTER_TEST_RUNNING
NT_FILTER_TEST_FAILED
NT_FILTER_TEST_PASSED
NT_STRATEGY_GENERATED
NT_COMPILE_RUNNING
NT_COMPILE_FAILED
NT_COMPILE_CLEAN
NT_OPTIMIZER_RUNNING
NT_OPTIMIZER_FAILED
NT_OPTIMIZER_PASSED
LOCKED_HOLDOUT_REQUESTED
LOCKED_HOLDOUT_FAILED
LOCKED_HOLDOUT_PASSED
SHADOW_ACTIVE
SHADOW_FAILED
SHADOW_PASSED
PAPER_ARMED
PAPER_ACTIVE
PAPER_FAILED
PAPER_PASSED
RETIRED
HUMAN_LIVE_REVIEW
```

Transitions must be deterministic and logged. The manager may choose the next
allowed transition, but it may not mutate evidence or bypass required gates.

## Proposed Package

Add a focused orchestration package:

```text
src/ta_foundation/autonomous_loop/
  __init__.py
  conductor.py
  state_machine.py
  candidate_queue.py
  evidence_bundle.py
  manager_policy.py
  deep_research.py
  nt_validation.py
  strategy_factory_bridge.py
  paper_trade.py
  runtime_monitor.py
  decisions.py
```

Responsibilities:

- `conductor.py`: one commandable loop that advances eligible candidates.
- `state_machine.py`: legal states, transitions, and guard checks.
- `candidate_queue.py`: pulls accepted hypotheses and eligible ledger
  candidates.
- `evidence_bundle.py`: normalized references to probe results, hardening
  results, NT artifacts, optimizer runs, shadow health, and paper events.
- `manager_policy.py`: deterministic decision policy and thresholds.
- `deep_research.py`: optional research-intake adapter that creates draft
  hypotheses or manager notes only.
- `nt_validation.py`: StrategyDiscoveryFilter and `nt_strategy_loop` adapters.
- `strategy_factory_bridge.py`: converts candidates to Strategy Factory specs
  and calls `build_strategy_outputs(...)`.
- `paper_trade.py`: submits approved Sim101 commands through the standardized
  NinjaAccountManager/TaFoundationExecutionBridge client.
- `runtime_monitor.py`: ingests runtime snapshots, fills, rejects, heartbeat
  faults, and protective-order events.
- `decisions.py`: append-only decision records: continue, refine, retire,
  shadow, paper, or human live review.

## Phase 0 - Ground Truth And Status Cleanup

Goal: make the current system state unambiguous before adding automation.

Tasks:

- Update stale status tables in `agentic_research_program.md` so they match
  the implemented Phase C/D code.
- Confirm current migration/schema fields in `src/ta_foundation/research_ledger`.
- Define the autonomous state enum and evidence reference shape.
- Add a dry-run CLI that only prints eligible candidates and next recommended
  transitions.

Exit criteria:

- A new dry-run command can report candidates and why each can or cannot move.
- No strategy tests or NT actions are triggered yet.

## Phase 1 - Statistical Discipline First

Goal: prevent autonomous scale from becoming autonomous overfitting.

Tasks:

- Add cumulative hypothesis/test counters across runs, families, instruments,
  sessions, and timeframes.
- Add structural hypothesis hash using family, direction, session, instrument,
  timeframe, parameter ranges, feature definitions, and outcome geometry.
- Refuse or require revival reason for near-duplicate graveyard clusters.
- Persist all manager decisions and refusal reasons.
- Add tests that verify duplicate/graveyard candidates cannot be run by the
  autonomous conductor.

Exit criteria:

- The conductor cannot launch a run that fails preregistration, duplicate,
  graveyard, or test-budget checks.
- The weekly/deep-research hypothesis backlog is consumed only through normal
  validation.

## Phase 2 - Pure Discovery Autopilot

Goal: run discovery continuously without touching NinjaTrader or execution.

Tasks:

- Wrap existing Hypothesis Author and Sweep Operator with the conductor.
- Let the manager inject registry-compatible hypotheses from:
  - accepted local backlog notes,
  - stale-search analysis,
  - graveyard gaps,
  - optional Deep Research intake.
- Enforce source confidence and family quotas.
- Run `fast_probe` and `hardened` exactly as registered.
- Retire no-trade, fragile, duplicate, or cost-sensitive candidates with
  machine-readable reasons.

Exit criteria:

- The loop can generate or select hypotheses, run probes, harden survivors, and
  retire failures without human input.
- No locked holdout is spent in this phase.

## Phase 3 - NT Validation Without Execution

Goal: give promising candidates additional NinjaTrader evidence before shadow.

Tasks:

- Add `strategy_factory_bridge.py` for candidate-to-Strategy-Factory spec
  conversion.
- Start with the most reliable factory-supported family, likely MA/trend or the
  first confirmed ORB/reference family.
- Use StrategyDiscoveryFilter for generic filter/regime validation when exact
  strategy generation is not ready.
- Use `nt_strategy_loop` for compile/repair and optimizer runs.
- Persist generated spec, `.ta_template.json`, `.cs`, XML template, manifest,
  optimizer session id, result CSVs, and NT decision summary.

Exit criteria:

- A candidate can move from hardening survivor to NT compile-clean and optimizer
  evidence without manual Strategy Analyzer babysitting.
- NT failures are classified as refine, unsupported family, repair exhausted,
  optimizer failed, or retired.

## Phase 4 - Locked Holdout And Shadow Automation

Goal: automate promotion into existing one-shot holdout and shadow observation.

Tasks:

- Teach the conductor when a candidate qualifies for locked holdout.
- Call existing promotion tools rather than adding a new holdout mechanism.
- Enroll passing candidates into existing shadow tooling.
- Add manager rules for shadow health, edge decay, slippage realism, and
  position-tracking safety.
- Begin collecting candidate overlap/correlation evidence, but do not allocate
  portfolio capital yet.

Exit criteria:

- No candidate enters shadow without passing locked holdout.
- Shadow pass/fail decisions are deterministic and journaled.

## Phase 5 - Sim101 Paper-Trade Loop

Goal: connect approved shadow candidates to supervised paper execution.

Tasks:

- Standardize the socket execution contract against `D:\NinjaAccountManager`
  on `tcp://127.0.0.1:8766`.
- Prefer the direct JSON-lines strategy API; keep legacy file bridge as fallback.
- Add a `paper_trade.py` adapter that sends only Sim101 or explicitly approved
  paper-account commands.
- Add runtime monitor ingestion for `STATE_SNAPSHOT`, `ACCEPTED`, `REJECTED`,
  `ENTRY_SUBMITTED`, `FILLED`, `PARTIAL_FILL`, `STOP_ATTACHED`,
  `TARGET_ATTACHED`, `HEARTBEAT_TIMEOUT`, `ERROR`, `EXIT_FILLED`, and
  `FLATTEN_AND_DISABLE`.
- Reconcile paper fills and rejects against shadow expectations.
- Auto-disable on heartbeat faults, protective-order faults, unexpected
  position state, duplicate signal, stale signal, or daily lockout.

Exit criteria:

- Approved candidates can paper-trade on Sim101 without manual order entry.
- Runtime events are written back as evidence.
- The manager can retire, refine, continue paper, or escalate to
  `HUMAN_LIVE_REVIEW`.

## Phase 6 - Manager Review And Learning Loop

Goal: close the loop so paper results improve future research without silently
weakening gates.

Tasks:

- Add daily/weekly manager reports that summarize:
  - candidates advanced,
  - candidates retired,
  - duplicate/graveyard refusals,
  - test budget spent,
  - NT validation failures,
  - shadow/paper health,
  - execution rejects/faults,
  - recommended hypothesis injections.
- Feed paper-trade failure reasons back into hypothesis generation as negative
  knowledge.
- Use Deep Research only for explicit knowledge gaps, stale families, or new
  source-backed mechanism ideas.
- Keep live approval as an explicit human gate.

Exit criteria:

- The system continues cycling through discovery, NT validation, shadow, and
  paper decisions while preserving auditability.

## Deep Research Policy

Deep Research is optional and must be throttled.

Allowed uses:

- Search for new mechanism vocabulary when local search is stale.
- Compare a failed family against known public variants.
- Build conservative backlog candidates from credible sources.
- Identify adverse tests, not just favorable filters.

Disallowed uses:

- Writing executable trading logic directly.
- Marking a strategy as validated.
- Bypassing family whitelist, graveyard similarity, or test-budget limits.
- Creating live execution decisions.

Deep Research outputs must be stored as intake artifacts and rewritten into
falsifiable hypotheses before entering the ledger.

## Initial Candidate Queue

Use the LDR backlog conservatively:

1. `orb_failure_reclaim`
2. `overnight_high_low_sweep_reclaim`
3. `prior_high_low_failed_breakout`
4. `initial_balance_reversal`
5. `compression_then_expansion`
6. `vwap_reclaim_continuation`
7. `vwap_reject_fade`

Run no more than a small quota per cycle. Do not register all backlog items in
one batch unless test-budget accounting is intentionally configured for it.

## Manager Decision Rules

The manager may autonomously:

- run eligible fast probes and hardening,
- refuse duplicates,
- retire fragile candidates,
- run NT validation,
- request locked holdout when gates pass,
- enroll shadow when holdout passes,
- arm Sim101 paper trading when shadow gates pass,
- flatten/disable paper trading on runtime faults,
- request Deep Research intake,
- inject new draft hypotheses from approved source notes.

The manager may not autonomously:

- trade live capital,
- increase global risk,
- weaken gates,
- spend a second locked holdout for the same candidate,
- ignore execution faults,
- bypass shadow,
- mutate historical evidence.

## Verification Strategy

Unit tests:

- state-machine legal/illegal transitions,
- duplicate/graveyard refusal,
- cumulative test-budget accounting,
- Deep Research intake cannot create executable strategies directly,
- Strategy Factory bridge spec generation,
- NT validation result classification,
- paper-trade command validation,
- runtime event ingestion and auto-disable rules.

Integration tests:

- dry-run candidate advancement,
- mocked `nt_strategy_loop` success/failure,
- mocked NinjaAccountManager socket events,
- end-to-end candidate from accepted hypothesis to `PAPER_ARMED` using fakes.

Manual/smoke tests:

- Strategy Analyzer AddOn RunBatch with known smoke strategy,
- NinjaAccountManager `tools/strategy_live_smoke.py` on Sim101,
- full paper loop with quantity 1 and forced flatten/disable cleanup.

## First Implementation Slice

Build this first:

```text
autonomous_loop dry-run
  -> list eligible candidates
  -> apply duplicate/graveyard/test-budget checks
  -> recommend next state
  -> emit manager decision preview
```

Then:

```text
hardening survivor
  -> Strategy Factory or StrategyDiscoveryFilter adapter
  -> nt_strategy_loop full-loop
  -> persist NT evidence
  -> manager decision
```

Only after that:

```text
shadow-passed candidate
  -> NinjaAccountManager Sim101 adapter
  -> runtime monitor
  -> paper decision loop
```
