# ta_foundation Capability Design: Regime-Aware Strategy Parameter Recommender

## 1) Problem Framing

### Real problem being solved
`ta_foundation` currently excels at ingesting run/trade artifacts, deriving analytics, and rendering reports. The missing capability is **operational parameter adaptation**: selecting strategy settings appropriate for the **current market regime** rather than always running static defaults.

The practical decision is not “predict price” but:

1. What regime is active now?
2. How does strategy `S` historically behave in that regime?
3. Should we run `S` now?
4. If yes, with which parameters?

### Why regime-based parameter selection is the correct approach
Most configurable strategies are **conditional edge extractors**. Their edge varies with:
- trend persistence,
- volatility structure,
- session behavior,
- breakout vs mean-reversion tendency.

A single fixed parameter set is usually a compromise across contradictory states. Regime-based selection explicitly models this non-stationarity while remaining interpretable.

### Definition of “best parameters”
Use a constrained multi-objective score, not raw PnL only:

`score = w1 * expectancy + w2 * profit_factor + w3 * stability - w4 * max_drawdown - w5 * tail_risk`

Where:
- **expectancy**: avg PnL/trade,
- **profit_factor**: gross win / gross loss,
- **stability**: consistency across adjacent windows/regimes,
- **max_drawdown**: absolute or normalized,
- **tail_risk**: MAE quantiles / loss clustering.

Hard gates before scoring:
- minimum sample size,
- max allowable drawdown,
- data quality checks,
- confidence threshold.

Output classes:
- `RECOMMEND_PARAMS`
- `RECOMMEND_BASELINE`
- `NO_TRADE`

---

## 2) Reuse of Existing ta_foundation Components

## Reuse directly (existing)
1. **Shared market storage/resampling**
   - `MarketDataStore.get_bars(...)` for timeframe extraction/resampling.
2. **Feature primitives**
   - `analysis/features/regime.py` (EMA, ATR, slope, session VWAP helpers).
3. **Regime summarization patterns**
   - `analysis/market_regime_store.py` for explainable regime bucketing/summaries.
4. **Recommendation pattern baseline**
   - `analysis/recommendations.py` for explainable bucket-based recommendation style.
5. **Report pipeline contract**
   - `load_report_config -> build_report_from_config -> HtmlReportBuilder`.
6. **Derived data attachment contract**
   - attach under `pkg.metadata["derived"][...]`.

## New functionality to add (minimal)
1. `analysis/strategy_metadata/` (new) — strategy understanding layer.
2. `analysis/regime_recommender/` (new) — multi-timeframe feature extraction, regime classification, recommendation scoring.
3. `analysis/regime_recommender/outcomes.py` (new) — feedback logging + learning dataset assembly.
4. Optional report section(s) under `reports/html/sections/` to render recommender output from derived metadata only.

## Integration points
- **Pipeline/analysis phase**: compute and attach derived artifacts to package metadata.
- **Report phase**: read precomputed derived artifacts and render.
- **No parser redesign required** unless strategy docs need a parser enhancement.

---

## 3) System Architecture

## End-to-end flow
1. **Market data ingestion (existing)**
   - Pull minute bars from `MarketDataStore` (shared).
   - Resample/slice into:
     - last 1 day of 15m
     - last 5 days of 60m
     - last 5 days of 240m

2. **Multi-timeframe feature extraction (new module)**
   - Compute deterministic feature vector with per-feature provenance.

3. **Regime classification (new module)**
   - Deterministic rule set (Phase 1), later hybrid classifier.
   - Emit regime label + sub-regime tags + confidence.

4. **Strategy understanding layer (new module)**
   - Parse strategy docs/templates/metadata.
   - Build normalized `StrategyProfile` object.

5. **Parameter recommendation engine (new module)**
   - Inputs: `StrategyProfile + regime_features + historical_examples`.
   - Outputs: parameter set, confidence, no-trade flag, explanation traces.

