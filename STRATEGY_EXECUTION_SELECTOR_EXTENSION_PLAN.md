# Strategy Execution Selector Extension Plan (ta_foundation)

## 1) Codebase Audit (current state, reusable components)

This audit is based on the current production contracts and existing modules.

### 1.1 Contracts and architecture already in place

- **Layering and report contracts are explicit and production-hardened** in `ARCHITECTURE.md`, `CONTRIBUTING.md`, `PROJECT_CONTEXT.md`, and `REPORTING_SECTIONS.md`.
- **Timezone contract is already explicit**: canonical datetimes are tz-aware and localized to `America/Denver`.
- **Data ownership contract exists**: run-scoped results in `AnalysisPackage`; shared market artifacts in `MarketDataStore` (`run_id=None`).
- **Report pipeline contract exists**: `report.yaml -> load_report_config -> build_report_from_config -> HtmlReportBuilder.build -> section.render_fn`.

### 1.2 Existing reusable analysis capability

The requested extension aligns strongly with modules already present:

- **Strategy discovery scaffold already exists** (`analysis/strategy_discovery/*`), including:
  - `entry_discovery.py`: rule-conjunction entry search over feature matrix.
  - `filter_discovery.py`: exclusion filter discovery.
  - `exit_discovery.py`: parameter sweeps across multiple exit families using existing sim engine.
  - `validation.py`: cost normalization, walk-forward, t-test, Monte Carlo checks.
  - `evaluation.py`, `risk_metrics.py`, `regime.py`, `ranking.py`: performance, regime, ranking logic.
- **Pattern and setup ecosystem exists** in `analysis/pattern_engine/*` (good base for setup-style triggers and diagnostics).
- **Regime-aware recommendation pipeline exists** in `analysis/regime_recommender/*` (features, classifier, recommendation, persistence hooks).

### 1.3 Gaps vs your requested end-state

Current implementation provides strong building blocks, but still needs these focused upgrades:

1. **Richer trigger taxonomy**
   - Current entry discovery is feature-threshold based; not yet a formalized trigger library covering MA cross, FVG entry/rejection, ORB, breakout/continuation/reversion, candle patterns, Bollinger, level-based setups as first-class pluggable trigger specs.
2. **Compositional strategy model**
   - Need a stable typed composition unit: `entry_trigger + filters + exit_policy (+ optional sizing/risk profile)` that can be generated and tested systematically.
3. **Market-state engine unification**
   - Regime signals exist, but session/location-vs-daily-level/volatility/trend context should be unified into one market-state snapshot used consistently for discovery, robustness scoring, and next-session recommendation.
4. **Cross-condition robustness ranking**
   - Ranking exists, but needs explicit regime/session/day-type conditional robustness metrics and anti-fragility checks.
5. **Next-session strategy selection layer**
   - Regime recommender exists; extend it to predict top strategy set for next session/day using current market features and daily levels.

---

## 2) Proposed Modular Architecture (incremental, no pipeline redesign)

### 2.1 Principle

Keep existing pipeline/layers intact. Add small, explicit modules inside `analysis/` and lightweight config wiring in `report.yaml`. No new global state, no section-side compute.

### 2.2 Proposed module map

Add / extend under `src/ta_foundation/analysis/strategy_discovery/`:

1. `trigger_library.py`
   - Registry of trigger evaluators (MA cross, FVG, ORB, breakout, continuation, reversion, candle pattern, Bollinger, level-based, etc.).
   - Input: feature/bar context DataFrame(s).
   - Output: boolean/intensity columns (e.g., `trig_ma_cross_fast_slow`, `trig_fvg_reject_bull`).

2. `filter_library.py`
   - Registry for filter predicates (trend, volatility, session, level-location, regime).
   - Output columns or callable predicates composable with triggers.

3. `exit_library.py`
   - Adapter over current `exit_discovery.py` families + additions for structure/time exits.
   - Keeps one interface for fixed / ATR / structure / indicator / time exits.

4. `composition.py`
   - Defines canonical `StrategyCandidateSpec` schema (id, entry, filters, exit, metadata).
   - Deterministic strategy-id hashing for traceability.

5. `candidate_generator.py`
   - Enumerates candidate specs under guardrails (depth limits, min support, family quotas).

6. `backtest_adapter.py`
   - Bridges candidate spec -> existing simulation/evaluation modules (reuses cost model + validation).

7. `market_state_engine.py`
   - Produces session/day market-state snapshots from bars + daily levels.
   - Feeds discovery, robustness slicing, and next-session selector.

8. `robustness.py`
   - Cross-regime/session/day-type stability, degradation distributions, parameter sensitivity aggregation.

