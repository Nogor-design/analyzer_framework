# 🏛️ ta_foundation — Architecture

`ta_foundation` is a production analytics/reporting framework for NinjaTrader exports.
It ingests run-scoped strategy files plus shared market data, computes reusable analytics,
and renders self-contained HTML reports.

---

## 1) End-to-end flow

```text
Raw Exports (CSV + *.Last*.txt)
          │
          ▼
Parsers (normalize artifacts)
          │
          ▼
Ingestion Pipeline (build AnalysisPackage(s) + MarketDataStore)
          │
          ▼
Analysis modules (attach derived metrics to metadata)
          │
          ▼
Report build (registry-driven HTML sections)
```

---

## 2) Non-negotiable contracts

### Time handling
- Canonical timezone: `America/Denver`.
- Canonical timestamps must be timezone-aware.
- Naive datetimes are forbidden.
- Minute/tick data may originate in UTC on disk, but are normalized during ingest.

### Data ownership
- **Run-scoped data** lives in `AnalysisPackage` (`run_id != None`):
  - `trades`, `daily`, `summary`, `settings`, `metadata`, `assets`, `warnings`.
- **Shared market data** lives in `MarketDataStore` (`run_id = None`):
  - minute bars, tick data, and derived/resampled bars cache.
- Shared artifacts must never be duplicated into each run package.

### Layer separation
1. **Parsers**: parse + normalize only.
2. **Pipeline**: assemble package/store objects and route artifacts.
3. **Analysis**: compute reusable derived metrics.
4. **Report sections**: render HTML only from context.

Sections must not read files, parse YAML, call ingest, or perform heavy analytics inline.

---

## 3) Core runtime model

### AnalysisPackage (run-scoped)
`src/ta_foundation/core/model.py`
- `run_id`
- `trades` (optional DataFrame)
- `daily` (optional DataFrame)
- `summary` (`SummaryBlock`)
- `settings` (optional DataFrame)
- `assets` (dict)
- `metadata` (dict)
- `warnings` (list)

### MarketDataStore (shared)
`src/ta_foundation/marketdata/store.py`
- `minute_bars[(instrument, contract)]`
- `ticks[(instrument, contract)]`
- `bars_cache[(instrument, contract, timeframe, source_policy)]`
- API examples:
  - `market.get(instrument, contract)` (minute bars)
  - `market.get_bars(..., timeframe="5m")`

---

## 4) Report system contract

Primary config/build path:

```text
report.yaml
  ↓ load_report_config(s)
build_report_from_config(packages, cfg, market, optimization_store)
  ↓ HtmlReportBuilder.build(context)
section.render_fn(section_ctx)
```

Context passed to sections includes:
- `packages`
- `market`
- `report_config`
- `section_id`
- `section`
- `options` (section-local)
- `all_options` (full merged YAML)

---

## 5) Repository layout (high-level)

```text
src/ta_foundation/
  analysis/
  cli/
  core/
  marketdata/
  optimization/
  parsers/
  reports/html/
  strategies/
  tests/
```

---

## 6) Design principles

- Deterministic outputs
- Contract-first data handling
- Minimal-scope extensibility
- Reusable analytics feeding multiple reports
- Registry-driven section rendering

If a change does not clearly fit parser / analysis helper / report section / minor pipeline enhancement,
rethink the design before implementation.