6. **Report generation (existing + optional new section)**
   - Human-readable narrative + machine JSON payload.

7. **Outcome tracking (new module)**
   - Record recommendation context + actual realized outcomes.

8. **Learning/feedback loop (new module)**
   - Periodically retrain/update ranking tables and confidence calibration.

## Recommended artifact placement
- `pkg.metadata["derived"]["regime_recommender"] = { ... }` per run.
- Shared learned tables can be persisted externally (SQLite/Parquet) referenced by run metadata snapshot IDs.

---

## 4) Strategy Understanding Layer

## Purpose
Convert heterogeneous strategy docs into structured, inspectable metadata used by recommendation logic.

## Inputs to ingest
- strategy markdown/docs,
- config templates (XML/YAML/etc.),
- known defaults/ranges,
- historical backtest summaries.

## Output: `StrategyProfile` (structured)
```json
{
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "style": "trend_following_breakout",
  "entry_logic": ["ema_alignment", "range_break", "session_filter"],
  "exit_logic": ["fixed_tp_sl", "trailing_stop"],
  "parameters": {
    "ema_fast": {"type": "int", "default": 21, "range": [8, 55], "role": "entry_timing", "sensitivity": "high"},
    "ema_slow": {"type": "int", "default": 55, "range": [21, 200], "role": "trend_filter", "sensitivity": "high"},
    "stop_atr_mult": {"type": "float", "default": 1.8, "range": [1.0, 3.5], "role": "risk_control", "sensitivity": "high"},
    "takeprofit_rr": {"type": "float", "default": 1.5, "range": [0.8, 4.0], "role": "payoff_shape", "sensitivity": "medium"}
  },
  "favorable_conditions": ["trend_strength_high", "volatility_mid_high", "breakout_bias"],
  "unfavorable_conditions": ["chop", "extreme_whipsaw"],
  "no_trade_rules": ["confidence_below_threshold", "feature_conflict_high"]
}
```

## Extraction method
- Phase 1: deterministic parser + mapping file per strategy.
- Phase 2: assisted doc extraction with strict schema validation and manual approval.

## Explainability requirement
Each parameter must have:
- semantic role,
- directionality assumptions (e.g., higher stop_atr_mult tolerates volatility),
- sensitivity rank,
- valid range + default.

### Strategy-grounded extraction requirements (from current repository assets)

The strategy-understanding layer must parse real fields already present under `src/ta_foundation/strategies/` instead of relying on generic assumptions.

#### PantheonMasterBotV01TesterV2 profile anchors
- Strategy behavior is a **3-SMA crossover with optional trend gating and optional direction reversal** (Fast/Slow cross events, `UseTrend`, `UseTrendReverse`, `Reverse`).
- Risk and exits are primarily fixed stop/target (`UseMaxStop`, `MaxStop`, `UseMaxTP`, `MaxTPRatio`) plus guardrails (`ProfitStop`, `LossStop`, `MaxTrades`, `UseKill`, kill thresholds).
- Time constraints are explicit (`UseTimeFilter`, `StartTimeH/M`, `DurationTimeH/M`), and docs indicate crossover-driven exits can occur outside window logic.
- Templates expose deploy-time defaults and variants (`sampleTemplate.xml`, `BronzeApolloGod.xml`) that must be treated as **named parameter presets**, not inferred defaults.

#### PantheonBotV2 profile anchors
- Extends crossover core with explicit **regime filter controls** (`RequiredTrendRegimeFilter`, `RequiredVwapRegimeFilter`, `RequiredVolatilityRegimeFilter`, `BlockedVolatilityRegimeFilter`).
- Uses multi-series context internally (`AddDataSeries` 5m/15m/30m/60m) and trend regime controls (`TrendHigherTimeFrameMinutes`, `TrendEmaPeriod`, slope/ATR thresholds).
- Adds dynamic stop stack (`UseDynamicStop`, `UseLockIn`, `LockInTriggerTicks`, `LockInPlusTicks`, `UseGiveback`, `Giveback*`, `UseTrail`, `Trail*`).
- Contains volatility percentile controls (`VolatilityLookbackWindow`, percentile thresholds), aligning naturally with regime-aware parameterization.

