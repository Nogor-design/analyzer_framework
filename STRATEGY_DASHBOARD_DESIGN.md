# ta_foundation Strategy Research Dashboard Design

## 1. Executive summary

Build a **hybrid dashboard architecture** that keeps `ta_foundation` as the source of truth for report generation while adding a **metadata index + query API** optimized for strategy exploration.

The practical model is:

1. `ta_foundation` keeps producing report artifacts from `report.yaml` (HTML sections, charts, summaries).
2. A new **indexer step** reads run outputs + machine-readable report summaries and writes:
   - a normalized relational store (strategy/run/report metadata)
   - a denormalized filter/search table (fast faceted filtering)
3. A web UI uses API endpoints for fast list/filter/compare workflows and deep-links into existing report artifacts.

This avoids re-platforming your mature report pipeline, while solving the core UX problem: quickly filter and drill into many strategies.

---

## 2. Assumptions

1. Existing `ta_foundation` pipeline remains authoritative for ingest, run packaging, analysis, and report generation.
2. Existing report artifacts already include enough metrics/charts to be reused (or can be extended with small additive summary outputs).
3. Strategy volume is potentially large (thousands of strategies x multiple runs/windows), so filter latency target should be sub-second for common queries.
4. Users are internal researchers, not public anonymous traffic; authentication can start simple.
5. Time semantics follow existing framework contracts (tz-aware, America/Denver canonical handling).
6. New dashboard metadata should be additive and non-breaking to current `report.yaml` behavior.

---

## 3. Recommended architecture

## 3.1 Recommended approach (best fit)

Use a **hybrid architecture**:

- **Files remain canonical** for full report outputs and artifacts (existing behavior).
- **Database-backed metadata index** is added for fast exploration/filtering.
- **Thin API service** sits between UI and index/artifact registry.

Why this is best here:

- Reuses existing `ta_foundation` output contracts.
- Avoids expensive live parsing of report files on every UI query.
- Supports high-performance faceted filtering and drilldown.
- Preserves reproducibility/versioning by linking every indexed row to artifact paths and run metadata.

## 3.2 High-level components

- `ta_foundation` pipeline (existing)
- Report artifact storage (filesystem/object storage)
- Dashboard indexer (new)
- Dashboard DB (new, PostgreSQL recommended)
- API service (new, FastAPI recommended)
- Frontend app (new, React/TypeScript recommended)

---

## 4. Detailed design by section

## 4.1 Product design (pages/screens)

### A) Strategy Catalog / Explorer

**Purpose**
- Single place to view all strategies/runs with sortable metrics and faceted filters.

**Main UI components**
- Top bar: search, saved views, dataset/run-window selector.
- Left filter panel: collapsible filter groups.
- Main table/grid: virtualized rows, pinned columns.
- Result summary bar: total count, active filters, quick metric aggregates.
- Bulk action bar: compare, favorite, export selection.

**Key interactions**
- Apply multiple filters with AND/OR group behavior.
- Sort by any metric (Sharpe, net profit, drawdown, stability).
- Save filter presets.
- Multi-select strategies and open comparison view.
- Click row to open strategy detail.

**Most important data shown**
- Strategy name/id, family/template, style tags.
- Instrument/timeframe/session coverage.
- Directionality.
- Key KPIs (net profit, PF, max DD, win rate, trade count, stability score).
- Last run timestamp + test window.

### B) Filter Panel

**Purpose**
- Fast narrowing by behavior, timing, risk profile, and performance.

**Main UI components**
- Facet groups with counts.
- Numeric range sliders + histogram previews.
- Inclusion/exclusion pills (e.g., include London, exclude Asia).
- “Advanced query” chip editor for complex filter logic.

**Key interactions**
- Toggle session buckets (London, NY premarket/open/midday/power hour/close, Asia).
- Set numeric thresholds (e.g., max drawdown < X).
- Add/remove tags (breakout, mean reversion, pattern family).
- Save preset.

**Most important data shown**
- Facet counts and distribution previews for current result set.

### C) Strategy Detail Page

