# Horizon Prediction System — Design Document

> Multi-timeframe, multi-horizon, calibrated probability forecasting layered on top of
> the existing daily prediction system.

## 1. Current architecture summary

The prediction package today (`src/ta_foundation/prediction/`) is built around a single
**`DailyPrediction`** — one structured forecast per agent, per instrument, per session.
The flow is:

```
build_prediction_context(market, calendar, store, asof)
    → context dict (regime, multitf features, daily levels, event proximity,
                    feature_vector, similar_contexts, agent_stats, market_summary)
agent_fn(context)        → raw response dict
predict_next_day(...)    → validate + persist DailyPrediction
… session closes …
measure_outcome(...)     → PredictionOutcome (actual OHLC, direction, ER, breakouts, level touches)
score_prediction(...)    → fill trend/chop/levels/breakout/composite scores
PredictionStore (JSONL)  → predictions.jsonl + outcomes.jsonl per (instrument, contract)
```

Strengths to preserve:

- Clean separation: context-builder → agent → validator → store.
- Outcome scoring uses Brier-style proper scoring rules.
- Walk-forward similarity search via 8-component normalized feature vector.
- Per-agent rolling stats (`compute_agent_stats`) including ECE and drift detection.
- `statistical_stub_agent` is wired as a `agent_fn`, no LLM required for offline tests.

## 2. Problems with the current `statistical_stub_agent`

Located in `orchestrator.py:205`. Its weaknesses, in order of importance:

1. **No probability distribution.** It outputs plurality-vote direction with `agreement`
   used as confidence. Plurality-of-K is not a calibrated probability. With K=5 and 4
   matches, it reports 80% — far overconfident given the sample size.
2. **No smoothing.** Small samples produce extreme probabilities. There is no shrinkage
   toward base rates, no fallback hierarchy when analogues are sparse.
3. **No horizon control.** Always predicts a single next-session window. Cannot answer
   "what is the probability the next 3 5m bars close up?".
4. **No timeframe awareness.** ATR is taken from `tf15m_atr` falling back to `tf60m_atr`
   without distinguishing the prediction's actual timeframe.
5. **No session conditioning.** A single similarity pool ignores Asia vs London vs NY.
6. **No path statistics.** Direction-only — silent on MFE, MAE, threshold-first-hit, or
   expected efficiency, all of which a strategy needs for stops/targets.
7. **No abstention.** Forced to emit a forecast even when no edge exists.
8. **Scoring rule mismatch.** Direction confidence is treated as Bernoulli evidence, but
   nothing prevents the agent from overstating it on small K, so calibration drifts.

## 3. Proposed prediction framework

A new layer called the **horizon system** sits parallel to the existing daily system —
it does **not** replace `DailyPrediction`. Daily continues to work unchanged.

```
HorizonContext  (asof, instrument, contract, timeframe, horizon_n, session, regime, …)
    │
    ▼
HorizonProbabilityAgent.predict(context)
    │
    ▼
CandleHorizonPrediction  (full distribution: direction probs, return percentiles,
                          MFE/MAE, threshold hit probs, sample_size, fallback_level)
    │  …horizon completes…
    ▼
measure_horizon_outcome(bars, asof_idx, horizon_n, thresholds)
    │
    ▼
CandleHorizonOutcome  (actual return/MFE/MAE/threshold hit/order)
    │
    ▼
score_horizon_prediction(pred, outcome, weights)
    └─→ Brier (direction), Brier (thresholds), MAE (return), MFE/MAE error,
        calibration error, composite
```

Multiple agents (statistical, Claude, regime-specialist, level-specialist, session-specialist)
emit the same `CandleHorizonPrediction` shape, so the scorer and ranking reports treat them uniformly.

## 4. Data model changes

### New: `CandleHorizonPrediction`

