# ta_foundation Regime Recommender — Implementation Plan

This document converts `TA_FOUNDATION_REGIME_PARAMETER_RECOMMENDER_DESIGN.md` into an execution-ready plan that fits existing `ta_foundation` contracts.

---

## 0) Guardrails (must hold in every task)

1. **Layering**
   - Parser changes only in `parsers/`.
   - New analytics in `analysis/`.
   - HTML rendering only in `reports/html/sections/`.
   - No heavy compute in section renderers.

2. **Data ownership**
   - Shared market bars remain in `MarketDataStore` (`run_id=None` artifacts only).
   - Recommender outputs attach only under:
     - `pkg.metadata["derived"]["regime_recommender"]`

3. **Time policy**
   - All canonical timestamps tz-aware.
   - Session template windows authored in `America/Denver` local time.
   - No naive datetimes.

4. **Report contract**
   - section uses only `ctx` (`packages`, `market`, `options`, `report_config`).
   - no disk reads / YAML parse / ingest calls inside sections.

---

## 1) Scope and deliverables

## In scope
- Build deterministic regime recommender (Phase 1 foundation).
- Parse strategy docs/templates into machine-usable metadata.
- Generate recommendation JSON + optional HTML section rendering.
- Export session-based NinjaTrader XML templates per recommendation.
- Add outcome capture structures for feedback loop (initial batch mode).

## Out of scope (for this implementation cycle)
- Full ML reranker training pipeline.
- Online learning.
- New ingest/CLI architecture.

---

## 2) Workstream map

## WS-A: Strategy metadata extraction
**Goal:** convert existing strategy assets (`.cs`, `.md`, `.xml`) into `StrategyProfile`.

### Files to create
- `src/ta_foundation/analysis/strategy_metadata/models.py`
- `src/ta_foundation/analysis/strategy_metadata/extractor.py`
- `src/ta_foundation/analysis/strategy_metadata/xml_templates.py`
- `src/ta_foundation/analysis/strategy_metadata/__init__.py`

### Core outputs
- `StrategyProfile` dict with:
  - `entry_model`, `risk_model`, `time_model`, `regime_filters`, `template_presets`.
- Source trace map per parameter:
  - `source_file`, `field`, `default`, `template_override`.

### Acceptance criteria
- Can build profile for:
  - `PantheonMasterBotV01TesterV2`
  - `PantheonBotV2`
- Missing fields handled with warnings (no crashes).

---

## WS-B: Multi-timeframe feature engine + regime classifier
**Goal:** produce deterministic current-state regime snapshot from shared market bars.

### Files to create
- `src/ta_foundation/analysis/regime_recommender/features.py`
- `src/ta_foundation/analysis/regime_recommender/classifier.py`
- `src/ta_foundation/analysis/regime_recommender/models.py`
- `src/ta_foundation/analysis/regime_recommender/__init__.py`

### Core behavior
- Pull bars via `market.get(...)` / `market.get_bars(...)`.
- Build required windows:
  - 1 day @ 15m
  - 5 days @ 60m
  - 5 days @ 240m
- Compute deterministic features:
  - trend, volatility, compression/expansion, range efficiency, VWAP pressure, cross-TF agreement.
- Classify regime and emit confidence decomposition.

### Acceptance criteria
- Returns stable `RegimeFeatureVector` and `regime` payload.
- If bar coverage insufficient, emits quality warning and reduced-confidence result.

---

## WS-C: Recommendation engine (deterministic + analog-ready hooks)
**Goal:** map `StrategyProfile + regime/features` to explainable parameter recommendation.

### Files to create
- `src/ta_foundation/analysis/regime_recommender/recommender.py`
- `src/ta_foundation/analysis/regime_recommender/confidence.py`
- `src/ta_foundation/analysis/regime_recommender/explanations.py`

### Core behavior
- Candidate generation around strategy defaults (bounded ranges).
- Rule gates + deterministic scoring.
- Output classes:
  - `RECOMMEND_PARAMS`
  - `RECOMMEND_BASELINE`
  - `NO_TRADE`
- Per-parameter explanation:
  - baseline value,
  - recommended value,
  - top influencing features.

### Acceptance criteria
- Recommendation JSON includes confidence components and reasons.
- `NO_TRADE` produced when confidence/data quality thresholds fail.

---

## WS-D: Session template XML generation
**Goal:** export executable template variants for session windows.

### Files to create
- `src/ta_foundation/analysis/regime_recommender/template_export.py`

### Core behavior
- Load selected base strategy template (`sampleTemplate.xml` or preset).
- Overlay recommended params.
- Generate one XML per session:
  - `london`, `ny_early`, `ny_midday`, `power_hour`, `asia`
- Set:
  - `UseTimeFilter=true`
  - `StartTimeH`, `StartTimeM`
  - `DurationTimeH`, `DurationTimeM`
- Write manifest with paths + hash + source template.

### Session defaults (America/Denver)
- london: start `01:00`, duration `03:00`
- ny_early: start `07:30`, duration `02:30`
- ny_midday: start `10:00`, duration `02:00`
- power_hour: start `13:00`, duration `01:00`
- asia: start `18:00`, duration `04:00`

### Acceptance criteria
- Five valid XML files emitted per recommendation run.
- Manifest emitted and attached to metadata.

---

## WS-E: Orchestration + metadata attachment
**Goal:** run recommender end-to-end in analysis phase and attach outputs for reporting.

### Files to create
- `src/ta_foundation/analysis/regime_recommender/orchestrator.py`