9. `selector.py`
   - Predicts top-K strategy candidates for next session/day from latest market-state snapshot.

10. `orchestrator.py` (extend)
    - Wire new modules as optional phases behind config flags.

### 2.3 Data placement (respect contracts)

- Run-scoped outputs attach under:
  - `pkg.metadata["derived"]["strategy_discovery_v2"][...]` (JSON-safe summaries)
  - `pkg.assets["strategy_discovery_v2"][...]` (DataFrames if needed for render/debug)
- Shared bars/levels remain in `MarketDataStore` only.
- No duplication of shared artifacts into per-run packages.

---

## 3) YAML Config Design (extensible but constrained)

Add a new top-level block while preserving existing config behavior:

```yaml
strategy_discovery_v2:
  enabled: true

  instrument: "NQ"
  contract: "M26"
  timeframe: "5m"
  tick_size: 0.25

  triggers:
    enabled:
      - ma_cross
      - fvg_entry
      - fvg_rejection
      - orb
      - breakout
      - continuation
      - reversion
      - candle_pattern
      - bollinger
      - level_setup
    ma_cross:
      fast_periods: [9, 20]
      slow_periods: [50, 100]
    orb:
      windows_min: [5, 15, 30]
      breakout_buffer_ticks: [0, 2]
    bollinger:
      periods: [20]
      stddev: [2.0, 2.5]

  filters:
    enabled:
      - trend
      - volatility
      - session
      - daily_level_location
      - regime
    trend:
      adx_min: [20, 25]
      slope_lookback: [10, 20]
    volatility:
      atr_pctile_min: [0.25]
      atr_pctile_max: [0.95]
    session:
      allow: ["london", "us_open", "us_lunch", "us_close"]
    daily_level_location:
      level_set: "prev_day_hl_vwap"
      distance_bands_ticks: [8, 20, 40]

  exits:
    enabled_families: [fixed, atr, structure, indicator, time]
    fixed:
      stop_ticks: [8, 12, 16]
      target_ticks: [12, 20, 32]
    atr:
      stop_mult: [0.8, 1.2, 1.6]
      trail_mult: [1.5, 2.0]
    structure:
      lookback_bars: [5, 10]
      rr_min: [1.2]
    indicator:
      ma_exit_periods: [20, 50]
    time:
      max_hold_min: [15, 30, 60]
      flatten_at_session_end: true

  generation:
    max_candidates_total: 1500
    max_conditions_per_side: 2
    min_trades_per_candidate: 40
    family_quota:
      ma_cross: 250
      orb: 250
      level_setup: 250

  validation:
    walk_forward:
      wf_type: "rolling"
      n_folds: 8
      is_pct: 0.70
      min_is_trades: 80
      min_oos_trades: 40
    purge:
      enabled: true
      bars: 5
    embargo:
      enabled: true
      bars: 5
    cost_model:
      commission_per_side: 2.09
      slippage_ticks: 1
      tick_value: 5.0
    significance:
      p_value_max: 0.05
    monte_carlo:
      n_sims: 2000

  ranking:
    objective: "robust_net"
    stability_weight: 0.30
    oos_weight: 0.35
    regime_consistency_weight: 0.20
    simplicity_weight: 0.15

  selector:
    enabled: true
    predict_horizon: "next_session"   # or next_day
    top_k: 3
    min_confidence: 0.55

  reporting:
    persist_candidate_table: true
    persist_fold_details: true
    persist_selector_snapshot: true
```

Design notes:
- Keep this fully optional, default-off.
- No CLI flags required for display behavior.
- Section options remain in `sections[].options`; heavy compute stays in analysis/orchestrator.

---

## 4) Strategy Composition Model

### 4.1 Canonical candidate spec

Each candidate should be explicit and serializable:

- `candidate_id`
- `entry`:
  - `trigger_type` (e.g., `orb_breakout`)
  - `params`
- `filters`: list of `{type, params}`
- `exit`:
  - `family` (`fixed|atr|structure|indicator|time`)
  - `params`
- `risk_profile` (optional, later phase)
- `metadata`:
  - generation lineage, feature version, bars universe, timezone policy

### 4.2 Composition semantics

Use a simple deterministic rule:

`enter = entry_trigger AND all(filters)`

`exit = first_exit_event(family logic)`

This avoids over-abstracting and maps directly to current discovery/evaluation engines.

### 4.3 Candidate space control

- Per-family quotas.
- Max depth for entry/filter conjunctions.
- Min support thresholds.
- Early pruning by cheap metrics before expensive sim.

---

## 5) Market State Engine Design

### 5.1 Purpose

Create a single source of truth for market environment features used by:

- discovery slicing,
- robustness scoring,
- next-session/day strategy selector.