```
prediction_id, agent_id, instrument, contract,
timeframe                  ("1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d")
asof_timestamp             tz-aware (Denver canonical)
session_label              ("asia" | "london" | "ny_open" | "ny_midday" | "ny_close" | "overnight")
horizon_candles            int N

# Direction distribution (must sum to ~1.0)
bullish_probability, bearish_probability, neutral_probability
confidence                 1 - max-other-class probability  (interpretive only)

# Return distribution
expected_return_points, expected_return_atr
median_return_points
p10_return_points, p25_return_points, p75_return_points, p90_return_points

# Path statistics
expected_mfe_points, expected_mae_points
upside_threshold_points, downside_threshold_points
upside_threshold_probability, downside_threshold_probability, neither_threshold_probability

# Diagnostics
predicted_volatility       expected sigma over horizon
sample_size                raw analogue count
effective_sample_size      after smoothing weight
method_used                e.g. "conditional_frequency_v1"
fallback_level             0 (full conditioning) → N (broadest fallback)
calibration_bucket         e.g. "tf=5m,session=ny_open,h=3"
feature_snapshot           dict — features used for conditioning
reasoning_summary          short string

abstain                    bool — true when sample size or calibration insufficient
abstain_reason             "insufficient_samples" | "uncalibrated" | "regime_drift" | …
schema_version
```

### New: `CandleHorizonOutcome`

```
prediction_id, agent_id, instrument, contract, timeframe,
asof_timestamp, horizon_candles, measured_at
asof_bar_index             int — index in the bar series at asof
horizon_end_timestamp

actual_return_points, actual_return_atr
actual_direction           "bullish" | "bearish" | "neutral"
actual_mfe_points, actual_mae_points
upside_threshold_hit, downside_threshold_hit
threshold_hit_order        "upside_first" | "downside_first" | "neither"
actual_efficiency_ratio    |Δclose| / sum |bar-to-bar Δ|

# Score fields (filled by horizon_scorer)
brier_score_direction
log_loss_direction
brier_score_thresholds
return_mae
mfe_error, mae_error
efficiency_ratio_error
calibration_error
composite_score
schema_version
```

### Why a separate model from `DailyPrediction`

1. The prompt explicitly asks for a horizon-aware schema with sample/calibration metadata.
2. Mixing horizon predictions into `DailyPrediction` would force every existing agent to
   populate horizon fields they don't produce, breaking validation.
3. Storage cleanly partitioned: `predictions.jsonl` (daily) and `horizon_predictions.jsonl`
   (horizon) — independent compaction, retention, and indexing.

## 5. Statistical methods — implementation order

| Method | Purpose | Phase |
|---|---|---|
| **Conditional frequency baseline** | Group historical bars by (timeframe, session, regime, horizon) buckets; emit empirical Brier-calibrated direction + return distributions with empirical-Bayes shrinkage. | 1 |
| **Bayesian / EB smoothing with fallback hierarchy** | Shrink small-sample buckets toward parent buckets: full → drop regime → drop session → instrument-level baseline. | 1 (basic), 2 (formalized) |
| **k-NN analogue (improved)** | Replace cosine-on-fixed-vector with configurable feature set + distance-weighted KNN + minimum sample threshold + regime/session-aware search. | 2 |
| **Logistic regression for direction** | Walk-forward fitted, instrument-scoped, interpretable feature weights. | 3 |
| **Quantile regression** for p10/p25/p75/p90 returns. | 3 |
| **Markov candle-state transitions** | Define candle states (strong-up, strong-down, doji, breakout, reversal-wick, compression, expansion) and learn N-step transition matrices conditioned on session/regime. | 3 |
| **Distribution forecast composition** | Combine direction, return distribution, threshold probabilities, and path statistics into one `CandleHorizonPrediction`. | already in Phase 1 schema |

Hard rule: **no future-looking features.** All conditioning features for an asof must
use only bars with `dt <= asof`. Outcomes are measured strictly with `dt > asof`.

## 6. Scoring and calibration

### Per-component scores (range [0, 1], higher is better)

- `direction_score = 1 − Brier(p_bull, p_bear, p_neu, actual_one_hot) / max_brier`
  with `max_brier = 1.5` for 3-class probability vector.
- `threshold_score = 1 − mean(Brier(p_upside, hit_up), Brier(p_downside, hit_down), Brier(p_neither, hit_neither))`.
- `return_score = max(0, 1 − |expected_return − actual_return| / atr)`.
- `path_score = max(0, 1 − (|MFE_err| + |MAE_err|) / (2*atr))`.
- `calibration_score = 1 − ECE(direction over recent bucket history)`.

### Composite

```yaml
horizon_scorer:
  weights:
    direction:        0.35
    thresholds:       0.25
    return:           0.20
    path:             0.10
    calibration:      0.10
```

All weights configurable in `prediction.yaml`. Weights must sum to 1.0; the scorer
validates and normalizes.

