# External Projects Deployment Plan

## Strategic Overview

This document outlines a plan to decompose **ta_foundation** into a collection of independent, reusable external projects. Each project will expose a single capability (or tightly-coupled capability cluster) via a clean web API, CLI, or library interface. This enables:

1. **Parallel Development** — teams can work on independent capabilities without blocking each other
2. **Independent Deployment** — capabilities can be versioned, released, and scaled separately
3. **Reusability** — D:\strategy-analysis (MA-cross) and future domain projects can compose capabilities as microservices
4. **Testability** — each project has tight unit/integration test scope
5. **Technology Flexibility** — projects can use different tech stacks (Python, Go, Rust C#) for their core logic

### Current Reference Implementation

**D:\strategy-analysis** is the first prototype of this architecture:
- Focuses on MA-cross analysis and reporting
- Packages multiple capabilities (strategy discovery, backtesting analysis, regime analysis, pattern recognition) into a cohesive web UI
- Will call external capability projects via APIs (REST/gRPC)
- Serves as the integration test for the decomposed architecture

---

## Capability Inventory & Decomposition

### Tier 1: Core Analysis Engines (Highest Priority)

These are the "meat" — the complex algorithms that drive trading insights.

#### 1.1 — Strategy Discovery Engine
**Current Location:** `ta_foundation/analysis/strategy_discovery/`

**What It Does:**
- Ingests trade-level backtest data + market data
- Discovers entry/filter/exit rules that beat baseline
- Walk-forward validation, metrics computation, ranking
- Generates NinjaTrader strategy templates + parameter recommendations
- Produces candidate scorecards with statistical robustness metrics

**External Project:** `strategy-discovery-engine`

**API Surface:**
```
POST /v1/discovery/run
  Input: backtest_trades (parquet/CSV), market_bars (OHLCV), config
  Output: ranked_candidates (JSON), templates (XML), manifests
  
GET /v1/discovery/jobs/{job_id}
  Status, progress, partial results

DELETE /v1/discovery/jobs/{job_id}
  Cancel long-running job
```

**Dependencies to Expose:**
- `analysis/entry_strategies/` (all 8 families: candle, ma, bb, orb, breakout, pullback, level, lcr)
- `analysis/strategy_discovery/mae_mfe.py`
- `analysis/strategy_discovery/exit_discovery.py`
- `analysis/strategy_discovery/nt_template_generator.py`
- `analysis/exits/policies.py` + `simulate.py`
- `marketdata/resample.py` (timeframe conversion)

**Internal Data Model:** `AnalysisPackage` (simplified, export as JSON)

**Deployment Target:** Docker container, 4GB RAM, 2 CPU cores for MVP

**MVP Scope (Phase 1):**
- Entry discovery only (no exit sweep)
- Single-timeframe
- JSON config input
- CSV/Parquet trade export

**Phase 2:** Full exit discovery, walk-forward, hold-out robustness, parameter sensitivity, cross-run ranking

---

#### 1.2 — Pattern Engine
**Current Location:** `ta_foundation/analysis/pattern_engine/`

**What It Does:**
- Sweeps parameterized pattern templates (ORB, RSI exhaustion, reversals, etc.) over market bars
- Clusters similar patterns, runs Monte Carlo, robustness CV
- Produces parquet artifacts (references, not embedded)
- Generates signal frequency + win-rate profiles

**External Project:** `pattern-engine-service`

**API Surface:**
```
POST /v1/patterns/sweep
  Input: market_bars (OHLCV), template_ids (list), param_ranges
  Output: sweep_results (parquet ref), diagnostics (JSON)
  
GET /v1/patterns/templates
  List available templates + param schema

POST /v1/patterns/monte-carlo
  Input: sweep_results_ref
  Output: robustness_stats (JSON)
```

**Dependencies to Extract:**
- `analysis/pattern_engine/templates/` (all template definitions)
- `analysis/pattern_engine/builtins.py` (template registry)
- `analysis/pattern_engine/engine.py` (sweep orchestrator)
- `analysis/pattern_engine/cluster.py`, `monte_carlo.py`, `robustness_cv.py`
- `marketdata/resample.py`

**Deployment Target:** Docker, 2 GB RAM (can be auto-scaled for parallel sweeps)

**MVP Scope:**
- 3–5 representative templates (ORB, box, reversal)
- Single timeframe
- Monte Carlo + basic CV
- CSV/Parquet output

**Phase 2:** All current templates, multi-timeframe, advanced robustness, streaming mode

---

#### 1.3 — Regime Recommender
**Current Location:** `ta_foundation/analysis/regime_recommender/`

**What It Does:**
- Classifies market regime (trending/ranging, vol expansion/compression, session context)
- Generates trading recommendations per regime (which entry families work? which exits?)
- Stores regime classifier and recommendations as artifacts

**External Project:** `regime-classifier-service`

**API Surface:**
```
POST /v1/regime/classify
  Input: market_bars (OHLCV), config (ADX period, ATR period, vol thresholds)
  Output: regime_labels (JSON time-series), features (ADX, ATR, etc.)

POST /v1/regime/recommend
  Input: regime_labels, backtest_data (optional)
  Output: signal_recommendations (JSON)
```

**Dependencies:**
- `analysis/regime_recommender/classifier.py`
- `analysis/regime_recommender/recommender.py`
- `analysis/indicators/` (ADX, ATR implementations)

**Deployment Target:** Lightweight, 500 MB RAM

**MVP Scope:**
- ADX/ATR regime classification only
- Simple thresholds
- Recommendation templates from knowledge base

---

#### 1.4 — Exit Policy Simulator
**Current Location:** `ta_foundation/analysis/exits/`

**What It Does:**
- Simulates trade outcomes under different exit policies (fixed stop/target, ATR trail, Chandelier, giveback, breakeven)
- Requires tick-level data for accuracy
- Used to recommend stop/target ticks and evaluate policy robustness

**External Project:** `exit-policy-simulator`

**API Surface:**
```
POST /v1/exits/simulate
  Input: trades (entry_dt, entry_px, direction), ticks (time-series), policies ([policy_ids])
  Output: exit_results (JSON rows: entry/exit px, exit_reason, pnl_ticks, mae/mfe)

GET /v1/exits/policies
  List available policies + their parameters
```

**Dependencies:**
- `analysis/exits/policies.py`
- `analysis/exits/simulate.py`
- `marketdata/tick_cache.py`

**Deployment Target:** CPU-intensive for large backtest volumes; 2–4 cores recommended

**MVP Scope:**
- Fixed stop/target, ATR trail, Chandelier (3 policies)
- Single-contract
- Tick CSV input

**Phase 2:** All policies, multi-contract, streaming mode, live-tick integration

---

#### 1.5 — MA Anchor Interaction Analyzer
**Current Location:** `ta_foundation/analysis/ma_structure/`

**What It Does:**
- Detects moving average anchors (support/resistance confluences)
- Segments market structure relative to multiple MA families
- Scores entries and exits relative to anchor positions
- Recommends TP/SL zones based on anchor interaction

**External Project:** `ma-anchor-analyzer`

**API Surface:**
```
POST /v1/ma/analyze
  Input: market_bars (OHLCV), ma_configs ([{length, source}])
  Output: anchors (JSON), segments (JSON), tp_sl_candidates (JSON)

POST /v1/ma/backtest-alignment
  Input: trades (entry/exit times/prices), market_bars, ma_configs
  Output: trade_anchor_alignment (JSON)
```

**Dependencies:**
- `analysis/ma_structure/anchors.py`
- `analysis/ma_structure/segment_detection.py`
- `analysis/ma_structure/tp_sl_engine.py`
- `analysis/ma_structure/regime_context.py`

**Deployment Target:** 500 MB RAM, lightweight

**MVP Scope:**
- SMA families (20, 50, 200)
- Segment detection + TP/SL scoring
- JSON output

---

#### 1.6 — Large Candle Excursion Analyzer
**Current Location:** `ta_foundation/analysis/large_candle_excursion/`

**What It Does:**
- Identifies abnormally large candles
- Traces tick-level reversal behavior within those candles
- Used to understand when large moves mean opportunity vs. mean reversion trap

**External Project:** `large-candle-analyzer`

**API Surface:**
```
POST /v1/candles/analyze
  Input: bars (OHLCV), ticks (optional time-series), config (size_threshold_atr)
  Output: large_candles (JSON), reversal_analysis (JSON)
```

**Dependencies:**
- `analysis/large_candle_excursion/tick_analyzer.py`
- `analysis/indicators/atr.py`

**Deployment Target:** 512 MB RAM

---

### Tier 2: Data I/O & Market Infrastructure

These services handle data ingestion, normalization, storage, and sharing.

#### 2.1 — Market Data Service
**Current Location:** `ta_foundation/marketdata/`

**What It Does:**
- Parses and normalizes NinjaTrader exports (`*.Last.txt`, `*Tick.Last.txt`)
- Provides OHLCV bars at any requested timeframe (1m, 5m, 15m, 1h, etc.)
- Caches tick parquet files for fast reloads
- Validates data quality (gaps, reverses)

**External Project:** `market-data-service`

**API Surface:**
```
POST /v1/market/ingest
  Input: nt_export_files (upload)
  Output: market_id (UUID), status

GET /v1/market/{market_id}/bars
  Query: instrument, contract, from_dt, to_dt, timeframe
  Output: OHLCV (CSV/Parquet stream)

GET /v1/market/{market_id}/ticks
  Query: instrument, contract, from_dt, to_dt
  Output: tick stream (parquet/CSV)

GET /v1/market/catalog
  List all loaded markets, instruments, contract ranges
```

**Dependencies:**
- `parsers/ninjatrader/minute_bars_last_txt.py`
- `parsers/ninjatrader/tick_last_txt.py`
- `marketdata/store.py`
- `marketdata/tick_cache.py`
- `marketdata/resample.py`

**Deployment Target:** Database backend (file-based or PostgreSQL)

**MVP Scope:**
- Single market (NQ 06-26)
- Minute + tick OHLCV
- In-memory cache (Redis optional)

**Phase 2:** Multi-market, S3 backing, streaming ingestion

---

#### 2.2 — NT Export Parser Service
**Current Location:** `ta_foundation/parsers/ninjatrader/`

**What It Does:**
- Parses NinjaTrader CSV exports (trades, analysis-by-day, summary, settings, optimization)
- Normalizes into standard schemas
- Validates and flags data quality issues

**External Project:** `nt-export-parser` (could be lightweight library)

**API Surface:**
```
POST /v1/parse/trades
  Input: CSV data
  Output: parsed trades (JSON/Parquet)

POST /v1/parse/summary
  Input: CSV data
  Output: KPI block (JSON)

POST /v1/parse/optimization
  Input: CSV data
  Output: optimization results (JSON/Parquet)
```

**Dependencies:**
- All parsers in `parsers/ninjatrader/`

**Deployment Target:** Lightweight library or serverless function

---

### Tier 3: Report Generation & Visualization

#### 3.1 — Report Builder Service
**Current Location:** `ta_foundation/reports/html/`

**What It Does:**
- Assembles HTML reports from analysis results
- Supports 100+ report sections (KPI cards, drawdown curves, discovery tables, etc.)
- YAML-configurable section selection
- Base64-embeds images and styles (self-contained HTML)

**External Project:** `report-builder-service`

**API Surface:**
```
POST /v1/reports/build
  Input: analysis_results (JSON/refs), report_config (YAML)
  Output: report.html (single file, self-contained)

GET /v1/reports/sections
  List available section keys + schema

GET /v1/reports/presets
  List curated report templates
```

**Dependencies:**
- `reports/html/registry.py` (section registry)
- `reports/html/sections/` (all section renderers)
- `reports/html/builder.py`
- `reports/html/embed.py` (image/asset embedding)
- `reports/html/theme.py` (CSS)

**Deployment Target:** 2 GB RAM, fast I/O

**MVP Scope:**
- 15–20 core sections
- JSON input (no parquet refs)
- Simple presets

**Phase 2:** All sections, multi-format (HTML, PDF, Jupyter), real-time streaming

---

#### 3.2 — Execution Terminal / Decision Dashboard
**Current Location:** `ta_foundation/web/`

**What It Does:**
- Web UI for reviewing discovered strategies, optimizer runs, deployment decisions
- Real-time job status + result drill-down
- Template + recipe management

**External Project:** `trading-decision-dashboard` (web app)

**Architecture:** React/Vue frontend + Python FastAPI backend

**Key Pages:**
- Strategy Discovery Workbench (filter, rank, drill into candidate)
- Optimizer Run Console (phases 1→2→3, final backtests, recommendations)
- Deployment Matrix Builder (lane grid, coverage, template selection)
- Market Regime Monitor (today's regime classification + signal opportunity)
- Backtester Result Viewer (run cards, daily drill-down, trade-by-trade detail)

**Dependencies:**
- Calls all downstream analysis services via HTTP

**Deployment Target:** Docker Compose (frontend + backend + job queue)

---

### Tier 4: Execution & Automation Bridge

#### 4.1 — NinjaTrader Strategy Loop Executor
**Current Location:** `ta_foundation/nt_strategy_loop/`

**What It Does:**
- Autonomous strategy generation → install → compile → repair → optimize loop
- Manages compile errors, retry logic, artifact versioning
- Bridges discovered edge constraints to NinjaTrader validation

**External Project:** `nt-strategy-loop-executor`

**API Surface:**
```
POST /v1/strategies/generate-and-validate
  Input: strategy_spec (JSON), market_data_ref
  Output: job_id, session_folder

GET /v1/strategies/jobs/{job_id}/status
  Real-time progress + artifacts

GET /v1/strategies/jobs/{job_id}/result
  Compile-clean strategy + optimization package (if completed)
```

**Dependencies:**
- `nt_strategy_loop/*` (session, authoring, installer, compile_worker, repair, optimizer_bridge)
- **External:** `D:\Backup\projects\PythonProject\NinjatraderDocScrapper` (strategy factory)
- **External:** `D:\ninjatraderOptimizer` (RunBatch IPC, AddOn)

**Deployment Target:** Requires NinjaTrader + compiler installed; typically on dedicated machine

**Important Note:** This project has a **hard dependency** on licensed NinjaTrader software. Not suitable for cloud scaling.

---

#### 4.2 — Execution Bridge (NT ↔ Live Trading)
**Current Location:** `ta_foundation/strategies/TaFoundationExecutionBridge/`

**What It Does:**
- Sends order/position/risk commands to live NinjaTrader accounts
- Monitors execution status, records fills, heartbeat
- Integrates with NinjaAccountManager for position tracking

**External Project:** `nt-execution-bridge-service`

**API Surface:**
```
POST /v1/execution/order
  Input: symbol, direction, qty, order_type, price
  Output: order_id, status

GET /v1/execution/position/{symbol}
  Current position, PnL

POST /v1/execution/close-position
  Input: symbol
  Output: exit order details

WebSocket /ws/execution/stream
  Real-time fills, position updates
```

**Dependencies:**
- `strategies/TaFoundationExecutionBridge/` (C# AddOn source)
- **External:** `D:\NinjaAccountManager` (account state + order API)

**Deployment Target:** Runs on same machine as NinjaTrader; exports via WebSocket/REST to remote services

---

### Tier 5: Prediction & Forecasting

#### 5.1 — Intraday Prediction Engine
**Current Location:** `ta_foundation/prediction/`

**What It Does:**
- Trains/scores models for next-hour direction, returns, drawdown
- Scores discovered strategies against market regime
- Horizon prediction (multi-day forecast)
- Produces daily prediction manifests

**External Project:** `prediction-engine-service`

**API Surface:**
```
POST /v1/predict/daily
  Input: market_bars (recent months), backtest_pool
  Output: daily_predictions (JSON), confidence scores, regime forecast

POST /v1/predict/horizon
  Input: market_bars, horizon_days
  Output: multi-day forecast, win probability
```

**Dependencies:**
- `prediction/run_prediction.py`
- `prediction/backtest_horizon_predictions.py`
- ML models (sklearn, joblib-persisted)

**Deployment Target:** 4 GB RAM, GPU optional

---

### Tier 6: Specialized Domain Projects

#### 6.1 — MA-Cross Analysis Project (Reference)
**Location:** `D:\strategy-analysis` (existing)

**Purpose:** Demonstrates composition of multiple capabilities for a single trading strategy family

**Composes:**
- Market Data Service (OHLCV bars)
- Strategy Discovery Engine (entry/filter/exit discovery for MA crossover)
- Exit Policy Simulator (best stops for MA cross)
- MA Anchor Analyzer (confluence scoring)
- Regime Recommender (entry timing)
- Report Builder (custom MA-cross report presets)
- Execution Terminal (daily decision board for MA-cross deployment)

**Pattern:** Every domain-specific project should follow this "capability composition" model.

#### 6.2 — Future: OrderFlow Analysis Project
**Template:** Similar to MA-Cross, but focused on order flow signal discovery
- Uses tick data from Market Data Service
- Composition: Pattern Engine + Order Flow Detector + Exit Simulator + Execution Terminal

#### 6.3 — Future: Options Strategy Project
**Template:** Derivatives pricing + delta/IV analysis
- Composes: Market Data + Pattern Engine + Exit Simulator + Custom Options Analytics

---

## Integration Architecture

### Deployment Topology (Multi-Machine)

```
┌─────────────────────────────────────────────────────────────────────┐
│                   Trading Decision Dashboard (Web UI)                │
│              (React + FastAPI, orchestrates everything)              │
└──────────┬──────────────────────────┬──────────────────────┬─────────┘
           │                          │                      │
      ┌────▼─────┐            ┌──────▼───────┐    ┌────────▼──────┐
      │ Market   │            │ Strategy     │    │ Regime        │
      │ Data     │───┐        │ Discovery    │    │ Recommender   │
      │ Service  │   │        │ Engine       │    │               │
      └──────────┘   │        └──────────────┘    └───────────────┘
                     │
                     ├──────────┬─────────┬────────┬────────┐
                     │          │         │        │        │
                  ┌──▼──┐  ┌───▼──┐  ┌──▼──┐  ┌──▼──┐  ┌──▼──┐
                  │Exit │  │MA    │  │Large│  │Patt.│  │Pred-│
                  │Sim. │  │Anch. │  │Candle  Engine  iction│
                  └─────┘  └──────┘  └─────┘  └─────┘  └─────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │ Report Builder      │
                          │ (HTML assembly)     │
                          └─────────────────────┘
                                     
┌─────────────────────────────────────────────────────────────────────┐
│        NinjaTrader Machines / Execution Bridge Layer                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐   │
│  │ NT Compile Loop  │  │ Strategy Analyzer│  │ Live Execution │   │
│  │ (repair + build) │  │ (optimizer phase)│  │ (order bridge) │   │
│  └──────────────────┘  └──────────────────┘  └────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Discovery & Orchestration

**Phase 1 (MVP):** Docker Compose on dev machine + hardcoded endpoints
- All services on localhost, connect via `localhost:port`
- Simple volume mounts for persistent state

**Phase 2:** Kubernetes + Service Mesh (Istio) for production
- Services can scale independently
- Built-in service discovery, load balancing, metrics
- Helm charts for each service

### Data Exchange Protocols

**Option A: REST (Simplest, HTTP-based)**
- JSON request/response bodies
- Parquet/CSV streaming via chunked encoding
- Open in browser, easy to debug

**Option B: gRPC (Higher throughput)**
- Protocol Buffers schema + code generation
- Bidirectional streaming
- Better for tight service-to-service coupling

**Recommendation:** Start with REST for MVP, migrate gRPC if bottleneck emerges.

### Artifact & State Persistence

**Tier 1: Hot (Memory + Fast Disk)**
- Current market data cache (Redis if distributed)
- Active job state (SQLite or PostgreSQL)
- Session artifacts (`.ta_artifacts/<session_id>/`)

**Tier 2: Cold (S3 / Blob Storage)**
- Historical market data (parquet files)
- Completed job results (reports, templates, manifests)
- Backups of analysis runs

**MVP Approach:** File-system on dev, S3 + PostgreSQL on staging/prod

---

## Build Order & Dependencies

### Phase 1: Foundation (Weeks 1–3)
Build the "data plumbing" first since all analysis depends on it.

1. **Market Data Service** (highest-priority dependency)
   - Parse NinjaTrader exports (trades, bars, ticks)
   - Serve OHLCV at any timeframe
   - Cache/normalize

2. **Exit Policy Simulator** (isolated, no dependencies)
   - Simulates stop/exit outcomes
   - Can test independently of market data

3. **Report Builder Service** (depends on #1)
   - Consume JSON analysis results
   - Assemble HTML

**Deliverable:** Local Docker Compose with 3 services, sample API calls

---

### Phase 2: Core Analytics (Weeks 4–7)
Build the domain engines.

4. **Strategy Discovery Engine**
   - Entry discovery, filter discovery, walk-forward validation
   - Depends on: Market Data Service

5. **Pattern Engine**
   - Template sweep, clustering, Monte Carlo
   - Independent of Market Data (can run batch)

6. **MA Anchor Analyzer**
   - Anchor detection, segment scoring
   - Depends on: Market Data Service

7. **Regime Recommender**
   - Regime classification, recommendations
   - Depends on: Market Data Service

8. **Large Candle Analyzer**
   - Candle detection, tick-path tracing
   - Depends on: Market Data Service (optional: ticks)

**Deliverable:** 5 new Docker services, sample trading discovery workflow

---

### Phase 3: Execution & Integration (Weeks 8–11)
Wire up live execution + orchestration.

9. **NinjaTrader Strategy Loop Executor**
   - Compile, repair, optimize loop
   - Depends on: Strategy Discovery Engine, NT compiler

10. **Execution Bridge Service**
    - Order execution, position monitoring
    - Depends on: NinjaAccountManager (external)

11. **Intraday Prediction Engine**
    - Daily + horizon forecasts
    - Depends on: Market Data Service

12. **Trading Decision Dashboard (Web UI)**
    - Orchestrates all services
    - Depends on: All above

**Deliverable:** End-to-end discovery → optimizer → deploy flow (with UI)

---

### Phase 4: Domain Projects & Scaling (Weeks 12+)
Replicate the MA-cross pattern for other strategies.

13. **Enhanced MA-Cross Project** (D:\strategy-analysis)
    - Compose services into cohesive MA-specific workbench

14. **Future Domain Projects**
    - Order Flow Analysis
    - Options Strategy Discovery
    - Momentum/Breakout Suite

---

## Development Workflow Per Service

### Repository Structure (Each Service)

```
strategy-discovery-engine/
├── README.md                 # service overview, API schema
├── docker-compose.yml        # local dev stack
├── requirements.txt
├── src/
│   ├── app.py                # FastAPI entry point
│   ├── models/               # Pydantic schemas
│   ├── routes/               # /v1/* endpoint handlers
│   ├── engines/              # core discovery logic
│   ├── config.py             # settings, env vars
│   └── utils/
├── tests/
│   ├── test_api.py           # endpoint tests
│   ├── test_engines.py       # unit tests for discovery
│   └── fixtures/             # sample data
├── notebooks/                # exploratory analysis
└── docs/
    ├── API.md                # OpenAPI documented
    ├── ARCHITECTURE.md       # design decisions
    └── DEVELOPMENT.md        # local setup guide
```

### Development Checklist Per Service

- [ ] **API Schema** — OpenAPI/Swagger defined before implementation
- [ ] **Local Docker Compose** — can run `docker-compose up` 
- [ ] **Unit Tests** — test core logic independent of I/O
- [ ] **Integration Tests** — test API endpoints + mock upstream services
- [ ] **Smoke Test** — CLI or notebook that exercises happy path
- [ ] **Documentation** — README, API schema, example requests
- [ ] **Error Handling** — meaningful HTTP status codes + error bodies
- [ ] **Logging** — structured logs (JSON) for debugging
- [ ] **Metrics** — expose Prometheus metrics (/metrics endpoint)

---

## Configuration Management

Each service should support:

1. **Environment Variables** (12-factor app)
   ```
   SERVICE_NAME=strategy-discovery-engine
   PORT=8001
   LOG_LEVEL=INFO
   MARKET_DATA_URL=http://market-data-service:8000
   RESULTS_STORAGE_PATH=/data/results
   MAX_CONCURRENT_JOBS=4
   ```

2. **Config Files** (YAML or JSON)
   ```yaml
   # config.yaml
   discovery:
     entry_families: [mv_cross, ma_divergence, ma_confluence]
     holdout_ratio: 0.2
     min_oos_trades: 20
   
   optimization:
     max_parameter_combos: 10000
     bootstrap_samples: 100
   ```

3. **Secrets** (kept outside config, use secrets manager)
   - NinjaTrader credentials (for nt_strategy_loop)
   - Database passwords
   - API keys for external services

**Tools:**
- Local dev: `.env` files + `python-dotenv`
- Docker: Docker Compose secrets
- Kubernetes: Sealed Secrets or Vault

---

## Testing Strategy

### Test Pyramid Per Service

```
                  ▲
               End-to-End
              (1–2 slow tests)
                 ▲
           Integration
          (5–10 tests)
             ▲
          Unit
      (30–50 fast tests)
```

**Unit Tests:**
- Algorithm correctness (discovery logic, exit simulation)
- Data transformation (parsing, normalization)
- Edge cases (empty input, NaN handling)
- Run in < 30 seconds

**Integration Tests:**
- Service API endpoints
- Mock upstream services (e.g., fake Market Data Service)
- Database operations
- Run in < 2 minutes

**End-to-End Tests:**
- Full workflow: discovery → template generation
- Real market data (small sample)
- Optional: real NinjaTrader backtest (slow, run nightly)
- Run nightly or on-demand

### CI/CD Pipeline (GitHub Actions / GitLab CI)

Per service:

```yaml
on: [push, pull_request]

jobs:
  test:
    - unit tests (fast gate)
    - integration tests (slower)
    - build Docker image
    - (optional) deploy to staging
    - smoke test on staging
```

---

## API Contracts & Versioning

### Versioning Strategy

- **Major version** (v1, v2): breaking changes to request/response schema
- **Minor version**: new endpoints, new optional fields
- **Patch version**: bug fixes, no schema changes

**Stability Guarantee:** All `/v1/` endpoints will remain compatible for 12 months before deprecation notice.

### Examples of Stable API Additions

✅ Adding optional field to response:
```json
{
  "candidates": [...],
  "new_confidence_score_v2": 0.87  // backwards-compatible
}
```

✅ New `/v1/discovery/advanced` endpoint (old `/v1/discovery/run` still works)

❌ Breaking: Removing a required field from input
❌ Breaking: Changing result type (string → number)

---

## Performance & Scaling Targets

### Phase 1 (MVP)

| Service | Single Instance | Throughput | Latency |
|---------|---|---|---|
| Market Data | 500 MB RAM | 1 market at a time | < 100ms per query |
| Strategy Discovery | 4 GB RAM | 1 discovery job (5–30 min) | – |
| Exit Simulator | 1 GB RAM | 1000 trades/second | < 1 second |
| Report Builder | 1 GB RAM | 1 report/second | < 5 sec |
| Pattern Engine | 2 GB RAM | 1 sweep (5–20 min) | – |

### Phase 2 (Production)

| Service | Scale-Out | Throughput | Latency |
|---------|---|---|---|
| Market Data | Horizontal (multiple partitions) | Full market (all contracts) | < 50ms 99th percentile |
| Strategy Discovery | Horizontal (job queue) | 10+ concurrent discoveries | – |
| Exit Simulator | Horizontal (parallel batch) | 100k trades/second | < 100ms |
| Report Builder | Horizontal (async job) | 100 reports/second | – |
| Pattern Engine | Horizontal (template partition) | 50 concurrent sweeps | – |

**Database:**
- Market Data: PostgreSQL + object storage (S3)
- Job Queue: Redis or PostgreSQL with pg_queue
- Session State: Redis or in-memory with persistence

---

## Security & Compliance Considerations

### Authentication (Phase 2+)

- API keys (for service-to-service)
- JWT tokens (for dashboard users)
- mTLS (certificates for Kubernetes)

### Data Privacy

- Market data: not personally identifiable (OHLCV only)
- Strategy templates: internal IP (company-specific)
- Execution records: PII if account holders identified (mask in logs)

### Audit Trail

Log all:
- Strategy discoveries (who, when, parameters, results)
- Optimizer runs (job id, stage, parameters, results)
- Execution orders (timestamp, symbol, qty, price, fill)
- Configuration changes (version control)

**Storage:** Immutable log file (append-only) + database backup

---

## Documentation & Knowledge Transfer

### Per-Service Documentation

1. **README.md**
   - What the service does in plain English
   - Quick-start (Docker or local Python)
   - Example API calls

2. **API.md** or Swagger/OpenAPI spec
   - Endpoint list with request/response schemas
   - Error codes and meanings
   - Rate limits and quotas

3. **ARCHITECTURE.md**
   - Design decisions and rationale
   - Dependencies (internal and external)
   - Data model / key concepts
   - Scaling strategy

4. **DEVELOPMENT.md**
   - Local setup (Python venv, dependencies)
   - How to run tests
   - Common debugging tips
   - Contributing guidelines

5. **DEPLOYMENT.md**
   - Docker build command
   - Environment variables
   - Database migrations (if applicable)
   - Upgrade procedure

### Runbooks (Operational)

- **Troubleshooting**: common errors and solutions
- **Performance Tuning**: knobs to adjust for throughput/latency
- **Disaster Recovery**: backup/restore procedures
- **Monitoring**: key metrics to watch

---

## Integration with D:\strategy-analysis (MA-Cross)

### Architecture

```
D:\strategy-analysis (MA-Cross Domain App)
├── frontend/
│   └── React UI (MA-cross specific workbench)
├── backend/
│   └── Python orchestrator that calls:
│       ├── Market Data Service API
│       ├── Strategy Discovery Engine API
│       ├── MA Anchor Analyzer API
│       ├── Exit Policy Simulator API
│       ├── Regime Recommender API
│       └── Report Builder API
└── docker-compose.yml (dev stack)
```

### Configuration

MA-Cross app has hardcoded service discovery:

```python
# ma_cross/services.py
MARKET_DATA_SERVICE = "http://market-data-service:8000"
DISCOVERY_ENGINE = "http://strategy-discovery-engine:8001"
MA_ANALYZER = "http://ma-anchor-analyzer:8005"
EXIT_SIMULATOR = "http://exit-policy-simulator:8002"
REGIME_SERVICE = "http://regime-recommender:8003"
REPORT_BUILDER = "http://report-builder-service:8004"
```

### Docker Compose (MA-Cross Local Dev)

```yaml
version: "3.8"

services:
  market-data-service:
    image: market-data-service:latest
    ports: ["8000:8000"]
    volumes: ["/path/to/MarketData:/data:ro"]
  
  strategy-discovery-engine:
    image: strategy-discovery-engine:latest
    ports: ["8001:8000"]
    depends_on: [market-data-service]
  
  ma-anchor-analyzer:
    image: ma-anchor-analyzer:latest
    ports: ["8005:8000"]
  
  # ...other services...
  
  ma-cross-backend:
    image: ma-cross-backend:latest
    ports: ["5000:5000"]
    depends_on: [market-data-service, strategy-discovery-engine, ...]
  
  ma-cross-frontend:
    image: ma-cross-frontend:latest
    ports: ["3000:3000"]
    depends_on: [ma-cross-backend]
```

### API Composition Example (Python)

```python
# ma_cross/orchestrator.py

async def run_ma_cross_discovery(market_id, config):
    # 1. Get market data
    bars = await market_data_service.get_bars(market_id, "NQ", "06-26")
    
    # 2. Run strategy discovery (MA crossover specific)
    discovery_result = await discovery_engine.run({
        "bars": bars,
        "entry_families": ["ma_cross", "ma_confluence"],
        "config": config
    })
    
    # 3. Analyze MA anchors for top candidates
    for candidate in discovery_result["top_candidates"][:5]:
        anchor_analysis = await ma_analyzer.analyze({
            "bars": bars,
            "ma_configs": candidate["ma_configs"]
        })
        candidate["anchor_insight"] = anchor_analysis
    
    # 4. Simulate exits for recommended stops
    exit_results = await exit_simulator.simulate({
        "trades": candidate["signal_trades"],
        "ticks": market_data.get_ticks(market_id, ...),
        "policies": ["fixed_rr", "atr_trail", "chandelier"]
    })
    candidate["exit_analysis"] = exit_results
    
    # 5. Get regime context
    regime = await regime_service.classify({"bars": bars})
    
    # 6. Build report
    report_html = await report_builder.build({
        "sections": ["ma_cross_overview", "discovery_results", "exit_recommendation"],
        "context": {
            "discovery": discovery_result,
            "regime": regime,
            ...
        }
    })
    
    return report_html
```

---

## Rollout Roadmap

### Month 1: Architecture & Proof-of-Concept

- [ ] Define service boundaries (this document)
- [ ] Create 1–2 proof-of-concept services (Market Data, Exit Simulator)
- [ ] Test Docker Compose + local orchestration
- [ ] Document API contracts

**Success Metric:** Can run `docker-compose up` and call `/v1/` endpoints via curl

---

### Month 2–3: Core Analytics Services

- [ ] Extract Strategy Discovery Engine (Phase 1 scope)
- [ ] Extract Pattern Engine
- [ ] Extract MA Anchor Analyzer
- [ ] Extract Regime Recommender
- [ ] Integrate with MA-Cross workbench
- [ ] Run end-to-end discovery workflow

**Success Metric:** MA-Cross app discovers a strategy using remote services

---

### Month 4: Execution & Orchestration

- [ ] NinjaTrader Strategy Loop Executor
- [ ] Execution Bridge Service
- [ ] Prediction Engine (basic)
- [ ] Decision Dashboard (MVP)

**Success Metric:** Discovered strategy auto-compiles in NT + generates template XML

---

### Month 5+: Hardening & Scaling

- [ ] Performance tuning (caching, parallel jobs)
- [ ] Kubernetes migration
- [ ] Additional domain projects (Order Flow, Options, etc.)
- [ ] Production deployment

---

## Risk Mitigation

### Risk: Service Interdependency Hell

**Mitigation:**
- Strict API versioning (no breaking changes)
- Fallback/default values for optional upstream calls
- Circuit breaker pattern (fail fast if upstream down)
- Local mock servers for testing

### Risk: Data Inconsistency

**Mitigation:**
- Single source of truth (Market Data Service)
- Audit logs for all data transformations
- Reconciliation batch jobs (daily/weekly)

### Risk: NinjaTrader Integration Complexity

**Mitigation:**
- Start with compile-observer only (simplest unit of work)
- Run all tests against real NT (not mocked) before prod
- Maintain rollback procedure (old monolithic ta_foundation still works)

### Risk: Performance Regression

**Mitigation:**
- Benchmark current ta_foundation performance
- Set latency thresholds per service (SLA)
- Load testing (small discovery with 1000-trade backtest)

---

## Conclusion

This plan transforms **ta_foundation** from a monolithic application into a **composable service-oriented architecture**. Each capability becomes independently deployable, testable, and scalable. **D:\strategy-analysis** serves as the integration test and first domain-specific consumer.

**Next Steps:**

1. Review and refine this plan with team/stakeholders
2. Build proof-of-concept (Market Data + Exit Simulator services)
3. Establish Docker Compose baseline
4. Begin Phase 1 extraction (Weeks 1–3)

---

**Document Version:** 1.0  
**Last Updated:** 2026-06-17  
**Author:** AI Coding Agent  
**Status:** Ready for Review & Implementation Planning