**Purpose**
- Deep summary of one strategy across runs/windows with links to reports/charts/trades.

**Main UI components**
- Header card: strategy identity, family, lineage, tags.
- KPI cards (global + by market regime/session/time bucket).
- Run timeline table (all runs/backtests).
- Artifact panel (report HTML, chart images, downloadable files).
- Related strategies panel.

**Key interactions**
- Switch run version/test window.
- Open report sections directly.
- Jump to chart drilldown with selected context.

**Most important data shown**
- Strategy metadata, entry/exit model, stop/target style, stability/performance summary.

### D) Chart Drilldown Page

**Purpose**
- Interactive chart analysis at strategy/run level.

**Main UI components**
- Chart tabs: equity, drawdown, PnL distribution, session performance, DOW/TOD heatmaps.
- Brush/zoom/time-range controls.
- Cross-filter controls (session, direction, instrument, regime).
- Trade inspector side panel.

**Key interactions**
- Hover/click to inspect points/trades.
- Filter chart by session/time/day/condition.
- Open individual trade examples (if available artifacts/trade logs exist).

**Most important data shown**
- Time-series curves and distribution behavior tied to same run version used by KPIs.

### E) Report Detail Views

**Purpose**
- Reuse existing ta_foundation report outputs while making them navigable.

**Main UI components**
- Report section list with anchor navigation.
- Embedded HTML section renderer or artifact iframe/sandboxed viewer.
- Metadata sidebar (report definition id/version/options/source run ids).

**Key interactions**
- Jump to section from dashboard context.
- Compare section across two runs.

**Most important data shown**
- Exact report artifacts generated by current system + machine-readable summary blocks.

### F) Strategy Comparison View (recommended)

**Purpose**
- Side-by-side research workflow for candidate selection.

**Main UI components**
- Multi-column KPI comparison table.
- Overlaid equity/drawdown charts.
- Session/time bucket difference charts.

**Key interactions**
- Add/remove strategies from comparison.
- Highlight deltas relative to baseline strategy.

**Most important data shown**
- Relative performance/stability and behavioral differences.

---

## 4.2 Domain model

Below is a concrete model that extends existing concepts.

### 1) Strategy
- `strategy_id` (stable key)
- `name`
- `family` (e.g., ORB, trend template)
- `template_id`
- `style_tags[]` (breakout, mean_reversion, etc.)
- `directionality` (long/short/both)
- `entry_model`
- `exit_model`
- `stop_target_style`
- `default_instrument`
- `default_timeframe`
- `created_at`, `updated_at`

### 2) StrategyRun
- `run_id` (maps to AnalysisPackage run)
- `strategy_id`
- `run_label`
- `report_config_id`
- `test_window_start`, `test_window_end`
- `in_sample_flag` / `out_of_sample_flag`
- `market_regime_tags[]`
- `status`
- `generated_at`
- `source_hash` (inputs fingerprint)

### 3) ReportDefinition
- `report_def_id`
- `source_yaml_path`
- `source_yaml_hash`
- `title`
- `section_defs[]` (ids + options)
- `ui_category`
- `version`

### 4) ReportInstance
- `report_instance_id`
- `report_def_id`
- `run_id` (nullable for multi-run report)
- `artifact_uri` (html)
- `summary_json_uri`
- `created_at`
- `build_version`

### 5) MetricSnapshot
- `metric_snapshot_id`
- `run_id`
- `metric_scope` (overall, session, day_of_week, hour_of_day, long_only, short_only)
- `metric_name`
- `metric_value_num`
- `metric_value_text`
- `unit`
- `window_label`

### 6) TradeSessionWindow
- `session_window_id`
- `name` (London, NY_open, etc.)
- `tz` (America/Denver canonical mapping)
- `start_local_time`, `end_local_time`
- `days_of_week[]`

### 7) StrategyTag / Characteristic
- `tag_id`
- `tag_type` (style, pattern_family, stop_target, condition)
- `tag_value`
- `source` (yaml, analysis, manual)
- many-to-many via `strategy_tag_map`

