# Prompt: Extend `large_candle_excursion` with Reusable Signal-Candle Context Intelligence

You are extending an existing production Python analytics/reporting framework: **`ta_foundation`**.

## Mission
Extend the existing **large candle excursion research engine** to add reusable, modular context enrichment so findings can distinguish:
- continuation impulses,
- exhaustion candles,
- level-driven reactions.

Do this by reusing existing architecture and patterns already present in the repository.

---

## Repository-first constraints (must follow)
Before coding anything, read and obey:
- `ARCHITECTURE.md`
- `CONTRIBUTING.md`
- `REPORTING_SECTIONS.md`
- `PROJECT_CONTEXT.md`

Non-negotiables:
1. Keep strict layer boundaries (analysis computes, sections render only).
2. Preserve timezone contract (canonical timestamps are tz-aware `America/Denver`; never return naive datetimes).
3. Keep shared market data in `MarketDataStore` (`run_id=None`) and run outputs in `AnalysisPackage`.
4. Attach all derived analytics under `pkg.metadata["derived"][...]`.
5. No file I/O or heavy compute inside report sections.
6. Make smallest possible change set; preserve existing behavior.

---

## Reuse targets in current codebase (build on these, don’t reinvent)
Use these existing modules as extension anchors:

### Existing LCE orchestration + config
- `src/ta_foundation/analysis/large_candle_excursion/sweep.py`
  - Already merges config and orchestrates detection → forward excursion → context enrichment → context stats.
  - Already supports opt-in context blocks and downstream metadata output.

### Existing modular context-enrichment pattern
- `src/ta_foundation/analysis/large_candle_excursion/context_enricher.py`
  - Already structured as reusable helper pipeline with independent feature blocks.
  - Already supports bucket assignment helper + opt-in context modules.

### Existing grouped context analytics
- `src/ta_foundation/analysis/large_candle_excursion/context_stats.py`
  - Already computes per-bucket and interaction stats with sample-size filtering behavior.

### Existing discovery/findings scoring and diagnostics
- `src/ta_foundation/analysis/large_candle_excursion/downstream_reports.py`
  - Already includes ranking, complexity/sample penalties, interaction diagnostics.

### Existing report surfaces for findings/interactions
- `src/ta_foundation/reports/html/sections/large_candle_excursion_findings_interactions.py`
- `src/ta_foundation/reports/html/sections/large_candle_excursion_findings_top_discoveries.py`
- `discovery/large_candle_excursion_findings_report.yaml`
- `discovery/large_candle_excursion_discovery_report.yaml`

Follow their style and naming patterns.

---

## Required new context dimensions
Implement all of the following as **modular context helpers** (not one monolithic function).

## 1) Pre-candle directional context
For each signal candle, compute:
- previous candle direction
- signal same direction as previous
- signal opposite direction to previous
- previous 2-candle and 3-candle same-direction streak length
- signal engulfs previous candle
- signal breaks previous candle high/low
- signal closes outside previous candle range

Goal: quantify reversal/continuation odds by immediate directional lead-in.

## 2) MA / VWAP location context
For each signal candle, compute:
- above/below MA100
- above/below MA200
- above/below VWAP
- signed distance to MA100/MA200/VWAP in ticks
- ATR-normalized distance to each
- extension buckets for each: `near`, `moderate`, `extended`

Goal: test whether extension from VWAP/MAs shifts reversal vs continuation odds.

## 3) Key level interaction context
At minimum support these levels:
- prior day high/low/close
- session high/low
- overnight high/low
- VWAP
- MA100
- MA200
- recent swing high/low

For each signal candle, compute:
- nearest level type
- distance to nearest level (ticks + ATR-normalized)
- interaction label: `at_level`, `approaching`, `breaking`, `rejecting`, `departing`

Goal: test whether large candles reverse into levels and continue through breaks.

## 4) Trend-state context
Compute:
- slope of MA100
- slope of MA200
- slope of VWAP
- whether price is stacked above/below MA100 and MA200
- trend alignment label

Goal: separate trend continuation from overextended reversal setups.

---

## Reporting requirements
Add findings sections for:
- Strongest Directional Context Effects
- Strongest MA/VWAP Location Effects
- Strongest Key Level Effects
- Strongest Trend Alignment Effects

Add interaction analysis for:
- session × key level
- previous candle direction × outcome
- VWAP distance × outcome
- MA location × outcome

Render sections as pure HTML from `ctx`, using existing section conventions.

---

## Discovery requirements (guardrails against combinatorial explosion)
Update discovery logic to rank setups using new contexts while enforcing:
- coarse buckets first,
- minimum sample sizes per context combo,
- penalties for overly narrow combos,
- preference for interpretable combinations.

Also add diagnostics showing why candidates were rejected (low sample, low edge lift, instability, complexity penalty).

---

## Strategy-readiness requirement
Top strategy candidates must describe:
1. what signal candle did vs previous candle,
2. where signal occurred relative to VWAP/MA100/MA200,
3. whether candle hit/rejected/broke a key level.

These descriptors must be carried through ranking outputs and report tables.

---

## Implementation design requirements (important)
1. **Modular helpers**
   - Add new helper modules/functions under `analysis/large_candle_excursion/` for:
     - directional context
     - MA/VWAP context
     - key level context
     - trend-state context
   - Keep `context_enricher.py` as orchestrator/composer of these helpers.

2. **Config-driven behavior**
   - Expose enable flags and bucket thresholds through report/discovery YAML.
   - No hardcoded one-off thresholds in discovery logic.

3. **Data contracts**
   - Ensure all computed context fields flow into forward-event rows and `context_analysis` outputs.
   - Keep outputs JSON-safe and backward compatible where possible.

4. **Testing**
   - Add/extend tests for:
     - context feature correctness,
     - bucket labeling,
     - interaction aggregations,
     - ranking penalties/min-sample gates,
     - downstream section rendering with missing/partial fields.

---

## Extra improvements to include (to improve edge quality)
In addition to requested features, include the following if feasible with small changes:
1. **Event uniqueness guardrail**: avoid double-counting identical event signatures across overlapping combos when computing context effect strength.
2. **Temporal robustness**: add split stability checks for major context effects (early/mid/late segments).
3. **Edge-lift framing**: report each context edge as lift vs baseline continuation/reversal rate (not only raw win%).
4. **Uncertainty reporting**: include confidence interval or Wilson interval for key rates when sample size is moderate.
5. **Regime-aware slicing**: optional stratification by volatility/session regime to detect context edge dependency.

---

## Deliverables format
When you implement, respond with:
1. Brief plan.
2. Exact file paths modified/created.
3. Complete ready-to-paste code blocks (no partial snippets).
4. Required dependencies (if any).
5. How to run and verify.

If any requirement conflicts with existing architecture, stop and explain the conflict with a minimal, architecture-compliant alternative.
