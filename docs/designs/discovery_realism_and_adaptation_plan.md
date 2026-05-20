# Discovery Realism and Adaptation Plan

Status: active. Emerged from the 2026-05-20 review of why the discovery
pipeline produces no trustworthy survivors. Companion to
`docs/runbooks/manual_pipeline_proof.md`.

## Problem

The pipeline searches for the wrong *shape* of edge: a static, mechanically
defined indicator pattern with a stable historical average. Real edges are
conditional (regime / time of day), live as much in trade management as in
entries, and a brute-force parameter sweep is structurally biased toward
overfit. Backtest fills are optimistic, so a "found" edge cannot be
distinguished from a mirage.

## Fix sequence — honesty before discovery before adaptation

The order is deliberate: an adaptive layer on top of a dishonest backtest only
produces more convincing mirages. Each step is trustworthy only if the ones
before it are in place.

### Step 1 — Honest fill realism — DONE (commit d12157a)

`analysis/strategy_discovery/honest_execution.py`: pessimistic per-exit-type
fill haircut + an absolute PF/expectancy survival gate, wired as a hard gate in
entry-strategy hardening. Rejects edges that exist only under optimistic fills.
Does **not** catch touch-fill win misclassification (deferred — tick replay) or
selection bias (step 2).

### Step 2 — Selection-bias / multiple-testing accounting — DONE (commit c3646fc)

`analysis/strategy_discovery/trial_budget.py`: computes an effective trial
count (within-run combinations + decayed prior-program trials), wired into
hardening to feed both the Bonferroni t-test correction and the Deflated
Sharpe Ratio gate — previously inert at n=1.

Follow-up — auto-populate the counts — partially DONE: the candle sweep now
computes its own grid size (`sweep.py` → `_compute_trial_grid_size`: signal
combos × outcome modes, plus a config-derived upper bound for the MTF passes)
and auto-fills the hardening `trial_budget.within_run_trials`, so the
correction is no longer opt-in there. Still not done: the same wiring for the
other five hardening sweep families (`orb_sweep`, `ma_sweep`, `lcr_sweep`,
`bb_sweep`, and breakout/pullback/level via `_sweep_base`), and the CLI
reading the cumulative program total from the research ledger into
`prior_program_trials`.

### Step 3 — Regime-conditioned discovery — DONE

`analysis/strategy_discovery/regime_scoping.py`: turns the per-candidate regime
breakdown from a *report* into a *selection* mechanism. It labels each realised
trade with the regime in force at entry (merge_asof backward against
`bars_with_regime`), re-prices each regime's trade subset under the honest fill
model + absolute survival gate (reuses `honest_execution`), and identifies the
regime(s) where the edge genuinely survives. It emits a dual-track label —
`durable` (every regime tested clears the gate, no scoping needed),
`regime-limited` (a strict subset clears it; a scoped variant pooling those
regimes is emitted and re-validated honestly), or `none`. A regime with too few
trades is reported as un-judgeable (`skipped_regimes`), not failed.

Wired into `entry_strategies/hardening.py` as the `regime_scoping` block, with
an opt-in hard gate (`require_regime_scoping_passed`, default off);
`bars_with_regime` now threads through `entry_strategies/sweep.py` into
hardening. The regime selection is itself multiple testing — picking the best of
N regimes is N more trials — so `regime_scoping` reports `n_regimes_evaluated`,
which `build_hardening_metadata` adds to the step-2 `trial_budget`
`within_run_trials` before the Bonferroni / DSR corrections are computed.

**Deeper alternative (later, not first):** make the 8 sweep engines treat the
regime filter as a parameter axis. Larger; touches every family. Not done.

### Step 4 — Journaled AI supervisory layer

A bounded, logged AI judgment at the regime / session / setup-quality timescale
(not tick scalping): read the live state, retrieve what the ledger has
*validated* for that regime, and make a call — suppress, size-down, or flag.

Rationale — why supervisory and measured, not a black-box learner. Markets are
non-stationary (the edge decays), adversarial (a discovered edge is arbitraged
away), low signal-to-noise, and sample-starved — the specific conditional
events that matter (e.g. FOMC-day behavior) number in the dozens, not the
millions deep learning needs. More learning capacity on this target mostly
buys more convincing overfit. So the AI layer's "learning" is **externalized
into the research ledger** — validated, decay-tracked, provenance-stamped — not
absorbed into model weights. The layer's contribution must be backtested as its
own variable; an unmeasured AI overlay is confident noise, not judgment.

Detailed blueprint: `docs/samples/adaptive_learning_layer_high_level_ideas.md`.
It is the full design for this step and is well-aligned — its `adaptation_alpha`
metric (result-with-decision minus baseline-without) is exactly the "measure it
as its own variable" discipline. Notes when step 4 begins: (a) build only its
"Version 0 — offline candidate-context profiler" first, not the 13-module
package; (b) that profiler overlaps step 3's `regime_scoping` — unify them,
don't build two regime-analysis paths; (c) step 4 depends on steps 1–3 and must
not start before them.

**Version 0 progress — adaptation_alpha measurement — DONE.** Per note (b), the
blueprint's `adaptation_alpha` metric was landed inside `regime_scoping` rather
than as a parallel module: each candidate now reports `adaptation_alpha` —
the honest result of trading only the edge regimes (the regime-suppression
decision) minus the honest result of trading every classified trade (the
baseline). Reports `expectancy_delta`, `net_profit_delta`,
`profit_factor_delta`, and `n_trades_delta` so the suppression trade-off is
explicit. Still needed for Version 0: the cross-candidate context profiler
(strong / weak / unknown contexts per candidate) and the regime/session
performance-matrix report surface — both need an architecture decision on
where a candidate corpus is read from, which is the natural next handoff point.

### Deferred — full tick-replay outcome resolution

Replace 1m-bar touch-fill resolution with tick-by-tick fills; kills the
touch-fill mirage at its source. Larger — needs tick-cache plumbing.

## Durable vs transient edges

The market shifts; a real edge can work for ~2 months and then fade. The
current robustness gates (multi-year walk-forward, OOS-degradation) are built
to find *durable* edges and will, by design, reject a genuine 2-month edge —
to a multi-year fold test it is indistinguishable from overfit.

Accommodating transient edges is therefore not a relaxation of the gates; it is
a second, explicitly-labelled validation track:

- **Durable track** — passes multi-year walk-forward; sized and held normally.
- **Transient / regime-limited track** — does not require multi-year
  stability, but must (a) have a mechanism story, (b) be currently working,
  (c) be enrolled in fast decay monitoring (CUSUM decay detection already
  exists in `shadow/decay.py`), and (d) be sized and capital-allocated as the
  high-decay-risk bet it is, with the graveyard treated as a rotation log.

This is the time-axis of step 3: "find where a pattern works" extended to
"find when it is *currently* in its working window."

Critical dependency: transient harvesting means continuously re-scanning, which
multiplies the number of trials run — so step 2 (trial-budget accounting) is a
hard prerequisite. Hunting recent winners without selection-bias correction is
just recency-chasing in a quant costume.

## Ordering principle

Honesty (steps 1–2) before discovery (step 3) before adaptation (step 4). The
constraint in this domain is honest validation under non-stationarity, not
learning capacity — so discipline is sequenced ahead of cleverness.
