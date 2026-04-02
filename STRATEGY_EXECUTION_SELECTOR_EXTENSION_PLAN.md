# ta_foundation Strategy Intelligence Extension (Concrete Architecture + YAML Design)

## 1) Current Project Audit

### 1.1 Core ingestion and ownership contracts (already implemented)

- `core/pipeline.py` already enforces the two data domains:
  - **run-scoped** artifacts go to `AnalysisPackage` keyed by `run_id`.
  - **shared market** artifacts go to `MarketDataStore` with `run_id=None`.
- Pipeline metadata already records timezone policy as `America/Denver` and localizes canonical run datetimes on ingest.
- Shared market ingestion already handles minute/tick sources, parser dispatching, and caching paths.

### 1.2 Existing strategy/discovery stack

- `analysis/strategy_discovery/orchestrator.py` already coordinates discovery phases and writes derived outputs into `pkg.metadata["derived"]`.
- Existing modules provide reusable foundations:
  - `entry_discovery.py`: rule/condition entry search.
  - `filter_discovery.py`: exclusion filter discovery.
  - `exit_discovery.py`: exit family sweeps via existing simulation.
  - `validation.py`: walk-forward, significance, Monte Carlo, cost normalization.
  - `evaluation.py`, `risk_metrics.py`, `ranking.py`, `regime.py`, `features.py`.

### 1.3 Existing indicator and pattern capability

- Indicator registry exists (`analysis/indicators/registry.py`, `basic.py`).
- Pattern engine exists (`analysis/pattern_engine/*`) with templates and discovery/diagnostics.
- Existing `trade_pattern_audit` bridge can feed pattern features into discovery.

### 1.4 Existing market context / regime capability

- `analysis/regime_recommender/*` already computes multi-timeframe features, classifies regime, and recommends parameter bundles.
- `analysis/features/regime.py`, `market_regime_store.py`, and discovery regime modules already provide reusable context primitives.

### 1.5 Existing YAML/report flow

- YAML is loaded in `reports/html/config.py` (`load_report_config` and multi-report support).
- Report flow is already correct and must remain unchanged:
  - `report.yaml` -> `load_report_config` -> `build_report_from_config(packages, cfg, market)` -> `HtmlReportBuilder.build(context)` -> section renderers.
- Section registry pattern already exists in `reports/html/registry.py`.

### 1.6 Existing reporting and batch analysis surface

- HTML sections are modular and config-driven via section IDs/options.
- Cross-run ranking/overview and strategy-discovery sections already exist.
- Batch analysis is naturally supported through multi-run ingest and comparison report generation.

### 1.7 Audit conclusion

The project is **not greenfield**; most required capability exists in partial form. The extension should:
- add a concrete component catalog and strategy-spec model,
- strengthen composition/evaluation wiring,
- unify market-state snapshots,
- and add selector/reporting outputs,
while preserving current contracts and flow.

---

## 2) Reusable Components

### Reuse directly (no redesign)

1. **Ingest + data ownership**
   - `core/pipeline.py`
   - `marketdata/store.py`
2. **Current discovery/evaluation**
   - `analysis/strategy_discovery/{features,entry_discovery,filter_discovery,exit_discovery,validation,evaluation,ranking}.py`
3. **Regime + recommendation seeds**
   - `analysis/regime_recommender/*`
4. **Pattern/entry signal seeds**
   - `analysis/pattern_engine/*`
5. **Report config + registry + section rendering contracts**
   - `reports/html/config.py`
   - `reports/html/registry.py`
   - `reports/html/sections/*`

### Generalize (incremental)

- Generalize current entry/filter/exit discovery into catalog-driven components.
- Generalize regime snapshot into a unified `market_state` structure used by both discovery and next-session selector.
- Generalize rankings into robustness-aware cross-condition scoring.

### Do not touch yet

- No parser redesign.
- No report-builder rewrite.
- No CLI behavior redesign.
- No global state introduction.

---

## 3) Gaps to Fill

1. No canonical component catalog format for entry/filter/exit/risk.
2. No formal `StrategySpec` object with compatibility and provenance fields.
3. Limited explicit support for many entry families (FVG, ORB variants, wick rejection, squeeze).
4. Risk model layer is implicit; needs explicit pairing and normalized R-unit outputs.
5. Market-state features are scattered across modules; need standardized snapshot schema.
6. Next-session/day strategy selector needs explicit, interpretable v1 design.
7. Reporting needs explicit component catalog, filter impact, exit pairing, robustness, and recommendation diagnostics.

---

## 4) Proposed Architecture

Keep existing layers. Add/extend **analysis helpers** and **report sections** only.

