# Discovery Hardening Plan

Sequenced fix list for the `ta_foundation` strategy-discovery system, derived from the fund-analyst audit performed on 2026-05-07. Each item closes a specific source of in-sample bias, validation theater, or unrealistic execution assumption identified in the audit.

This file is the single source of truth for the work queue. It exists so a fresh chat can pick up where the previous session stopped without re-deriving context. **Update this file as items are completed**, not just the in-memory task list.

---

## Status snapshot

| ID | Item | Status | Notes |
|----|------|--------|-------|
| P0-1 | Locked holdout enforced in orchestrator | ✅ Done | 60/20/20 chronological partition, default ON. Discovery runs on dev (IS+VAL) only; holdout evaluated once. |
| P0-2 | Rank on OOS metrics (not full-period) | ✅ Done | Ranking prefers `evaluation_oos`, falls back to `evaluation`. Cross-run row exposes `ranking_source`, `holdout_enabled`, dev/oos/holdout side-by-side. |
| P0-3 | Separate exit search from entry search | ✅ Done | T5. `_split_dev_for_search()` partitions dev (IS+VAL) into chronological entry/exit halves; `pkg.trades` is swapped to entry slice around `entry_discovery`, to exit slice around `exit_discovery`, and `signal_feature_df` is split before `signal_entry_discovery` / `signal_exit_sweep`. |
| P0-4 | Hard exclusion gates with fund-grade defaults | ✅ Done | T6. `evaluate_hard_gates()` in ranking.py; survivors vs rejected in cross-run output. Defaults: `min_oos_trades=30`, `min_profit_factor=1.0`. Fund-grade users tighten via `strategy_discovery.ranking_gates:` YAML. |
| P1-5 | True k≥6 rolling walk-forward + fold distribution | ✅ Done | T7. `wf_type='rolling'` divides dev into n_folds+1 disjoint blocks; per-fold IS slices verifiably non-overlapping. Default n_folds=6. `folds_distribution`, `oos_expectancy_std`, `oos_pf_std`, `oos_sharpe_mean` surfaced. Stability score now blends fold-variance penalty. |
| P1-7 | Slippage and latency stress sweep | ✅ Done | T8. New `slippage_stress.py` re-prices dev trades across `slippage_ticks × entry_delays` grid using a delay→tick cost model. `discovery_block["slippage_stress"]` surfaces the matrix + a configurable `stress_cell` (default slip=2, delay=1). Opt-in `require_slippage_stress_passed` gate added to `evaluate_hard_gates`. |
| P1-6 | Permutation/null tests | ✅ Done | T9. New trade-level sign-flip null test module plus opt-in ranking gate. |
| P1-9 | Cross-period and cross-instrument robustness | ⏳ Queued | T10 |
| P1-8 | Tick-replay intra-bar touch resolution | ⏳ Queued | T11 |
| P1-10 | Romano-Wolf / Deflated-Sharpe family-wise correction | ⏳ Queued (blocked by T9) | T12 (T5 + T7 now done) |
| P2 | Day/time gates + regime independence + correlation diversity | ⏳ Queued (blocked by T6) | T13 |

---

## Already-shipped changes (P0-1, P0-2)

For continuity. Files touched:

- `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
  - Added `DEFAULT_HOLDOUT_CONFIG` (60/20/20 default, ON).
  - Added `_resolve_holdout()`, `_compute_oos_evaluation()`, `_compute_holdout_evaluation()`.
  - Per-package loop partitions trades, mutates `pkg.trades` to dev slice for the duration of discovery, computes three evaluation views (`evaluation` = dev, `evaluation_oos` = OOS-fold concat, `evaluation_holdout` = locked one-shot), restores `pkg.trades` before ranking/reporting.
- `src/ta_foundation/analysis/strategy_discovery/validation.py`
  - Added `extract_oos_pool(trades, wf_config)` helper.
- `src/ta_foundation/analysis/strategy_discovery/ranking.py`
  - Added `_select_ranking_evaluation()` — prefers `evaluation_oos`, falls back to `evaluation`.
  - Cross-run leaderboard rows include `ranking_source`, `holdout_enabled`, and dev/oos/holdout PF/n_trades/net_profit.

YAML config block (under `strategy_discovery:`):
```yaml
strategy_discovery:
  holdout:
    enabled: true              # default ON
    is_frac: 0.60
    val_frac: 0.20
    time_col: entry_time
    min_dev_trades: 50         # below this, holdout disabled (insufficient sample)
    min_holdout_trades: 20