### 8) ChartArtifact
- `chart_id`
- `run_id`
- `chart_type` (equity_curve, drawdown, distribution, etc.)
- `artifact_uri` (png/svg/html/json)
- `spec_json` (optional plotting spec)
- `generated_at`

### 9) BacktestResult
- `backtest_id`
- `run_id`
- `engine_version`
- `instrument`
- `timeframe`
- `trade_count`
- `net_profit`
- `profit_factor`
- `max_drawdown`
- `sharpe`
- `stability_score`

### 10) Instrument
- `instrument_id`
- `symbol`
- `asset_class`
- `exchange`

### 11) Timeframe
- `timeframe_id`
- `label` (1m, 5m, 15m, range bars, tick)
- `bar_type` (time/range/tick/volume)
- `bar_value`

### 12) EntryExitModel (optional normalized table)
- `model_id`
- `model_type` (entry or exit)
- `name`
- `parameters_json`

---

## 4.3 Filtering system design

## Data modeling for filters

Use two layers:

1. **Normalized source tables** (authoritative)
2. **Denormalized strategy_filter_index** (fast faceted lookup)

`strategy_filter_index` row granularity: one row per `(run_id, strategy_id, test_window)` with flattened filterable fields.

Example columns:
- categorical: `session_flags` (bitset/jsonb), `directionality`, `style_primary`, `style_tags`, `instrument`, `timeframe`, `stop_target_style`, `pattern_family`
- numeric: `trade_freq_per_day`, `net_profit`, `max_dd`, `pf`, `sharpe`, `stability`
- temporal: `test_window_start/end`, `generated_at`
- text/search: `search_text_tsv`

## Backend support

- Faceted query endpoint accepts structured filter DSL.
- Use PostgreSQL indexes:
  - btree on common single columns
  - GIN on JSONB/tags arrays
  - BRIN for large time-based partitions
  - materialized aggregates for facet counts if needed
- Return both rows and facet counts in one response.

## Frontend behavior

- Maintain filter state in URL query params for shareable research links.
- Apply optimistic UI updates; debounce server calls for sliders/text.
- Show active filter pills with one-click remove.
- Persist saved presets server-side (`saved_filter_set`).

## Performance strategy at scale

- Server-side pagination + cursor-based API.
- Precompute heavy derived metrics in indexer step.
- Cache frequent query signatures (Redis optional).
- Incremental re-index on new run/report only.
- Keep table virtualization in UI (no client-side rendering of huge datasets).

---

## 4.4 Drilldown and chart architecture

Drilldown chain:

1. **Catalog row** (`strategy_id`, `run_id`) 
2. **Strategy detail** (fetch strategy + run summaries + available artifacts)
3. **Report instance** (open existing generated report/sections)
4. **Trade-level endpoints** (paginated trades and derived distributions)
5. **Chart drilldown** (interactive charts backed by precomputed series)

### Linking architecture

- Every metric/chart shown in UI must carry provenance keys:
  - `strategy_id`, `run_id`, `report_instance_id`, `source_section_id`
- Artifact registry table maps logical chart/report IDs to URIs.
- Chart APIs serve either:
  - precomputed JSON series (preferred for speed), or
  - static image/html artifact URIs from existing reports.

### Required drilldown chart types

- Equity curve
- Drawdown curve
- Trade PnL distribution (histogram/box)
- Session performance matrix
- Day-of-week / hour-of-day heatmaps
- Trade examples list (if trade row data and chart snapshots exist)

---

## 4.5 Data architecture

## Storage strategy

Use **both files and DB**:

- Files/object storage: canonical report artifacts (HTML/images/json produced by ta_foundation).
- DB: metadata index + filter/search + relational linkage.

## Metadata index

Create normalized core tables + denormalized filter index.

## Transform to UI-friendly data

Introduce a new post-report step:
- `index_reports` job reads run outputs + report summary JSON files.
- Produces:
  - canonical rows in DB tables
  - precomputed chart series JSON (optional) for API use

## Versioning

- Version every run/report by content hash and generated timestamp.
- Keep immutable historical `StrategyRun`/`ReportInstance`; mark latest via view/materialized table.

## Incremental updates