#### Required metadata schema additions
The `StrategyProfile` schema must include these explicit groups so recommendations stay strategy-correct:

1. `entry_model`
   - `signal_family`: `sma_crossover`
   - `direction_controls`: `long_enabled`, `short_enabled`, `reverse`
   - `trend_filter_controls`: `use_trend`, `use_trend_reverse`, `trend_period`

2. `regime_filters` (critical for PantheonBotV2)
   - required/blocked trend, vwap, volatility filter enums
   - fallback behavior when filter fields are absent (PantheonMaster)

3. `risk_model`
   - static stop/target controls
   - dynamic stop controls (lock-in/giveback/trail)
   - session guards and kill-switch thresholds

4. `time_model`
   - enabled flag + start/duration local-time window
   - behavior outside window (`no_new_entries`, `conditional_exit_on_crossover`)

5. `template_presets`
   - list of parsed XML templates by name (e.g., `sampleTemplate`, `BronzeApolloGod`)
   - exact parameter map + source hash for auditability

#### Extraction pipeline (deterministic)
1. Parse `.cs` property declarations and defaults to build canonical parameter dictionary.
2. Parse `.xml` strategy templates as named preset overrides.
3. Parse `.md` behavior docs as constrained annotations (entry/exit semantics, caveats) with rule-based keyword extraction + manual review flag.
4. Emit a versioned `StrategyProfile` where every recommended parameter can be traced back to:
   - source file path,
   - field name,
   - default value and allowed type/range (when available).

---

## 5) Market Feature Engine

Compute a single `RegimeFeatureVector` from the required windows.

## Feature catalog

| Feature | What | Why it matters | How to compute | TF |
|---|---|---|---|---|
| `trend_dir_ema` | Trend direction | Aligns trend-following vs mean-reversion behavior | Sign of `(EMA_fast - EMA_slow)` and slope | 60m, 240m |
| `trend_strength_adx_like` | Trend strength proxy | Distinguishes directional vs chop regimes | `abs(ema_slope)/ATR` or ADX-equivalent proxy | 60m, 240m |
| `vol_level_atr_pct` | Volatility level | Risk sizing + stop distance tuning | ATR / close percentile vs trailing 20-day baseline | 15m, 60m |
| `vol_expansion` | Compression vs expansion | Breakout probability often rises post-compression | Ratio ATR(short)/ATR(long), Bollinger bandwidth delta | 15m, 60m |
| `range_efficiency` | Range/chop tendency | Prevent overtrading in low-efficiency movement | Efficiency ratio = abs(close_t-close_0)/sum(abs(diff)) | 15m |
| `breakout_pressure` | Potential directional release | Helps breakout strategies enable entries | Donchian width contraction + recent range breaks | 15m, 60m |
| `mr_pressure` | Mean-reversion tendency | Helps fade strategies and no-trade filters | Z-score of distance from anchored VWAP + reversion speed | 15m |
| `momentum_persistence` | Follow-through persistence | Determines hold-time / TP calibration | Autocorr of returns, streak duration, Hurst proxy | 15m, 60m |
| `ma_structure_state` | MA stack regime | Fast summary for trend alignment quality | Ordered state of MA set (e.g., 20>50>100) + separation normalized by ATR | 60m, 240m |
| `session_behavior_bias` | Session impact | Strategies can degrade outside active hours | Segment features by local session buckets (America/Denver) | 15m |
| `liquidity_proxy` | Participation proxy | Low liquidity amplifies slippage/noise | volume percentile, bar range anomalies | 15m |
| `cross_tf_agreement` | Multi-timeframe coherence | Penalize contradictory signals | weighted agreement between 15/60/240 trend states | all |

