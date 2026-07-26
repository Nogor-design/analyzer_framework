# MA Strategy Analysis Capabilities

**Purpose**: Complete inventory of all moving-average-related analysis, entry discovery, reporting, and integration in the repo.

This document was generated from a discovery-only deep dive (following `docs/AI_WORKFLOW.md`): starting from CLAUDE.md, DOCS_INDEX.md, AI_REPO_INDEX.md, ANALYSIS_CAPABILITY_GUIDE.md, and AI_CAPABILITY_MAP.md, then narrow targeted inspection of the relevant subsystems only.

Date: 2026-06-17

---

## Overview

Moving average analysis exists in **two primary, distinct subsystems**:

1. **MA Anchor Interaction** (`analysis/ma_structure/`) — structural analysis of price behavior around MA anchors, segment detection, TP/SL recommendation, trade alignment, and regime context.
2. **MA Entry Discovery** (`analysis/entry_strategies/ma/` + `ma_sweep.py`) — signal generation (cross + pullback) with full parameter sweep, outcome simulation, hardening, and validation.

These are **not** the same thing:
- Anchor analysis is primarily about *context and structural levels* for stops/targets (can run with or without trades).
- Entry discovery is about *generating trade signals* from MA rules.

Both feed reports, strategy discovery, and NinjaTrader template generation (especially Pantheon-family strategies).

All analysis results are stored under `pkg.metadata["derived"]` (JSON-safe only) and/or `pkg.assets`.

---

## 1. MA Anchor Interaction Analysis (`analysis/ma_structure/`)

**Primary entry point**:
- `src/ta_foundation/analysis/ma_structure/orchestrator.py:run_anchor_interaction_analysis(pkg, market, options)`
- Called unconditionally early in CLI pipeline if config present (`cli/main.py:373` and `reports/html/config.py`).

**Invocation order** (CLAUDE.md): ingest → **MA anchor analysis** → pattern engine → strategy discovery → ...

**Key modules**:
- `orchestrator.py` — main coordinator, anchor resolution, bars extraction, assembly of results.
- `models.py` — `AnchorSpec`, `EngineConfig` (dataclasses).
- `anchors.py` — `build_anchors_table`, `compute_anchor_series` (SMA, EMA, WMA supported).
- `segment_detection.py` — `detect_segments` (cross, touch, recross, censored logic).
- `path_metrics.py` — path statistics around anchors.
- `tp_sl_engine.py` — `score_tp_sl_candidates`, fold validation (`build_validation_folds`).
- `trade_alignment.py` — `build_trade_recommendation_alignment`.
- `trade_time_tp_sl.py` — trade-time TP/SL simulation from actual backtest trades.
- `regime_context.py` — attaches regime labels to segments.
- `aggregation.py` — summaries by anchor and by anchor×regime.
- `settings_extractor.py` — extracts anchors from `pkg.settings` (see below).

**Modes** (EngineConfig):
- `discovery` (default): YAML-driven anchors only. No dependency on trades.
- `strategy_aware`: extracts from settings (with YAML fallback); enables trade-time simulation when trades present.

**Anchor resolution** (`resolve_anchors_for_run`):
- `anchor_source`: "yaml" | "settings" | "auto"
- Strategy-family aware extraction.

**Data produced** (in `pkg.metadata["derived"]["anchor_interaction"]` and `pkg.assets["anchor_interaction"]`):
- `anchors`, `segments`, `segment_path_stats`
- `summary_by_anchor`, `summary_by_anchor_regime`
- `tp_sl_candidates`, `recommendations`, `validation_folds`
- `trade_recommendation_alignment`
- `trade_time_candidates`, `trade_time_per_trade`
- Full engine config snapshot + diagnostics (n_bars, n_segments, censored %, etc.)

**Expected artifact keys** (orchestrator.py:19):
```python
EXPECTED_ARTIFACT_KEYS = (
    "anchors", "segments", "segment_path_stats",
    "summary_by_anchor", "summary_by_anchor_regime",
    "tp_sl_candidates", "recommendations", "validation_folds",
    "trade_recommendation_alignment",
    "trade_time_candidates", "trade_time_per_trade",
)
```

**Settings extraction** (`settings_extractor.py`):
- Registry via `@register_extractor("FamilyName")`.
- Primary support for Pantheon strategies:
  - `averageFast`, `averageSlow`, `averageTrend` (respects `UseTrend`).
  - Tolerant section/item matching (e.g. "Fast Averages", "averageFast", etc.).
  - Fallback to generic keyword scan ("fast", "slow", "trend").