- Detect new/changed outputs via manifest/hash diff.
- Upsert changed strategy/run/report rows.
- Recompute only impacted index rows.

## Keeping website in sync

- Option A: trigger indexer after each report build (preferred).
- Option B: periodic polling job scans output manifests.
- Expose data freshness timestamp in UI.

---

## 4.6 Backend design

## API style

- REST + JSON for core operations (simple and practical).
- Optional GraphQL later for custom analytic views.

## Recommended backend shape

**Hybrid service**:
- FastAPI monolith (single deployable) with modules:
  - query/filter service
  - strategy/run/report service
  - artifact proxy/redirect service

This gives simplicity of monolith while still DB-backed for scale.

## Main endpoint categories

1. `GET /strategies` (catalog with filters/sort/page)
2. `POST /strategies/search` (advanced DSL)
3. `GET /strategies/{strategy_id}` (metadata + latest runs)
4. `GET /runs/{run_id}` (run detail + KPIs)
5. `GET /runs/{run_id}/metrics` (scoped metrics)
6. `GET /runs/{run_id}/trades` (paginated trades)
7. `GET /runs/{run_id}/charts/{chart_type}` (series/artifact)
8. `GET /reports/{report_instance_id}` (report metadata + uri)
9. `GET /filters/facets` (facet counts for active query)
10. `POST /compare` (multi-strategy comparison payload)
11. `POST /saved-filters` / `GET /saved-filters`
12. `POST /favorites` / `GET /favorites`

## Query patterns

- Faceted filtering over denormalized index.
- Detail endpoints hit normalized tables.
- Artifact endpoints return signed URL/redirect to file storage.

---

## 4.7 Frontend design

## Recommended stack

- React + TypeScript
- TanStack Router (URL-state-centric navigation)
- TanStack Query (server-state caching)
- Zustand or Redux Toolkit for local UI state
- AG Grid or TanStack Table + virtualization
- ECharts/Plotly for interactive analytics charts

## Research workflow UX

- Left persistent filters + right results grid.
- Keyboard-friendly controls and multi-select compare basket.
- Sticky KPI columns and fast sort toggles.
- Detail route keeps backlink state to preserve exploration context.

## Large dataset handling

- Server-side pagination/sorting/filtering.
- Row virtualization.
- Lazy-load heavy chart tabs.
- Cache by query key + prefetch adjacent pages.

---

## 4.8 Integration with `report.yaml`

Additive evolution only (non-breaking).

## Proposed `report.yaml` extensions

Add optional metadata blocks:

- `ui:` at report level
  - `category`
  - `tags`
  - `priority`
- section-level `summary_exports` declaration
  - each section can emit machine-readable summary JSON schema id

Example concept:

```yaml
report:
  id: strategy_research
  title: Strategy Research Report
  ui:
    category: strategy_analysis
    tags: [research, dashboard]
sections:
  - id: run_kpi_cards
    options: {...}
    summary_exports:
      - schema: kpi_snapshot_v1
  - id: equity_curve_comparison
    options: {...}
    summary_exports:
      - schema: equity_curve_series_v1
```

## Machine-readable summaries

- Each key section writes compact JSON summary artifact alongside HTML.
- Dashboard indexer consumes these summaries (not raw HTML scraping).

## Separate index generation step

Yes—recommended. Keep current render pipeline unchanged; append `index_reports` stage after report build.

## Avoid breaking existing pipeline

- Make all new YAML keys optional.
- If missing, fallback to existing behavior.
- Indexer should tolerate legacy reports with partial summary data.

---

## 4.9 Extensibility design

Design hooks:

- **New strategy families/types:** add tags/metadata values, no schema rewrite.
- **New report types/charts:** register new `chart_type` + summary schema.
- **Comparison:** already first-class via `/compare` and compare view.
- **Saved filters/favorites:** separate user-scoped tables.
- **AI summaries:** `strategy_insights` table storing generated narrative + evidence links.
- **Similarity recommendations:** embedding/index table using strategy feature vectors.

---

## 4.10 Implementation plan

## Phase 1 — Fastest usable version

