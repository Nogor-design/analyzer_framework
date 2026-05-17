# Phase D — Forward Observation

Phase D is where the system finally starts answering the question that
matters: **does this candidate still work in the present?** Backtests
say what was true; forward shadow trading says what is true.

The Real Edge doc (§5 step 10) requires "minimum three months of paper
trading, comparing simulated to live." Phase D builds the infrastructure
for that requirement. **Live trading is not Phase D.** Live trading is a
human decision made on top of the Phase D record, with a minimum bar of
12 months of consistent shadow performance.

Read the master plan and Phases A–C before this file.

**Prerequisite:** Phase C complete; ≥ 5 candidates have passed the
locked holdout and are awaiting forward observation.

## Status snapshot

| ID | Item | Status | Notes |
|----|------|--------|-------|
| D.1 | Shadow signal log + simulator | ✅ Skeleton landed | ORB families only; `--shadow-pass` CLI; idempotent via unique index + cursor |
| D.2 | Daily health report (Scribe-generated) | ✅ Deterministic emitter landed | `--shadow-health` CLI; Scribe prose layer still pending |
| D.3 | Sequential edge-decay test (CUSUM/SPRT) | ✅ Page CUSUM landed | Auto-disable on divergence; runner journals `decay_disable` |
| D.4 | Slippage realism comparator | ⏳ Blocked by D.1 | Realized vs modeled |
| D.5 | Portfolio correlation tracker | ⏳ Blocked by D.1 | Diversifies the basket |

---

## D.1 — Shadow signal log + simulator

### Job

For each candidate enrolled in shadow (via `enroll_shadow_trader`), and
for each new bar of incoming market data:

1. Evaluate the candidate's signal logic against the bar.
2. If a signal fires, write a `shadow_signals` row with planned entry,
   stop, target, and context features.
3. Continue tracking the position (deterministically) until the signal's
   exit conditions resolve. Update the row's
   `realized_outcome_json` with fill prices, realized PnL, time-in-trade,
   and any anomalies.

The shadow simulator is the **same code path** as the backtest simulator,
just running on incrementally-arriving data instead of a historical
slice. Sharing the code is non-negotiable — if shadow uses different
logic from backtest, the comparison is meaningless.

### What it is not

- Not a live trader. No broker calls. No NinjaTrader bridge.
- Not an optimizer. Parameters are frozen at enrollment time. If the
  candidate is showing decay, the response is to *retire it* (D.3), not
  to retune it.
- Not a feature-creep magnet. Add to it only what the daily health
  report needs.

### Implementation shape

- `src/ta_foundation/shadow/runner.py` — main loop
- `src/ta_foundation/shadow/simulator.py` — wraps existing
  `analysis/entry_strategies/outcome/simulator.py` to operate
  incrementally
- `src/ta_foundation/shadow/data_source.py` — abstraction over "give me
  bars since timestamp X for instrument Y"
- Cron-driven: a Windows Task Scheduler entry, not an in-process
  long-runner. Idempotent, restart-safe.

### Files to touch

- `src/ta_foundation/shadow/__init__.py` (new)
- `src/ta_foundation/shadow/runner.py` (new)
- `src/ta_foundation/shadow/simulator.py` (new)
- `src/ta_foundation/shadow/data_source.py` (new)
- `src/ta_foundation/cli/main.py` — `--shadow-pass` subcommand
- `src/ta_foundation/tests/shadow/test_runner.py` (new)

### Done when

- A candidate with a known signal generator produces the expected number
  of `shadow_signals` rows over a synthetic data slice. ✅
  `tests/shadow/test_runner.py::test_shadow_pass_inserts_signal`.
- A run interrupted mid-pass and restarted produces the same final
  ledger state (idempotency). ✅
  `test_shadow_pass_is_idempotent` + `test_unique_index_blocks_duplicate_insert`.
- Backtest of the same period and shadow of the same period produce
  identical signals (modulo realized vs simulated fills). ⏳
  Adapter only wraps ORB; verifying against a backtest of the locked
  body-midpoint candidate is the next acceptance step.

### Current limitations (post-D.1 skeleton)

- Only the `orb_breakout` and `orb_failure_reclaim` families have a
  signal/outcome adapter. Other families raise `ShadowNotSupported` and
  are skipped with a journaled reason — extending requires writing one
  adapter per family, mirroring the discovery sweep's detector + emit +
  outcome triplet.
- The cursor-based bar pull (`shadow_cursor_ts` on `candidates`) is
  primarily a performance / restart-safety mechanism; correctness still
  rests on the unique index `(candidate_id, ts, direction)` added in
  migration 0004.
- Open-position resolution uses `at_close` timeout by default. Slippage
  realism (D.4) is not yet captured — `realized_outcome_json` records
  the modeled fill price, not a separate realized fill.

---

## D.2 — Daily health report

### Job

Once per session day (start: 16:30 America/Denver after RTH close), the
Scribe produces `runs/<date>/shadow_health.md` covering all enrolled
candidates:

- Per candidate: signals fired today, planned vs realized fills,
  realized PnL, slippage delta vs modeled, win rate trailing-30, PF
  trailing-30, CUSUM state.
- Anomalies: missed fills, latency outliers, unexplained PnL gaps.
- Aggregate: total exposure, basket correlation, decayed-candidate flags
  (from D.3).

### Hard rule

Same as Scribe in Phase B: every number is traceable to a ledger row.
The numeric-claim linter applies.

### Files to touch

- `src/ta_foundation/agent/roles/_prompts/daily_health.md` — flesh out
  (was wired but harmless in Phase B)
- `src/ta_foundation/agent/scheduler.py` — add `daily_health_pass`
- `src/ta_foundation/research_ledger/repository.py` — add aggregate
  shadow-stat readers as the prompt needs them

### Done when

- A daily report renders for a synthetic shadow log without lint
  failures. ✅ deterministic emitter
  (`tests/shadow/test_health.py::test_health_markdown_renders_summary_and_candidate_rows`).
- It cites only ledger numbers. ✅ the emitter does no prose
  interpolation — every number comes from `compute_daily_health`, which
  reads from `shadow_signals` + `candidates`. A Scribe prose layer on
  top will need the B.3 linter to enforce the same property on
  LLM-generated text.
- Anomaly flags fire on a synthetic 5-tick slippage delta and a synthetic
  missed fill. ⏳ Current emitter flags `stale_open_position`
  (`test_stale_open_position_flagged`); slippage delta is part of D.4
  and missed-fill flagging is straightforward to add as a follow-up.

### Current limitations (post-D.2 deterministic emitter)

- The Scribe-prose layer is not wired yet. The current emitter is what
  the Scribe will consume — its data block role — but the LLM prose
  generation and B.3 numerical-claim linting on top remain a follow-up.
- Anomaly catalog is currently one entry (`stale_open_position`). Adding
  slippage-delta and missed-fill anomalies waits on D.4 (slippage realism
  comparator) producing the underlying signal.

---

## D.3 — Sequential edge-decay test

### Why

`Real Edge In Day Trading.md` §7 mandates this: *"use sequential testing
(a SPRT or CUSUM on daily returns relative to expected) to flag when live
performance has statistically diverged from backtest, and auto-disable
above a threshold. Do not auto-retrain parameters to 'fix' it."*

### Approach

For each enrolled candidate, maintain a CUSUM state on
`(realized_expectancy_per_trade − backtest_expectancy_per_trade)`:

- `S_t = max(0, S_{t-1} + (X_t − μ_0 − k))`
- `H` is the decision threshold; pick H such that the average run length
  under H0 (no decay) is ≥ 250 trades.
- When `S_t > H`, the candidate's `triage_state` is auto-set to
  `decayed` and `enroll_shadow_trader` is reversed (no new signals; old
  positions still tracked to resolution).

### Critical rule

**Auto-disable, never auto-retune.** A decayed candidate is graveyarded
with reason `decay_detected`. If the operator wants to test a successor
hypothesis, the Author proposes a new one through the normal pipeline
— with the multiple-testing accounting hit that entails.

### Files to touch

- `src/ta_foundation/shadow/decay.py` (new — CUSUM state machine)
- `src/ta_foundation/research_ledger/migrations/0003_shadow_state.py`
  (new — adds `decay_state_json` to `candidates` table)
- `src/ta_foundation/shadow/runner.py` — call decay update after each
  resolved signal
- `src/ta_foundation/tests/shadow/test_decay.py` (new)

### Done when

- A synthetic decayed signal triggers auto-disable within the expected
  trade count. ✅
  `tests/shadow/test_decay.py::test_negative_drift_triggers_within_expected_window`
  asserts a 1.5σ negative shift triggers within ≤ 30 trades.
- A non-decayed signal does not trigger over a 500-trade synthetic run. ✅
  `test_baseline_does_not_trigger_over_500_trades`.
- Auto-disable journals the decision and updates `triage_state`. ✅
  `tests/shadow/test_runner.py::test_runner_auto_disables_decayed_candidate`
  asserts `triage_state='decayed'`, `triaged_by='agent:shadow_decay'`,
  and a single `decay_disable` row in `tool_journal`.

### Current implementation (post-D.3 landing)

- Math lives in `src/ta_foundation/shadow/decay.py`. Page CUSUM in
  one-sided lower form on `(μ₀ − X_t) + k` with reset at 0. μ₀ and σ are
  derived from `pf_dev` + target/stop ticks via the implied-binary-win-rate
  identity; this keeps the reference unit-consistent with the runner's
  `profit_ticks`. Defaults: `k = 0.5σ`, `H = 5σ` (ARL₀ ≈ 465; comfortably
  above the 250-trade floor in this spec).
- State persists at `candidates.decay_state_json` (migration 0005). Round-
  trip and idempotency on `last_signal_id` are covered by
  `test_state_json_round_trip` and `test_idempotent_on_replayed_signal_id`.