### A) Component Catalog Layer (analysis)

Create registry-driven catalogs for:
- entries,
- filters,
- exits,
- risk models,
- regime classifiers,
- market-state feature families.

### B) Strategy Composition Layer (analysis)

Build deterministic `StrategySpec` objects from compatible component combinations:
- entry + filters + exit + risk + session/regime eligibility.

### C) Evaluation Layer (analysis)

Use current backtest/sim/validation stack with normalized outputs:
- absolute PnL + R-multiple metrics,
- IS/OOS + WF fold diagnostics,
- regime/session breakdowns,
- robustness flags.

### D) Market State Layer (analysis)

Produce reusable, timestamped `market_state_snapshot` rows from bars + daily levels + higher-timeframe context.

### E) Strategy Intelligence Layer (analysis)

Map market state -> eligible strategies -> ranked recommendations.

### F) Reporting Layer (report sections)

Render precomputed outputs only (no IO/heavy compute in sections).

### G) NinjaTrader Replication Layer (export/codegen artifacts from analysis)

Add a **code generation helper** under `analysis/` that exports NinjaTrader-ready indicator and strategy code from discovered component specs.  
This is not a new runtime layer in ta_foundation; it is an analysis-side artifact exporter.

Primary goals:
- generate NT indicator code for filter/entry primitives,
- generate NT strategy code that composes those indicators/rules,
- preserve parameter parity between Python discovery and NT execution,
- allow side-by-side validation between historical research output and NT runtime behavior.

---

## 5) Component Interfaces

All components should use lightweight protocol/dataclass contracts in `analysis/strategy_discovery/`.

### 5.1 Entry Trigger Interface

```python
class EntryTrigger(Protocol):
    entry_id: str
    family: str
    version: str

    def required_columns(self) -> list[str]: ...

    def emit_signals(
        self,
        bars: pd.DataFrame,
        features: pd.DataFrame,
        params: dict[str, Any],
    ) -> pd.DataFrame:
        """
        returns columns:
          signal_long: bool
          signal_short: bool
          signal_strength: float (0..1, optional)
          trigger_meta: dict-like/json-safe optional
        index aligned to bars/features dt.
        """
```

Required metadata fields:
- `entry_id`, `family`, `label`, `description`, `direction_support`, `default_params`, `param_space`, `tags`.

Validation:
- required columns present,
- params within declared bounds,
- no future-looking feature dependencies.

### 5.2 Filter Interface

```python
class StrategyFilter(Protocol):
    filter_id: str
    family: str

    def required_columns(self) -> list[str]: ...

    def evaluate(
        self,
        bars: pd.DataFrame,
        features: pd.DataFrame,
        params: dict[str, Any],
    ) -> pd.Series:
        """bool eligibility mask aligned to dt"""
```

Validation:
- monotonic timestamp alignment,
- no lookahead fields,
- minimum support threshold checks.

### 5.3 Exit Strategy Interface

```python
class ExitPolicy(Protocol):
    exit_id: str
    family: str

    def required_columns(self) -> list[str]: ...

    def build_policy(self, params: dict[str, Any]) -> Any:
        """returns adapter object compatible with existing exit simulator"""
```

Validation:
- positive stop distance,
- compatible with entry direction mode,
- session-close flatten behavior explicit.

### 5.4 Risk Model Interface

```python
class RiskModel(Protocol):
    risk_id: str
    family: str

    def size_and_levels(
        self,
        signal_row: pd.Series,
        bars: pd.DataFrame,
        features: pd.DataFrame,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        returns:
          stop_price / stop_ticks
          target_price / target_ticks(optional)
          unit_size
          r_value_ticks
        """
```

Validation:
- finite stop distance,
- normalized `r_value_ticks > 0`,
- optional contract normalization.

### 5.5 Market State Feature Interface

```python
class MarketStateFeatureFamily(Protocol):
    feature_family_id: str

    def compute(
        self,
        bars: pd.DataFrame,
        features: pd.DataFrame,
        levels: pd.DataFrame | None,
        params: dict[str, Any],
    ) -> pd.DataFrame:
        """returns timestamp-aligned state columns"""
```

Validation:
- explicit source timeframe,
- explicit feature availability timestamp,
- leakage annotation.

### 5.6 Strategy Selector Interface

```python
class StrategySelector(Protocol):
    selector_id: str
    mode: str  # rule_based|score_based|ml_ready

    def recommend(
        self,
        market_state: dict[str, Any],
        candidates: pd.DataFrame,
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """top-k strategy recommendations + rationale + confidence"""
```

Validation:
- only uses pre-target historical stats,
- emits confidence and risk flags,
- emits fallback when confidence low.

