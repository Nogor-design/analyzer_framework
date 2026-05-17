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
  - **Full asset key map:** `patterns`, `signals`, `outcomes`, `pattern_stats`, `events`, `events_exec`, `clusters`, `cluster_members`, `cluster_stats`, `mc_summary`, `mc_regime_summary`, `mc_slippage_surface`, `oos_stats`, `discovery_events`, `discovery_stats`, `discovery_regime_stats`, `discovery_stability`, `__scopes__`.
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
  - **Already reads from `pkg.assets["pattern_engine"]`:** `pe_signals`, `pe_outcomes`, `pe_stats`, `pe_patterns` — funneled through `entry_pattern_bridge.build_signal_feature_matrix()`.
  - Outputs: `pkg.metadata["derived"]["strategy_discovery"]` (JSON-safe), `pkg.assets["strategy_discovery"]["feature_matrix"]`, `pkg.assets["strategy_discovery"]["signal_feature_matrix"]`.
- `src/ta_foundation/analysis/strategy_discovery/validation.py`
  - Hard-gate validation: min trade counts (IS ≥ 50, OOS ≥ 20), walk-forward degradation < 20%, t-test (p < 0.05, t > 0), Monte Carlo drawdown sequence test, cost normalization.
  - **Gap:** does not accept external inputs for fold sign consistency, session concentration, regime dispersion, or parameter sensitivity classification — those all require explicit wiring.
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
  - **Confidence is consumed here, not computed.** Reads `regime.get("confidence")` from upstream classification; defaults to 0.0; gates on `min_confidence` threshold (default 0.55). Returns `"NO_TRADE"` decision if below threshold.
  - **Gap:** no decomposition of confidence into components (CV stability, MC survival, sensitivity, audit rate). Confidence is a single float from the classifier with no explainability downstream.
- `src/ta_foundation/analysis/regime_recommender/outcomes.py`
  - Outcome snapshot (net pnl, drawdown, MAE/MFE-derived metrics) for recommendation feedback loop.

### Session-aware and trade-outcome feature layers
- `src/ta_foundation/analysis/trade_feature_store.py`
  - Builds per-trade joined feature frame: ATR, EMA slope, VWAP distance, HTF slope, microstructure, `entry_hour`, `entry_dow`.
  - Config-driven: `bar_tf`, `htf_tf`, `ema_period`, `atr_period`, `slope_lookback`, `micro_window`.
- `src/ta_foundation/analysis/market_regime_store.py`
  - **Authoritative session taxonomy:** `london` (00-06), `pre_open` (07), `us_open` (08), `us_morning` (09-11), `us_midday` (12-13), `us_afternoon` (14-15), `globex_evening` (16-23).
  - Provides `summarize_entry_hour_risk()` and `optimize_entry_hour_window()` (beam search for best trading window) — these are not yet consumed by pattern sweep pruning or discovery gating.
- `src/ta_foundation/analysis/trade_entry_signal_store.py`
  - Entry context store: pivot distances (ATR/ticks/dollars), opening range levels, overnight levels, swing points.

### Portfolio combination
- `src/ta_foundation/analysis/combo_selection.py`
  - Finds low co-loss strategy combinations: `top_k2_exact()` (exhaustive pairs), `top_k_beam()` (beam search k≥3), scored by `any_coloss_rate`, `all_loss_rate`, `combo_cum_end`.
  - **Gap:** not wired into strategy discovery final output — combo results are never emitted as a "deployable basket" recommendation.
- `src/ta_foundation/analysis/daily_matrix.py`
  - Aligns daily P&L across all packages into a single `DailyMatrix` structure (tolerant parsing of date/pnl columns, fallback from trades aggregation).

### Report composition and orchestration
- `src/ta_foundation/reports/html/config.py`
  - YAML config loading + report build orchestration.
  - Triggers analysis engines (pattern engine, regime recommender, anchor interaction) before rendering.
- `src/ta_foundation/reports/html/registry.py`
  - 65 section renderers registered across: anchor interaction (7), pattern engine (6), strategy discovery (24), run cards (8), performance (7), trade analysis (3), discovery (3), exit policies (2).
- Representative sections:
  - `sections/pattern_market_discovery.py`
  - `sections/market_regime_discovery.py`
  - `sections/regime_parameter_recommendation.py`

---

## 2) Underused or disconnected components to combine

> **Note:** Based on actual code inspection, several items previously listed here are already partially connected. The gaps below reflect what is genuinely missing.