```

All 1,174 tests pass; one pre-existing `regime_recommender` failure unrelated to these changes.

### Already-shipped: T6 — P0-4 hard exclusion gates

- `src/ta_foundation/analysis/strategy_discovery/ranking.py`
  - Added `DEFAULT_RANKING_GATES` (defaults: `min_oos_trades=30`, `min_profit_factor=1.0`; everything else null/skip).
  - Added `evaluate_hard_gates(sd, gates_config) → list[failure_dict]` covering: `min_oos_trades`, `min_profit_factor`, `min_sharpe`, `max_drawdown_pct`, `require_sensitivity_class`, `require_validation_passed`.
  - `run_ranking()` now takes a `gates_config` kwarg, partitions packages into `ranked` / `rejected`, and the cross-run dict surfaces both lists plus `n_survivors` / `n_rejected` diagnostics. Rejected runs get `confidence_tier="rejected"` and `rejection_reasons=[...]` attached on `pkg.metadata["derived"]["strategy_discovery"]["ranking"]`.
- `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
  - Threads `options.get("ranking_gates")` into `run_ranking`.

YAML config block (under `strategy_discovery:`):
```yaml
strategy_discovery:
  ranking_gates:
    enabled: true
    min_oos_trades: 30           # statistical-sanity floor; fund-grade ≈ 150
    min_profit_factor: 1.0       # not unprofitable; fund-grade ≈ 1.2–1.5
    min_sharpe: null             # fund-grade ≈ 1.0
    max_drawdown_pct: null       # fund-grade ≈ 25.0
    require_sensitivity_class: null   # null | "moderate" | "robust"
    require_validation_passed: false
```

All 1,174 tests still pass after T6. Smoke test confirms thin-sample and unprofitable mock runs are correctly partitioned to `rejected` with structured reasons; fund-grade thresholds reject all three test runs as expected.

### Already-shipped: T5 — P0-3 separate exit search from entry search

- `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
  - Added `DEFAULT_DEV_SPLIT_CONFIG` (defaults: `entry_frac=0.50`, `mode="chronological"`, `min_entry_trades=30`, `min_exit_trades=30`).
  - Added `_split_dev_for_search(dev_trades, cfg) → (meta, entry_trades, exit_trades)` — chronological-by-date-span partition, with an `interleaved` mode for small samples; falls back to full dev when disabled / sample too small / time column missing.
  - Added `_split_signal_feature_df(sfdf, meta, entry_frac, mode) → (entry_sfdf, exit_sfdf)` — same convention applied to the per-signal corpus via the `dt` column.
  - Per-package loop now records `discovery_block["dev_split"]`, swaps `pkg.trades` to `entry_trades` around `entry_discovery` and to `exit_trades` around `exit_discovery` (with `try/finally` restore), and feeds the entry slice of `signal_feature_matrix` to `signal_entry_discovery`.
  - `__market_discovery__` branch builds the full corpus matrix once, splits it, and feeds the entry half to `signal_entry_discovery` / `signal_validation` and the exit half to `signal_exit_sweep`. Records `corpus_block["dev_split"]`.
- `src/ta_foundation/tests/analysis/strategy_discovery/test_strategy_discovery.py`
  - Added `TestDevSplitForSearch` (6 tests): chronological + interleaved disjoint splits, disabled fallback, too-small-sample fallback, signal_feature_df split, signal_feature_df split inactive.

YAML config block (under `strategy_discovery:`):
```yaml
strategy_discovery:
  dev_split:
    enabled: true
    entry_frac: 0.50
    mode: chronological        # 'chronological' | 'interleaved'
    min_entry_trades: 30
    min_exit_trades: 30
    time_col: entry_time
