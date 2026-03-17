# MA / Anchor Interaction Engine Design (ta_foundation-Aligned)

## 1) Scope and Intent (reframed for current framework)
This feature should be implemented as an **analysis subsystem**, not as a direct strategy backtest module.

Primary objective:
- Quantify conditional post-interaction path behavior around moving anchors (SMA/EMA first), including extension, reversion, adverse movement, and regime dependence.

Decision support objective:
- Identify which anchors behave like trend rails vs mean-reversion magnets.
- Produce robust TP/SL candidate maps from path-aware simulations.
- Detect redundant anchor lengths and unstable recommendations.

Non-objective:
- Do **not** present this as a standalone executable strategy truth engine.
- Any trade-like interpretation is derived from structural segments.

---

## 2) Architecture placement (must match existing contracts)
### Correct layer mapping
- Parsing changes (if needed): `src/ta_foundation/parsers/...`
- Shared market access: `MarketDataStore` only (run_id = None)
- Segment/path analytics: `src/ta_foundation/analysis/ma_structure/...`
- Report visuals: `src/ta_foundation/reports/html/sections/...`
- Section wiring/options: `report.yaml` and `reports/html/registry.py`

### Runtime flow (no drift)
1. Pipeline ingests run-scoped + shared market data.
2. Analysis phase computes anchor interaction artifacts and attaches references in package metadata.
3. Sections render from `ctx` only (no file IO, no inline heavy compute).

### Data ownership rules
- Run-scoped outputs: attach under `pkg.metadata["derived"]["anchor_interaction"]`.
- Shared bars stay in `MarketDataStore`; never duplicate shared bars into package payloads.

---

## 3) Time and determinism contracts
- Canonical timestamps must remain tz-aware.
- Canonical timezone remains `America/Denver`.
- Segment detection, fold assignment, and TP/SL scoring must be deterministic given same inputs/config.
- Naive datetimes are forbidden in any generated artifact.

---

## 4) Domain model (aligned to existing `metadata["derived"]` pattern)
Use normalized tables serialized as parquet artifacts and referenced from metadata.

Recommended metadata envelope:

```python
pkg.metadata.setdefault("derived", {})["anchor_interaction"] = {
    "version": "ai_v1",
    "engine": {
        "instrument": "NQ",
        "contract": "...",
        "timeframe": "1m",
        "timezone": "America/Denver",
        "cross_mode": "close",
        "exit_mode": "close",
        "recross_policy": "first_return",
    },
    "artifacts": {
        "anchors": {"type": "parquet", "path": "..."},
        "segments": {"type": "parquet", "path": "..."},
        "segment_path_stats": {"type": "parquet", "path": "..."},
        "summary_by_anchor": {"type": "parquet", "path": "..."},
        "summary_by_anchor_regime": {"type": "parquet", "path": "..."},
        "tp_sl_candidates": {"type": "parquet", "path": "..."},
        "recommendations": {"type": "parquet", "path": "..."},
        "validation_folds": {"type": "parquet", "path": "..."},
    },
    "diagnostics": {
        "n_segments": 0,
        "pct_censored": 0.0,
        "warnings": [],
    },
}
```

Do not store DataFrames directly in metadata.

---

## 5) Naming and semantics correction
Rename internal “trade” concept to a structural segment term:
- `anchor_segment` (preferred)
- `ma_interaction_segment` (alias)

Definition baseline:
- Entry: price crosses anchor under configured cross mode.
- Exit: first configured return to anchor (with optional hysteresis).

This preserves your existing concept while avoiding strategy-execution overclaims.

---

## 6) Minimum viable analytic schema
## 6.1 Segment table (`segments`)
Required columns:
- `segment_id`, `run_id`, `instrument`, `anchor_id`, `direction`
- `entry_ts`, `entry_bar_index`, `entry_price`, `entry_anchor_value`
- `exit_ts`, `exit_bar_index`, `exit_price`, `exit_anchor_value`
- `entry_cross_mode`, `exit_mode`, `bars_held`, `minutes_held`
- `censored`, `gap_cross`, `immediate_failure`, `re_cross_count`
- `trend_regime_at_entry`, `vol_regime_at_entry`, `anchor_slope_at_entry`

## 6.2 Path stats table (`segment_path_stats`)
Required columns:
- `segment_id`
- `mfe_price`, `mae_price`, `mfe_atr`, `mae_atr`, `net_outcome_price`
- `mfe_ts`, `mae_ts`, `time_to_mfe_bars`, `time_to_mae_bars`
- `mfe_before_mae`, `etd_price`, `etd_ratio`
- `max_anchor_distance`, `mean_anchor_distance`, `anchor_distance_auc`
- `path_length_abs`, `path_efficiency`

## 6.3 Summary tables
- `summary_by_anchor`
- `summary_by_anchor_regime`

Must include percentile-heavy outputs (p10/p25/p50/p75/p90) for MFE/MAE/ETD/duration.

---

## 7) Critical missing pieces to add now
1. **Censoring support**
   - Mark non-terminating segments (`censored=True`).
   - Exclude from return-duration estimates unless survival methods are explicitly enabled.