## Regime classification output
```json
{
  "regime_id": "trend_up_expanding_vol_breakout_favored",
  "primary": "trend_up",
  "secondary": ["vol_expanding", "breakout_bias"],
  "conflicts": ["15m_mr_pressure_high"],
  "confidence": 0.74
}
```

---

## 6) Parameter Recommendation Engine

## Input mapping
- `StrategyProfile`
- `RegimeFeatureVector`
- historical analogs and outcome tables
- baseline/default parameter set

## Output
- recommended parameter set,
- confidence score,
- top feature influences,
- per-parameter reason,
- fallback action,
- `no_trade` decision.

## Approach comparison

1. **Rule-based**
   - Pros: transparent, fast, deterministic.
   - Cons: rigid, manual maintenance.

2. **Historical analog matching**
   - Pros: intuitive and inspectable (“similar periods”).
   - Cons: feature weighting sensitivity.

3. **Scoring/ranking models (GBDT/linear ranker)**
   - Pros: learns interactions, still explainable with SHAP/feature contributions.
   - Cons: needs robust labeled outcomes.

4. **Supervised direct parameter prediction**
   - Pros: potentially strong performance.
   - Cons: high leakage/overfit risk, lower transparency.

5. **Hybrid (recommended)**
   - Rule gates + analog evidence + learned reranker.

## Build order recommendation
- **Build first**: Rule-based + analog matching (auditable, fast to production).
- **Evolve later**: Add scoring model as tie-breaker/reranker with calibrated confidence.

## Decision logic (Phase 1)
1. Quality gates (data sufficiency, feature integrity).
2. Regime classification confidence gate.
3. Parameter candidate generation around defaults with role-aware constraints.
4. Score candidates with weighted objective + robustness penalty.
5. If best score below threshold or uncertainty high → `NO_TRADE`.

## Confidence score design
`confidence = data_quality * regime_certainty * analog_support * stability_penalty_adjustment`

Range `[0,1]`, with explicit decomposition in report.

---

## 7) Report Design

## A) Human-readable report
Sections:
1. Market regime snapshot (15m/60m/240m findings)
2. Strategy profile summary (how it trades)
3. Recommendation result
4. Parameter-by-parameter explanation vs baseline
5. Confidence breakdown
6. No-trade rationale (if applicable)
7. Similar historical conditions and outcomes
8. Risks / expected failure modes

## B) Machine-readable JSON schema
```json
{
  "timestamp": "2026-03-28T14:00:00-06:00",
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "baseline_params": {"ema_fast": 21, "ema_slow": 55, "stop_atr_mult": 1.8},
  "recommended_params": {"ema_fast": 18, "ema_slow": 50, "stop_atr_mult": 2.2},
  "decision": "RECOMMEND_PARAMS",
  "no_trade": false,
  "regime": {
    "id": "trend_up_expanding_vol_breakout_favored",
    "confidence": 0.74,
    "top_features": [
      {"feature": "trend_strength_adx_like_240m", "value": 1.92, "impact": 0.27},
      {"feature": "vol_expansion_15m", "value": 1.35, "impact": 0.21}
    ]
  },
  "parameter_reasons": [
    {
      "name": "stop_atr_mult",
      "baseline": 1.8,
      "recommended": 2.2,
      "direction": "increase",
      "because": ["vol_level_atr_pct high", "vol_expansion positive"],
      "expected_effect": "reduce stop-outs during volatility expansion"
    }
  ],
  "confidence": {
    "overall": 0.71,
    "components": {
      "data_quality": 0.95,
      "regime_certainty": 0.74,
      "analog_support": 0.69,
      "stability": 0.76
    }
  },
  "risks": ["possible whipsaw if expansion fails"],
  "similar_history": [
    {"snapshot_id": "snap_2025_11_03_1400", "distance": 0.12, "realized_expectancy": 42.5}
  ]
}
```

