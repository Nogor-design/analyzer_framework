# Design: Daily Lineup Selector (Phase 1)

*Created 2026-06-05. Part of [strategy_business_roadmap.md](strategy_business_roadmap.md) Phase 1.
Status: design for build. PM: Claude. Intended executor: Codex (harness) + Claude (scorer + review).*

## Problem (reframed 2026-06-05)

The "prediction AI" is **not a trained model** — it is a worker hand-prompting ChatGPT with chart
data + templates to pick each day's lineup. That process is **unmeasured, non-reproducible, and
account-blind.** We cannot tell if it beats flipping a coin, and it can't be audited or scaled.

We are not reverse-engineering it. We are **replacing it** with a selector that is:
1. **Deterministic & reproducible** — same inputs → same lineup, logged.
2. **Measured** — proven against baselines out-of-sample before anyone trusts it.
3. **Separable from risk** — the selector picks *which edges look good today*; the Phase-2
   allocator decides *how much each account trades* given its drawdown budget. Do not merge them.

> **Pushback worth stating plainly:** the win here is **not** a smarter black box. A transparent
> scorer we can validate beats a smarter oracle we can't. "Better prediction from your knowledge"
> should mean *making selection measurable*, not swapping one unmeasurable picker for another.

## Inputs (all already produced upstream)

- **Deployment-matrix manifest with features** (`build_session_deployment_manifest(..., with_features=True)`):
  per-cell template + metrics (PF, net, trades) + features (`effective_trades`, `daily_green_pct`,
  `mae_mfe_ratio_median`, walk-forward IS/OOS degradation, permutation p-value, exit-robustness margin,
  regime fit). This is the candidate universe.
- **Current market context** — regime classification for the upcoming session from
  `analysis/regime_recommender` (ADX/ATR features).
- **Outcome ledger** (Phase 0) — historical recommended-vs-actual results, for replay/validation.

## Selector v1 — transparent composite scorer (NOT an LLM)

A deterministic scoring function over the manifest features, ranked and filtered:

1. **Robustness gate** (hard filters): permutation p-value below threshold, IS/OOS degradation
   within bounds, `effective_trades` above a floor, exit-robustness margin positive. Anything that
   smells like a fake edge is dropped here. Ties to anti-fake-edge work in
   [project_edge_discovery]; trade count is itself a permutation-robustness signal.
2. **Regime match**: weight templates whose discovered regime matches today's classified regime.
3. **Composite score**: a documented weighted sum of normalized features (PF, daily_green_pct,
   mae_mfe, net, robustness). Weights are config, tunable, version-stamped.
4. **Diversity constraint**: cap correlated picks (e.g. don't return 10 templates from the same
   session/tier); enforce spread across sessions/tiers so a single regime miss can't sink the day.
5. **Output**: a ranked daily lineup (template → cell → score → rationale fields), serialized to
   the outcome ledger so it can be scored later.

The scorer is intentionally boring and explainable. It is the candidate we must prove beats baselines.

## Baseline harness (the actual deliverable that gates trust)

Replay historical sessions and compare the selector against baselines on **held-out** days:

- **Baselines**: (a) top-PF template per cell, (b) regime-matched template, (c) equal-weight the
  pool, (d) *if reconstructable from history*, the ChatGPT-worker's actual past picks.
- **Metrics per day/week**: realized expectancy, hit rate, **max adverse excursion / survival**
  (does the lineup keep accounts alive?), and dispersion. Survival is weighted heavily — a lineup
  with slightly lower expectancy but far better worst-case is preferred for prop accounts.
- **Verdict**: selector ships only if it beats baselines OOS on expectancy *and* survival. A
  negative result is a valid outcome — then a baseline becomes the production selector until beaten.

## Where an LLM legitimately fits (later, not v1)

- **Explanation, not selection**: an LLM can turn the deterministic picks + rationale into the
  client-facing weekly/daily email (Phase 3). The decision stays deterministic; the LLM only writes.
- **Hypothesis generation**: an LLM can propose new feature-weight sets or candidate features to
  *test* through the harness. It never picks live without passing the harness.

## Module layout (additive)

```
src/ta_foundation/analysis/selection/
  scoring.py      # composite score + robustness gate + diversity constraint (pure, tested)
  baselines.py    # top_pf / regime_matched / equal_weight / replayed_worker
  replay.py       # historical replay over outcome ledger -> metrics table
  selector.py     # orchestrates: manifest + regime -> ranked daily lineup
src/ta_foundation/web/  # "Daily Lineup" view (read-only renderer of selector output)
```

## Tests & exit criteria

- Unit: scorer determinism, robustness gate drops known-bad rows, diversity cap holds.
- Integration: replay over a fixture ledger reproduces a known metrics table.
- **Exit criterion (Phase 1 gate):** harness shows selector v1 ≥ best baseline OOS on expectancy
  and survival on the available history; result documented. Until then, production uses the
  winning baseline, not the hand-ChatGPT process.

## Executor guidance (handoff)

- Codex: build `replay.py` + `baselines.py` + the metrics table from this spec; pure functions,
  full unit tests, no web coupling.
- Claude: own `scoring.py` (the judgment-laden part) and review Codex's harness for leakage
  (no future data in replay; strict train/test split by date).
- Do **not** introduce any model inference into the selection path in v1.
