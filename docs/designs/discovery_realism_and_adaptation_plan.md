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
Sharpe Ratio gate — previously inert at n=1. Opt-in via the hardening
`trial_budget` config. Follow-up (not done): auto-populate the counts — the
sweep auto-reporting its grid size, the CLI reading the program total from the
research ledger.

### Step 3 — Regime-conditioned discovery — NEXT (not started)

Turn the per-candidate regime breakdown from a *report* into a *selection*
mechanism. The system already computes `regime_breakdown` per candidate
(`entry_strategies/sweep.py` → `compute_regime_breakdown`) and has regime gates
in `validation.py` (`min_per_regime_expectancy`, `regime_dispersion`) — but
those *require robustness across all regimes*, the opposite of finding the
regime where a pattern works. Regime classifiers exist
(`regime_recommender/classifier.py`: trend_up / trend_down / range, with
vol_expanding/compressed and trend_strong/weak secondaries).

**Build — post-hoc approach (recommended; mirrors steps 1–2, contained,
downstream of the sweep).** New module
`analysis/strategy_discovery/regime_scoping.py`:

1. Take a candidate's trades + per-trade regime label; compute per-regime
   honest metrics (reuse `honest_execution`).
2. Identify the regime(s) with a genuine edge.
3. Emit a regime-scoped candidate variant that trades only in those regimes.
4. Dual-track label: works across regimes → `durable`; works in a subset →
   `regime-limited` (see "Durable vs transient edges" below).
5. Re-validate the scoped variant honestly. The regime selection itself is
   multiple testing — picking the best of N regimes is N more trials — so feed
   that into the step 2 `trial_budget` (`within_run_trials`).

Then wire into `entry_strategies/hardening.py` and add tests, same shape as
steps 1–2.

**Deeper alternative (later, not first):** make the 8 sweep engines treat the
regime filter as a parameter axis. Larger; touches every family.

**RESUME HERE (new chat):** read `compute_regime_breakdown` in
`analysis/strategy_discovery/evaluation.py` for its output shape, and check how
per-trade regime labels / `bars_with_regime` flow into
`entry_strategies/sweep.py`. Then build `regime_scoping.py`. Steps 1–2 are
committed (`d12157a`, `c3646fc`); run `git log --oneline -6` and `git status`
to orient — the working tree has other unrelated mid-stream changes.

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
