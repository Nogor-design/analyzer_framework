# ta_foundation: Edge Discovery Build Plan

Generated: 2026-04-20 | Branch: CandleDiscovery

## Goal
Transform ta_foundation from a NinjaTrader analytics dashboard into a system capable of
discovering and validating statistically defensible day-trading edge.

---

## Phase A — Stabilize Validation Infrastructure

### Step 1: Look-Ahead Audit [COMPLETE — 2026-04-20]
**Verdict: PASS on all verified paths**

| Path | Finding |
|---|---|
| `candle/features.py` | All rolling indicators use `shift(1)` — no future data |
| `signals.py emit_next_open` | Entry at next bar open (next bar's dt/open via `shift(-1)` merge) |
| `signals.py emit_break_extreme` | Limit price from signal bar's high/low — no future data |
| `simulator.py simulate_atr_outcomes` | Outcome scan starts at `signal_bar_idx + 1` |
| `simulator.py simulate_tick_outcomes` | Vectorized forward window from `b + 1` — clean |

**Remaining uncertainty:** Other signal family feature implementations (ma, bb, orb, breakout,
pullback, level, lcr) were not individually audited. Each new family should assert look-ahead
safety in its docstring, confirmed by code review, before its sweep results are trusted.

---

### Step 2: Bonferroni-Corrected T-Stat + Rolling Walk-Forward [COMPLETE — 2026-04-20]
**File:** `src/ta_foundation/analysis/strategy_discovery/validation.py`

Changes implemented:
- `run_t_test()`: added `n_hypotheses_tested` (Bonferroni alpha correction) and
  `min_t_stat_abs` (e.g., pass 3.0 to enforce t > 3 requirement).
  `required_t_stat = max(t_critical(corrected_alpha, df), min_t_stat_abs)`
- Added `compute_walk_forward_rolling()`: anchored expanding IS across `n_folds`,
  returns per-fold metrics, `fold_sign_consistency`, and `oos_expectancy_across_folds`.
- `run_validation()`: added `n_hypotheses_tested`, `min_t_stat_abs`, `use_rolling_wf`
  parameters. Rolling WF result auto-wires into `fold_sign_consistency` gate.

**Backward compatibility:** All new parameters have defaults that preserve prior behavior.

---

### Step 3: Prop-Firm Simulation Relocation [COMPLETE — 2026-04-20]
**From:** `analysis/pattern_engine/monte_carlo.py`
**To:** `analysis/prop_evaluation/simulation.py`

Moved: `_simulate_prop_path`, `run_prop_monte_carlo`, `run_prop_monte_carlo_regime`,
`apply_microstructure_stress`, `apply_trade_intake_model`, `build_markov_regime_model`,
all private helpers.

`pattern_engine/monte_carlo.py` is now a placeholder reserved for the edge-validation
shuffle/permutation test (future Step 3 follow-up).

`pattern_engine/orchestrator.py` import updated to point to new location.
Dead `apply_trade_intake_model` call in `engine.py` removed (was never imported
and its result was never used — always masked by try/except fallback).

754 tests pass. 1 pre-existing regime_recommender failure unrelated to this change.

---

## Phase B — Experiment Persistence

### Step 4: DuckDB Experiment Registry [COMPLETE — 2026-04-20]
**New file:** `src/ta_foundation/persistence/db.py`

Tables created:
```sql
experiments(id, hypothesis_text, family, signal_id, params_json, instrument,
            contract, timeframe, date_range_start, date_range_end,
            cost_model_json, registered_at, holdout_locked_at, status)

validation_results(id, experiment_id, run_at, n_hypotheses_at_time,
                   validation_result_json, passed)

optimization_batches(id, batch_id, strategy_name, instrument, imported_at,
                     row_count, results_parquet_path)
```

`ExperimentRegistry` class wraps all table operations with per-call connections.
`holdout_locked_at` is set on first `register_experiment()` call.
`count_experiments()` supplies automatic Bonferroni denominator to `record_validation()`.

Wiring:
- `run_validation()` gains `db_path` + `experiment_id` params; DB write is best-effort (never blocks).
- `run_strategy_discovery()` in orchestrator.py gains `db_path` param; passes to `run_validation()`.
- CLI gains `--db-path` flag; passed through to orchestrator.
- `duckdb` added to `pyproject.toml` under `[project.optional-dependencies] persistence`.
- 122/123 tests pass (pre-existing regime_recommender failure unchanged).

### Step 5: Pre-Registration Workflow [COMPLETE — 2026-04-20]
**New file:** `src/ta_foundation/cli/register_hypothesis.py`

CLI: `python -m ta_foundation.cli.register_hypothesis --db-path <file>`

Modes:
- Interactive: prompts for family, signal_id, instrument, contract, timeframe,
  date_range_start/end, hypothesis text; confirms before writing.
- Non-interactive: `--non-interactive` flag; all fields passed as CLI args.
- List: `--list` flag prints all experiments in the registry.

`assert_registered(db_path, family=..., signal_id=..., instrument=..., ...)` helper:
  raises `RuntimeError` if no matching registered experiment exists.
  Callers (sweeps, validation runs) can use this as an optional enforcement gate.

`holdout_locked_at` is set by `ExperimentRegistry.register_experiment()` on first write.
122/123 tests pass (pre-existing regime_recommender failure unchanged).

---

## Phase C — Validation Layer Upgrade

### Step 6: Deflated Sharpe Ratio [COMPLETE — 2026-04-20]
**New file:** `src/ta_foundation/analysis/statistics/dsr.py`

Formula: López de Prado (2018) Deflated Sharpe Ratio.
`compute_dsr(returns, n_trials, annualization_factor)` → `DsrResult`
`compute_dsr_gate(trades, n_trials, profit_col)` → dict for GateResult wiring.

SR* computed via EVT approximation (Euler-Mascheroni constant, normal quantiles).
V[SR] includes skewness and excess kurtosis correction (non-normal returns).

Wiring in `run_validation()`:
- New params: `dsr_n_trials`, `dsr_threshold` (default 0.95), `dsr_annualization_factor`.
- When `dsr_n_trials` is None but `db_path` is set, auto-reads `count_experiments()` from DB.
- Gate added only when a trial count is available — backward-compatible default: gate absent.
122/123 tests pass (pre-existing regime_recommender failure unchanged).

### Step 7: Holdout Management [COMPLETE — 2026-04-20]
**Files:** `src/ta_foundation/analysis/strategy_discovery/holdout.py`,
           `src/ta_foundation/cli/promote_strategy.py`

`partition_trades(trades, is_frac=0.60, val_frac=0.20)` → `TradePartition`
  Splits by *date boundary* (chronological), not row count.

`lock_holdout(db_path, experiment_id, trades)`
  Writes holdout_start/holdout_end into experiments table (adds columns if absent).

`filter_for_validation(trades, db_path, experiment_id)`
  Returns IS+VAL slice (all trades before locked holdout_start).
  Falls back to all trades if no lock is stored.

`run_holdout_evaluation(trades, db_path, experiment_id, ...)`
  One-shot: runs run_validation() on holdout slice, updates status to
  "promoted" or "holdout_failed". Raises RuntimeError if called twice.

CLI: `python -m ta_foundation.cli.promote_strategy --db-path <f> --experiment-id N --trades-parquet <f>`
  Accepts parquet or CSV. Exits 0 (passed) or 1 (failed).
122/123 tests pass (pre-existing regime_recommender failure unchanged).

### Step 8: Regime Stratification Gate [COMPLETE — 2026-04-20]
`compute_regime_breakdown()` is now computed *before* `run_validation()` in
`orchestrator.py`, allowing its output to gate validation automatically.

Changes:
- `orchestrator.py`: applies cost model, calls `compute_regime_breakdown()` with
  `bars_with_regime`, extracts `regime_dispersion_count`, passes both to `run_validation()`.
  Evaluation block reuses the pre-computed breakdown (no double computation).

- `run_validation()` new params:
    `regime_breakdown: dict` — keyed by regime label, values include `n_trades`/`avg_trade`
    `min_per_regime_expectancy: float` — all regimes with trades must have avg_trade ≥ threshold

- New `min_per_regime_expectancy` gate: reports failing regime labels; hard gate.
- `regime_dispersion_count` gate was already present and is now auto-populated.

122/123 tests pass (pre-existing regime_recommender failure unchanged).

---

## What NOT to Build
- New report sections (reporting is over-invested relative to research rigor)
- New entry strategy families (until validation layer is complete)
- Native Python backtester (NT + sweeps are sufficient if look-ahead clean)
- Live execution layer (NinjaTrader handles this)
- LLMs in signal / validation / execution path

---

## Architecture Target Mapping

| Target Layer | Current | Target |
|---|---|---|
| Data Foundation | NT exports + minute bars + ticks | Add point-in-time timestamp assertions |
| Backtest/Simulation | NT + entry sweep simulator | Keep; assert look-ahead safety per family |
| Statistical Validation | Single WF, naive t-test | DSR, multi-fold WF, Bonferroni (Step 2 done) |
| Experiment Tracking | None | DuckDB registry (Step 4) |
| Execution | NT external | Keep external |
| Risk Engine | NT external | Keep external |
| Reporting | 100+ sections (mature) | Freeze; do not expand |

---

## Per-Session Checklist
Before starting a build session, read this file.
After completing a step, mark it [COMPLETE — YYYY-MM-DD] and add findings.
If a step reveals a new problem, add it as a new step before continuing.