- Registered for: PantheonMasterBotV01TesterV2, PantheonBotV2, PantheonMasterBot.

**Supported anchor families**: SMA, EMA, WMA.

**Report sections** (8 registered, some duplication noted):
- `anchor_interaction_config`
- `anchor_interaction_overview`
- `anchor_interaction_anchor_matrix`
- `anchor_interaction_tp_sl_spec`
- `anchor_interaction_diagnostics`
- `anchor_tp_sl_recommendations`
- `anchor_interaction_hourly_profile`
- `strategy_discovery_anchor_confluence` (cross-surface)

See `reports/html/sections/anchor_interaction_*.py` and `anchor_tp_sl_recommendations.py`.

**Config example** (from CLAUDE.md):
```yaml
anchor_interaction:
  enabled: true
  strategy_family: "SMA"
  anchors:
    - family: "SMA"
      length: 20
      source: "close"
```

---

## 2. MA Entry Strategy Discovery (`analysis/entry_strategies/ma/` + `ma_sweep.py`)

**Files**:
- `ma/features.py` — `compute_ma_features`, `ma_feature_column_names`
- `ma/signals.py` — `detect_ma_cross`, `detect_ma_pullback`, registry
- `ma_sweep.py` — `run_ma_discovery` (the orchestrator)

**Features** (lag-safe):
- `ma_{period}_{ema|sma}`
- `ma_{...}_dist` (close - ma) / ATR
- `ma_{...}_slope`
- `ma_{...}_above` (+1 / -1)
- ATR column

Default periods: [9, 20, 50]

**Signals**:
- `ma_cross`: price crosses the MA (with slope and distance guards).
- `ma_pullback`: trend confirmation + touch zone + close back on trend side + slope filter.

**Sweep** (`run_ma_discovery`):
- Resamples 1m bars to configured timeframes.
- Sweeps over signal types × period × ma_type × direction × filters × entry timing modes × outcome configs.
- Reuses:
  - `candle/signals.py` for entry timing (next_open, break_extreme, body_midpoint).
  - `outcome/simulator.py`
  - `strategy_discovery/evaluation.py`
  - hardening + IS/OOS validation.
- Results: `sweep_results`, `n_combinations_run`, `n_results`.

**YAML block** (documented at top of ma_sweep.py):
```yaml
ma_discovery:
  enabled: true
  timeframes: [1, 5]
  min_trades: 20
  signals:
    ma_cross:
      enabled: true
      period: [9, 20, 50]
      ma_type: [ema, sma]
      direction: 0
      min_slope: [0.0, 0.05]
      min_atr_dist_before: [0.0]
    ma_pullback:
      enabled: true
      ...
  entry_timing: ...
  outcome: ...
  filter_discovery: ...
```

**Storage**:
- `pkg.metadata["derived"]["ma_discovery"]`

**Report section**:
- `ma_discovery_overview` (`reports/html/sections/ma_discovery_overview.py`)

**Broader integration**:
- `unified_discovery_runner.py`
- Agent facade (`agent/facade.py`)
- Web: `discovery_stages.py`, `discovery_summary.py`, `report_builder.py`, capabilities, conditional_promotion
- Cross-family optimizer

---

## 3. Report Surfaces

Registered in `reports/html/registry.py`:

**Anchor**:
- `anchor_interaction_overview`, `anchor_interaction_config`, `anchor_interaction_anchor_matrix`, `anchor_interaction_tp_sl_spec`, `anchor_interaction_diagnostics`, `anchor_tp_sl_recommendations`, `anchor_interaction_hourly_profile`, `strategy_discovery_anchor_confluence`

**MA Discovery**:
- `ma_discovery_overview`

Also surfaces in unified strategy discovery views and many presets (e.g. `strategy_discovery_full`).

---

## 4. Pipeline / Invocation Points

**CLI** (`src/ta_foundation/cli/main.py`):
- `_find_anchor_interaction_config`
- Runs anchor analysis for every package before pattern engine.
- `_run_discovery_module("ma_discovery", ..., run_ma_discovery, ...)`

**Report builder** (`reports/html/config.py`):
- Also runs anchor analysis during report config processing.

**Web surfaces** expose both blocks separately.

---

## 5. NT / Pantheon / Strategy Template Integration

Strong coupling:

- Pantheon* strategies expose: `averageFast`, `averageSlow`, `averageTrend`, `UseTrend`, `UseTrendReverse`.
- `settings_extractor.py` pulls these into `AnchorSpec` (SMA) objects.
- `analysis/strategies/pantheon_bot_v2/param_map.py` and `template_writer.py` map:
  - `ma_discovery.signals.ma_cross.period` → `averageFast` + `averageSlow`
  - Trend period handling.
