# ta_foundation Repository Review

## Scope
Senior quant systems engineering review focused on:
- pattern discovery
- market regime analysis
- trade outcome analysis
- session-aware analysis
- multi-timeframe context
- parameter recommendation
- report generation
- validation against overfitting

---

## 1) Capability map (major modules)

### Ingestion and canonical data contracts
- `src/ta_foundation/core/pipeline.py`
  - Builds `AnalysisPackage` per `run_id` and `MarketDataStore` for shared data.
  - Enforces `metadata["timezone"] = "America/Denver"` contract at package creation.
  - Attaches run-scoped derived analytics under `pkg.metadata["derived"]` and DataFrame assets under `pkg.assets`.
  - Ingests both run CSVs and shared market `.txt` artifacts, with tick-cache support.
- `src/ta_foundation/marketdata/store.py`
  - Shared canonical market data store with minute/tick ownership and derived timeframe caching.
  - `get_bars(...)` provides multi-timeframe bars from either minute bars or ticks (`source=auto/minute/ticks`).

### Pattern discovery and robustness stack
- `src/ta_foundation/analysis/pattern_engine/orchestrator.py`
  - Main pattern pipeline driver.
  - Produces patterns/signals/outcomes/events, clusters, CV robustness (`oos_stats`), Monte Carlo, regime-aware Monte Carlo, and optional market-discovery synthetic package.
  - Writes parquet artifacts and caches in-memory DataFrames in `pkg.assets["pattern_engine"]`.
- `src/ta_foundation/analysis/pattern_engine/engine.py`
  - Pattern sweep core, pattern/signal IDs, event construction for downstream MC.
- `src/ta_foundation/analysis/pattern_engine/discovery.py`
  - Market-derived forward-outcome discovery by horizon, volatility regime, and fold stability.
- `src/ta_foundation/analysis/pattern_engine/robustness_cv.py`
  - Time-based anchored fold CV; produces fold stats and OOS stability scores.
- `src/ta_foundation/analysis/pattern_engine/trade_pattern_audit.py`
  - Trade-by-trade pattern confirmation audit (entry context alignment between actual trades and template patterns).

