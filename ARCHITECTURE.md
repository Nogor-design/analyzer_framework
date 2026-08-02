# 🏛️ TA Foundation --- Architecture

A production-grade analytics and reporting framework for **NinjaTrader
strategy exports**.

------------------------------------------------------------------------

# 📦 Overview

`ta_foundation` ingests multiple NinjaTrader runs simultaneously,
normalizes datasets into structured packages, computes reusable
analytics, and generates **fully self-contained HTML reports**.

The architecture emphasizes:

-   **Strict data contracts**
-   **Extensibility**
-   **Deterministic analytics**
-   **Clean separation of responsibilities**

Core workflow:

    Raw NinjaTrader Exports
            │
            ▼
        Parsers
            │
            ▼
       Ingestion Pipeline
            │
            ▼
     Analysis + Derived Metrics
            │
            ▼
      HTML Report Generation

------------------------------------------------------------------------

# ⚖️ Core Non-Negotiable Contracts

## 🕒 1. Time Handling (Authoritative)

All canonical timestamps follow strict rules.

  Rule              Description
  ----------------- -------------------------
  Timezone          `America/Denver`
  Datetime type     **Timezone-aware only**
  Naive datetimes   ❌ Forbidden

### Ingest Behavior

  Source                       Handling
  ---------------------------- -------------------------------
  NinjaTrader run timestamps   Localized to `America/Denver`
  Shared market exports        Converted from UTC
  Canonical models             Always timezone-aware

------------------------------------------------------------------------

## 📊 2. Data Ownership

The system distinguishes **run-scoped data** and **shared market data**.

### 📁 Run-Scoped Data

Each run produces **one `AnalysisPackage`** keyed by:

    run_id

Fields stored:

    AnalysisPackage
    ├── trades
    ├── daily
    ├── summary
    ├── settings
    ├── metadata
    ├── assets
    └── warnings

------------------------------------------------------------------------

### 🌎 Shared Market Data

Stored once inside **`MarketDataStore`**.

    MarketDataStore
    ├── market_minute_bars
    ├── market_ticks
    └── derived/resampled cache

Rule:

    run_id = None

Shared data **must never be duplicated into packages**.

------------------------------------------------------------------------

# 🧱 Layering Contract

The system is divided into **four strict layers**.

    1️⃣ Parsers
    2️⃣ Pipeline
    3️⃣ Analysis Helpers
    4️⃣ Report Sections

------------------------------------------------------------------------

# 📂 Project Structure

    src/ta_foundation
    │
    ├── analysis/           analytics helpers (see below)
    ├── agent/              agent-facing entry points
    ├── cli/                ta_foundation.cli.main
    ├── core/               shared primitives + contracts
    ├── discovery_registry/ registered discovery capabilities
    ├── marketdata/         MarketDataStore (shared, run_id=None)
    ├── nt_strategy_loop/   NinjaTrader strategy loop bridge
    ├── optimization/       optimizer engines
    ├── parsers/            CSV / export ingest
    ├── persistence/        storage adapters
    ├── plots/              chart rendering
    ├── prediction/         prediction + context building
    ├── reports/html/       report builder + sections
    ├── research_intake/    inbound research capture
    ├── research_ledger/    candidate ledger + migrations
    ├── shadow/             shadow-run comparison
    ├── strategies/         strategy definitions
    ├── validation/         validation helpers
    └── web/                Flask optimizer surface

`analysis/` holds 16 subpackages. The largest, by some margin:

    large_candle_excursion  ~27k   excursion research + the Dynamic Phase 1-7
                                   pipeline (outcome cube, opportunity oracle,
                                   fixed-share selector, multisequence replay,
                                   representation audit/confirmation, factorial
                                   shift audit, opening-inventory audits,
                                   adaptive context gate)
    strategy_discovery      ~19k   entry/filter/exit discovery + validation
    entry_strategies        ~14k   the 8 entry families (ma, candle, bb,
                                   breakout, pullback, level, orb, lcr)
    pattern_engine          ~4k    pattern templates, sweeps, clustering
    exits                   ~3k    exit policy simulation
    ma_structure            ~2k    MA regime context

Remaining: `features`, `indicators`, `prop_evaluation`, `regime_recommender`,
`risk`, `selection`, `statistics`, `strategies`, `strategy_composer`,
`strategy_metadata`.

> Research entry points under `large_candle_excursion` rerun the exact
> NinjaTrader parity audit and verify input artifact hashes before producing a
> result, so an output cannot be built from unverified inputs. The thin runners
> that drive them live in the sibling `strategy-analysis` repo
> (`large-candle/run_dynamic_*.py`).

------------------------------------------------------------------------

# 💻 CLI & Outputs

Primary CLI:

    ta_foundation.cli.main

Capabilities:

-   ingest CSV exports
-   recursive scanning
-   run-id regex override
-   shared market ingest
-   HTML report generation
-   manifest generation

Output artifacts:

    report.html
    manifest.json
    unparsed_files.txt

------------------------------------------------------------------------

# 🏁 Design Philosophy

The framework prioritizes:

-   Deterministic analytics
-   Strict contracts
-   Reproducibility
-   Composable reporting
-   Safe extensibility

> Analytics pipelines should be predictable, inspectable, and
> composable.