- `optimization/template_generator.py` and `result_intake.py` use `averageFast` / `averageSlow`.
- `nt_strategy_loop/` has `sma_cross` family smoke/authoring support.

MA parameters are a first-class part of recipe/seed/template generation for PantheonMaster family.

---

## 6. Supporting / Shared Components

- `analysis/features/regime.py`: `ema()`, `ema_slope()` (used by regime_recommender).
- `analysis/indicators/basic.py`: registered `ema` indicator.
- Prediction: SMA trend feature in `analogue_probability_agent.py`.
- Outcome simulation, hardening, validation, and evaluation are shared across entry families (including MA).
- MarketDataStore supplies the 1m bars (required for both anchor and ma_discovery).

---

## 7. Tests

**Dedicated**:
- `tests/analysis/ma_structure/`:
  - `test_orchestrator.py`
  - `test_tp_sl_engine.py`
  - `test_trade_alignment.py`
  - `tst.py`

**Cross-covered**:
- `tests/analysis/entry_strategies/test_entry_strategies.py` (MA features)
- `tests/analysis/entry_strategies/test_sweep_trial_grid.py` (imports `ma_sweep`)
- Pantheon template tests exercise ma_discovery → param mapping.
- General entry strategy hardening, validation, and multi-symbol tests.

---

## 8. Key Contracts Observed

- All derived outputs under `pkg.metadata["derived"]["anchor_interaction"]` or `["ma_discovery"]`.
- Timestamps must be tz-aware America/Denver.
- No DataFrames or non-JSON-safe data in metadata.
- Report sections are pure renderers (ctx only).
- Anchor analysis can run purely from market data (discovery mode).
- Settings extraction is the bridge from real NT backtest exports to anchor analysis.

---

## 9. Key Files (Fast Reference)

**Core Analysis**:
- `src/ta_foundation/analysis/ma_structure/orchestrator.py`
- `src/ta_foundation/analysis/ma_structure/settings_extractor.py`
- `src/ta_foundation/analysis/entry_strategies/ma/features.py`
- `src/ta_foundation/analysis/entry_strategies/ma/signals.py`
- `src/ta_foundation/analysis/entry_strategies/ma_sweep.py`

**Wiring**:
- `src/ta_foundation/cli/main.py`
- `src/ta_foundation/reports/html/config.py`
- `src/ta_foundation/reports/html/registry.py`
- `src/ta_foundation/analysis/entry_strategies/unified_discovery_runner.py`

**Reports**:
- `src/ta_foundation/reports/html/sections/anchor_*`
- `src/ta_foundation/reports/html/sections/ma_discovery_overview.py`
- `src/ta_foundation/reports/html/sections/strategy_discovery_anchor_confluence.py`

**Integration**:
- `src/ta_foundation/analysis/strategies/pantheon_bot_v2/`
- `src/ta_foundation/optimization/template_generator.py`

**Tests**:
- `src/ta_foundation/tests/analysis/ma_structure/`
- `src/ta_foundation/tests/analysis/entry_strategies/test_entry_strategies.py`

---

## Notes & Observations

- The two MA systems complement each other and are often used together (`anchor_interaction` + `ma_discovery` + `strategy_discovery_anchor_confluence`).
- PantheonMaster-family strategies are the primary real-world consumers of this MA machinery.
- Much of the heavy lifting (outcome sim, hardening, regime) is intentionally shared rather than duplicated per family.
- No dedicated pure "MA-only backtester" — MA is used as a component inside discovery, anchor analysis, and template generation.
- As of the source snapshot, `anchor_interaction_overview` appears twice in the registry (known minor duplication).

---

## How to Explore Further

1. Start with a report YAML containing both:
   ```yaml
   anchor_interaction: {enabled: true, ...}
   ma_discovery: {enabled: true, ...}
   ```
2. Feed `--market-data "D:\MarketData"` (or equivalent) and a run that has settings/trades.
3. Inspect `derived["anchor_interaction"]` and `derived["ma_discovery"]` in the resulting manifest / package.
4. Look at the corresponding report sections in the generated HTML.

**Canonical references** (per docs):
- `CLAUDE.md` (architecture + MA anchor description)
- `docs/ANALYSIS_CAPABILITY_GUIDE.md`
- `docs/AI_REPO_INDEX.md`

This file is the single authoritative summary of MA strategy analysis capabilities as of the discovery session.

---

*End of MA Strategy Analysis inventory.*