2. **Recross policy configuration**
   - `first_return` (MVP)
   - Optional hysteresis via `return_band_ticks` or `return_band_atr`.

3. **Cross/exit mode dimensions**
   - `cross_mode: close|touch|hybrid`
   - `exit_mode: close|touch`

4. **Moving-anchor distortion diagnostics**
   - Add `anchor_drift_burden` metric:
     - Portion of “return-to-anchor” attributable to anchor movement vs price movement.

5. **Sample-floor enforcement**
   - Descriptive summary floor (default >=100).
   - Regime bucket floor (default >=75).
   - TP/SL recommendation floors (train >=150, OOS >=50).

6. **Recommendation stability scoring**
   - Fold consistency + neighboring-length consistency + sensitivity penalty.

---

## 8) Analysis module layout (fits existing project style)
Create:
- `src/ta_foundation/analysis/ma_structure/models.py`
- `src/ta_foundation/analysis/ma_structure/anchors.py`
- `src/ta_foundation/analysis/ma_structure/segment_detection.py`
- `src/ta_foundation/analysis/ma_structure/path_metrics.py`
- `src/ta_foundation/analysis/ma_structure/regime_context.py`
- `src/ta_foundation/analysis/ma_structure/tp_sl_engine.py`
- `src/ta_foundation/analysis/ma_structure/aggregation.py`
- `src/ta_foundation/analysis/ma_structure/stability.py`
- `src/ta_foundation/analysis/ma_structure/orchestrator.py`

Implementation style:
- Vectorized for anchor/pre-feature series.
- Event/state-machine for segment lifecycle and path-aware TP/SL first-hit simulation.
- No report rendering and no section imports in analysis layer.

---

## 9) Config surface (report-driven, no CLI drift)
Add top-level block in `report.yaml`:

```yaml
anchor_interaction:
  enabled: true
  instrument: NQ
  contract: null
  timeframe: 1m
  anchors:
    - { family: SMA, length: 20, source: close }
    - { family: EMA, length: 21, source: close }
    - { family: SMA, length: 50, source: close }
  cross_mode: close
  exit_mode: close
  recross_policy: first_return
  return_band_atr: 0.0
  min_bars_after_entry: 1
  tp_sl:
    enabled: true
    unit: atr
    tp_grid: [0.8, 1.0, 1.3, 1.6, 2.0]
    sl_grid: [0.6, 0.8, 1.0, 1.2]
    folds:
      mode: anchored_walk_forward
      min_train_segments: 150
      min_test_segments: 50
```

Sections consume their own `ctx["options"]`; analysis consumes global config from report configuration wiring.

---

## 10) Report outputs (section-safe)
Recommended new sections (renderer-only):
1. `anchor_interaction_overview`
   - KPI cards: n segments, censoring rate, median MFE/MAE ATR, ETD ratio.
2. `anchor_length_heatmaps`
   - Heatmaps for median MFE ATR, MAE ATR, ETD ratio, path efficiency, immediate failure rate.
3. `anchor_regime_tables`
   - Anchor × trend/vol regime percentile tables.
4. `anchor_tp_sl_recommendations`
   - Conservative/Balanced/Aggressive recommendations with stability score and OOS fields.

All visuals must be embedded base64 images.

---

## 11) Validation framework (required for recommendations)
- Use anchored walk-forward folds for TP/SL recommendation scoring.
- Report IS and OOS separately.
- Downgrade recommendations when:
  - OOS rank instability is high.
  - Neighboring lengths are inconsistent.
  - Top expectancy is tail-dominated by rare outliers.

Recommended diagnostics fields:
- `stability_score`
- `fold_agreement`
- `neighbor_consistency`
- `tail_dependency_share`
- `sample_quality_flag`

---

## 12) MVP vs advanced scope
### MVP (ship first)
- SMA/EMA.
- One active segment per anchor config.
- Close/touch modes.
- First-return termination (+ optional hysteresis).
- Core path metrics: MFE/MAE/ETD, timing, efficiency, anchor distance, immediate failure.
- Summary tables and heatmaps.
- ATR-grid TP/SL with anchored walk-forward scoring.

### Advanced (phase 2+)
- WMA/VWAP/anchored VWAP.
- Survival/hazard modeling for censored segments.
- Path archetype clustering and redundancy matrix across lengths.
- Session-aware behavior slices.
- Partial TP / trailing-exit experiments.

---

## 13) Risks and explicit guardrails
- Do not interpret structural segments as executable fills without explicit execution modeling.
- Do not ignore moving-anchor effects; include drift diagnostics.
- Do not over-partition regime dimensions below sample floors.
- Do not compute heavy analysis inside report sections.
- Do not store shared bars in run-scoped package data.

---

## 14) Implementation roadmap (minimal disruption)
1. Add `analysis/ma_structure` module skeleton + orchestrator.
2. Wire analysis execution in existing analysis phase (no section-side compute).
3. Persist parquet artifacts + metadata references under `pkg.metadata["derived"]["anchor_interaction"]`.
4. Add initial HTML sections and registry entries.
5. Add report.yaml examples and thresholds.
6. Add validation diagnostics and recommendation tiering.