### 5.8 NinjaTrader code generation interface

```python
class NinjaScriptExporter(Protocol):
    exporter_id: str
    version: str

    def export_indicator(
        self,
        component_spec: dict[str, Any],
        out_dir: Path,
    ) -> dict[str, Any]:
        """returns artifact metadata for generated .cs indicator file(s)"""

    def export_strategy(
        self,
        strategy_spec: dict[str, Any],
        out_dir: Path,
    ) -> dict[str, Any]:
        """returns artifact metadata for generated .cs strategy file(s)"""
```

Required metadata for each generated artifact:
- `artifact_type` (`indicator` or `strategy`)
- `artifact_name`
- `component_ids`/`strategy_id`
- `param_map` (Python parameter name -> NinjaScript property name)
- `code_hash`
- `generated_at` (tz-aware timestamp string)
- `ninjatrader_target` (e.g., `NT8`)

### 5.7 Entry family definitions (measurable/parameterized)

| Family | Rule structure | Required fields | Timing | Direction logic | Key params |
|---|---|---|---|---|---|
| MA cross | fast MA crosses slow MA, optional slope gate | close, ma_fast, ma_slow | bar close | long on cross_up, short on cross_down | fast_period, slow_period, slope_min |
| FVG entry | identify 3-bar imbalance, enter revisit into gap | high, low, close | on revisit bar | side by gap direction | min_gap_ticks, max_revisit_bars |
| FVG rejection | revisit then reject from FVG boundary | FVG bounds + wick/body stats | rejection bar close | direction away from rejected boundary | rejection_wick_ratio, confirm_bars |
| Candle pattern | bullish/bearish engulfing/pin/etc. | OHLC + body/wick metrics | bar close | pattern-defined | pattern_set, min_body_pct |
| Breakout | break prior range/level with buffer | range high/low, levels, ATR | breakout bar close or stop-entry | both | lookback_bars, buffer_ticks, vol_confirm |
| Continuation | pullback in trend then resume | trend state + pullback depth | trigger bar | trend-following | trend_len, pullback_atr_max |
| Reversion | extreme extension then mean reversion | zscore/bands/ATR distance | reversal confirmation | toward mean | z_thresh, confirm_type |
| Level reaction | touch/reject PDH/PDL/ONH/ONL/VWAP | level distances, wick/body | touch+confirm | away from level | level_set, proximity_ticks |
| Volatility squeeze | low BB width then expansion trigger | BB width pctile, range expansion | post-squeeze breakout | both | width_pctile_max, release_confirm |
| ORB | opening range breakout/retest | OR high/low | after OR window end | both | or_minutes, retest_required, buffer_ticks |

Compatibility notes:
- Reversion entries should default to reversion-friendly exits (time/indicator) and filtered against high-trend regimes.
- Breakout/continuation entries should prefer trend/vol expansion filters and structure/ATR trailing exits.

---

## 6) Strategy Object Design

Canonical object (JSON-safe):

```yaml
strategy_id: "strat_nq_5m_orb_breakout_sess_us_open_v1"
family: "orb_breakout"
entry_id: "entry_orb_breakout"
filter_ids: ["flt_trend_adx", "flt_session_us_open", "flt_level_outside_pdr"]
exit_id: "exit_atr_trail"
risk_id: "risk_atr_stop_r2"
symbols: ["NQ"]
timeframes: ["5m"]
direction_support: ["long", "short"]
session_constraints: ["us_open"]
regime_constraints: ["trend", "expansion"]
params:
  entry: {...}
  filters: {...}
  exit: {...}
  risk: {...}
metadata:
  created_from: "strategy_discovery_v2"
  version: "v1"
  tags: ["orb", "trend", "intraday"]
```

Flow:
1. Generator builds candidate `StrategySpec`.
2. Validator checks compatibility + support + leakage guard.
3. Evaluator runs simulation + validation (IS/OOS/WF/MC).
4. Aggregator computes regime/session/day metrics.
5. Ranker outputs robustness-ranked table.
6. Selector consumes ranked table + current market state.
7. Report sections render spec + results + recommendation.

---

## 7) YAML Schema

Top-level block (backward compatible):

```yaml
strategy_discovery_v2:
  enabled: true
  research_scope: {}
  catalogs: {}
  generation: {}
  evaluation: {}
  market_state: {}
  selector: {}
  outputs: {}
```

Validation/default rules:
- If `strategy_discovery_v2.enabled` is false or missing -> no new behavior.
- Missing sub-blocks use defaults.
- Unknown component IDs -> warning + skip (not crash).
- All time/session values interpreted in `America/Denver`.
- Any naive datetime in derived outputs -> validation error.