### Strategy discovery and anti-overfitting layer
- `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
  - Cross-cutting orchestration for regime labeling, MAE/MFE profiling, validation, evaluation, feature matrix, importance, and discovery submodules.
- `src/ta_foundation/analysis/strategy_discovery/validation.py`
  - Hard-gate validation: min trade counts, IS/OOS degradation, t-test, Monte Carlo trade-sequence robustness, cost normalization.
- `src/ta_foundation/analysis/strategy_discovery/pure_discovery.py`
  - Controlled candidate strategy assembly from market structure templates and synthetic entry/exit generation.
- `src/ta_foundation/analysis/strategy_discovery/parameter_sensitivity.py`
  - Threshold perturbation robustness analysis (fragile/moderate/robust classification).

### Regime intelligence + parameter recommendation
- `src/ta_foundation/analysis/regime_recommender/orchestrator.py`
  - End-to-end regime recommendation flow: build MTF features -> classify regime -> recommend params -> optionally export templates + persist outcomes.
- `src/ta_foundation/analysis/regime_recommender/features.py`
  - Multi-timeframe market snapshot (15m/1h/4h) with trend/ATR/compression/VWAP distance + cross-TF agreement.
- `src/ta_foundation/analysis/regime_recommender/recommender.py`
  - Confidence-gated parameter adjustment logic around strategy defaults and parameter bounds.
- `src/ta_foundation/analysis/regime_recommender/outcomes.py`
  - Outcome snapshot (net pnl, drawdown, MAE/MFE-derived metrics) for recommendation feedback loop.

### Session-aware and trade-outcome feature layers
- `src/ta_foundation/analysis/trade_feature_store.py`
  - Builds per-trade joined feature frame from market bars/ticks (ATR, EMA slope, VWAP distance, HTF slope, microstructure, entry hour/day).
- `src/ta_foundation/analysis/market_regime_store.py`
  - Session/trend/vwap/vol regime classification and risk/performance summaries (including hour-level risk and optimizer windows).
- `src/ta_foundation/analysis/trade_entry_signal_store.py`
  - Entry context store with pivots/opening range/overnight levels and distance-in-ATR/ticks features.

### Report composition and orchestration
- `src/ta_foundation/reports/html/config.py`
  - YAML config loading + report build orchestration.
  - Triggers analysis engines (pattern engine, regime recommender, anchor interaction) before rendering.
- `src/ta_foundation/reports/html/registry.py`
  - Large section registry exposing pattern/regime/discovery/reporting capabilities.
- Representative sections:
  - `sections/pattern_market_discovery.py`
  - `sections/market_regime_discovery.py`
  - `sections/regime_parameter_recommendation.py`

---

## 2) Underused or disconnected components to combine

1. **Pattern engine robustness outputs are not tightly consumed by strategy discovery ranking/selection.**
   - `pattern_engine` generates `oos_stats`, `discovery_stability`, MC summaries.
   - `strategy_discovery` has its own validation + ranking path.
   - Leverage: unify these into a single candidate scorecard keyed by pattern/candidate IDs.

2. **Trade Pattern Audit is decoupled from pattern discovery selection pressure.**
   - Audit output exists in `pkg.assets["trade_pattern_audit"]["audit_df"]`, but mostly used for reporting/optional features.
   - Leverage: use audit confirmation rates directly as priors/weights in pattern ranking and parameter recommendation confidence.

3. **Session-aware regime analytics are rich but not consistently upstreamed into pattern sweep filters.**
   - `market_regime_store.py` provides session+hour regime edge and risk summaries.
   - Pattern sweep currently uses weaker generic buckets in several places.
   - Leverage: feed session danger/edge scores into sweep pruning and validation gating.

4. **Regime recommender and strategy discovery operate with parallel feature worlds.**
   - Recommender uses snapshot MTF features (`tf15m/tf60m/tf240m`).
   - Strategy discovery builds trade-level feature matrix and classification separately.
   - Leverage: establish shared derived feature schema and reuse the same features for both prediction and validation.

5. **Reporting is broad but can mask pipeline consistency issues.**
   - `build_report_from_config` runs multiple heavy engines opportunistically.
   - Some sections contain debug prints/duplicated paths and rely on in-memory asset availability.
   - Leverage: central run diagnostics summary section + strict artifact presence contracts before section render.

6. **Portfolio-combination logic appears underutilized in discovery closure.**
   - `analysis/combo_selection.py` and `analysis/daily_matrix.py` can choose low co-loss combinations.
   - Leverage: apply this after single-strategy ranking to output deployable strategy baskets, not only single winners.

---

## 3) Top architectural improvements for reusability/composability

1. **Unify derived artifact schema and IDs across engines.**
   - Standardize keys under `pkg.metadata["derived"]` for:
     - `pattern_candidates`
     - `validation`
     - `robustness`
     - `regime_alignment`
     - `recommendation`
   - Ensure deterministic IDs map across pattern_engine, strategy_discovery, and recommender.

2. **Create one “discovery state object” in metadata (JSON-safe) + DataFrame assets map.**
   - Keep DataFrames only in `pkg.assets[...]`.
   - Keep only lightweight index/summary in metadata.
   - Make every section read from the same stable state map.

3. **Move gating logic into a common validator interface.**
   - Reuse `strategy_discovery/validation.py` as canonical gate, but allow pluggable checks:
     - CV stability from pattern_engine
     - regime dispersion
     - session concentration
     - audit confirmation
   - Prevent fragmented pass/fail criteria across modules.

4. **Promote session+regime context into first-class discovery constraints.**
   - Standardize session labels/hours in one place (avoid duplicates between modules).
   - Maintain consistent session windows from ingest through discovery, validation, and reporting.

5. **Separate compute triggering from rendering intent more explicitly in report orchestration.**
   - Keep current contracts, but introduce a pre-report “analysis plan resolver” that resolves which engines run once and which artifacts are expected.
   - This reduces hidden coupling from section selection and avoids compute duplication.

6. **Introduce a single parameter recommendation confidence model.**
   - Blend regime classifier confidence + walk-forward stability + Monte Carlo survivability + sensitivity class + audit agreement.
   - Replace isolated threshold checks with one explainable composite confidence.

---

## 4) Concrete design: improved pattern-discovery pipeline using existing pieces

### Goal
Use current components to produce robust, session-aware, regime-conditioned strategy candidates with explicit anti-overfitting checks and recommendation outputs.

### Proposed pipeline (no new architectural layer; composed from existing modules)

1. **Ingest + canonical market context**
   - Use existing `core/pipeline.py` + `MarketDataStore.get_bars(...)`.
   - Ensure instrument/contract/timeframe resolved once per run and reused.

2. **Trade-context feature enrichment (run-scoped)**
   - Build `trade_feature_store` frame and `trade_entry_signal_store` frame.
   - Attach to `pkg.assets["strategy_discovery"]` and reference from downstream modules.

3. **Pattern sweep + market discovery + audit (pattern_engine)**
   - Run `run_pattern_sweep`, `compute_market_discovery`, `compute_trade_pattern_audit`, CV, and MC in one pass.
   - Persist artifacts already produced by `pattern_engine/orchestrator.py`.

4. **Candidate assembly and scoring bridge**
   - Build a bridge table (new helper inside existing strategy_discovery package) that joins:
     - pattern stats/discovery stability
     - oos stats (CV)
     - MC survival
     - audit confirmation rates
     - regime/session edge from market_regime summaries
   - Output: one candidate row per (pattern_id, horizon, regime/session slice).

5. **Validation hard gates**
   - Pass bridge candidates through `strategy_discovery.validation.run_validation` checks.
   - Augment with:
     - min regime diversity
     - session concentration cap
     - sign consistency threshold from discovery stability.

6. **Parameter robustness + recommendation**
   - For surviving candidates:
     - run `parameter_sensitivity` classification
     - run `regime_recommender` snapshot + recommendation
   - Merge outcomes into one recommendation packet:
     - baseline params
     - candidate-conditioned parameter deltas
     - confidence decomposition.

7. **Portfolio-aware final selection (optional but high leverage)**
   - Use `daily_matrix` + `combo_selection` on surviving candidates/runs.
   - Select low co-loss sets, not just top standalone strategy.

8. **Reporting and traceability**
   - Reuse existing sections; add/extend one integrated “Discovery Decision Ledger” section using existing context contract.
   - Ledger should show for each candidate: source artifacts, gates passed/failed, sensitivity class, recommendation decision, anti-overfit evidence.

---

## 5) Prioritized implementation roadmap (specific files/functions)

### Priority 0 (high leverage, low disruption)
1. **Create cross-engine candidate bridge in strategy_discovery.**
   - Modify: `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
     - After pattern/validation assets exist, assemble unified candidate table into `pkg.assets["strategy_discovery"]["candidate_bridge"]`.
   - Read from:
     - `pkg.assets["pattern_engine"]` keys (`pattern_stats`, `discovery_stats`, `discovery_stability`, `oos_stats`, `mc_summary`, `mc_regime_summary`)
     - `pkg.assets["trade_pattern_audit"]["audit_df"]`