```

All 1,174 + 6 new = 323 strategy_discovery tests pass; full suite still has only the pre-existing `regime_recommender` failure unrelated to this work.

### Already-shipped: T7 — P1-5 true non-overlapping rolling walk-forward

- `src/ta_foundation/analysis/strategy_discovery/validation.py`
  - `DEFAULT_WF_CONFIG` default `n_folds` bumped 5 → 6; `wf_type` documented as 'rolling' (new) | 'anchored' (legacy).
  - Added `_compute_sharpe()` helper (per-trade mean/std, not annualised — used only for fold-vs-fold comparison).
  - Rewrote `compute_walk_forward_rolling()` to support both modes. In `'rolling'` mode the dev set is partitioned into `n_folds + 1` contiguous, equal-size blocks; for fold *i* the IS slice is block *i-1* and the OOS slice is block *i*. **Every fold's IS slice is verifiably disjoint from every other fold's IS slice** — no expanding-window leak. Result dict additionally surfaces `wf_type`, `folds_distribution` (list of `{fold, oos_pf, oos_sharpe, oos_expectancy, oos_n_trades}`), `oos_expectancy_std`, `oos_pf_std`, `oos_sharpe_mean`, plus `is_idx_range` / `oos_idx_range` per fold for verification. Legacy keys (`folds`, `fold_sign_consistency`, `oos_expectancy_across_folds`, `passed`) preserved.
  - Extracted `_per_fold_metrics()` so rolling and anchored branches share the same per-fold schema.
  - `extract_oos_pool()` is now wf_type-aware: rolling mode strips only the first `n // (n_folds + 1)` block; anchored mode keeps the legacy `is_pct` cut. Comment updated.
- `src/ta_foundation/analysis/strategy_discovery/ranking.py`
  - `_score_stability()` now blends a 4th component: a coefficient-of-variation penalty on per-fold OOS expectancy (`oos_expectancy_std / |across-fold mean|`). Weights rebalanced to retention 40% / oos_pf 25% / ratio 15% / fold_var 20%. Falls back to neutral 50 when fewer than 2 folds were retained.
- `src/ta_foundation/tests/analysis/strategy_discovery/test_strategy_discovery.py`
  - Added `TestRollingWalkForward` (6 tests): pairwise-disjoint IS slices, OOS pool = union of fold OOS, `folds_distribution` schema, variance fields populated, anchored-mode backward compat, and `extract_oos_pool` rolling-mode boundary check.

YAML config block (under `strategy_discovery:`):
```yaml
strategy_discovery:
  walk_forward:
    wf_type: rolling           # 'rolling' (T7 default) | 'anchored' (legacy expanding IS)
    n_folds: 6                 # k≥6 per spec
    min_is_trades: 50
    min_oos_trades: 20
    is_pct: 0.70               # only used when wf_type='anchored'
    degradation_threshold: 0.20
```

All 1,174 + 12 new = 329 strategy_discovery tests pass; full suite unchanged at 1,186 passing with the same pre-existing `regime_recommender` failure.

### Already-shipped: T8 — P1-7 slippage / latency stress sweep

- `src/ta_foundation/analysis/strategy_discovery/slippage_stress.py` (new)
  - `DEFAULT_SLIPPAGE_STRESS_CONFIG` (defaults: `slippage_ticks=[1,2,3]`, `entry_delays=[0,1,2]`, `delay_cost_per_bar_ticks=1.0`, `max_expectancy_loss_pct=40.0`, `stress_cell=[2,1]`).
  - `run_slippage_stress(trades, baseline_cost_model, options) → dict` — re-prices each trade across a Cartesian product of (slippage, entry-delay) cells using `commission + 2·slip·tick_value + 2·delay·delay_cost_per_bar_ticks·tick_value`. Reports per-cell expectancy / profit_factor / net_profit / `expectancy_loss_pct` (relative to the most-optimistic cell = lowest slip, zero delay), plus a single `stress_cell` row used for the rejection gate. Falls back to the worst observed cell when the configured `stress_cell` lies outside the grid so the gate never silently passes.
