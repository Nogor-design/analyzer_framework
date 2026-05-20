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

### Step 2 — Selection-bias / multiple-testing accounting — NEXT

Discount a swept candidate's significance by the breadth of the search across
runs, families, instruments, sessions, and timeframes. A profit factor found
after 5,000 combinations is not the same evidence as one found after 5. A
partial mechanism exists (`n_hypotheses_tested` / the "P0-CUMULATIVE" path in
hardening validation) — extend it to true cumulative accounting.

### Step 3 — Regime-conditioned discovery

Search "this pattern, in this regime, at this hour" instead of "on average."
Most conditional edges are destroyed by averaging across regimes. Regime
classifiers already exist; the discovery search does not yet condition on them.

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