## C) Template XML output bundle (new required output)
In addition to JSON/report outputs, each recommendation cycle must generate **session-scoped NinjaTrader strategy templates** with parameter values embedded for each session profile:
- `london`
- `ny_early`
- `ny_midday`
- `power_hour`
- `asia`

These files are execution artifacts for importing directly into NinjaTrader and must be generated from existing strategy template skeletons under `src/ta_foundation/strategies/.../templates/`.

### Session window defaults (America/Denver)
Use explicit local-time defaults (configurable in `report.yaml` options for the recommender analysis block, not CLI flags):

| Session | Start (HH:MM) | Duration | Notes |
|---|---:|---:|---|
| london | 01:00 | 03:00 | London overlap behavior |
| ny_early | 07:30 | 02:30 | pre-open + open impulse |
| ny_midday | 10:00 | 02:00 | lower volatility/chop regime candidate |
| power_hour | 13:00 | 01:00 | late US session expansion |
| asia | 18:00 | 04:00 | overnight range/transition |

### XML generation rules
1. Start from a selected base template (`sampleTemplate.xml` or named preset).
2. Apply recommended strategy parameters for the current regime.
3. Apply session window fields (`UseTimeFilter=true`, `StartTimeH/M`, `DurationTimeH/M`).
4. Apply session-specific risk modifiers only when explicitly configured (e.g., tighter stops for midday).
5. Preserve all non-overridden XML fields exactly.
6. Emit one file per session with deterministic naming:
   - `{strategy_id}__{regime_id}__{session_key}.xml`

### Required template manifest output
Each run must also emit a manifest payload describing all generated templates:
```json
{
  "strategy_id": "PantheonBotV2",
  "regime_id": "trend_up_expanding_vol",
  "generated_at": "2026-03-28T14:00:00-06:00",
  "templates": [
    {
      "session": "ny_early",
      "path": "outputs/templates/PantheonBotV2__trend_up_expanding_vol__ny_early.xml",
      "start_time": "07:30",
      "duration": "02:30",
      "params_hash": "...",
      "source_template": "sampleTemplate.xml"
    }
  ]
}
```

---

## 8) Feedback Loop / Learning System

## What to record per recommendation
- feature vector snapshot,
- regime label + confidence,
- strategy + full parameter set,
- decision type (trade/no-trade),
- realized trades linked to recommendation horizon,
- metrics: PnL, drawdown, MAE, MFE, ETD, hit rate, expectancy,
- baseline counterfactual metrics (when available).

## Learning mechanism
- **Batch updates first** (daily/weekly): robust and auditable.
- Recompute:
  - analog library embeddings/features,
  - candidate score weights,
  - confidence calibration curve.

## Overfitting controls
- walk-forward splits only,
- min sample constraints per regime,
- regularization + shrinkage,
- cap parameter drift per update,
- require out-of-sample improvement before promotion.

## Degradation detection
Monitor rolling:
- recommendation uplift vs baseline,
- calibration error (Brier/ECE),
- regime frequency drift,
- feature distribution drift (PSI/KS).

Trigger fallback to conservative mode (baseline or no-trade bias) when degradation exceeds thresholds.

## Regime change handling
- maintain versioned regime taxonomy,
- allow “unknown/transition” regime class,
- lower confidence for out-of-distribution states.

---

## 9) Data Model / Storage

Use append-only audit tables (SQLite or Parquet + manifest).

## `strategy_profiles`
- `strategy_id` (PK)
- `version`
- `profile_json`
- `created_at`
- `source_hash`

## `regime_snapshots`
- `snapshot_id` (PK)
- `timestamp_local` (tz-aware America/Denver)
- `instrument`, `contract`
- `feature_vector_json`
- `regime_id`
- `regime_confidence`
- `feature_quality_score`