Backward compatibility:
- Keep existing `strategy_discovery` block untouched.
- Allow side-by-side execution for transition (`strategy_discovery` and `strategy_discovery_v2`).

NinjaTrader compatibility notes:
- Codegen must be optional (`ninjatrader_export.enabled: false` by default).
- Generated C# files are run-scoped artifacts and should be attached via `pkg.assets` paths/metadata only (not as shared market artifacts).
- Selector/report behavior must not depend on codegen being enabled.

---

## 8) Minimal YAML Example

```yaml
report:
  title: "Strategy Intelligence V2 (Minimal)"
  output_filename: "strategy_intel_minimal.html"

strategy_discovery_v2:
  enabled: true

  research_scope:
    symbols: ["NQ"]
    contract: "M26"
    timeframe: "5m"
    directions: ["long", "short"]
    sessions: ["us_open"]

  catalogs:
    entries:
      enabled: ["entry_ma_cross", "entry_orb_breakout"]
    filters:
      enabled: ["flt_trend_adx", "flt_session_us_open"]
    exits:
      enabled: ["exit_fixed_rr", "exit_atr_trail"]
    risks:
      enabled: ["risk_fixed_ticks", "risk_atr_stop"]

  generation:
    raw_entries: true
    one_filter_variants: true
    max_filter_combo: 1
    max_candidates_total: 200
    min_trades_per_candidate: 40

  evaluation:
    cost_model:
      commission_per_side: 2.09
      slippage_ticks: 1
      tick_value: 5.0
    walk_forward:
      wf_type: "rolling"
      is_pct: 0.70
      n_folds: 5
      min_is_trades: 60
      min_oos_trades: 30

  selector:
    mode: "rule_based"
    top_k: 3

sections:
  - id: strategy_discovery_overview
  - id: strategy_discovery_ranked_table
  - id: strategy_v2_selector_recommendation
```

---

## 9) Full YAML Example