1. **Candidate bridge exists but is incomplete — OOS stats, MC survival, and audit confirmation are not joined.**
   - `strategy_discovery/orchestrator.py` already reads `pe_signals/pe_outcomes/pe_stats/pe_patterns` via `entry_pattern_bridge`.
   - However, `oos_stats`, `mc_summary`, `mc_regime_summary`, and `trade_pattern_audit` confirmation rates are **not** joined into a unified candidate scorecard.
   - Leverage: extend `entry_pattern_bridge` (or add a dedicated `candidate_scorecard.py`) to join all five evidence sources into one row per `(pattern_id, horizon, regime/session)`.

2. **Trade Pattern Audit confirmation rate is computed but never used as a scoring weight.**
   - `pkg.assets["trade_pattern_audit"]["audit_df"]` exists after pattern engine runs.
   - Audit confirmation rates are consumed only by the `trade_pattern_audit` report section.
   - Leverage: feed per-pattern audit confirmation rates as a prior weight into pattern ranking and into `recommender.py`'s confidence decomposition.

3. **Session danger/edge scores from `market_regime_store` are not upstream of pattern sweep or validation gating.**
   - `summarize_entry_hour_risk()` and `optimize_entry_hour_window()` are rich outputs.
   - Pattern sweep does not use session edge scores for pruning; `validation.py` does not enforce a session concentration cap.
   - Leverage: pass session risk summaries into `validation.py` as an optional gate input and into sweep `min_edge_score` filters.

4. **`recommender.py` confidence is opaque — no decomposition into contributing factors.**
   - Confidence is a single float passed down from the regime classifier.
   - Validation results (walk-forward degradation, Monte Carlo pass/fail), sensitivity classification (fragile/moderate/robust), and audit confirmation rates are computed but never merged into this number.
   - Leverage: build one `ConfidenceDecomposition` dataclass (pattern_cv_stability, mc_survival_rate, sensitivity_class, audit_confirmation_rate, regime_classifier_confidence) and expose it in the recommendation payload.

5. **`combo_selection` and `daily_matrix` are never called from `strategy_discovery/orchestrator.py`.**
   - Both modules are fully implemented and tested in isolation.
   - Strategy discovery currently ranks single runs — the final output never proposes a deployable basket.
   - Leverage: add a final step in `strategy_discovery/orchestrator.py` that runs `build_daily_matrix` → `top_combos` and emits `pkg.assets["strategy_discovery"]["combo_basket"]` alongside the existing single-winner ranking.

6. **Report orchestration has no pre-render artifact contract check.**
   - `build_report_from_config` runs engines opportunistically; sections that expect `pkg.assets["pattern_engine"]["oos_stats"]` silently degrade if the key is absent.
   - Leverage: add a structured `diagnostics` block to the report context before section rendering — one dict that lists which engines ran, which asset keys are present, and any pipeline warnings. Sections can check it instead of silently failing.

---

## 3) Top architectural improvements for reusability/composability

1. **Extend the candidate bridge to a full scorecard table.**
   - The existing `entry_pattern_bridge` already joins pattern signals with feature matrix data.
   - Missing columns: `oos_stability_score` (from `oos_stats`), `mc_survival_rate` (from `mc_summary`), `mc_regime_survival` (from `mc_regime_summary`), `audit_confirmation_rate` (from `audit_df`), `session_edge_score` (from `market_regime_store`).
   - Output: one candidate row per `(pattern_id, horizon, regime_slice)`, stored in `pkg.assets["strategy_discovery"]["candidate_scorecard"]`.

2. **Extend `validation.py` with a pluggable gate bundle.**
   - Current gates are hardcoded: trade counts, walk-forward, t-test, Monte Carlo, cost normalization.
   - Add optional keyword arguments: `fold_sign_consistency`, `session_concentration_cap`, `regime_dispersion_min`, `sensitivity_class_min`.
   - These map directly to already-computed values in `oos_stats`, `market_regime_store`, and `parameter_sensitivity`.
   - Gate bundle should return a structured `ValidationResult` with per-gate pass/fail and the composite decision.

3. **Build a `ConfidenceDecomposition` model in `regime_recommender`.**
   - `recommender.py` currently gets a single float; replace with a named structure:
     ```python
     @dataclass
     class ConfidenceDecomposition:
         regime_classifier: float      # from classifier upstream
         cv_stability: float           # from oos_stats fold consistency
         mc_survival_rate: float       # from mc_summary
         sensitivity_class: str        # fragile/moderate/robust
         audit_confirmation_rate: float
         composite: float              # weighted blend, replaces current single float
     ```
   - Expose all components in the recommendation payload for report rendering.

