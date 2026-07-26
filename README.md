# ta_foundation

Production analytics and reporting framework for NinjaTrader exports. It ingests run-scoped strategy outputs plus shared market data, computes reusable analytics, and generates self-contained HTML reports with configurable section pipelines.

## Capabilities (full)

### 1) Ingestion and normalization
- Ingests folders of NinjaTrader exports and groups files into runs (`run_id`) across multiple artifacts.
- Supports recursive ingest, configurable run-id extraction (`--run-id-regex`), and explicit output directories.
- Uses parser registry composition so file handling is extensible without changing pipeline contracts.
- Produces deterministic outputs: run packages + manifest files.

### 2) Supported input artifacts
- Run-scoped inputs:
  - Trades CSV (`*_Trades.csv`)
  - Daily Analysis CSV (`*_Analysis.csv`)
  - Summary CSV (`*_Summery.csv` and `*_Summary.csv`)
  - Settings CSV
  - Optimization CSV
- Shared market inputs:
  - Minute bars (`*.Last.txt`)
  - Tick data (`*.Last.txt`)
- Optional tick-load bypass (`--no-tick-data`) for minute-bar-only workflows.

### 3) Time and data contracts
- Canonical timestamp policy is tz-aware only (`America/Denver`).
- UTC market minute data can be converted/localized during ingest to canonical timezone.
- Strict separation between:
  - `AnalysisPackage` (run-scoped: trades/daily/summary/settings/metadata/assets/warnings)
  - `MarketDataStore` (shared market artifacts with `run_id=None`)

### 4) Analytics and derived engines
- Strategy/market analytics modules for:
  - Drawdown and risk profiles
  - APEX trailing model and trailing risk analysis
  - Daily/leaderboard aggregations
  - Trade enrichment / feature stores
  - Regime recommendation and classification
- Discovery and orchestration engines for:
  - Strategy discovery pipelines (ranking, validation, sensitivity, MAE/MFE, risk metrics, cohorting)
  - Pattern engine (discovery, clustering, diagnostics, Monte Carlo robustness)
  - Entry-strategy sweeps (candle, MA, ORB, BB, breakout, pullback, level, LCR, premarket)
  - Large candle excursion analysis and downstream findings/reports
  - Anchor interaction analytics and TP/SL recommendation support

### 5) Reporting system
- YAML-driven report configs loaded then rendered through HTML builder + registry-driven sections.
- Section architecture is pure rendering from context (no file IO, no ingest in section, no heavy inline compute).
- Generates single-file HTML reports with embedded base64 images.
- Supports card export utilities for PNG outputs used by leaderboard/deployment-style views.

### 6) Report section catalog
- Includes 120 registered HTML sections covering:
  - Core run comparison (overview, KPI cards, metadata, equity/drawdown)
  - Operational boards (daily winners, deployment boards, strategy/session momentum)
  - Execution diagnostics (tick diagnostics, exit simulations, trade overlays)
  - Pattern engine diagnostics/discovery
  - Market regime + anchor interaction suites
  - Strategy discovery suites (overview, entry/exit/filter rules, validation, ranking, templates, decision ledger, combo baskets)
  - Discovery-overview suites (candle/MA/ORB/BB/breakout/pullback/level/LCR/premarket)
  - Large candle excursion suite (summary, context families, findings, recursive search, strategy construction, downstream next steps)

### 7) CLI and automation workflow
- Primary entrypoint: `python -m ta_foundation.cli.main`.
- High-level CLI flow:
  1. Build parser registry.
  2. Ingest input folder (+ optional market-data folder).
  3. Load report YAML config(s).
  4. Run enabled analytics/discovery orchestrators.
  5. Build HTML report(s), text summaries, manifests, and optional card exports.

### 8) Project extensibility model
- Four-layer contract:
  1. Parsers
  2. Pipeline
  3. Analysis helpers
  4. Report sections
- Extension points are first-class for:
  - Adding file parsers
  - Adding derived analytics modules
  - Adding registry-backed HTML sections
  - Adding YAML-configurable behaviors

### 9) Packaging and dependencies
- Python package (`src/` layout) with optional extras:
  - Base: `pandas`, `matplotlib`, `pyyaml`
  - Analysis: `numpy`, `scipy`, `scikit-learn`, `pyarrow`
  - Reporting: `playwright`
  - Dev: `pytest`

### 10) Quality and test coverage
- Extensive tests across:
  - Parsers and market data handling
  - Core pipeline helpers
  - Analysis modules (strategy discovery, regime recommender, large candle excursion, MA structure)
  - HTML section renderers
  - Bridge/execution shell harness and acceptance specs

## Capabilities (compact quick list)

- Multi-run NinjaTrader ingest with run grouping and parser registry extensibility.
- Strict timezone-safe canonical model (`America/Denver`, tz-aware only).
- Shared-vs-run scoped data ownership contracts enforced.
- Broad analytics stack (risk, regime, pattern engine, discovery, excursion, anchor interaction).
- 120-section YAML-configurable HTML reporting system with embedded media.
- CLI pipeline for ingest → analysis → report generation + manifests/text exports.
- Extensible architecture (parsers, analysis helpers, report sections).
- Production-oriented testing across pipeline, analytics, reporting, and execution harnesses.

## Quick start

```bash
pip install -e .
pip install -e .[analysis,reporting,dev]
python -m ta_foundation.cli.main \
  --input /path/to/exports \
  --output ./outputs \
  --report-config ./report.yaml
```

## References
- `ARCHITECTURE.md`
- `CONTRIBUTING.md`
- `REPORTING_SECTIONS.md`
- `PROJECT_CONTEXT.md`