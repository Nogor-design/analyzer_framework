# MA Anchor Interaction Engine Design (ta_foundation-Aligned)

## 1) Scope and Intent

This subsystem quantifies how price behaves after crossing a moving average
anchor (SMA/EMA).  It produces two complementary views:

1. **Structural view** — how does price *generally* behave after crossing an
   anchor, measured over all cross-events in the market bar history?
2. **Trade-entry view** — for the trades a specific strategy actually took,
   what TP/SL (in ATR units) would have produced the best outcomes measured
   forward from each precise entry bar?

Decision support objectives:
- Identify which anchor lengths behave like trend rails vs mean-reversion magnets.
- Produce robust TP/SL candidate maps from path-aware simulations.
- Align structural recommendations with the specific MA settings a strategy uses.
- Detect redundant anchor lengths and unstable recommendations.

Non-objective: do not present this as a standalone executable strategy truth
engine.  Any trade-like interpretation is derived from structural segments or
historical entry simulation, not live fills.

---

## 2) Architecture Placement

| Layer | Location |
|---|---|
| Parsing (if new file types needed) | `src/ta_foundation/parsers/...` |
| Settings extraction | `src/ta_foundation/analysis/ma_structure/settings_extractor.py` |
| Anchor / segment analytics | `src/ta_foundation/analysis/ma_structure/...` |
| Trade-time simulation | `src/ta_foundation/analysis/ma_structure/trade_time_tp_sl.py` |
| Report visuals | `src/ta_foundation/reports/html/sections/anchor_*.py` |
| Section wiring / options | `report.yaml` and `reports/html/registry.py` |

### Runtime flow (no drift)
1. Pipeline ingests run-scoped + shared market data.
2. CLI calls `run_anchor_interaction_analysis()` per package in the analysis
   phase, before any section rendering.
3. Orchestrator resolves anchors (YAML or settings), runs structural analysis,
   optionally runs trade-time simulation.
4. Results attach under `pkg.metadata["derived"]["anchor_interaction"]`
   (JSON-safe) and `pkg.assets["anchor_interaction"]` (DataFrames in memory).
5. Sections render from `ctx` only — no file IO, no heavy compute.

---

## 3) Two Operating Modes

### Mode A: Discovery (`mode: discovery`)

Anchors come entirely from `report.yaml`.  No dependency on `pkg.trades` or
`pkg.settings`.  Good for sweeping a broad range of MA lengths to find
structural edges in the market independent of any specific strategy.

```yaml
anchor_interaction:
  enabled: true
  mode: discovery
  instrument: NQ
  contract: "03-26"
  timeframe: 1m
  anchors:
    - { family: SMA, length: 20 }
    - { family: SMA, length: 50 }
    - { family: SMA, length: 200 }
  tp_sl:
    enabled: true
    tp_grid: [0.8, 1.0, 1.3, 1.6, 2.0]
    sl_grid: [0.6, 0.8, 1.0, 1.2]
    folds:
      mode: anchored_walk_forward
      min_train_segments: 150
      min_test_segments: 50
```

### Mode B: Strategy-Aware (`mode: strategy_aware`)

Anchors are derived **per-run** from `pkg.settings` using a strategy-specific
extractor.  Falls back to YAML anchors if extraction fails.  Trade-time TP/SL
simulation runs automatically when `pkg.trades` is present.

```yaml
anchor_interaction:
  enabled: true
  mode: strategy_aware
  anchor_source: settings        # "yaml" | "settings" | "auto"
  strategy_family: PantheonMasterBotV01TesterV2
  instrument: NQ
  contract: "03-26"
  timeframe: 1m
  # anchors: block is still used as fallback if settings extraction fails
  anchors:
    - { family: SMA, length: 50 }
    - { family: SMA, length: 200 }
  tp_sl:
    enabled: true
    tp_grid: [0.8, 1.0, 1.3, 1.6, 2.0]
    sl_grid: [0.6, 0.8, 1.0, 1.2]
  trade_time_tp_sl:
    enabled: true            # default true when mode: strategy_aware
    atr_period: 14
    max_bars_forward: 120
    tp_grid: [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    sl_grid: [0.3, 0.5, 0.75, 1.0, 1.25]
```

---

## 4) Anchor Resolution (`anchor_source`)

