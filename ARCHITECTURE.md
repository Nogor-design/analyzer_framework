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
    ├── analysis/
    ├── cli/
    ├── core/
    ├── marketdata/
    ├── parsers/
    └── reports/html/

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