### Calibration tracking

For each `(agent_id, timeframe, horizon, session, regime)` bucket, accumulate `(predicted_p, actual)`
pairs and compute ECE on a 10-bin grid. Surface ECE in the calibration report.

## 7. Session / timeframe / horizon ranking

Reports produced by `horizon_reports.py` (Phase 3):

1. **Agent leaderboard** — composite score, n, ECE, drift flag.
2. **Timeframe × horizon matrix** — composite score and Brier per cell.
3. **Session × (timeframe + horizon) matrix** — where each agent is strongest.
4. **Best-edge finder** — sorts cells by realized edge with minimum sample-size guard.
5. **Calibration report** — per agent/bucket: confidence band → empirical hit rate.
6. **Drift report** — recent vs long-window composite delta with stdev guard.

All reports read from `horizon_predictions.jsonl` + `horizon_outcomes.jsonl`.

## 8. Anti-leakage safeguards

The contracts that the implementation MUST honor:

1. **Asof-only conditioning.** All feature extraction takes `bars_up_to(asof)`, never the
   full series. The agent never sees `bars[asof+1:]`.
2. **Outcome measurement is decoupled.** `measure_horizon_outcome(bars, asof_idx, N)` only
   reads `bars[asof_idx+1 : asof_idx+1+N]` and never re-uses those bars in re-prediction.
3. **Walk-forward analogue search.** When building conditional-frequency tables for a
   prediction at asof T, only historical samples with `sample_asof < T` are eligible. The
   default lookback window is configurable.
4. **Session label is timestamp-only.** No outcome data may influence the session label.
5. **No random train/test split.** All historical evaluation uses walk-forward replay
   (Phase 3 backtest_horizon_predictions.py).
6. **No future-leakage in regime label.** Regime classifier features use only bars
   `<= asof`; this is already enforced in the existing `build_multitf_features`.

## 9. Implementation plan

### Phase 1 — minimum viable horizon system (THIS PHASE)

Goal: be able to produce a calibrated horizon prediction from market data alone, measure
its outcome on a target window, and score it. No new storage layer required — the agent
operates directly off `MarketDataStore` and a freshly resampled bar series.

Modules added:

- `horizon_models.py` — `CandleHorizonPrediction`, `CandleHorizonOutcome`, `HorizonAbstention`
- `session_classifier.py` — `SessionConfig`, `label_session(ts, config)`
- `horizon_outcome_measurer.py` — `measure_horizon_outcome(...)`
- `statistical_probability_agent.py` — `StatisticalProbabilityAgent` (conditional-frequency baseline + EB smoothing + fallback)
- `horizon_scorer.py` — `score_horizon_prediction(pred, outcome, weights)`
- `tests/prediction/test_horizon_phase1.py`

Existing modules unchanged. `prediction/__init__.py` re-exports new names additively.

### Phase 2 — analogue model + storage  ✅ SHIPPED

- `analogue_probability_agent.py` — distance-weighted KNN over a 4-dim
  continuous feature vector (atr_zscore, trend_slope, body_atr,
  momentum_norm) with Gaussian kernel weighting. Filters by exact
  session/regime match by default, with a 3-level fallback hierarchy
  (full → drop regime → unfiltered) and graceful low-confidence fallback
  to uniform weights when neighbor weights collapse.
- `horizon_store.py` — `HorizonPredictionStore` with JSONL persistence
  (`horizon_predictions.jsonl`, `horizon_outcomes.jsonl`), partitioned by
  (instrument, contract). Filtered queries by timeframe / horizon /
  session / agent / regime / asof window. Idempotent prediction saves;
  duplicate outcomes raise `DuplicateHorizonOutcomeError`.
- `horizon_calibrator.py` — `HorizonBucketKey`,
  `HorizonBucketStats`, `compute_horizon_bucket_stats`,
  `compute_per_bucket_ece`, `compute_all_bucket_stats`,
  `group_by_bucket`, `lookup_calibration_error`. Top-label ECE on
  argmax(direction probs); abstaining predictions are excluded from the
  reliability buckets but counted in `sample_count`. The lookup table
  feeds the scorer's `calibration_error` argument so confident-but-
  miscalibrated agents incur a composite-score penalty.

### Phase 3 — multi-TF batch + walk-forward backtest + reports  ✅ SHIPPED