### 5.2 Snapshot schema (example)

`market_state_snapshot` (per day/session block):

- `timestamp` (tz-aware America/Denver)
- `session_label`
- `trend_state` (up/down/neutral + strength)
- `vol_state` (low/normal/high via ATR pctile)
- `regime_state` (trend/range/chop etc.)
- `location_vs_levels`:
  - dist to PDH/PDL/open/vwap/key levels
  - inside/outside prior range flags
- `microstructure` (optional later)

### 5.3 Placement

- Shared calculations can be held in `MarketDataStore` caches.
- Run-specific derived summaries go under `pkg.metadata["derived"]["strategy_discovery_v2"]["market_state"]`.

---

## 6) Reporting Design

Add new report sections (pure renderer only), each reading precomputed outputs:

1. `strategy_v2_candidate_leaderboard`
   - top candidates, OOS metrics, robustness rank.
2. `strategy_v2_robustness_matrix`
   - heatmap by regime/session/day-type.
3. `strategy_v2_overfit_diagnostics`
   - IS vs OOS degradation, fold dispersion, Monte Carlo tails, parameter sensitivity.
4. `strategy_v2_selector_recommendation`
   - next-session/day top-K recommended candidates + confidence + rationale features.

All sections must use ctx contract only and never run ingest or disk IO.

---

## 7) Overfit Risk, Validation, and Lookahead-Bias Controls (explicit)

### 7.1 Overfit controls

- Hard minimum trade counts by fold and by condition slice.
- Walk-forward with multiple folds (rolling or anchored), not single split only.
- Candidate complexity penalties (conditions count, parameter count, fragility).
- Parameter sensitivity: reject sharp-narrow optima (instability around best params).
- Monte Carlo sequence robustness checks on returns and DD profile.
- Holdout regime/session slices never used in candidate generation.

### 7.2 Lookahead-bias controls

- All features at trade decision time must be computed from information available **at or before** that timestamp.
- Daily level features must use only prior-known levels for intraday decisions (e.g., previous day H/L, session open, rolling VWAP up to t).
- Purged/embargoed walk-forward splits to avoid leakage around adjacent trades/bars.
- Strict temporal sort before split; no random shuffling for training/evaluation.
- Selector model trained only on historical snapshots preceding target session/day.

### 7.3 Data leakage checks to implement

- Automated assertion that every feature column has a declared availability timestamp rule.
- Audit report listing feature freshness and potential leakage flags.
- Fail-fast when unknown/undeclared feature provenance appears.

---

## 8) Practical Implementation Roadmap (incremental)

### Phase 0 — Stabilize contracts and schema (small change)

- Add `strategy_discovery_v2` config schema + defaults.
- Add candidate spec datamodel and JSON-safe serializer.
- Extend orchestrator with feature-flag gate, no behavior change when disabled.

### Phase 1 — Trigger/filter libraries + composition

- Implement initial trigger set: MA cross, ORB, breakout, continuation, reversion, Bollinger, level-based, basic candle patterns, initial FVG heuristics.
- Implement filter library (trend/vol/session/level/regime).
- Generate candidates with quotas + min-support pruning.

### Phase 2 — Exit library unification

- Wrap existing exit discovery families under unified exit interface.
- Add structure-based + time-based exits with conservative defaults.
- Ensure all exits evaluated under same cost model.

### Phase 3 — Validation hardening

- Upgrade walk-forward to purged + embargo option.
- Add fold-level reporting and automatic reject reasons.
- Add parameter-stability rejection rules.

### Phase 4 — Robust ranking

- Extend ranking to include cross-condition consistency and fragility penalties.
- Add robust composite objective as default deployment rank.

### Phase 5 — Market-state engine + selector

- Build daily/session market-state snapshots from current market features + daily levels.
- Train lightweight selector (calibrated probabilistic model or score-based ensemble) to choose top-K strategies for next session/day.
- Persist selector diagnostics for drift monitoring.

### Phase 6 — Reporting

- Add V2 sections for leaderboard, robustness, overfit diagnostics, selector recommendation.
- Keep all rendering pure, read from precomputed metadata/assets only.

---

## 9) Minimal first implementation target (recommended)

For fastest safe value, implement this first slice:

1. Candidate spec + generator with **3 trigger families** (`ma_cross`, `orb`, `level_setup`),
2. **2 filter families** (`session`, `regime`),
3. Existing fixed/ATR exits only,
4. Purged rolling walk-forward + Monte Carlo + ranking,
5. One selector output: next-session top-3 candidates with confidence,
6. One report section showing OOS + robustness + selector recommendation.

This delivers end-to-end usefulness quickly while minimizing risk and preserving framework stability.