2. **Standardize anti-overfit gate bundle.**
   - Modify: `src/ta_foundation/analysis/strategy_discovery/validation.py`
   - Add optional inputs for:
     - fold sign consistency
     - session concentration
     - regime dispersion
     - sensitivity classification.

3. **Integrate recommendation confidence decomposition.**
   - Modify:
     - `src/ta_foundation/analysis/regime_recommender/recommender.py`
     - `src/ta_foundation/analysis/regime_recommender/orchestrator.py`
   - Add confidence components from validation + MC + sensitivity + audit into output payload.

### Priority 1 (session/regime composability)
4. **Unify session taxonomy and reuse in discovery.**
   - Modify:
     - `src/ta_foundation/analysis/market_regime_store.py`
     - `src/ta_foundation/analysis/strategy_discovery/pure_discovery.py`
     - `src/ta_foundation/analysis/trade_entry_signal_store.py`
   - Ensure one authoritative mapping for session windows/labels.

5. **Promote market_regime summaries into candidate scoring.**
   - Modify: `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
   - Use existing `summarize_entry_hour_risk`, `optimize_entry_hour_window`, and regime summaries as explicit ranking penalties/bonuses.

### Priority 2 (reporting and operational reliability)
6. **Add integrated discovery ledger section.**
   - Create: `src/ta_foundation/reports/html/sections/strategy_discovery_decision_ledger.py`
   - Modify registry: `src/ta_foundation/reports/html/registry.py`
   - Input only from context assets/metadata; no disk IO.

7. **Clean report orchestration coupling and diagnostics output.**
   - Modify: `src/ta_foundation/reports/html/config.py`
   - Add one structured diagnostics block into context (engine run flags, artifact availability, warnings).

### Priority 3 (portfolio-level deployment outputs)
8. **Wire combo selection into final recommendation package.**
   - Modify:
     - `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
     - `src/ta_foundation/analysis/daily_matrix.py`
     - `src/ta_foundation/analysis/combo_selection.py`
   - Emit “single best” + “best diversified basket” recommendations.

---

## Where leverage is highest

1. **Cross-engine unification of candidate evidence** (pattern robustness + strategy validation + regime recommendation) is the single biggest performance/robustness gain per unit effort.
2. **Session/regime-aware gating** using existing market_regime and entry-signal stores can materially reduce overfit patterns without adding new data sources.
3. **Portfolio-aware selection** (already mostly implemented in utility form) can convert isolated alpha into deployable, lower co-loss strategy sets.