- `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
  - Per-package loop now calls `run_slippage_stress` after the holdout evaluation, feeding the dev-slice cost-normalized trades, and stores the result under `discovery_block["slippage_stress"]`. Honours `options.get("slippage_stress")` so users can override the grid in YAML.
- `src/ta_foundation/analysis/strategy_discovery/ranking.py`
  - `DEFAULT_RANKING_GATES` gains `require_slippage_stress_passed: false` (opt-in).
  - `evaluate_hard_gates()` now reads `sd["slippage_stress"]`; when the gate is on, the candidate is rejected if the sweep was disabled / errored or the stress cell's `expectancy_loss_pct` exceeds the configured `max_expectancy_loss_pct`. Rejection rows carry the actual loss percentage and threshold so reviewers see exactly why a candidate dropped out.
- `src/ta_foundation/tests/analysis/strategy_discovery/test_strategy_discovery.py`
  - Added `TestSlippageStress` (11 tests): matrix shape, monotonic loss-pct vs slip/delay, configured stress-cell selection, gate fail under thin expectancy, gate pass under strong edge, disabled fallback, empty-trades skip, missing-`profit`-column skip, ranking-gate rejection on failure, ranking-gate rejection when sweep not run, gate off by default.

YAML config block (under `strategy_discovery:`):
```yaml
strategy_discovery:
  slippage_stress:
    enabled: true
    slippage_ticks: [1, 2, 3]
    entry_delays: [0, 1, 2]
    delay_cost_per_bar_ticks: 1.0
    max_expectancy_loss_pct: 40.0
    stress_cell: [2, 1]          # (slip_ticks, delay_bars) used for the gate

  ranking_gates:
    require_slippage_stress_passed: true   # opt-in; defaults to false