```yaml
report:
  title: "Strategy Intelligence V2 (Full)"
  output_filename: "strategy_intelligence_v2.html"
  timezone: "America/Denver"

strategy_discovery_v2:
  enabled: true

  research_scope:
    symbols: ["NQ", "MNQ"]
    contract: "M26"
    timeframe: "5m"
    higher_timeframes: ["15m", "60m"]
    sessions: ["asia", "london", "us_open", "us_lunch", "us_close"]
    date_range:
      start: "2024-01-01"
      end: "2026-03-01"
    directions: ["long", "short"]

  catalogs:
    entries:
      enabled:
        - entry_ma_cross
        - entry_fvg_entry
        - entry_fvg_rejection
        - entry_candle_pattern
        - entry_breakout
        - entry_continuation
        - entry_reversion
        - entry_level_reaction
        - entry_vol_squeeze
        - entry_orb_breakout
      params:
        entry_ma_cross:
          fast_period: [9, 20]
          slow_period: [50, 100]
          slope_min: [0.0, 0.05]
        entry_fvg_entry:
          min_gap_ticks: [3, 5]
          max_revisit_bars: [8, 20]
        entry_fvg_rejection:
          min_gap_ticks: [3]
          rejection_wick_ratio: [1.2, 1.8]
          confirm_bars: [1, 2]
        entry_candle_pattern:
          pattern_set: ["engulfing", "pin", "outside"]
          min_body_pct: [0.45, 0.60]
        entry_breakout:
          lookback_bars: [20, 40]
          buffer_ticks: [0, 2]
          vol_confirm: [true]
        entry_continuation:
          trend_len: [10, 20]
          pullback_atr_max: [0.7, 1.2]
        entry_reversion:
          z_thresh: [1.5, 2.2]
          confirm_type: ["close_back_inside_band"]
        entry_level_reaction:
          level_set: ["prev_day_hl_vwap", "overnight_hl"]
          proximity_ticks: [4, 8, 12]
        entry_vol_squeeze:
          width_pctile_max: [0.15, 0.25]
          release_confirm: ["range_break"]
        entry_orb_breakout:
          or_minutes: [5, 15, 30]
          retest_required: [false, true]
          buffer_ticks: [0, 2]

    filters:
      enabled:
        - flt_trend_direction
        - flt_trend_strength
        - flt_htf_alignment
        - flt_vol_regime
        - flt_atr_pctile
        - flt_bb_width_pctile
        - flt_session
        - flt_day_of_week
        - flt_vwap_location
        - flt_ma_location
        - flt_near_prior_day_levels
        - flt_near_overnight_levels
        - flt_inside_outside_pdr
        - flt_breakout_confirm
        - flt_retest_confirm
        - flt_candle_quality
        - flt_regime_tag
      params:
        flt_trend_direction: { mode: ["up_only", "down_only"] }
        flt_trend_strength: { adx_min: [20, 25, 30] }
        flt_htf_alignment: { tf: ["15m", "60m"], require_ma_stack: [true] }
        flt_vol_regime: { allowed: ["normal", "high"] }
        flt_atr_pctile: { min: [0.25], max: [0.95] }
        flt_bb_width_pctile: { min: [0.10], max: [0.90] }
        flt_session: { allow: ["london", "us_open", "us_lunch"] }
        flt_day_of_week: { allow: [1, 2, 3, 4, 5] }
        flt_vwap_location: { side: ["above", "below"] }
        flt_ma_location: { ma_period: [20, 50], side: ["above", "below"] }
        flt_near_prior_day_levels: { max_dist_ticks: [6, 12] }
        flt_near_overnight_levels: { max_dist_ticks: [6, 12] }
        flt_inside_outside_pdr: { mode: ["inside", "outside"] }
        flt_breakout_confirm: { min_close_pct_of_range: [0.6] }
        flt_retest_confirm: { lookback_bars: [5, 10] }
        flt_candle_quality: { min_body_pct: [0.5], max_opposite_wick_pct: [0.25] }
        flt_regime_tag: { allow: ["trend", "expansion", "reversion"] }

    exits:
      enabled:
        - exit_fixed_rr
        - exit_r_multiple
        - exit_atr_static
        - exit_atr_trail
        - exit_structure
        - exit_ma_crossback
        - exit_opposite_signal
        - exit_time_stop
        - exit_session_flatten
      params:
        exit_fixed_rr:
          stop_ticks: [8, 12, 16]
          target_ticks: [12, 20, 32]
        exit_r_multiple:
          stop_r: [1.0]
          target_r: [1.5, 2.0, 3.0]
        exit_atr_static:
          stop_atr: [0.8, 1.2, 1.6]
          target_atr: [1.5, 2.0, 3.0]
        exit_atr_trail:
          stop_atr: [1.0, 1.25]
          trail_atr: [1.5, 2.0, 2.5]
        exit_structure:
          pivot_lookback: [5, 10, 20]
          buffer_ticks: [1, 2]
        exit_ma_crossback:
          ma_fast: [9, 20]
          ma_slow: [20, 50]
        exit_opposite_signal:
          source_entry_family: ["same_family"]
        exit_time_stop:
          max_hold_min: [15, 30, 60]
        exit_session_flatten:
          flatten_minutes_before_close: [0, 5]

    risks:
      enabled:
        - risk_fixed_ticks
        - risk_atr_stop
        - risk_candle_structure
        - risk_swing_structure
        - risk_unit_1_contract
      params:
        risk_fixed_ticks: { stop_ticks: [8, 12, 16] }
        risk_atr_stop: { stop_atr: [1.0, 1.5] }
        risk_candle_structure: { buffer_ticks: [1, 2] }
        risk_swing_structure: { swing_lookback: [5, 10], buffer_ticks: [1, 2] }
        risk_unit_1_contract: { contracts: [1] }

  generation:
    raw_entries: true
    one_filter_variants: true
    filter_combo_variants: true
    max_filter_combo: 2
    compatible_exit_only: true
    compatible_risk_only: true
    family_quota:
      breakout_like: 400
      reversion_like: 300
      session_orb: 300
    max_candidates_total: 1500
    min_trades_per_candidate: 50
    de_duplicate:
      enabled: true
      signature_fields: ["entry_family", "core_params", "filter_core", "exit_core"]

  evaluation:
    cache:
      enabled: true
      key_fields: ["strategy_id", "symbol", "timeframe", "date_range"]
    cost_model:
      commission_per_side: 2.09
      slippage_ticks: 1
      tick_value: 5.0
    normalization:
      compute_r_multiple: true
      compare_on_r_units: true
    walk_forward:
      wf_type: "rolling"
      n_folds: 8
      is_pct: 0.70
      min_is_trades: 80
      min_oos_trades: 40
      purge_bars: 5
      embargo_bars: 5
    significance:
      ttest_alpha: 0.05
      min_effect_size_r: 0.05
    monte_carlo:
      n_sims: 2000
      dd_percentile_limit: 95
    robustness:
      min_regimes_covered: 2
      max_single_session_trade_share: 0.70
      max_fold_dispersion_pf: 0.80

  market_state:
    enabled: true
    trend:
      ma_stack_periods: [20, 50, 200]
      slope_lookback: 20
    volatility:
      atr_period: 14
      atr_pctile_window: 120
      bb_period: 20
      bb_std: 2.0
      bb_width_pctile_window: 120
    levels:
      include:
        - prior_day_high
        - prior_day_low
        - prior_day_close
        - overnight_high
        - overnight_low
        - vwap_session
        - opening_range_high
        - opening_range_low
      proximity_ticks: [4, 8, 12]
    session_structure:
      session_windows:
        asia: ["17:00", "00:00"]
        london: ["00:00", "06:30"]
        us_open: ["06:30", "09:30"]
        us_lunch: ["09:30", "12:00"]
        us_close: ["12:00", "15:00"]

  selector:
    enabled: true
    mode: "score_based"
    horizon: "next_session"
    top_k: 3
    min_confidence: 0.55
    score_weights:
      oos_quality: 0.35
      robustness: 0.25
      market_state_match: 0.30
      implementation_simplicity: 0.10
    penalties:
      low_sample: 0.20
      unstable_fold: 0.15
      regime_concentration: 0.15
    fallback:
      action: "no_trade"
      if_confidence_below: 0.45

  outputs:
    persist_candidate_table: true
    persist_fold_metrics: true
    persist_market_state_history: true
    persist_selector_decisions: true

sections:
  - id: strategy_discovery_overview
  - id: strategy_discovery_ranked_table
  - id: strategy_v2_component_catalog
  - id: strategy_v2_filter_impact
  - id: strategy_v2_exit_pairing
  - id: strategy_v2_regime_mapping
  - id: strategy_v2_robustness
  - id: strategy_v2_selector_recommendation
```