- `horizon_batch.py` — `HorizonBatchRunner` drives one or more agents
  through a list of `HorizonBatchSpec(instrument, contract, timeframe,
  horizon, asof)` entries. Bars are loaded once per
  `(instrument, contract, timeframe)` and reused across asofs.
  `resolve_asof_idx` accepts both integer indices and tz-aware
  Timestamps. Per-spec failures are captured on the result rather than
  raised, so a bad asof never aborts a batch. Includes
  `make_market_bar_loader` adapter for `MarketDataStore` and
  `make_static_bar_loader` for tests, plus `build_schedule` /
  `asofs_from_bars` helpers.
- `backtest_horizon_predictions.py` — `run_horizon_backtest` calls the
  runner, then for each non-abstain prediction measures the realized
  outcome on the same bar series, scores it, and persists to the
  store. Walk-forward leakage is enforced both by the agent (history
  ends strictly before asof) and the measurer (only reads
  `bars[asof+1 : asof+1+horizon]`). Calibration feedback is *not*
  used during scoring — that would leak future hit-rates into past
  scores; reports compute ECE separately.
  `run_walk_forward_replay` is the high-level convenience wrapper.
- `horizon_reports.py` — six structured reports built from the store:
  agent leaderboard (with composite, ECE, drift flag), timeframe ×
  horizon matrix, session × (timeframe, horizon) matrix, best-edge
  finder (sign(argmax) × `actual_return_atr`, min-n guarded),
  calibration report (per-bucket reliability bands), and drift
  report (recent-vs-long composite z-score). Each builder pairs
  with a `format_*` plain-text renderer; `build_full_report` /
  `format_full_report` produce the entire bundle in one call.

### Phase 4 — model ensemble  ✅ SHIPPED

- `horizon_specialists.py` — `make_regime_specialist_agent`,
  `make_session_specialist_agent`. Thin factories that produce
  `AnalogueProbabilityAgent` instances pre-configured with
  `require_regime_match` / `require_session_match` flags. As part of this
  phase, `_filter_with_fallback` in the analogue agent gained a
  regime-only branch (fallback level 1) so a regime specialist actually
  receives a regime-matched neighbor pool instead of falling straight
  through to unfiltered.
- `horizon_ensemble.py` — `EnsembleHorizonAgent`, `StackingKey`,
  `StackingWeightTable`, `compute_stacking_weights`. The ensemble runs
  every member once per asof and weighted-averages their direction
  probabilities, threshold probabilities, returns / percentiles, and
  path statistics. Members that abstain are excluded from the average;
  if every member abstains the ensemble itself abstains. Member
  failures are isolated and surfaced via
  `feature_snapshot["ensemble_errors"]` instead of propagating.
- Stacking weights are learned per
  `(timeframe, horizon, session, regime)` bucket from the rolling mean
  composite score. A configurable `floor_weight` reserves a guaranteed
  minimum mass per agent (post-normalization) so a transiently-bad
  agent stays in the mix and can recover. When the exact bucket has
  no history the lookup falls back to pooled weights, then to uniform
  — never silently down-weighting a fresh member.
- Both specialists and the ensemble emit the same
  `CandleHorizonPrediction` shape, so the existing scorer, store, and
  reports treat them uniformly.

### Phase 5 — strategy-useful outputs  ✅ SHIPPED

- `horizon_costs.py` — `CostModel(fixed_points_per_side,
  slippage_atr_per_side, spread_points)` produces a per-instrument
  round-trip cost in points or ATR units, scaled by the asof bar's
  prior_atr.
- `horizon_tradable_zone.py` — `evaluate_tradable_zone(pred,
  cost_model, config)` returns a `TradableZoneVerdict` carrying
  `is_tradable`, `recommended_direction`, post-cost expected edge in
  points and ATR, recommended stop / target (from the prediction's
  threshold fields, vol-floored), and a Kelly-lite size fraction
  capped at `kelly_cap` (0.25 default — quarter-Kelly). Rejections
  surface as a list of human-readable reasons (`"confidence<0.55"`,
  `"edge_atr<0.05"`, etc.) so a report can show *why* a candidate
  was rejected.
- `horizon_abstention.py` — `AbstentionPolicy` with named flat rules
  (`min_sample_size`, `max_fallback_level`, `min_confidence`,
  regime / session blacklists, `honor_agent_abstain`). Evaluation
  order is fixed; `apply()` returns the original prediction or a
  zero-probability abstain clone. No expression DSL — keeps the YAML
  schema readable and avoids `eval`-flavored footguns.