| Value | Behaviour |
|---|---|
| `yaml` (default) | Use the `anchors:` list from report.yaml for every run. |
| `settings` | Extract from `pkg.settings` per run; fall back to YAML anchors on failure, with a warning in diagnostics. |
| `auto` | Same as `settings` but no warning on fallback. |

Settings extraction is implemented in `settings_extractor.py`.  Extractors are
registered per `strategy_family` name.  Currently registered:

- `PantheonMasterBotV01TesterV2` / `PantheonBotV2` / `PantheonMasterBot` —
  reads `averageFast`, `averageSlow`, `averageTrend` (only if `UseTrend=True`)
  from the `_Settings.csv` export under NinjaTrader category sections
  "Fast Averages", "Slow Averages", "Trend Averages".
- Generic fallback — scans all items for names containing "fast", "slow",
  "trend" with numeric values.

To add a new strategy extractor:
```python
from ta_foundation.analysis.ma_structure.settings_extractor import register_extractor, AnchorSpec

@register_extractor("MyStrategyName")
def _extract_my_strategy(settings_df):
    # read items from settings_df (columns: section, item, value)
    # return list[AnchorSpec]
    ...
```

---

## 5) Data Contracts (Non-Negotiable)

| Rule | Detail |
|---|---|
| Timezone | All canonical timestamps tz-aware `America/Denver` |
| Naive datetimes | Forbidden |
| Shared bars | Stay in `MarketDataStore`; never duplicated into packages |
| Derived data | Attach under `pkg.metadata["derived"]["anchor_interaction"]` |
| JSON safety | `pkg.metadata` must remain JSON-safe (no DataFrames, callables) |
| Heavy tables | Written to parquet under `.ta_artifacts/`; metadata holds path refs |
| Sections | Pure renderers; read `ctx` only; no disk IO, no heavy compute |

---

## 6) Analysis Module Layout

```
src/ta_foundation/analysis/ma_structure/
├── models.py              # AnchorSpec, EngineConfig (including mode/anchor_source/trade_time_* fields)
├── settings_extractor.py  # Extract AnchorSpec from pkg.settings per strategy family  [NEW]
├── anchors.py             # Build MA value series tables
├── segment_detection.py   # Detect price-crosses-anchor segments
├── path_metrics.py        # MFE/MAE/ETD/timing per segment
├── regime_context.py      # Attach trend/vol regime at entry
├── aggregation.py         # Summary tables per anchor and anchor×regime
├── tp_sl_engine.py        # Structural TP/SL scoring (segment-based)
├── trade_alignment.py     # Align trades to structural recommendations (context only)
├── trade_time_tp_sl.py    # Trade-entry simulation TP/SL scoring              [NEW]
└── orchestrator.py        # Main entry point: resolve anchors, run all engines
```

---

## 7) Structural TP/SL vs Trade-Time TP/SL

These are **complementary**, not alternatives.

| | Structural (segment-based) | Trade-Entry Simulation |
|---|---|---|
| **Input** | All MA cross-events in bar history | Actual trade entries from `pkg.trades` |
| **Question answered** | "How does price generally behave after crossing this MA?" | "What TP/SL would have worked on the trades this bot actually took?" |
| **Anchor dependence** | Tied to configured anchor set | Tied to same anchors (but uses trade entry bars) |
| **Output** | `tp_sl_candidates`, `recommendations` per anchor | `trade_time_candidates` per group (all/long/short) |
| **Grouping** | Per anchor_id | Per direction group × tp × sl |
| **Validation** | Anchored walk-forward folds on segments | Win rate / expectancy across all trades |
| **Location** | `tp_sl_engine.py` | `trade_time_tp_sl.py` |

---

## 8) Metadata Envelope