Add this optional export block (same config root):

```yaml
strategy_discovery_v2:
  ninjatrader_export:
    enabled: true
    target: "NT8"
    output_dir: "outputs/ninjatrader"
    namespace: "TAFoundation.Generated"
    export_indicators: true
    export_strategies: true
    include_component_indicators:
      - entry_ma_cross
      - entry_orb_breakout
      - flt_trend_strength
      - flt_vwap_location
    strategy_template:
      name_prefix: "TF_"
      include_long_short_toggles: true
      include_session_filter: true
      include_regime_filter: true
    parity_checks:
      enabled: true
      compare_fields:
        - entry_timestamp
        - direction
        - stop_ticks
        - target_ticks
      max_signal_mismatch_rate: 0.02
```

---

## 10) Pipeline Design

1. **Load market and run data**
   - Input: existing parsers + `core/pipeline.py`.
   - Output: `packages`, `market`.
   - Reuse: current ingestion unchanged.
   - Bias risk: timezone mishandling; validate tz-aware dt.

2. **Build indicator/context features**
   - Input: shared bars + run trades.
   - Reuse: `strategy_discovery/features.py`, indicators registry.
   - Cache: per symbol/contract/timeframe feature table.
   - Bias risk: future-window indicators.

3. **Build daily/session levels**
   - Input: bars.
   - Output: level table (PDH/PDL/ONH/ONL/ORB/VWAP).
   - Cache: per day/session level snapshot.
   - Bias risk: using day-end levels intraday.

4. **Generate entry signals**
   - Input: entry catalog + feature tables.
   - Output: signal masks and metadata.
   - Validation: required columns + no-leak feature set.

5. **Apply filters**
   - Input: filter catalog + entry signal masks.
   - Output: eligible-entry masks by combination.
   - Explosion control: filter depth/quotas.

6. **Attach exits and risk models**
   - Input: eligible entries + exit/risk catalogs.
   - Output: candidate strategy specs.
   - Explosion control: compatibility matrix + quotas.

7. **Evaluate candidates**
   - Reuse: validation/evaluation/exit simulator modules.
   - Output: standardized candidate metrics with IS/OOS/WF.

8. **Aggregate by market condition**
   - Group by symbol/timeframe/session/regime/level-location states.

9. **Rank and prune**
   - Robust composite scoring, fragility penalties, support thresholds.

10. **Build market-state -> strategy mapping**
    - Use historical market-state snapshots + candidate outcomes.

11. **Read current market-state snapshot**
    - Built from latest bars/levels in `MarketDataStore`.

12. **Select next-session/day strategies**
    - Selector ranks eligible set and returns top-k + confidence/rationale.

13. **Report and export**
    - Write derived outputs into metadata/assets.
    - Render via new sections.
    - Optionally generate NinjaTrader indicator/strategy `.cs` files for selected components/candidates.

---

## 11) Market State Engine

### Feature families and intent

1. **Trend state**
   - Compute: MA stack order + slope + ADX.
   - TF: 5m + 15m/60m alignment.
   - Predicts: continuation viability.
   - Leakage risk: future bars in slope window.
   - Usage: selection + reporting.