## `recommendations`
- `recommendation_id` (PK)
- `snapshot_id` (FK)
- `strategy_id`
- `baseline_params_json`
- `recommended_params_json`
- `decision`
- `confidence_overall`
- `confidence_components_json`
- `explanations_json`
- `model_version`

## `trade_outcomes`
- `outcome_id` (PK)
- `recommendation_id` (FK)
- `run_id`
- `start_dt`, `end_dt` (tz-aware)
- `trades_count`
- `net_pnl`, `max_dd`, `mae_p50`, `mae_p95`, `mfe_p50`, `etd_mean`
- `baseline_net_pnl`, `baseline_max_dd`

## `learning_dataset`
- `row_id` (PK)
- flattened features,
- parameter encoding,
- target metrics,
- split tag (`train/val/test/wf_n`),
- created_at.

## `recommendation_reports`
- `recommendation_id`
- `human_report_html`
- `machine_report_json`
- `generated_at`

---

## 10) Validation Framework

## Tests of effectiveness
1. **Backtesting recommendations**
   - Replay historical snapshots, generate recommendations, simulate outcomes.

2. **Walk-forward evaluation**
   - Train on past window, test on next window; roll forward.

3. **Baseline comparison**
   - Compare against default parameters and static best-known set.

4. **Stability tests**
   - Slightly perturb features; recommendation should not oscillate excessively.

5. **Confidence calibration**
   - Higher confidence should correspond to higher realized success probability.

6. **Failure analysis**
   - Cluster misses by regime/feature conflict; feed insights into rule updates.

## Core acceptance metrics
- uplift in expectancy vs baseline,
- controlled drawdown vs baseline,
- no-trade precision (avoids bad conditions without suppressing too many good ones),
- calibration quality (ECE/Brier),
- recommendation stability.

---

## 11) Phased Implementation Plan

## Phase 1 — Deterministic rule-based recommender
**Deliverables**
- Feature engine for required 15m/60m/240m windows.
- Rule-based regime classifier.
- StrategyProfile schema + manual profiles for first strategies.
- Parameter rule maps + confidence decomposition.
- JSON + HTML report outputs.

**Risks**
- Rule brittleness.
- Incomplete strategy metadata.

**Success criteria**
- End-to-end recommendation generated deterministically.
- Clear per-parameter explanation and no-trade support.

## Phase 2 — Historical analog matching
**Deliverables**
- Snapshot similarity index.
- Analog outcome retrieval.
- Evidence-weighted recommendation adjustments.

**Risks**
- Feature distance metric mis-specified.

**Success criteria**
- Recommendations backed by top-N similar historical states.
- Better stability and confidence calibration than Phase 1.

## Phase 3 — Scoring/ML reranker
**Deliverables**
- Candidate parameter generator + learned reranker.
- Explainability outputs (feature contributions).
- Model versioning + promotion criteria.

**Risks**
- Leakage/overfitting.

**Success criteria**
- Out-of-sample uplift and acceptable calibration.

## Phase 4 — Adaptive learning system
**Deliverables**
- Automated batch retraining.
- Drift/degradation detection and rollback.
- Adaptive thresholds by strategy and regime.

**Risks**
- Drift detection false positives/negatives.

**Success criteria**
- Sustained uplift, bounded drawdown, auditable model lifecycle.

---

## 12) Developer Implementation Guidance

## Suggested modules (aligned with existing layers)
- `src/ta_foundation/analysis/strategy_metadata/models.py`
- `src/ta_foundation/analysis/strategy_metadata/extractor.py`
- `src/ta_foundation/analysis/regime_recommender/features.py`
- `src/ta_foundation/analysis/regime_recommender/classifier.py`
- `src/ta_foundation/analysis/regime_recommender/recommender.py`
- `src/ta_foundation/analysis/regime_recommender/outcomes.py`
- `src/ta_foundation/analysis/regime_recommender/template_export.py`
- `src/ta_foundation/analysis/regime_recommender/orchestrator.py`
- `src/ta_foundation/reports/html/sections/regime_parameter_recommendation.py` (optional view)

