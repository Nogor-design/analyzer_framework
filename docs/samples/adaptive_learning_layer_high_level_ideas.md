# High-Level Ideas Document: Adaptive Learning Layer for `ta_foundation`

## Purpose

This document gives local LLM developers and system implementers a high-level design direction for adding adaptive market-decision intelligence to the existing `ta_foundation` trading research platform.

The goal is **not** to replace the deterministic research, validation, execution, and risk-control system. The goal is to add an adaptive supervisory layer that helps the system decide:

- Which validated strategies fit the current market context.
- Which strategies should be suppressed, reduced, or shadow-only.
- Which regimes, sessions, and event conditions a candidate performs best or worst in.
- Whether recent evidence suggests a candidate is improving, degrading, or becoming stale.
- Whether the system should generate new falsifiable research hypotheses from observed failures or anomalies.

The central idea:

> Adaptation should not mean “let an AI trade.”  
> Adaptation should mean “use live context, historical evidence, scored predictions, and regime-aware memory to choose among prevalidated actions under deterministic guardrails.”

---

## Existing System Assumptions

This document assumes the project already has some or all of the following capabilities:

- A research ledger that stores strategy runs, metrics, sample windows, hardening verdicts, candidate notes, shadow state, and decay state.
- A discovery/hardening funnel with walk-forward validation, OOS evaluation, locked holdout support, slippage/one-bar-delay stress, Monte Carlo checks, and parameter/fold consistency checks.
- Graveyard and anti-overfitting infrastructure, including structural hashing, duplicate refusal, revival reasons, hypothesis counters, and machine-readable rejection reasons.
- A forward shadow runner that records signals, resolves outcomes, produces health reports, and monitors decay.
- Prediction systems that score daily and horizon forecasts using proper scoring rules, calibration reports, regime-conditional performance, and drift reports.
- An autonomous-loop direction where agents assist, but deterministic code owns validation, state transitions, promotion, paper trading, retirement, and risk controls.
- NinjaTrader validation, Strategy Factory, optimizer tooling, Sim101 bridge, and runtime monitoring may already exist in separate packages or local projects.

This proposal should be treated as a conceptual overlay. Developers should map it onto the actual package boundaries and existing abstractions.

---

## Core Thesis

The missing piece is not a bigger model by itself. The missing piece is **measurable adaptation**.

Most trading systems fail because they treat edge as static:

```text
signal = buy/sell
```

A more realistic model is:

```text
pattern + context + regime + session + event state + execution conditions = conditional edge
```

A strategy may be valid in one environment and dangerous in another. Therefore, the system should not only ask:

```text
Does this strategy work?
```

It should ask:

```text
Where does this strategy work?
Where does it fail?
When should it be reduced?
When should it be disabled?
When should it be shadow-only?
When is the current context too different from the validated evidence?
```

This is the system version of discretionary trader adaptation.

---

## What Adaptation Should Mean

Adaptation should be decomposed into several separate capabilities.

### 1. Regime Adaptation

The system classifies the current market environment.

Examples:

- Trend day
- Chop day
- High-volatility open
- Low-volatility compression
- Post-news expansion
- Overnight range breakout
- Overnight range rejection
- VWAP trend continuation
- VWAP chop/reversion
- Liquidity-sweep environment
- Failed-breakout environment
- Event-risk environment

The system then maps validated strategies to regimes.

Example:

```yaml
candidate: orb_failure_reclaim_body_midpoint
instrument: NQ
session: NY_OPEN
regime: high_volatility_failed_breakout
status: allowed
risk_multiplier: 0.50
reason:
  - Candidate has positive expectancy in similar opening contexts.
  - Current volatility increases slippage risk.
  - Event risk is elevated, so full size is not allowed.
```

### 2. Playbook Adaptation

The system chooses from a library of prevalidated playbook cells.

A playbook cell is not just a strategy name. It is a conditional strategy identity:

```text
candidate + instrument + session + regime + level context + volatility bucket + event state
```

Example:

```yaml
playbook_cell:
  candidate_family: orb_failure_reclaim
  instrument: NQ
  session: NY_OPEN
  direction: long
  setup_context:
    opening_range_failed_breakdown: true
    same_bar_reclaim: true
    price_relative_to_vwap: above
    sweep_distance_ticks_min: 4
  regime_context:
    volatility_bucket: high
    trend_chop_state: expansion
  allowed_actions:
    - shadow
    - paper
    - reduced_size
  forbidden_actions:
    - increase_size
    - bypass_shadow
    - modify_entry_live
```

### 3. Confidence Adaptation

The system updates confidence in each candidate/context cell as evidence accumulates.

Confidence should be based on:

- Historical development performance.
- OOS performance.
- Locked holdout performance.
- Shadow results.
- Sim101 paper results.
- Regime-specific expectancy.
- Calibration quality.
- Recent decay signals.
- Similarity between current context and validated historical contexts.

The system should be more willing to act when:

```text
current context ≈ validated profitable context
```

The system should reduce or abstain when:

```text
current context is far from the validated evidence
```

### 4. Event Adaptation

Sparse events like FOMC, CPI, NFP, Fed speakers, or earnings-driven macro repricing should not be treated as ordinary days.

The wrong approach:

```text
Train a model on FOMC days and ask what NQ does at 10:30.
```

The better approach:

```text
Decompose event days into reusable event-state features.
```

Example event-state fields:

```yaml
event_state:
  event_type: FOMC
  event_phase:
    - pre_event
    - announcement_window
    - press_conference_window
    - post_event_repricing
    - next_day_followthrough
  minutes_to_event: 210
  minutes_since_event: null
  expected_volatility: elevated
  surprise_available: false
  prior_market_posture:
    - compressed
    - extended
    - trend_up
    - trend_down
    - balanced
```

This allows the system to learn from similar **event-risk structures**, not just from one named event class.

### 5. LLM Supervisor Adaptation

The local LLM should not be the execution engine.

It should act as a bounded supervisor that reads structured context and produces structured recommendations.

Example output:

```yaml
adaptive_supervisor_decision:
  timestamp: "2026-05-20T08:42:00-06:00"
  instrument: NQ
  decision_type: strategy_gate
  candidate_id: orb_failure_reclaim_body_midpoint_v3
  action: allow_reduced_size
  size_multiplier: 0.50
  confidence: 0.68
  valid_until: "2026-05-20T09:30:00-06:00"
  reason_codes:
    - high_volatility_open
    - event_risk_elevated
    - similar_context_positive_expectancy
    - slippage_risk_elevated
  human_readable_summary: >
    Candidate remains allowed because similar NY-open failed-breakout contexts
    have shown positive expectancy, but volatility and event risk justify reduced size.
  forbidden:
    - increase_global_risk
    - bypass_shadow_or_paper_state
    - create_new_live_variant
    - alter_strategy_parameters_live
```

The deterministic manager policy decides whether this recommendation is legal.

---

## Proposed Package Concept

Potential package name:

```text
src/ta_foundation/adaptive_supervisor/
```

Possible module layout:

```text
adaptive_supervisor/
  __init__.py
  context_snapshot.py
  regime_state.py
  event_state.py
  playbook_registry.py
  candidate_context_profile.py
  similarity_retriever.py
  adaptive_policy.py
  llm_supervisor.py
  decision_schema.py
  decision_logger.py
  outcome_scorer.py
  adaptation_alpha.py
  reports.py
```

### Module Responsibilities

#### `context_snapshot.py`

Builds a single normalized snapshot of the current market state.

Possible inputs:

- Current bars.
- Session/time bucket.
- Instrument.
- Volatility percentile.
- Range expansion/compression.
- VWAP position.
- Prior day high/low relation.
- Overnight high/low relation.
- Opening range state.
- Liquidity sweep state.
- Spread/slippage proxy if available.
- Runtime/account state from execution bridge.

Output:

```yaml
context_snapshot:
  instrument: NQ
  timestamp: ...
  session: NY_OPEN
  time_bucket: 09:30-10:00_ET
  volatility_percentile: 87
  trend_chop_state: expansion
  vwap_relation: above
  overnight_range_state: broke_above_then_reclaimed
  opening_range_state: failure_reclaim
```

#### `regime_state.py`

Classifies current regime using deterministic rules, statistical models, or existing prediction outputs.

Should support multiple regime labels rather than one forced label.

Example:

```yaml
regime_state:
  primary: high_volatility_expansion
  secondary:
    - failed_breakout_environment
    - event_risk
    - ny_open
  confidence:
    high_volatility_expansion: 0.78
    chop: 0.22
```

#### `event_state.py`

Normalizes economic/news/event context.

For early versions, this can be simple:

```yaml
event_state:
  high_impact_event_today: true
  event_type: FOMC
  minutes_to_event: 210
  phase: pre_event
  event_risk_score: 0.82
```

Later it can include:

- Consensus vs actual surprise.
- Rate-path context.
- Inflation/growth regime.
- Liquidity stress regime.
- Post-event reaction classification.

#### `playbook_registry.py`

Maps validated strategy candidates into context-aware playbook cells.

A playbook cell should answer:

- What candidate does this refer to?
- What context was it validated in?
- What actions are allowed?
- What actions are forbidden?
- What evidence supports it?
- What decay or warning states exist?

Example:

```yaml
playbook_cell:
  id: nq_orb_failure_reclaim_nyopen_highvol_v1
  candidate_id: orb_failure_reclaim_body_midpoint_v3
  instrument: NQ
  session: NY_OPEN
  context_filters:
    volatility_percentile_min: 60
    opening_range_state: failure_reclaim
    sweep_ticks_min: 4
  evidence_refs:
    hardening_run_id: ...
    holdout_run_id: ...
    shadow_report_id: ...
  allowed_actions:
    - shadow
    - paper
    - reduced_size
  max_risk_multiplier: 0.50
```

#### `candidate_context_profile.py`

Builds a profile of where each candidate has historically worked or failed.

Questions this module should answer:

```text
Which sessions have positive expectancy?
Which volatility buckets are dangerous?
Does long side behave differently from short side?
Does the setup degrade near high-impact events?
Does performance improve after compression?
Does it fail during trend days?
Does slippage destroy the edge in high-volatility windows?
```

Output example:

```yaml
candidate_context_profile:
  candidate_id: orb_failure_reclaim_body_midpoint_v3
  strong_contexts:
    - NY_OPEN + failed_breakout + medium_high_volatility
  weak_contexts:
    - low_volatility_chop
    - pre_FOMC_final_hour
  unknown_contexts:
    - post_FOMC_press_conference
  warnings:
    - small_holdout_sample
    - slippage_sensitive
```

#### `similarity_retriever.py`

Finds historical contexts similar to the current context.

This should retrieve from:

- Research ledger.
- Shadow outcomes.
- Prediction outcomes.
- Daily analogues.
- Horizon prediction records.
- Event-day records.
- Post-mortems.
- Graveyard/rejection reasons.

Similarity should be transparent and inspectable.

Example output:

```yaml
similar_contexts:
  - date: 2025-03-14
    similarity_score: 0.81
    regime: high_volatility_ny_open_failure_reclaim
    candidate_result: win
    notes: slippage elevated but target reached
  - date: 2025-08-22
    similarity_score: 0.76
    regime: event_risk_pre_fed_failed_breakout
    candidate_result: scratch_or_loss
    notes: choppy before event
```

#### `adaptive_policy.py`

The deterministic policy engine.

It decides what the system is allowed to do with the LLM recommendation and current evidence.

Allowed action types:

```text
allow
allow_reduced_size
suppress
shadow_only
paper_only
request_review
request_research
retire_candidate
mark_context_unknown
```

Forbidden action types:

```text
trade_live_capital_without_human_approval
increase_global_risk
bypass_validation
bypass_shadow
change_strategy_logic_live
spend_extra_locked_holdout_without_policy
ignore_runtime_faults
```