**Scope**
- Strategy catalog + basic filters + strategy detail + links to existing report HTML.

**Engineering tasks**
- Create DB schema for strategy/run/report metadata.
- Build indexer for existing outputs.
- Implement `/strategies`, `/strategies/{id}`, `/reports/{id}`.
- Build basic React explorer table + filter panel.

**Risks**
- Inconsistent legacy output formats.

**Postpone**
- Deep interactive charts, comparison, saved views.

## Phase 2 — Strong filtering + detail depth

**Scope**
- Full faceted filtering and run-level detail with scoped metrics.

**Engineering tasks**
- Add denormalized `strategy_filter_index`.
- Add facet endpoint and advanced filter DSL.
- Add run detail tabs (metrics by session/time/day).

**Risks**
- Filter schema drift if metadata not standardized.

**Postpone**
- AI/semantic features.

## Phase 3 — Advanced analytics + comparison

**Scope**
- Chart drilldown + strategy comparison workspace.

**Engineering tasks**
- Precompute chart series endpoints.
- Build compare page and delta visualizations.
- Add trade examples and richer report-section linking.

**Risks**
- Chart performance and payload size.

**Postpone**
- Similarity recommendations.

## Phase 4 — AI/insight features

**Scope**
- AI-generated summaries + similarity recommendations.

**Engineering tasks**
- Generate embeddings from strategy features.
- Build insight generation pipeline with provenance.
- Add “similar strategies” endpoint and UI module.

**Risks**
- Hallucination/trust; need evidence-backed outputs.

**Postpone**
- Fully autonomous strategy ranking.

---

## 5. Tradeoffs

## File-based vs database-backed

- File-only: simplest, but poor query performance and hard faceting at scale.
- DB-backed: adds infra complexity, but required for fast multi-dimensional filtering.
- **Recommendation:** hybrid (files canonical + DB index).

## Precomputed summaries vs live queries

- Live from raw outputs: flexible but slow and compute-heavy.
- Precomputed: fast and stable for dashboard UX.
- **Recommendation:** precompute common metrics/charts; keep raw drilldown links.

## Static site vs dynamic app

- Static: easy deploy, weak for heavy filtering/drilldown.
- Dynamic: supports user state, saved filters, compare flows.
- **Recommendation:** dynamic SPA + API.

## Embedded artifacts vs on-demand chart generation

- Embedded artifacts: reproducible and cheap to serve.
- On-demand: interactive but compute-heavy.
- **Recommendation:** serve precomputed JSON series for interactivity; fallback to artifacts.

## Denormalized index vs normalized model

- Normalized only: integrity but slower filtering.
- Denormalized only: fast but harder consistency.
- **Recommendation:** both (normalized source + denormalized filter index).

---

## 6. MVP plan (practical recommendation)

Build an MVP in 6–8 weeks:

1. Add indexer that ingests existing report outputs into DB.
2. Ship strategy explorer with top filters:
   - session
   - directionality
   - style
   - instrument/timeframe
   - core KPIs (net profit, DD, PF, trade count)
3. Strategy detail page with:
   - metadata
   - KPI cards
   - run list
   - deep links to existing report artifacts
4. Add basic compare (2–4 strategies).

Do **not** start with custom chart generation engine; reuse existing artifacts + limited precomputed series.

---

## 7. Example schemas/endpoints

## 7.1 Example component diagram (text)

```text
[ta_foundation Pipeline]
   -> generates -> [Report Artifacts (HTML/PNG/JSON) + Manifest]
   -> triggers -> [Dashboard Indexer]

[Dashboard Indexer]
   -> reads -> Artifacts + Summary JSON
   -> writes -> [PostgreSQL: normalized tables + filter index]

[Frontend SPA]
   -> queries -> [Dashboard API (FastAPI)]
[Dashboard API]
   -> reads -> PostgreSQL
   -> resolves -> Artifact URIs (filesystem/object storage)
```

## 7.2 Example data flow diagram (text)