4. **Establish one authoritative session taxonomy.**
   - `market_regime_store.py` has the canonical session label map.
   - `pure_discovery.py` and `trade_entry_signal_store.py` may define session windows independently.
   - Extract the label map to a shared constant in `analysis/session_constants.py` (or `core/session.py`) and import from there in all three modules.

5. **Add a pre-render analysis plan resolver to `config.py`.**
   - Before any section renders, resolve which engines ran and which `pkg.assets` keys are present.
   - Inject a `ctx["pipeline_diagnostics"]` dict: `{ "engines_run": [...], "asset_keys_present": {...}, "warnings": [...] }`.
   - Sections that need optional assets check `ctx["pipeline_diagnostics"]` instead of catching KeyErrors silently.

6. **Wire `combo_selection` into `strategy_discovery/orchestrator.py`.**
   - After single-run ranking, call `build_daily_matrix(packages)` → `top_combos(matrix, k=2)` and `top_combos(matrix, k=3)`.
   - Emit results under `pkg.assets["strategy_discovery"]["combo_basket"]` as a list of `ComboScore` objects (JSON-safe summary in metadata).
   - Add one new report section (`strategy_discovery_combo_basket.py`) to render the basket output.

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
   - All artifact keys already produced by `pattern_engine/orchestrator.py` — no changes needed here.

4. **Candidate scorecard assembly (extend entry_pattern_bridge)**
   - Extend `entry_pattern_bridge` (or new `candidate_scorecard.py` inside `strategy_discovery`) to join:
     - `pattern_stats` / `discovery_stability` — base pattern metrics
     - `oos_stats` — CV fold stability score
     - `mc_summary` / `mc_regime_summary` — MC survival rates
     - `audit_df` — per-pattern confirmation rates from trade audit
     - `market_regime_store` session/hour summaries — session edge scores
   - Output: `pkg.assets["strategy_discovery"]["candidate_scorecard"]`.

5. **Validation hard gates (extend validation.py)**
   - Pass scorecard candidates through `run_validation` with the new optional gate inputs:
     - `fold_sign_consistency` from `oos_stats`
     - `session_concentration_cap` from entry hour summaries
     - `regime_dispersion_min` from regime summaries
     - `sensitivity_class_min = "moderate"` from `parameter_sensitivity`
   - Each gate emits a structured pass/fail with the reason string.

6. **Parameter robustness + recommendation (extend recommender)**
   - For surviving candidates:
     - run `parameter_sensitivity` classification (already done in strategy discovery)
     - run `regime_recommender` snapshot + recommendation
   - Replace the single confidence float with `ConfidenceDecomposition` that blends:
     - regime classifier confidence
     - CV stability (from oos_stats)
     - MC survival rate
     - sensitivity class
     - audit confirmation rate

7. **Portfolio-aware final selection**
   - On surviving single-run candidates, run `build_daily_matrix` → `top_combos(k=2)` + `top_combos(k=3)`.
   - Emit `pkg.assets["strategy_discovery"]["combo_basket"]` alongside the existing single-winner ranking.

8. **Reporting and traceability**
   - Reuse existing 65 sections.
   - Add `strategy_discovery_decision_ledger.py` section: one row per candidate showing source artifacts, gate results (pass/fail + reason), sensitivity class, confidence decomposition, recommendation decision.
   - Add `strategy_discovery_combo_basket.py` section: renders the combo basket with co-loss rates and cumulative P&L.
   - Add `ctx["pipeline_diagnostics"]` block so all sections can check artifact availability before rendering.

---

## 5) Prioritized implementation roadmap (specific files/functions)

### Priority 0 — High leverage, low disruption (extend existing, no new layers)

1. **Extend candidate bridge to full scorecard.**
   - File: `src/ta_foundation/analysis/strategy_discovery/entry_pattern_bridge.py`
   - Add: `build_candidate_scorecard(pkg)` — joins `oos_stats`, `mc_summary`, `mc_regime_summary`, `audit_df`, session risk summaries into one DataFrame.
   - Caller: `strategy_discovery/orchestrator.py` — store result in `pkg.assets["strategy_discovery"]["candidate_scorecard"]`.

2. **Extend validation.py gate bundle.**
   - File: `src/ta_foundation/analysis/strategy_discovery/validation.py`
   - Add optional kwargs to `run_validation(...)`:
     - `fold_sign_consistency: float | None = None` (from `oos_stats`)
     - `session_concentration_cap: float | None = None` (max fraction of trades in one session)
     - `regime_dispersion_min: int | None = None` (min number of regimes represented)
     - `sensitivity_class_min: str | None = None` (`"moderate"` or `"robust"`)
   - Return `ValidationResult` dataclass with per-gate `{name, passed, value, threshold, reason}` list.