## Interface suggestions
```python
# analysis/strategy_metadata/extractor.py
def build_strategy_profile(strategy_id: str, docs: str, template_data: dict) -> dict: ...

# analysis/regime_recommender/features.py
def build_multitf_features(
    market: MarketDataStore,
    instrument: str,
    contract: str,
    asof: pd.Timestamp,
) -> dict: ...

# analysis/regime_recommender/classifier.py
def classify_regime(features: dict, cfg: dict) -> dict: ...

# analysis/regime_recommender/recommender.py
def recommend_parameters(
    strategy_profile: dict,
    regime: dict,
    features: dict,
    historical_store,
    cfg: dict,
) -> dict: ...

# analysis/regime_recommender/outcomes.py
def record_outcome(recommendation_id: str, trades_df: pd.DataFrame, baseline_metrics: dict) -> dict: ...
```

## Orchestrator contract
```python
def compute_and_attach_regime_recommendation(
    pkg,
    market: MarketDataStore,
    strategy_id: str,
    options: dict,
) -> None:
    """Compute once in analysis layer; attach under pkg.metadata['derived']['regime_recommender']."""
```

## Template generation interfaces (required)
```python
# analysis/regime_recommender/template_export.py
def generate_session_templates(
    strategy_id: str,
    recommended_params: dict,
    regime: dict,
    session_windows: dict,
    base_template_path: str,
    output_dir: str,
) -> dict:
    """Return manifest dict and write one xml per session."""

# analysis/regime_recommender/orchestrator.py
def compute_attach_and_export_recommendation(
    pkg,
    market: MarketDataStore,
    strategy_id: str,
    options: dict,
) -> None:
    """Attach derived recommendation metadata and export session template xml artifacts."""
```

## Integration notes
- Keep heavy compute in analysis modules only.
- Report section must only render from context/derived metadata.
- All datetimes tz-aware, localized to America/Denver.
- Session template windows are authored in America/Denver local time fields (`StartTimeH/M`, `DurationTimeH/M`).
- Shared market bars remain in `MarketDataStore` (no duplication into package).
- Store exported template metadata under `pkg.metadata["derived"]["regime_recommender"]["template_bundle"]` and keep file paths in run assets/outputs manifests.

---

## 13) Risks and Failure Modes

1. **Overfitting recent data**
   - Mitigation: walk-forward, regularization, min sample thresholds.

2. **Unstable regime classification**
   - Mitigation: hysteresis/smoothing and confidence-aware hold behavior.

3. **Misleading features**
   - Mitigation: feature ablation, drift monitoring, remove low-value features.

4. **Bad strategy metadata extraction**
   - Mitigation: schema validation + manual approval + versioning.

5. **Black-box drift toward opacity**
   - Mitigation: keep rule-gates and explanation requirements mandatory.

6. **Data leakage**
   - Mitigation: strict as-of snapshoting; forbid future bars in feature computation.

7. **Parameter explosion**
   - Mitigation: role-based constrained search around defaults; cap simultaneously changed parameters.

8. **No-trade misuse (too conservative or too loose)**
   - Mitigation: explicit precision/recall tracking for no-trade outcomes.

---

## Final Explainability Guarantee (Design Requirement)
Every recommendation must carry:
1. **Parameter delta explanation**: baseline vs recommended value and reason.
2. **Top feature influence list**: ranked contributors with signed impact.
3. **Confidence decomposition**: explicit sub-scores.
4. **Counterfactual fallback**: what baseline would have implied and why not chosen.
5. **No-trade rationale** when applicable.

This ensures the system can always state **WHY each parameter was recommended** and **WHICH market features influenced it most**.