```python
pkg.metadata["derived"]["anchor_interaction"] = {
    "version": "ai_v1",
    "engine": {
        "mode": "strategy_aware",           # or "discovery"
        "anchor_source_configured": "settings",
        "anchor_source_used": "settings",   # "settings" | "yaml_fallback" | "yaml"
        "strategy_family": "PantheonMasterBotV01TesterV2",
        "anchors_resolved": ["SMA_50_close", "SMA_200_close", "SMA_300_close"],
        "instrument": "NQ",
        "contract": "03-26",
        "timeframe": "1m",
        "timezone": "America/Denver",
        "cross_mode": "close",
        "exit_mode": "close",
        "recross_policy": "first_return",
        "tp_sl_fold_mode": "anchored_walk_forward",
        "tp_sl_min_train_segments": 150,
        "tp_sl_min_test_segments": 50,
        "trade_time_tp_sl_enabled": True,
        "trade_time_max_bars_forward": 120,
    },
    "artifacts": {
        "anchors":                      {"type": "parquet", "path": "..."},
        "segments":                     {"type": "parquet", "path": "..."},
        "segment_path_stats":           {"type": "parquet", "path": "..."},
        "summary_by_anchor":            {"type": "parquet", "path": "..."},
        "summary_by_anchor_regime":     {"type": "parquet", "path": "..."},
        "tp_sl_candidates":             {"type": "parquet", "path": "..."},
        "recommendations":              {"type": "parquet", "path": "..."},
        "validation_folds":             {"type": "parquet", "path": "..."},
        "trade_recommendation_alignment": {"type": "parquet", "path": "..."},
        "trade_time_candidates":        {"type": "parquet", "path": "..."},  # NEW
        "trade_time_per_trade":         {"type": "parquet", "path": "..."},  # NEW
    },
    "diagnostics": {
        "ok": True,
        "n_input_bars": 0,
        "n_segments": 0,
        "n_censored": 0,
        "pct_censored": 0.0,
        "anchors_tested": 3,
        "anchor_source_used": "settings",
        "tp_sl_candidates": 0,
        "recommendations": 0,
        "validation_fold_count": 0,
        "trade_alignment_count": 0,
        "trade_time_tp_sl_run": True,       # NEW
        "trade_time_candidates": 0,         # NEW
        "trade_time_per_trade": 0,          # NEW
        "timezone": "America/Denver",
        "warnings": [],
        "validation": { ... },
    },
}
```

---

## 9) Segment Schema (unchanged from original)

### `segments` table
Required columns: `segment_id`, `run_id`, `instrument`, `anchor_id`,
`direction`, `entry_ts`, `entry_bar_index`, `entry_price`,
`entry_anchor_value`, `exit_ts`, `exit_bar_index`, `exit_price`,
`exit_anchor_value`, `entry_cross_mode`, `exit_mode`, `bars_held`,
`minutes_held`, `censored`, `gap_cross`, `immediate_failure`,
`re_cross_count`, `trend_regime_at_entry`, `vol_regime_at_entry`,
`anchor_slope_at_entry`.

### `segment_path_stats` table
Required columns: `segment_id`, `mfe_price`, `mae_price`, `mfe_atr`,
`mae_atr`, `net_outcome_price`, `mfe_ts`, `mae_ts`, `time_to_mfe_bars`,
`time_to_mae_bars`, `mfe_before_mae`, `etd_price`, `etd_ratio`,
`max_anchor_distance`, `mean_anchor_distance`, `anchor_distance_auc`,
`path_length_abs`, `path_efficiency`.

### `trade_time_candidates` table (NEW)
Columns: `group` (`all`/`long`/`short`), `tp_atr`, `sl_atr`, `sample_n`,
`n_decisive`, `n_tp_hit`, `n_sl_hit`, `win_rate`, `expectancy_atr`,
`avg_bars_to_tp`, `avg_bars_to_sl`, `pct_neither`.

### `trade_time_per_trade` table (NEW)
Columns: `trade_id`, `direction`, `entry_price`, `atr_at_entry`, `tp_atr`,
`sl_atr`, `first_hit` (`tp`/`sl`/`neither`), `bars_to_hit`, `within_window`.

---

## 10) `trade_recommendation_alignment` (clarified role)

This table (built by `trade_alignment.py`) provides **factual context** for
each trade — not a recommendation match.  It answers:
- Which structural recommendation's `(tp_atr, sl_atr)` is nearest to the
  trade's realized `(mfe_atr, mae_atr)` path?
- What was the ATR at entry?  What was the realized outcome in ATR units?

It is a diagnostic aid, not an authoritative recommendation.  The authoritative
trade-specific recommendation comes from `trade_time_candidates`.

---

## 11) Report Sections