This module should be strict and boring.

#### `llm_supervisor.py`

Calls the local LLM with a structured prompt and structured context.

The LLM should receive:

- Current context snapshot.
- Candidate context profile.
- Similar historical contexts.
- Current prediction outputs.
- Shadow/paper health.
- Risk limits.
- Allowed actions.
- Forbidden actions.

The LLM should return:

- Decision recommendation.
- Confidence.
- Reason codes.
- Explanation.
- Uncertainty flags.
- Required follow-up evidence.

The LLM should not return executable trading logic.

#### `decision_schema.py`

Defines strict schemas for adaptive decisions.

Every adaptive decision should be machine-readable.

Important fields:

```yaml
decision_id:
timestamp:
instrument:
candidate_id:
decision_type:
action:
confidence:
valid_until:
input_context_hash:
evidence_refs:
reason_codes:
llm_model:
llm_prompt_hash:
policy_version:
allowed_by_policy:
final_manager_action:
```

#### `decision_logger.py`

Writes every recommendation and final policy decision to an append-only store.

This is critical. If the adaptive layer cannot be evaluated later, it should not exist.

#### `outcome_scorer.py`

Scores what happened after each adaptive decision.

Examples:

- Did suppressing a strategy avoid a loss?
- Did suppressing a strategy miss a winner?
- Did reduced size reduce drawdown but also reduce expectancy?
- Did allowing a strategy improve results versus baseline?
- Did the LLM recommendation agree with deterministic policy?
- Was the context classification correct after the session closed?

#### `adaptation_alpha.py`

Measures whether adaptation adds value.

Core metric:

```text
adaptation_alpha = result_with_adaptive_decision - baseline_result_without_adaptive_decision
```

This should be tracked by:

- Candidate.
- Candidate family.
- Instrument.
- Session.
- Regime.
- Event type.
- Volatility bucket.
- Decision type.
- LLM model version.
- Policy version.

The adaptive layer is only useful if it improves one or more of:

- Net expectancy.
- Drawdown.
- Tail risk.
- Risk-adjusted return.
- Calibration.
- Abstention quality.
- Strategy survival/retirement timing.
- False promotion prevention.

#### `reports.py`

Produces developer and operator reports.

Example reports:

- Daily adaptive-decision report.
- Weekly adaptation-alpha report.
- Candidate context map.
- Regime performance heatmap.
- LLM decision quality report.
- Suppression accuracy report.
- Missed-opportunity report.
- Decay early-warning report.

---

## How This Fits With the Autonomous Loop

The adaptive supervisor should not replace the autonomous loop. It should feed into it.

Existing autonomous-loop direction:

```text
Hypothesis intake
  -> duplicate/graveyard/test-budget checks
  -> fast probe
  -> hardening
  -> NinjaTrader validation
  -> locked holdout
  -> shadow observation
  -> Sim101 paper trade
  -> manager decision
  -> refine/promote/retire/request live review
```

Adaptive supervisor additions:

```text
Before shadow/paper session:
  build context snapshot
  score event/regime state
  retrieve similar contexts
  evaluate candidate-context fit
  recommend allow/suppress/reduce/shadow-only
  deterministic policy approves or rejects recommendation
  log decision

After session:
  score decision outcome
  compare against baseline
  update candidate context profile
  update adaptation-alpha metrics
  generate post-mortem if needed
```

The supervisor should be a layer around candidate selection and risk gating, not a replacement for validation.

---

## Recommended First Implementation Slice

The first version should be deliberately simple.

### Version 0: Offline Analysis Only

No live influence.

Build reports that answer:

```text
For each existing candidate:
  In which sessions did it work?
  In which regimes did it fail?
  What volatility buckets helped or hurt?
  Did event days differ from non-event days?
  Would simple suppression rules have improved results?
```

Deliverables:

- Candidate context profiler.
- Regime/session performance matrix.
- Simple adaptation-alpha backtest.
- No LLM required yet.

### Version 1: Shadow-Only Adaptive Decisions