### Files to modify
- `src/ta_foundation/reports/html/config.py` (minimal integration hook, feature-flagged via YAML)

### Attach contract
```python
pkg.metadata["derived"]["regime_recommender"] = {
    "version": "rr_v1",
    "strategy_id": "...",
    "snapshot": {...},
    "recommendation": {...},
    "template_bundle": {...},
    "warnings": [...],
}
```

### Acceptance criteria
- Hook runs only when enabled in report config.
- Failures attach non-fatal warning payload and do not break report generation.

---

## WS-F: Reporting output
**Goal:** render recommendation details without heavy compute.

### Files to create
- `src/ta_foundation/reports/html/sections/regime_parameter_recommendation.py`

### Files to modify
- `src/ta_foundation/reports/html/registry.py`
- `report.yaml` (section enablement/options)

### Section behavior
- Read only from `pkg.metadata["derived"]["regime_recommender"]`.
- Display:
  - regime summary,
  - recommendation vs baseline,
  - confidence components,
  - top feature influences,
  - generated template files by session.

### Acceptance criteria
- Section renders even when recommender missing/disabled (graceful message).

---

## WS-G: Outcome tracking and learning dataset bootstrap
**Goal:** capture audit trail for later learning.

### Files to create
- `src/ta_foundation/analysis/regime_recommender/outcomes.py`
- `src/ta_foundation/analysis/regime_recommender/storage.py`

### Core behavior
- Persist records:
  - recommendation context,
  - chosen params,
  - confidence,
  - realized outcomes and baseline comparison.
- Initial persistence backend: local SQLite or parquet manifest (config-driven).

### Acceptance criteria
- Reproducible append-only records with ids linking recommendation → outcomes.

---

## 3) report.yaml configuration additions

Add top-level block (no CLI flags):

```yaml
regime_recommender:
  enabled: true
  strategy_id: PantheonBotV2
  instrument: NQ
  contract: "03-26"
  source_template: sampleTemplate.xml

  session_windows:
    london: { start: "01:00", duration: "03:00" }
    ny_early: { start: "07:30", duration: "02:30" }
    ny_midday: { start: "10:00", duration: "02:00" }
    power_hour: { start: "13:00", duration: "01:00" }
    asia: { start: "18:00", duration: "04:00" }

  thresholds:
    min_confidence: 0.55
    min_data_quality: 0.80

sections:
  - id: regime_parameter_recommendation
    title: "Regime Parameter Recommendation"
    options:
      show_template_bundle: true
```

---

## 4) Milestone plan

## Milestone M1 (1 week): Metadata + features + classifier skeleton
- Deliver WS-A + WS-B.
- Unit tests for profile extraction and feature windows.

## Milestone M2 (1 week): Recommender + orchestrator
- Deliver WS-C + WS-E.
- End-to-end derived metadata payload per package.

## Milestone M3 (1 week): XML template export + report section
- Deliver WS-D + WS-F.
- Confirm 5 session template outputs and HTML visibility.

## Milestone M4 (1 week): Outcomes storage bootstrap
- Deliver WS-G.
- Persist recommendation/outcome links.

---

## 5) Test plan (implementation-ready)

## Unit tests
- `tests/analysis/strategy_metadata/test_extractor.py`
  - parses both Pantheon strategies.
- `tests/analysis/regime_recommender/test_features.py`
  - validates window slicing and tz-aware dt handling.
- `tests/analysis/regime_recommender/test_classifier.py`
  - deterministic regime outputs from fixed fixtures.
- `tests/analysis/regime_recommender/test_recommender.py`
  - recommendation classes + explanation completeness.
- `tests/analysis/regime_recommender/test_template_export.py`
  - 5 XML outputs + session field correctness.

## Integration tests
- `tests/reports/html/sections/test_regime_parameter_recommendation.py`
  - section rendering from precomputed metadata only.
- `tests/analysis/regime_recommender/test_orchestrator_integration.py`
  - non-fatal behavior on data gaps and proper metadata attachment.

## Validation checks
- all timestamps are tz-aware,
- no shared market duplication into package,
- no file I/O in section,
- no crashes when recommender disabled.

---

## 6) Implementation checklist

- [ ] Add strategy metadata models/extractor.
- [ ] Add multitf feature engine.
- [ ] Add regime classifier.
- [ ] Add deterministic recommender + confidence/explanations.
- [ ] Add template XML exporter + manifest.
- [ ] Add orchestrator integration in analysis phase.
- [ ] Add report section + registry + YAML config option.
- [ ] Add outcomes storage bootstrap.
- [ ] Add tests.
- [ ] Run full test suite.

---

## 7) Risk controls during implementation

1. **Overfitting / complexity creep**
   - Keep M1–M3 fully deterministic and inspectable.

2. **Contract drift**
   - Add tests for tz-awareness, metadata placement, and section purity.

3. **Template safety**
   - Preserve non-overridden XML nodes; only patch approved parameter keys.

4. **Operational safety**
   - `NO_TRADE` on low confidence / low data quality.

---

## 8) Definition of done (Phase 1)

Phase 1 is complete when:
1. A selected strategy can produce a recommendation from current market bars.
2. Recommendation includes confidence + per-parameter explanations.
3. Five session XML templates are generated with time windows in America/Denver.
4. Results attach under `pkg.metadata["derived"]["regime_recommender"]`.
5. Optional report section renders without disk I/O/heavy inline compute.
6. Outcome capture tables can record recommendation and realized performance links.