2. **Volatility state**
   - Compute: ATR percentile, BB width percentile, range expansion ratio.
   - TF: execution TF + optional HTF normalization.
   - Predicts: breakout vs reversion suitability.
   - Leakage risk: percentile windows including future rows.
   - Usage: selection + reporting.

3. **Location vs levels**
   - Compute: distance to PDH/PDL/PDC/ONH/ONL/VWAP/ORB levels.
   - TF: intraday bars.
   - Predicts: rejection/breakout response likelihood.
   - Leakage risk: levels not known at decision time.
   - Usage: both.

4. **Session structure**
   - Compute: session label, minutes since open, OR status, session range expansion.
   - Predicts: time-dependent edge concentration.
   - Leakage risk: using session-close data early.
   - Usage: both.

5. **Balance/imbalance + compression/expansion**
   - Compute: short-term rotation metrics, overlap %, realized expansion bursts.
   - Predicts: reversion vs momentum preference.
   - Leakage risk: symmetric windows around current bar.
   - Usage: selection primary.

6. **Breakout/reversion tendency tags**
   - Compute: rolling hit-rate estimates by condition bins (historical only).
   - Predicts: family-level suitability.
   - Leakage risk: contamination with target period.
   - Usage: selector only.

### Output snapshot schema

```yaml
market_state_snapshot:
  asof_dt: "tz-aware America/Denver"
  symbol: "NQ"
  timeframe: "5m"
  trend_state: {dir: "up", strength: 0.72, adx: 27.1, htf_align: true}
  vol_state: {atr_pctile: 0.68, bb_width_pctile: 0.52, expansion: "normal"}
  level_location:
    dist_pdh_ticks: 6
    dist_pdl_ticks: 44
    dist_vwap_ticks: -3
    inside_prior_day_range: true
  session_state: {label: "us_open", minutes_from_open: 22, or_complete: true}
  structure_state: {compression: "low", imbalance: "moderate"}
  regime_tags: ["trend", "expansion"]
```

---

## 12) Strategy Selector Design

### V1 (practical, interpretable)

Mode: `rule_based` or `score_based`.

Algorithm:
1. Filter candidate pool by hard eligibility:
   - symbol/timeframe/session/regime constraints,
   - min support and min OOS performance,
   - robustness pass flags.
2. Compute composite score:
   - `score = w1*oos_quality + w2*robustness + w3*state_match + w4*simplicity - penalties`.
3. Return top-k with:
   - confidence,
   - rationale components,
   - avoid-list (high fragility under current state).

Output contract:

```yaml
selector_output:
  asof_dt: "tz-aware"
  horizon: "next_session"
  recommended:
    - strategy_id: "..."
      score: 78.4
      confidence: 0.66
      rationale:
        state_match: ["trend_up", "atr_pctile_high", "outside_pdr"]
        strengths: ["oos_pf>1.4", "regime_coverage>=3"]
        risks: ["session concentration 62% us_open"]
  avoid:
    - strategy_id: "..."
      reason: "reversion strategy in high-trend expansion"
  fallback: "no_trade"
```

### V2/V3 evolution path

- V2: supervised ranking model on historical `(market_state, strategy_id) -> outcome_bin`.
- V3: contextual bandit / online adaptation with exploration guardrails.
- Keep same selector interface so downstream reporting remains stable.

---

## 13) Reporting Design

Add section IDs (pure render only):

1. `strategy_v2_component_catalog`
   - lists active entry/filter/exit/risk components, params, status.
2. `strategy_v2_candidate_report`
   - strategy specs + key metrics + support + constraints.
3. `strategy_v2_filter_impact`
   - per entry family: raw vs +filter lifts and degradation cases.
4. `strategy_v2_exit_pairing`
   - best exit families by entry family and state.
5. `strategy_v2_regime_mapping`
   - heatmap/table of performance by regime/session/location bins.
6. `strategy_v2_robustness`
   - fold dispersion, MC drawdown tails, concentration warnings.
7. `strategy_v2_selector_recommendation`
   - current state summary, recommendations, confidence/rationale, avoid-list.
8. `strategy_v2_ninjatrader_export_manifest`
   - generated indicators/strategies, parameter map, code hash, parity-check status.

All data source contract:
- Read from `pkg.metadata["derived"]["strategy_discovery_v2"]` and optional `pkg.assets["strategy_discovery_v2"]`.
- Never parse files or YAML in section renderers.

---

## 14) Implementation Roadmap

### Phase A — Audit + contracts + YAML skeleton