The adaptive supervisor makes decisions, but they do not affect execution.

It logs:

```text
I would have allowed this.
I would have suppressed this.
I would have reduced size.
I would have marked context unknown.
```

Then it scores those decisions against what actually happened.

Deliverables:

- Decision schema.
- Decision logger.
- Outcome scorer.
- Adaptation-alpha report.
- LLM optional.

### Version 2: Deterministic Policy Gating in Shadow/Paper

The system can apply simple deterministic adaptive gates to shadow or Sim101 paper trading.

Examples:

```text
Suppress candidate if current context is in known weak bucket.
Reduce size if volatility percentile exceeds threshold.
Mark shadow-only if event type is unknown for this candidate.
Disable if Page-CUSUM decay has triggered.
```

Deliverables:

- Adaptive policy engine.
- Playbook registry.
- Integration with autonomous manager decision preview.
- Strict guardrails.

### Version 3: Local LLM Supervisor

The local LLM receives structured context and recommends bounded actions.

Start with recommendations only:

```text
allow
allow_reduced_size
suppress
shadow_only
request_review
request_research
```

The deterministic policy can reject any recommendation.

Deliverables:

- Structured LLM prompt.
- JSON/YAML output parser.
- Schema validation.
- Prompt hash and model version logging.
- Decision quality report.

### Version 4: Conservative Online Confidence Updating

Add online learning to update candidate-context confidence.

Recommended early methods:

- Exponentially weighted moving performance.
- Bayesian shrinkage by context bucket.
- Contextual bandit for allow/suppress/reduce decisions.
- Calibration-aware confidence penalties.
- Drift/decay-aware risk reduction.

Avoid early use of deep reinforcement learning for live decision authority.

---

## Important Design Principles

### 1. Adaptation Is Not Edge by Itself

The adaptive layer does not create edge. It may improve edge usage by selecting the right context and avoiding bad contexts.

A strategy still needs:

- Clear market thesis.
- Positive expectancy after costs.
- Independent validation.
- Walk-forward survival.
- Slippage and one-bar delay survival.
- Non-cliff parameter neighborhood.
- Forward shadow consistency.
- Fit inside a broader playbook.

### 2. Every Adaptive Decision Must Be Logged Before the Outcome

No hindsight narratives.

The system must know:

```text
What did the adaptive layer know at the time?
What did it recommend?
Was the recommendation allowed?
What actually happened?
Would baseline have done better?
```

### 3. LLMs Should Explain and Supervise, Not Execute

LLMs are useful for:

- Context summarization.
- Anomaly explanation.
- Similarity reasoning.
- Hypothesis generation.
- Post-mortems.
- Warning about unusual conditions.
- Recommending bounded actions.

LLMs should not:

- Generate live entries.
- Override risk.
- Increase size beyond policy.
- Change strategy logic live.
- Mark a strategy validated.
- Bypass shadow or holdout requirements.

### 4. Prefer Suppression Before Optimization

The safest first use of adaptation is avoiding bad trades.

Early adaptive value should come from:

```text
Do not trade this candidate in this context.
Trade smaller in this context.
Keep this in shadow because current context is unknown.
Request review because the current regime is outside known evidence.
```

Not from:

```text
Change the entry.
Move the stop.
Double size.
Invent a new strategy.
```

### 5. Make Unknown Context Explicit

The system should be comfortable saying:

```text
This setup is validated generally, but not in this event/regime/session context.
Therefore action = shadow_only or reduced_size.
```

Unknown is not failure. Unknown is information.

---

## FOMC / Sparse Event Handling

Sparse events should be handled by feature decomposition and analog reasoning, not by pretending there is a large sample.

For FOMC-like events, developers should avoid a model that only learns:

```text
FOMC day -> direction
```

Instead, represent event structure:

```yaml
event_features:
  event_type: FOMC
  phase: pre_event
  minutes_to_event: 210
  volatility_percentile: 85
  overnight_range_percentile: 73
  pre_event_compression: false
  price_location:
    relative_to_vwap: above
    relative_to_prior_high: near
    relative_to_overnight_high: above
  opening_behavior:
    opening_drive: up
    failed_breakout: true
    reclaim: true
```