```text
1) Research run completes in ta_foundation
2) report.yaml report generation outputs HTML + section summaries
3) manifest records run_id/report_instance/artifact paths
4) indexer consumes manifest diff and upserts DB rows
5) user loads catalog page
6) frontend sends filter query -> /strategies/search
7) API queries strategy_filter_index and returns rows + facets
8) user opens strategy detail -> /strategies/{id}
9) user opens chart/report -> /runs/{run_id}/charts/* or /reports/{id}
10) API returns series JSON or artifact URL
```

## 7.3 Proposed folder/module structure

```text
src/ta_foundation/
  dashboard/
    indexer/
      manifest_reader.py
      summary_parsers.py
      upsert_service.py
    api/
      main.py
      routes/
        strategies.py
        runs.py
        reports.py
        filters.py
        compare.py
      services/
        strategy_query_service.py
        facet_service.py
        artifact_service.py
      schemas/
        strategy.py
        run.py
        filter.py
    db/
      models.py
      migrations/

web/strategy_dashboard/
  src/
    pages/
      StrategyCatalogPage.tsx
      StrategyDetailPage.tsx
      ChartDrilldownPage.tsx
      ComparePage.tsx
    components/
      FilterPanel.tsx
      StrategyTable.tsx
      KpiCards.tsx
      ReportArtifactViewer.tsx
    state/
      filters.ts
      compare.ts
    api/
      client.ts
      strategies.ts
      runs.ts
      reports.ts
```

## 7.4 Example API endpoint list

```text
GET    /api/v1/strategies
POST   /api/v1/strategies/search
GET    /api/v1/strategies/{strategy_id}
GET    /api/v1/strategies/{strategy_id}/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/metrics?scope=session
GET    /api/v1/runs/{run_id}/trades?page=1&page_size=100
GET    /api/v1/runs/{run_id}/charts/equity_curve
GET    /api/v1/runs/{run_id}/charts/drawdown
GET    /api/v1/reports/{report_instance_id}
GET    /api/v1/filters/facets
POST   /api/v1/compare
POST   /api/v1/saved-filters
GET    /api/v1/saved-filters
POST   /api/v1/favorites
GET    /api/v1/favorites
```

## 7.5 Example schema definitions

```sql
-- normalized core
create table strategy (
  strategy_id text primary key,
  name text not null,
  family text,
  template_id text,
  directionality text,
  entry_model text,
  exit_model text,
  stop_target_style text,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table strategy_run (
  run_id text primary key,
  strategy_id text not null references strategy(strategy_id),
  report_config_id text,
  test_window_start timestamptz,
  test_window_end timestamptz,
  generated_at timestamptz not null,
  source_hash text,
  status text not null
);

create table report_instance (
  report_instance_id text primary key,
  run_id text references strategy_run(run_id),
  report_def_id text not null,
  artifact_uri text not null,
  summary_json_uri text,
  created_at timestamptz not null
);

-- denormalized filter index
create table strategy_filter_index (
  run_id text primary key references strategy_run(run_id),
  strategy_id text not null references strategy(strategy_id),
  directionality text,
  style_tags jsonb,
  session_flags jsonb,
  instrument text,
  timeframe text,
  stop_target_style text,
  trade_freq_per_day numeric,
  net_profit numeric,
  max_drawdown numeric,
  profit_factor numeric,
  sharpe numeric,
  stability_score numeric,
  test_window_start timestamptz,
  test_window_end timestamptz,
  search_text tsvector
);

create index sfi_style_tags_gin on strategy_filter_index using gin(style_tags);
create index sfi_session_flags_gin on strategy_filter_index using gin(session_flags);
create index sfi_search_idx on strategy_filter_index using gin(search_text);
create index sfi_net_profit_idx on strategy_filter_index(net_profit);
create index sfi_max_dd_idx on strategy_filter_index(max_drawdown);
```

---

## 8. Final recommendation

Implement a **hybrid, index-backed dashboard** that treats `ta_foundation` report artifacts as canonical outputs and adds a thin metadata/index layer for speed.

If you do only one thing first: build the **indexer + strategy catalog with faceted filtering + deep links to existing reports**. That delivers immediate research value with minimal disruption to your mature reporting pipeline.