3. **Add `ConfidenceDecomposition` to regime recommender.**
   - File: `src/ta_foundation/analysis/regime_recommender/recommender.py`
   - Add `ConfidenceDecomposition` dataclass (see Section 3 above).
   - File: `src/ta_foundation/analysis/regime_recommender/orchestrator.py`
   - Populate decomposition components from already-available outputs before calling `recommend_parameters`.
   - Surface full decomposition in recommendation payload dict.

### Priority 1 — Session/regime composability

4. **Create authoritative session constants module.**
   - Create: `src/ta_foundation/analysis/session_constants.py`
   - Move session label → hours mapping from `market_regime_store.py` here.
   - Update imports in: `market_regime_store.py`, `pure_discovery.py`, `trade_entry_signal_store.py`.
   - No behavior change — pure deduplication of a shared constant.

5. **Feed session risk summaries into candidate scoring.**
   - File: `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
   - After building regime frame, call `summarize_entry_hour_risk()` and `optimize_entry_hour_window()`.
   - Pass hour risk summaries into `build_candidate_scorecard()` as `session_risk_df` arg.
   - This surfaces session danger zones as an explicit scoring penalty on candidates.

### Priority 2 — Reporting and operational reliability

6. **Add pipeline diagnostics to report context.**
   - File: `src/ta_foundation/reports/html/config.py`
   - Before section rendering loop, build `ctx["pipeline_diagnostics"]`:
     ```python
     {
       "engines_run": ["pattern_engine", "anchor_interaction", ...],
       "asset_keys": {run_id: list(pkg.assets.keys()) for run_id, pkg in packages.items()},
       "warnings": [w for pkg in packages.values() for w in pkg.warnings],
     }
     ```
   - Eliminates silent section degradation when optional assets are absent.

7. **Add Discovery Decision Ledger section.**
   - Create: `src/ta_foundation/reports/html/sections/strategy_discovery_decision_ledger.py`
   - Input: `ctx["packages"]` → reads `candidate_scorecard` and `ValidationResult` list from `pkg.assets["strategy_discovery"]`.
   - Renders: one row per candidate — source artifacts, gate pass/fail, sensitivity class, `ConfidenceDecomposition`, recommendation decision.
   - Register: `src/ta_foundation/reports/html/registry.py` → key `"strategy_discovery_decision_ledger"`.

### Priority 3 — Portfolio-level deployment outputs

8. **Wire combo selection into strategy discovery orchestrator.**
   - File: `src/ta_foundation/analysis/strategy_discovery/orchestrator.py`
   - After single-run ranking, call:
     ```python
     matrix = build_daily_matrix(packages)
     basket_k2 = top_combos(matrix, k=2, top_n=5)
     basket_k3 = top_combos(matrix, k=3, top_n=3)
     ```
   - Store JSON-safe summary in `pkg.metadata["derived"]["strategy_discovery"]["combo_basket"]`.
   - Store full `ComboScore` list in `pkg.assets["strategy_discovery"]["combo_basket"]`.

9. **Add Combo Basket report section.**
   - Create: `src/ta_foundation/reports/html/sections/strategy_discovery_combo_basket.py`
   - Renders: top k=2 and k=3 combinations, co-loss rates, combined vs individual cumulative P&L chart.
   - Register in `registry.py` → key `"strategy_discovery_combo_basket"`.

---

## 6) Where leverage is highest

1. **Candidate scorecard unification (Priority 0, item 1):** The pattern engine already produces `oos_stats`, `mc_summary`, and `discovery_stability`. The audit DataFrame exists. None of these are currently joined into a single evidence table for ranking. Building this join is the highest-signal, lowest-disruption change available — it requires no new analysis, only assembly of existing outputs.

2. **Validation gate extension (Priority 0, item 2):** `validation.py` is already the canonical hard gate. Adding optional inputs for fold sign consistency, session concentration, and regime dispersion closes the biggest anti-overfit gap. These inputs are already computed elsewhere — the only work is wiring them through.

3. **Confidence decomposition (Priority 0, item 3):** Replacing the single opaque confidence float with a `ConfidenceDecomposition` struct makes the recommender's decision explainable and auditable. This is a small structural change with large diagnostic payoff.

4. **Combo basket wiring (Priority 3, items 8–9):** `combo_selection.py` and `daily_matrix.py` are fully implemented but never called from the main pipeline. Wiring them in converts isolated alpha into deployable, lower co-loss strategy sets with minimal new code.