The model can then find analogs across:

- FOMC.
- CPI.
- NFP.
- Fed speakers.
- Major macro days.
- Other high-volatility event-risk states.

The goal is not to memorize one event type. The goal is to understand reusable conditional structures.

---

## Developer Questions to Answer

Local developers should evaluate these questions:

### Data Availability

- Do we already have enough regime/session labels?
- Do we have historical event calendars aligned to bar timestamps?
- Do we have slippage/fill-quality proxies?
- Are shadow outcomes stored with enough context?
- Are prediction outputs stored in a queryable way?
- Can we reconstruct what the system knew at each historical time?

### Architecture Fit

- Should `adaptive_supervisor` be part of `autonomous_loop` or a separate package?
- Where should adaptive decisions be stored?
- Should playbook cells live in the research ledger or separate registry?
- How should candidate context profiles be generated and cached?
- What existing schemas can be reused?

### Policy and Safety

- What actions can adaptive policy take in dry-run?
- What actions can it take in shadow?
- What actions can it take in Sim101?
- What actions require human review?
- What actions are permanently forbidden?

### Evaluation

- What is the baseline comparison?
- How do we measure avoided losses versus missed winners?
- How do we avoid hindsight bias?
- How do we prevent the LLM from changing its reasoning after outcomes?
- How much sample size is needed before a context rule becomes active?

---

## Example End-to-End Flow

### Before Market / Session Start

```text
1. Build daily context snapshot.
2. Load candidate list eligible for shadow/paper.
3. Pull candidate context profiles.
4. Pull event state.
5. Pull prediction outputs.
6. Retrieve similar historical contexts.
7. Adaptive supervisor recommends allow/suppress/reduce/shadow-only.
8. Deterministic policy validates recommendation.
9. Manager logs final decision.
```

### During Session

```text
1. Runtime monitor tracks fills, rejects, protective orders, heartbeat, and faults.
2. Adaptive layer may update context classification at defined intervals.
3. It may recommend suppressing new entries if context shifts.
4. It may not alter open trade logic unless deterministic policy explicitly supports that.
```

### After Session

```text
1. Resolve trade/shadow outcomes.
2. Score prediction quality.
3. Score adaptive decisions.
4. Compare against baseline.
5. Update candidate context profiles.
6. Generate post-mortem notes.
7. Mark candidate/context cells as improving, stable, weak, decaying, or unknown.
```

---

## Success Criteria

The adaptive layer is successful only if it can prove one or more of the following:

- It reduces drawdown without destroying expectancy.
- It improves expectancy by suppressing weak contexts.
- It detects decay earlier than existing methods alone.
- It improves calibration of when strategies should be active.
- It reduces false promotion of marginal strategies.
- It improves paper-trading performance versus static strategy activation.
- It produces useful new falsifiable hypotheses from observed regime failures.
- It provides clear, auditable explanations for decisions.

If it cannot prove measurable improvement versus baseline, it should remain a reporting/research tool only.

---

## Suggested Developer Deliverable

Ask local LLM developers to produce a design proposal with:

1. Existing modules to reuse.
2. Proposed package boundaries.
3. Database/schema additions.
4. Decision schemas.
5. First offline report implementation.
6. First shadow-only adaptive decision implementation.
7. Evaluation metrics.
8. Guardrails.
9. Integration points with autonomous loop.
10. Risks and open questions.

Recommended first build target:

```text
Offline candidate-context profiler + adaptation-alpha report.
```

This gives the project useful insight without introducing live execution risk.

---

## Final Framing

This project should not try to build an “AI trader” first.

It should build an **adaptive evidence supervisor**.

The supervisor’s job is to help the system become better at:

```text
knowing when an edge is valid,
knowing when the market has changed,
knowing when to stand down,
and knowing which new hypotheses are worth testing.
```

That is the safest and most useful form of adaptive intelligence for this system.