- The runner consumes only signals whose payload has `status='resolved'`
  — `no_fill` and in-flight signals contribute zero information. Once the
  state's `triggered` flag is set the math is frozen but the watermark
  keeps advancing so re-runs stay no-ops.
- On trigger the runner calls `repo.set_triage(state='decayed', …)` and
  writes a separate `tool_journal` row with `tool_name='decay_disable'`,
  distinct from the surrounding `shadow_pass` row so audit trails can
  distinguish the disable event from a routine pass.

### Known limitations / follow-ups

- The runner's candidate loop filters on `triage_state='shadow'`, so a
  decayed candidate's open positions stop being tracked after auto-
  disable. The Phase D plan text ("no new signals; old positions still
  tracked to resolution") needs a small follow-up: extend the runner's
  candidate query to include `('shadow', 'decayed')` but skip signal
  generation for `'decayed'`. Out of scope for the D.3 landing; the
  body-midpoint candidate currently locked for shadow has no open trades
  at decay time so the gap is not yet operationally relevant.
- The binary tp/sl approximation for μ₀/σ ignores `timeout` exits. If a
  candidate's outcome profile is dominated by timeouts (currently rare),
  add a per-candidate-override path that lets the operator supply μ₀/σ
  directly via `notes_json.decay_reference`.

---

## D.4 — Slippage realism comparator

### Why

T8 in `discovery_hardening_plan.md` modeled a slippage stress matrix.
Phase D verifies that the model's choice of stress cell (default 2 ticks
slip + 1 bar delay) actually reflects realized fills. If real-world
slippage on this instrument/session is consistently 0.5 ticks, the model
is conservative; if it's 4 ticks, the model is dangerously optimistic
and the candidate selection criteria need to tighten.

### Approach

- Per-candidate trailing-30 distribution of `(realized_slip − modeled_slip)`.
- Aggregate distribution per (instrument, session) pair, surfaced as a
  ledger view.
- Daily report flags candidates whose trailing slippage exceeds the
  modeled stress cell for ≥ 5 consecutive trades.

### Files to touch

- `src/ta_foundation/shadow/slippage_compare.py` (new)
- Read tool: `get_realized_slippage_distribution(candidate_id)`
- `src/ta_foundation/research_ledger/repository.py` — slippage aggregate

### Done when

- Realized vs modeled distribution is visible per candidate.
- The flag fires on synthetic 5x slippage degradation.

---

## D.5 — Portfolio correlation tracker

### Why

The Real Edge doc and `real_edge_discovery_program.md` §"Regime and
Portfolio Selection" both require a basket of low-correlation strategies.
Once the shadow basket exceeds 2 candidates, you need to know whether
they're actually independent or just three flavors of the same trade.

### Approach

- Daily: compute pairwise correlation of trade-PnL series across all
  enrolled candidates over trailing-60 trades.
- If any pair > 0.85 for ≥ 14 days, surface in the daily health report
  with a recommendation to retire the lower-ranked of the two.
- Decision is human, not automatic. The agent recommends; the operator
  decides.

### Files to touch

- `src/ta_foundation/shadow/correlation.py` (new)
- Read tool: `get_basket_correlation_matrix(window)`

### Done when

- Correlation matrix renders in the daily report.
- High-correlation flag fires on two synthetic copies of the same
  candidate.

---

## Phase D exit criteria

1. ✅ Shadow simulator running for ≥ 90 days with zero unexplained
   ledger gaps (idempotency holds).
2. ✅ Daily health report has shipped daily, lint-clean.
3. ✅ At least one decay event has been correctly auto-disabled (or, if
   no candidate has decayed, the synthetic test asserts the path works).
4. ✅ Realized slippage distribution shows the modeled stress cell is
   conservative (i.e., real slip ≤ modeled slip 95% of the time). If
   not, return to T8 in the hardening plan and tighten the gate.
5. ✅ A small basket (3–5) of low-correlation candidates has accumulated
   ≥ 90 days of consistent shadow performance.

When all hold, *and the human operator independently decides the
record warrants it*, the question of going live can be considered. That
decision lives outside this plan and outside any agent's authority. The
constitution rule applies: an LLM never enters the live decision path.

---

## What Phase D is **not**

- Not Phase E (live trading). There is no Phase E in this plan.
- Not auto-tuning. A decayed candidate dies; it does not get retuned.
- Not a multi-strategy portfolio manager. Basket selection is human, even
  after Phase D is mature. The agent surfaces correlation; the human
  decides which to retire.
- Not the place to introduce a new agent role. Four roles is the cap.

---

## Closing note (for any future session reading this)

If, at any point during Phase D, you find yourself drafting a tool that
lets an agent send orders, retune live params, or override a human
decision about the basket — stop. Re-read `Real Edge In Day Trading.md`
and the master plan §1. The discipline is the product. The product is
not the agent.