- Purpose: freeze interfaces and config behavior.
- Add/modify:
  - `analysis/strategy_discovery/models_v2.py` (spec dataclasses)
  - `analysis/strategy_discovery/config_v2.py` (defaults/validation)
  - `report.yaml` example block docs only
- Dependencies: existing orchestrator/config loader.
- Testing: schema parse tests + default merge tests.
- Risks: over-config complexity.

### Phase B — Component catalogs (initial families)

- Add:
  - `entry_catalog.py`, `filter_catalog.py`, `exit_catalog.py`, `risk_catalog.py`
  - `entries_v2.py`, `filters_v2.py`, `exits_v2.py`, `risk_v2.py`
- Implement initial concrete families (MA/ORB/breakout/reversion + core filters + fixed/ATR exits + fixed/ATR risk).
- Testing: deterministic signal/eligibility unit tests.
- Risks: inconsistent column naming.

### Phase C — Composition + evaluation integration

- Add:
  - `composition_v2.py`, `candidate_generator_v2.py`, `evaluator_v2.py`
- Reuse existing `validation.py`, `evaluation.py`, `exit_discovery` simulator adapters.
- Testing: integration on sample runs; ensure JSON-safe outputs.
- Risks: combinatorial explosion, runtime.

### Phase D — Market-state engine + mapping

- Add:
  - `market_state_v2.py`, `regime_mapping_v2.py`
- Produce historical state snapshots and strategy-condition mapping tables.
- Testing: leakage assertions and timestamp availability checks.
- Risks: hidden lookahead from level construction.

### Phase E — Selector v1 + report sections

- Add:
  - `selector_v1.py`
  - report sections for catalog/impact/robustness/recommendation
  - section registrations in registry + report yaml templates
- Testing: selector decision reproducibility + section rendering smoke tests.
- Risks: confidence calibration drift.

### Phase F — NinjaTrader exporter + parity validation

- Add:
  - `analysis/strategy_discovery/ninjatrader_export.py`
  - `analysis/strategy_discovery/ninjatrader_templates.py`
  - optional report section renderer for export manifest
- Implement:
  - component indicator code generation
  - composed strategy code generation from `StrategySpec`
  - parity-check summary attachment in derived metadata
- Testing:
  - golden-file codegen tests
  - parity-check fixture tests (Python signals vs expected NT signals on sampled windows)
- Risks:
  - semantic drift between Python and NinjaScript indicator math
  - session/timezone mismatch in external NT execution environment

### Phase G — V2/V3 enhancement track

- V2: supervised selector, richer risk sizing.
- V3: adaptive/bandit layer with strict safety gates.
- Testing: offline replay / shadow-mode evaluation.

---

## 15) Risks and Validation Concerns

1. **Combinatorial explosion**
   - Control with quotas, max filter depth, compatibility matrices, early pruning.
2. **Lookahead bias**
   - Enforce feature availability timestamps, purge/embargo WF, strict temporal sorting.
3. **Leakage in market-state features**
   - Require per-feature provenance metadata and leak checks.
4. **Overfit due to small samples**
   - Minimum trade thresholds by candidate/fold/regime bucket + penalties.
5. **Redundant near-identical strategies**
   - Signature-based dedup and correlation clustering.
6. **False precision in regime labels**
   - Use coarse, stable bins; treat regime labels probabilistically in selector scoring.
7. **Too many weak filters**
   - Rank filters by marginal lift and stability, drop non-generalizing filters.
8. **Excessive parameter search**
   - bounded grids, family budgets, sensitivity penalties.
9. **Poor OOS validation**
   - require multi-fold OOS retention and dispersion limits.
10. **Selector trained on unstable rankings**
   - only learn from strategies passing robustness stability gates.

---

## 16) Recommended V1 vs Later V2/V3 Enhancements

### V1 (ship first)

- Entry families: MA cross, ORB, breakout, reversion, level reaction.
- Filters: trend strength, session, ATR percentile, level location, regime tag.
- Exits: fixed RR + ATR trail + time stop + session flatten.
- Risks: fixed ticks + ATR stop (1-contract normalization + R-multiple reporting).
- Selector: score-based top-3 with confidence + fallback `no_trade`.
- Reporting: candidate table, filter impact, exit pairing, robustness, recommendation.
- NinjaTrader: export top validated strategies and core indicator components for live-replication testing.

### V2

- Add FVG variants, candle-quality families, squeeze subtypes.
- Add structure/swing risk sizing and partial exit runner variants.
- Add supervised selector in shadow mode.

### V3

- Add contextual bandit/adaptive selection with governance constraints.
- Add ensemble recommendation and drift-aware reweighting.