- `horizon_config.py` — `HorizonConfig` bundles the three sections
  above; `HorizonConfig.apply(pred)` runs the full deployment pipeline
  (abstention → tradable-zone) and returns a `HorizonPipelineResult`.
  `load_horizon_config(path)` parses YAML; `load_horizon_config_or_default`
  is the safe wrapper for callers without a config file.
- `prediction.yaml` ships at the package root as the documented
  default. The loader accepts both `horizon: { … }` (preferred) and a
  flat top-level mapping; missing sections fall back to dataclass
  defaults; unknown keys are ignored so adding fields does not break
  older configs.

**Bonus improvements landed in Phase 5**

- `StackingWeightTable.save_to_path()` / `load_from_path()` JSON
  persistence so an expensive ensemble-weight computation survives
  process restarts.

### Post-Phase-5 hardening

- `horizon_agent.py` — formal `HorizonAgent` runtime-checkable Protocol
  + `AgentRegistry` with auto-registration of the four built-in agent
  types (`statistical`, `analogue`, `regime_specialist`,
  `session_specialist`). The duck-typed `HorizonAgentProtocol` in
  `horizon_batch.py` is now an alias of the canonical name.
- `horizon_features.py` — single source of truth for the shared
  feature primitives (`compute_atr`, `compute_regime_labels`,
  `compute_session_regime_atr`, `compute_lite_outcome`,
  `NEUTRAL_ATR_THRESHOLD`). The statistical and analogue agents both
  call into it; the previous file-level duplication is gone, so
  divergence between the two agents is no longer possible.
- `backtest_horizon_predictions.py` CLI — now a real end-to-end
  pipeline: parses a NinjaTrader minute-bars file via the existing
  parser, populates a `MarketDataStore`, runs the walk-forward replay,
  prints the summary, and (with `--print-report`) the full report
  bundle.
- `reports/html/sections/horizon_overview.py` — new pure-HTML section
  registered as `horizon_overview` in `SECTION_REGISTRY`. Consumes
  either an in-memory `HorizonReportBundle` or a store directory
  passed via `options:`. Renders the leaderboard, timeframe ×
  horizon matrix, session matrix, best-edge, calibration, and drift
  reports as self-contained HTML tables (no external resources).

## 10. File-by-file change plan (Phase 1)

| File | Change | Purpose |
|---|---|---|
| `prediction/horizon_models.py` | NEW | Dataclasses for horizon prediction + outcome |
| `prediction/session_classifier.py` | NEW | Configurable session labelling (NY clock, DST-safe) |
| `prediction/horizon_outcome_measurer.py` | NEW | Compute next-N outcome on a TF-resampled bar series |
| `prediction/statistical_probability_agent.py` | NEW | Conditional-frequency baseline w/ EB smoothing + fallback |
| `prediction/horizon_scorer.py` | NEW | Brier / MAE / composite scoring of horizon predictions |
| `prediction/__init__.py` | EDIT | Re-export new names without breaking existing exports |
| `tests/prediction/test_horizon_phase1.py` | NEW | Unit tests for all five new modules |

Daily prediction code (`models.py`, `orchestrator.py`, `outcome_measurer.py`,
`scorer.py`, `calibrator.py`, `context_builder.py`, `claude_agent.py`,
`run_prediction.py`, `store.py`) is **not modified** in Phase 1.

### Configurability defaults

Phase 1 ships configurable constants directly on the agent constructor (no YAML wiring
yet). The session classifier uses an explicit `SessionConfig` dataclass with sensible
NY-clock defaults; users can override per-instrument if needed. Phase 3 will surface
all of these in `prediction.yaml`.

### Assumptions

- Bar series passed to the horizon agent are TF-resampled OHLCV with tz-aware `dt` index.
- ATR is provided by the caller (or computed by the agent from a fixed lookback inside
  the bar series — defaults to 14 bars, falling back to `range/2` if insufficient).
- Session boundaries default to common futures conventions in `America/New_York`. DST is
  handled automatically because `pd.Timestamp.tz_convert` is DST-aware.
- "Neutral" direction uses an ATR-fraction threshold (default 0.30) — same convention as
  the existing daily `outcome_measurer.py` for consistency.
