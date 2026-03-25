\% ta_foundation --- Architecture % Production System Blueprint %
Updated 2026-02-24

------------------------------------------------------------------------

# 🏛 ta_foundation

## Architecture Blueprint

### Production Analytics & Reporting Framework

------------------------------------------------------------------------

> This document defines the immutable structural architecture of
> `ta_foundation`. It is the governing systems blueprint for all
> ingestion, analysis, and reporting behavior.

------------------------------------------------------------------------

# I. System Overview

`ta_foundation` is a deterministic, layered analytics framework designed
to:

-   Ingest multi-run NinjaTrader exports
-   Normalize data into canonical internal models
-   Support shared market reference data
-   Produce fully self-contained HTML reports
-   Emit reproducible manifests
-   Enable safe extensibility

------------------------------------------------------------------------

# II. Architectural Law

The system is a strict four-layer pipeline:

    Parsers → Pipeline → Analysis → Sections

Layer collapse is forbidden.

------------------------------------------------------------------------

# III. Core Contracts

## 1. Time Handling

Canonical timezone: **America/Denver**

### Requirements

-   All datetimes are tz-aware
-   Localization occurs during ingest
-   UTC market data converted during ingest
-   Internal models contain no naive timestamps

### Forbidden

-   Naive datetimes
-   Mixing timezone-aware and naive timestamps

------------------------------------------------------------------------

## 2. Data Ownership Model

### Run-Scoped Artifacts

Stored in `AnalysisPackage` (run_id != None)

-   Trades
-   Daily analysis
-   Summary
-   Settings
-   Metadata
-   Assets
-   Warnings

### Shared Artifacts

Stored in `MarketDataStore` (run_id=None)

-   Minute bars (`*.Last.txt`)
-   Tick streams
-   Market reference data

Shared artifacts must never be duplicated in packages.

------------------------------------------------------------------------

## 3. Metadata Integrity

Derived metrics attach under:

``` python
pkg.metadata["derived"]
```

Rules:

-   Metadata must be JSON-safe
-   No DataFrames stored directly
-   No callables or registries
-   Heavy tables written as parquet artifacts
-   Metadata stores only references

------------------------------------------------------------------------

# IV. Repository Structure

    ta_foundation/
    │
    ├── ARCHITECTURE.md
    ├── PROJECT_CONTEXT.md
    ├── REPORT_SECTIONS.md
    ├── EXTENSION_DESIGN_SPEC.md
    ├── report.yaml
    │
    └── src/ta_foundation/
        ├── core/
        │   ├── model.py
        │   ├── registry.py
        │   ├── pipeline.py
        │   └── manifest.py
        │
        ├── parsers/
        │   ├── base.py
        │   └── ninjatrader/
        │       ├── trades_csv.py
        │       ├── analysis_by_day_csv.py
        │       ├── summary_csv.py
        │       ├── settings_csv.py
        │       └── minute_bars_last_txt.py
        │
        ├── marketdata/
        │   └── store.py
        │
        ├── analysis/
        │   ├── apex_trailing_model.py
        │   └── pattern_engine/
        │       ├── orchestrator.py
        │       ├── engine.py
        │       ├── clustering.py
        │       ├── walkforward.py
        │       ├── monte_carlo.py
        │       └── templates/
        │           ├── builtins.py
        │           └── <family>.py
        │
        ├── reports/html/
        │   ├── builder.py
        │   ├── registry.py
        │   ├── config.py
        │   ├── theme.py
        │   ├── embed.py
        │   └── sections/
        │       ├── ...
        │       ├── pattern_engine_overview.py
        │       └── pattern_cluster_drilldown.py
        │
        └── cli/
            └── main.py

------------------------------------------------------------------------

# V. Runtime Data Flow

## 1. Ingest Phase

    Folder
      ↓
    ParserRegistry
      ↓
    ParsedArtifact
      ↓
    ingest_folder()
      ↓
    packages + market + unparsed_files

## 2. Report Construction

    report.yaml
      ↓
    load_report_config()
      ↓
    build_report_from_config()
      ↓
    HtmlReportBuilder.build()
      ↓
    section.render_fn()

Sections never perform ingestion or analysis.

------------------------------------------------------------------------

# VI. HTML Section System

## Section Definition

Defined in `reports/html/registry.py`

``` python
@dataclass(frozen=True)
class SectionDef:
    id: str
    default_title: str
    render_fn: Callable[[dict], str]
```

### Section Rules

-   Pure HTML output
-   Base64-embedded assets
-   No disk IO
-   No YAML parsing
-   No analysis execution

------------------------------------------------------------------------

## Options Semantics

### Section-local options

``` python
ctx["options"]
```

### Full merged YAML

``` python
ctx["all_options"]
```

Sections must not assume global blocks are inside `ctx["options"]`.

------------------------------------------------------------------------

# VII. Pattern Engine (First-Class Analysis Subsystem)

## Responsibilities

-   Template-based pattern detection
-   Parameter sweeps
-   Multi-horizon outcome computation
-   Pattern clustering
-   Walk-forward validation
-   Monte Carlo survivability modeling
-   Deterministic artifact emission

## Execution Phase

Runs during analysis phase in:

    build_report_from_config()

Runs once per report build.

------------------------------------------------------------------------

## Template Registry

Key format:

    {family}::{structure}

Example:

    ORB::orb_break_retest

Registered in `default_template_registry()`.

------------------------------------------------------------------------

## Artifact Location

    .ta_artifacts/pattern_engine/<run_id>/

Metadata reference:

``` python
pkg.metadata["derived"]["pattern_engine"]
```

------------------------------------------------------------------------

# VIII. Safe Extension Principles

## Adding a Parser

-   Implement `can_parse()`
-   Implement `parse()`
-   Return `ParsedArtifact`
-   Register in registry

## Adding Analysis

-   Place in `analysis/`
-   Keep heavy logic out of sections
-   Store JSON-safe metadata only

## Adding a Section

-   Create renderer
-   Register in registry
-   Enable via `report.yaml`

------------------------------------------------------------------------

# IX. Governing Principle

This framework is intentionally layered:

**Parsers → Pipeline → Analysis → Sections**

Extensions must respect the boundary of each layer.

Architecture is the product.

------------------------------------------------------------------------