```

All 329 + 11 new = 340 strategy_discovery tests pass; full suite at 1,197 passing with the same pre-existing `regime_recommender` failure unrelated to this work.

### Already-shipped: T9 — P1-6 permutation / null tests

- `src/ta_foundation/analysis/strategy_discovery/permutation_tests.py` (new)
  - Added `PermutationResult`, `permutation_test_returns()`, and `permutation_test_for_discovery()`.
  - Uses a seeded `numpy.random.Generator` and a trade-level sign-flip null: per-trade magnitudes are preserved while random signs destroy directional edge.
  - Reuses existing evaluation/risk metric functions for expectancy, profit factor, and Sharpe.
  - Discovery helper tests the OOS pool via `extract_oos_pool()` when full cost-normalized trades are supplied, and returns `status="insufficient_trades"` below 30 OOS trades.
- `src/ta_foundation/analysis/strategy_discovery/ranking.py`
  - Added opt-in `require_permutation_passed` gate with `max_permutation_p` defaulting to `0.05`.
  - With the gate disabled, ranking behavior is unchanged; when enabled, candidates with an OOS permutation p-value above threshold are rejected with reason `permutation_p>{threshold}`.
- `src/ta_foundation/tests/analysis/strategy_discovery/test_permutation_tests.py`
  - Covers pure-noise non-significance, strong-edge significance, deterministic seeding, OOS-pool extraction, and the opt-in ranking gate.

---

## Sequencing rationale

Order is chosen so each fix makes the next one's numbers more honest:

1. **T5 (P0-3) first** — exits are co-fit with entries on the same trades today. That's the largest remaining edge inflator after the locked holdout.
2. **T6 (P0-4) second** — hard exclusion gates. Cheap, standalone, unblocks T13.
3. **T7 (P1-5) after T5** — k≥6 rolling WF only makes sense once exits aren't being co-fit per fold.
4. **T8, T9, T10, T11** — independent of each other; can interleave or parallelize.
5. **T12 (P1-10) last in P1** — multiple-comparisons correction needs the candidate generator stable, otherwise it just deflates noisy numbers.
6. **T13 (P2) last** — gates and breakdowns that consume metrics produced upstream.

---

## Per-task implementation specs

### T5 — P0-3: Separate exit search from entry search  ✅ DONE

**Problem:** `simulator.py:56-73`, `sweep.py:88-96` co-grid stops/targets against entry patterns on the same dev trades. Joint optimization inflates apparent edge by an order of magnitude.

**Approach:**
- In `orchestrator.py`, sub-partition the dev (IS+VAL) slice into `entry_dev` and `exit_dev` (default 50/50 chronological — could be interleaved by date if sample is small).
- Pass `entry_dev` to `entry_discovery`, `signal_entry_discovery`, `entry_pattern_bridge`.
- Pass `exit_dev` to `exit_discovery`, `signal_exit_sweep`.
- Joint performance reported only on the OOS-fold concat (`evaluation_oos`) and the locked holdout (`evaluation_holdout`).
- New YAML block:
  ```yaml
  strategy_discovery:
    dev_split:
      enabled: true
      entry_frac: 0.50
      mode: chronological        # 'chronological' | 'interleaved'
      min_entry_trades: 30
      min_exit_trades: 30
  ```

**Files to touch:**
- `orchestrator.py` — add `_split_dev_for_search()`; mutate `pkg.trades` to entry-slice during entry-search phase, switch to exit-slice during exit-search phase, restore at end.
- Possibly `signal_exit_sweep.py` and `exit_discovery.py` if they implicitly assume the full dev set.

**Done when:**
- `discovery_block["dev_split"]` records the partition.
- Test confirms entry-discovery and exit-discovery see disjoint trade slices.
- Existing tests still pass (default thresholds permissive enough).

### T6 — P0-4: Hard exclusion gates with fund-grade defaults  ✅ DONE

**Problem:** `ranking.py:147-156` ranks PF→score; today's "weight sensitivity" tests scoring weights, not strategy params. Fragile rules survive ranking with a flag instead of being excluded.

**Approach:**
- Add `evaluate_hard_gates(sd, gates_config) → list[GateFailure]` in `ranking.py`.
- Gates (each individually opt-in via threshold; null/None = skip):
  - `min_oos_trades` — n_trades on `evaluation_oos` or fallback `evaluation`. **Default 30** (statistical sanity floor; real fund-grade users will set 150+).
  - `min_profit_factor` — PF on the same eval block. **Default 1.0** (not unprofitable).
  - `min_sharpe` — `risk_metrics.sharpe_ratio`. Default `None`. Fund-grade: 1.0.
  - `max_drawdown_pct` — `drawdown_analysis.overall.max_drawdown_pct`. Default `None`. Fund-grade: 25.0.
  - `require_sensitivity_class` — one of `null` / `"moderate"` / `"robust"`. Default `None`.
  - `require_validation_passed` — boolean. Default `False`.
- In `run_ranking()`: partition packages into survivors and rejected before scoring. Output two tables: `ranked` (survivors, sorted by final_score) and `rejected` (with `rejection_reasons: list[dict]`).
- Rejected runs still get a per-run ranking attached but with `confidence_tier="rejected"`.
- New YAML block:
  ```yaml
  strategy_discovery:
    ranking_gates:
      enabled: true
      min_oos_trades: 30
      min_profit_factor: 1.0
      min_sharpe: null
      max_drawdown_pct: null
      require_sensitivity_class: null
      require_validation_passed: false
  ```

**Files to touch:**
- `ranking.py` — `evaluate_hard_gates`, `_RankingGates` dataclass, partition in `run_ranking`.
- `orchestrator.py` — pass `options.get("ranking_gates")` through to `run_ranking`.

**Done when:**
- Cross-run output has both `ranked` and `rejected` arrays.
- Passing `min_oos_trades=200` on a small synthetic test rejects all runs and surfaces them with reasons.
- Existing tests pass (defaults are gentle).

### T7 — P1-5: True rolling walk-forward, k≥6  ✅ DONE

**Problem:** `validation.py:312-418` rolling WF uses anchored expanding windows — later folds reuse earlier-fold IS data, leaking information.

**Approach:**
- Replace `compute_walk_forward_rolling()` with non-overlapping rolling IS windows (e.g., k=6 folds, each fold's IS = previous OOS chunks excluded).
- Report per-fold metric distribution: list of (fold_idx, oos_pf, oos_sharpe, oos_expectancy, oos_n_trades).
- Add fold-to-fold variance as a stability score component.
- Update `extract_oos_pool` to return the union of all fold OOS slices (already non-overlapping in current code, but make explicit).
- Default `n_folds=6`. Min IS window proportional to `n_trades / (k+1)`.

**Files to touch:**
- `validation.py` — rewrite `compute_walk_forward_rolling`; keep old behavior under a `wf_type: "anchored"` flag if needed for backwards compat.
- `orchestrator.py` — surface per-fold distribution in `discovery_block["validation"]["wf_results"]["folds_distribution"]`.
- `ranking.py` — use fold variance in stability score.

**Done when:**
- Each fold's IS slice is verifiably non-overlapping with other folds' IS slices.
- Fold distribution surfaces in cross-run output.
- Tests pass.

### T8 — P1-7: Slippage and latency stress sweep  ✅ DONE

See "Already-shipped: T8" above. Entry-delay is modelled as additional per-bar slippage (config: `delay_cost_per_bar_ticks`, default 1.0) rather than re-simulating exits; that's the right granularity for re-pricing realised trades and matches the existing per-trade cost-model abstraction. Tick-replay intra-bar resolution remains T11's job.

### T9 — P1-6: Permutation/null tests

**Problem:** `validation.py:488-555` Monte Carlo only shuffles trade order — answers "is sequencing lucky?" not "is the edge real?"

**Approach:**
- Add `run_permutation_null(trades, market, signal_generator, n_perms=500)`:
  - Randomize entry timestamps within session boundaries.
  - Regenerate trade outcomes from market data using the SAME exit logic.
  - Build null distribution of PF/Sharpe.
  - Report empirical p-value of observed metric vs. null.
- Augment, don't replace, the existing trade-shuffle MC.
- Surface `discovery_block["validation"]["permutation_null"]`.

**Files to touch:**
- New `analysis/strategy_discovery/permutation_null.py`.
- `validation.py` — wire as an additional gate result.
- `ranking.py` — null p-value as a robustness component.

**Done when:**
- A known random-noise signal yields p > 0.5; a known structural signal yields p < 0.05.
- Existing trade-shuffle MC still reports independently.

### T10 — P1-9: Cross-period and cross-instrument robustness

**Problem:** Single-symbol, single-time-slice discovery only. No structural-edge validation.

**Approach:**
- For each top-N surviving candidate (post-T6 gates):
  - **Cross-period**: re-run on a held-out time slice from the same instrument (separate from the locked holdout).
  - **Cross-instrument**: re-run on sibling instruments when present in `MarketDataStore` (NQ → ES, RTY).
- Require positive expectancy on at least one sibling to confirm structural edge.
- Failing candidates drop out of the leaderboard via T6 framework.
- New YAML block:
  ```yaml
  strategy_discovery:
    cross_robustness:
      enabled: true
      cross_period:
        enabled: true
        slice_frac: 0.15      # carved before holdout
      cross_instrument:
        enabled: true
        siblings: ["ES", "RTY"]
        require_positive_on: 1
  ```

**Files to touch:**
- New `analysis/strategy_discovery/cross_robustness.py`.
- `orchestrator.py` — call post-discovery, pre-ranking.
- `ranking.py` — gate via T6 framework.

**Done when:**
- Cross-instrument re-fit runs only when sibling data is loaded.
- Skipped gracefully (with reason) when not available.

### T11 — P1-8: Tick-replay intra-bar touch resolution

**Problem:** `simulator.py:135-139` resolves stop-vs-target ambiguity within a bar heuristically. Real execution depends on tick sequence.

**Approach:**
- When tick data is present in `MarketDataStore`, replace the heuristic with tick-by-tick replay:
  - Walk the bar's ticks chronologically.
  - First level (stop or target) hit wins.
- When ticks are absent, log per-candidate **ambiguity rate** (fraction of trades where stop and target both occur in the same bar) so reviewers see residual risk.
- Gate on tick-data availability — never block the pipeline.

**Files to touch:**
- `analysis/entry_strategies/outcome/simulator.py` — new `_resolve_outcome_with_ticks()` path.
- `marketdata/store.py` — confirm tick lookup API.
- `orchestrator.py` — pass tick store into simulator.

**Done when:**
- A test trade with stop-then-target tick sequence resolves to stop; reverse sequence resolves to target.
- Ambiguity rate appears in candidate metadata when ticks unavailable.

### T12 — P1-10: Multiple-comparisons accounting

**Problem:** Thousands of candidates evaluated with no family-wise error control. P-values reported per candidate are individually correct but family-wise meaningless.

**Approach:**
- After T5, T7, T9 stabilize the candidate generator:
  - Track number of candidates evaluated per discovery run via `orchestrator.py` (already partially counted in `count_experiments`).
  - Apply Romano-Wolf step-down on top-K candidates' p-values from T9 permutation null.
  - Make Deflated Sharpe Ratio (already in `validation.py`) a hard gate via T6 framework instead of optional.
- Update ranking diagnostics to show family-wise error control metrics.

**Files to touch:**
- `analysis/statistics/dsr.py` — possibly extend.
- New `analysis/strategy_discovery/multiple_comparisons.py`.
- `ranking.py` — wire DSR as default-on hard gate.

**Done when:**
- Top-K leaderboard shows family-wise corrected p-values.
- DSR rejection paths trigger correctly.

### T13 — P2: Day/time gates, regime independence, diversity

**Problem:** Day-of-week / hour breakdowns are descriptive only. Regime classification is computed on the same window as validation. Top-N can be all variants of one edge.

**Bundle approach:**
- **Day/time gates**: reject candidates whose edge concentrates >X% in one hour or one day-of-week (fragile to schedule changes). Configurable threshold.
- **Regime independence**: compute regime classifier on a window that doesn't overlap the validation window. Stops the regime-dispersion gate from being trivially satisfied.
- **Diversity**: correlation matrix of trade-pnl-series across surviving top-N. If two candidates are >0.85 correlated, keep the higher-ranked, drop the other.

**Files to touch:**
- `ranking.py` — day/time concentration gate via T6 framework; correlation-based deduplication post-ranking.
- `analysis/strategy_discovery/regime.py` — accept a separate `classification_window` parameter.

**Done when:**
- Day/time gate rejects schedule-fragile candidates.
- Top-N leaderboard is verifiably uncorrelated.

---

## How to resume in a new chat

1. Read this file (`docs/designs/discovery_hardening_plan.md`).
2. Read `CLAUDE.md` for repo conventions.
3. Check the **Status snapshot** table above — find the next 🔄 In progress or ⏳ Queued item that isn't blocked.
4. Read the original audit findings if needed: the discovery system's structural issues are documented in the prior chat's Tool Quality Assessment but the relevant file:line citations are in this plan's per-task specs.
5. Run the strategy_discovery test suite before and after each change:
   ```
   python -m pytest src/ta_foundation/tests/analysis/strategy_discovery/test_strategy_discovery.py -x --tb=short
   ```
6. After each item ships, update this file's Status snapshot row to ✅ Done with a one-line summary of files touched.

The pre-existing `test_recommender_orchestrator.py::test_orchestrator_attaches_payload_and_exports_templates` failure is unrelated to this plan and exists on `main` — ignore it.