| Section ID | Purpose |
|---|---|
| `anchor_interaction_overview` | KPI cards: n segments, censoring rate, median MFE/MAE ATR, ETD ratio |
| `anchor_interaction_anchor_matrix` | Heatmaps: median MFE ATR, MAE ATR, ETD, path efficiency, failure rate |
| `anchor_interaction_tp_sl_spec` | Structural TP/SL config + **trade-time simulation results** |
| `anchor_interaction_diagnostics` | Detailed diagnostics: fold counts, warnings, anchor source used |
| `anchor_tp_sl_recommendations` | Conservative/Balanced/Aggressive structural recommendations |

The `anchor_interaction_tp_sl_spec` section now renders two panels:
1. **Structural TP/SL** — the YAML-configured grid and fold parameters.
2. **Trade-Entry Simulation Results** — per-run tables of top `(tp, sl)` combos
   from `trade_time_candidates`, grouped by `all` / `long` / `short`.

---

## 12) Feature Placement Matrix

| Feature type | Correct location |
|---|---|
| New strategy extractor | `settings_extractor.py` — `@register_extractor("Name")` |
| New file format parser | `parsers/<vendor>/` — register via parser registry/CLI |
| Ingest grouping/routing | `core/pipeline.py` — minimal change |
| New MA family (WMA, VWAP) | `anchors.py` — add to `compute_anchor_series()` |
| New segment metric | `path_metrics.py` — no HTML |
| New regime feature | `regime_context.py` — no HTML |
| New visual/report block | `reports/html/sections/anchor_*.py` — renderer only |
| Section order/title/options | `report.yaml` |

---

## 13) Extension Playbook

### Add a new strategy extractor
```python
# settings_extractor.py
@register_extractor("MyNewStrategy")
def _extract_my_strategy(settings_df: pd.DataFrame) -> list[AnchorSpec]:
    fast = _lookup_int(settings_df, ["Fast MA"], ["Period", "Length"])
    slow = _lookup_int(settings_df, ["Slow MA"], ["Period", "Length"])
    specs = []
    if fast: specs.append(AnchorSpec("SMA", fast))
    if slow: specs.append(AnchorSpec("SMA", slow))
    return specs
```

### Add a new anchor family
```python
# anchors.py — extend compute_anchor_series()
if family == "WMA":
    weights = np.arange(1, spec.length + 1, dtype=float)
    return s.rolling(spec.length).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )
```

### Add a new report section
1. Create `reports/html/sections/anchor_<name>.py` with `render_anchor_<name>(ctx)`.
2. Register in `reports/html/registry.py`.
3. Add to `report.yaml` sections list.

---

## 14) Hard Rejection Checklist

Reject and redesign if a proposal includes:
- Reading files inside a section renderer
- Parsing YAML inside a section renderer
- Calling ingest/pipeline inside a section renderer
- Creating global mutable state
- Returning/storing naive datetimes
- Duplicating shared market bars in run-scoped package data
- Storing DataFrames or non-serializable objects in `pkg.metadata`
- Bypassing the SECTION_REGISTRY
- Adding new CLI flags for report rendering behaviour

---

## 15) MVP vs Advanced Scope

### MVP (implemented)
- SMA and EMA anchors.
- Settings-based anchor extraction for Pantheon strategy family.
- Close/touch cross and exit modes.
- First-return termination with optional hysteresis.
- Core path metrics: MFE/MAE/ETD, timing, efficiency, anchor distance.
- Structural TP/SL with anchored walk-forward validation.
- Trade-entry forward simulation TP/SL (all/long/short groups).
- Two explicit modes: `discovery` and `strategy_aware`.

### Advanced (phase 2+)
- WMA / VWAP / anchored VWAP anchor families.
- Survival/hazard modelling for censored segments.
- Path archetype clustering and redundancy matrix across lengths.
- Session-aware behaviour slices.
- Partial TP / trailing-exit experiments in trade-time simulation.
- Tick-level tie-breaking for same-bar TP+SL hits.

---

## 16) Implementation Checklist (for AI-assisted development)

Before implementing any change, state:
1. Which mode is affected: `discovery`, `strategy_aware`, or both?
2. Which layer: parser / analysis / section?
3. Anchor source impact: does it change per-run anchor resolution?
4. Data ownership: run-scoped (AnalysisPackage) or shared (MarketDataStore)?
5. Metadata JSON-safety: are any DataFrames or callables being added to metadata?

Then deliver:
- Brief plan
- Exact file paths to change
- Full code blocks
- Verification steps
