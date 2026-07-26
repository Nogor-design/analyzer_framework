\% ta_foundation --- Extension Design Specification % Production
Architecture Contract % Updated 2026-02-24

------------------------------------------------------------------------

# 🏛 ta_foundation

## Extension Design Specification

### Production Architecture Contract

------------------------------------------------------------------------

> **Purpose**\
> This document defines the architectural contract for extending
> `ta_foundation`\
> while preserving determinism, structural integrity, and production
> guarantees.

------------------------------------------------------------------------

# I. Architectural Doctrine

`ta_foundation` is a strict four-layer system:

    Parsers → Pipeline → Analysis → Sections

## Layer Boundaries

1.  **Parsers**\
    `src/ta_foundation/parsers/`

2.  **Pipeline**\
    `src/ta_foundation/core/pipeline.py`

3.  **Analysis**\
    `src/ta_foundation/analysis/`

4.  **Sections (Pure Renderers)**\
    `src/ta_foundation/reports/html/sections/`

------------------------------------------------------------------------

# II. Non‑Negotiable Contracts

-   All canonical datetimes are **tz-aware**
-   Canonical timezone: **America/Denver**
-   Naive datetimes are forbidden
-   Shared artifacts → `MarketDataStore` (`run_id=None`)
-   Run-scoped artifacts → `AnalysisPackage`
-   Sections must never:
    -   Read files
    -   Parse YAML
    -   Call ingest
    -   Execute heavy analytics

------------------------------------------------------------------------

# III. Canonical Data Ownership

## Run-Scoped (`AnalysisPackage`)

-   trades\
-   daily\
-   summary\
-   settings\
-   metadata\
-   assets\
-   warnings

## Shared (`MarketDataStore`)

-   Minute candles (`*.Last.txt`)\
-   Market reference artifacts

------------------------------------------------------------------------

# IV. Derived Metrics Contract

All computed metrics attach under:

``` python
pkg.metadata["derived"][...]
```

Never create dynamic top-level attributes.

Metadata must remain JSON-safe.

------------------------------------------------------------------------

# V. End‑to‑End Flow

    Input Folder
        ↓
    ParserRegistry
        ↓
    ParsedArtifact
        ↓
    ingest_folder()
        ↓
    packages + market
        ↓
    report.yaml
        ↓
    build_report_from_config()
        ↓
    HtmlReportBuilder.build()
        ↓
    section.render_fn()
        ↓
    Self-contained HTML

------------------------------------------------------------------------

# VI. Pattern Engine (First-Class Subsystem)

## Responsibilities

-   Pattern detection sweeps
-   Multi-horizon outcome computation
-   Clustering
-   Walk-forward OOS stability
-   Monte Carlo survivability
-   Deterministic parquet artifact writing

## Execution Phase

Runs once during report build (analysis phase).\
Never inside sections.

## Artifact Storage

    .ta_artifacts/pattern_engine/<run_id>/

Metadata attachment:

``` python
pkg.metadata["derived"]["pattern_engine"]
```

Artifacts stored by path reference only.

------------------------------------------------------------------------

# VII. Feature Placement Matrix

  ------------------------------------------------------------------------------------
  Feature                    Location                                  Rule
  -------------------------- ----------------------------------------- ---------------
  New parser                 `parsers/...`                             Register in
                                                                       registry

  Ingest logic               `core/pipeline.py`                        Minimal change

  Pattern detection          `analysis/pattern_engine/templates/...`   Register
                                                                       template

  Sweep / MC / CV            `analysis/pattern_engine/...`             Deterministic

  Derived metric             `analysis/...`                            Attach under
                                                                       derived

  New section                `reports/html/sections/...`               Renderer only

  Layout config              `report.yaml`                             Runtime only
  ------------------------------------------------------------------------------------

------------------------------------------------------------------------

# VIII. Hard Rejection Criteria

Reject immediately if proposal includes:

-   File IO inside sections
-   YAML parsing inside sections
-   Pipeline calls inside sections
-   Naive datetimes
-   Shared artifact duplication
-   DataFrames in metadata
-   Global mutable state

------------------------------------------------------------------------

# IX. Implementation Protocol

1.  Define intent\
2.  Declare impacted files\
3.  Preserve architectural contracts\
4.  Validate timezone & JSON safety\
5.  Deliver full implementation package

------------------------------------------------------------------------

# 🏛 Final Principle

Architecture is layered by design:

**Parsers → Pipeline → Analysis → Sections**

No extension may collapse these layers.

------------------------------------------------------------------------
