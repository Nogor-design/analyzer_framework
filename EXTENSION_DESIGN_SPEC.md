EXTENSION_DESIGN_SPEC.md.

# ta_foundation Extension Design Specification (Updated)

## 1) Purpose
This specification defines the approved workflow for adding new functionality to `ta_foundation` while preserving production architecture, deterministic behavior, and existing data contracts.

It is designed to be copied into future ChatGPT prompts so implementations stay aligned with the framework’s structure and data flow.

This version also documents the **Pattern Engine** subsystem and its extension surface.

---

## 2) Architectural Baseline (Must Not Drift)
`ta_foundation` is a 4-layer system:

1. **Parsers** (`src/ta_foundation/parsers/`)
2. **Pipeline** (`src/ta_foundation/core/pipeline.py`)
3. **Analysis** (`src/ta_foundation/analysis/`)
4. **Report Sections** (`src/ta_foundation/reports/html/sections/`) (pure renderers)

### Non-negotiable contracts
- Canonical datetimes are always tz-aware.
- Canonical timezone is **America/Denver**.
- Naive datetimes are forbidden.
- Shared market artifacts use `run_id = None` and live in `MarketDataStore`.
- Run-scoped artifacts live in `AnalysisPackage`.
- Sections must not read files, parse YAML, call ingest, or run heavy analytics.

---

## 3) Canonical Data Ownership

### Run-scoped (inside `AnalysisPackage`)
- `trades`
- `daily`
- `summary`
- `settings`
- `metadata`
- `assets`
- `warnings`

### Shared (inside `MarketDataStore`)
- Minute candles (`*.Last.txt`)
- Other shared market reference artifacts

### Derived data location
All computed metrics must be attached under:

```python
pkg.metadata["derived"][...]

Never add dynamic top-level attributes directly on pkg.

4) End-to-End Data Flow (Authoritative)
Input folder
  ↓
ParserRegistry selects parser per file
  ↓
Parser returns ParsedArtifact(run_id=... or None)
  ↓
ingest_folder groups artifacts into:
  - packages: dict[str, AnalysisPackage]
  - market: MarketDataStore (shared artifacts)
  - unparsed_files
  ↓
report.yaml
  ↓
load_report_config(...)
  ↓
build_report_from_config(packages, cfg, market)
  ↓
HtmlReportBuilder.build(context)
  ↓
SECTION_REGISTRY[id].render_fn(section_ctx)
  ↓
Self-contained HTML (base64-embedded assets)
5) Report Context Contract (CRITICAL: Options Semantics)

There are two “options” concepts:

A) Section-local options (what sections should use for display behavior)

In report.yaml:

sections:
  - id: some_section
    options:
      top_n: 12

In section ctx:

ctx["options"] MUST be section-local options only.

B) Global report config / feature blocks (what analysis layers should use)

Top-level YAML blocks:

pattern_engine:
  enabled: true
  ...

These MUST be passed via:

ctx["all_options"] (full merged YAML)

Rule: Renderers MUST NOT assume ctx["options"] contains global feature blocks.

6) Section Contract (Strict)

Every section renderer should start with:

def render_my_section(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}          # section-local options
    all_options = ctx.get("all_options") or {}  # full YAML
    market = ctx.get("market")
    report_config = ctx.get("report_config")

Rules:

Return pure HTML string.

Embed images as base64 (no image files on disk).

No disk IO, no YAML parsing, no pipeline calls, no ingest calls.

Use market only as shared MarketDataStore.

7) Time Handling Specification

Canonical timezone: America/Denver

Required:

Ingest-localize strategy timestamps to America/Denver.

Convert minute bars that are UTC-on-disk into America/Denver during ingest.

Keep tz-aware datetimes throughout parsing, analysis, rendering.

Forbidden:

Returning/storing naive datetimes.

Mixing naive and tz-aware timestamps.

8) Pattern Engine (Analysis Subsystem)
8.1 Purpose

Pattern Engine is an analysis subsystem that:

Sweeps parameterized pattern templates on market bars/ticks

Computes outcomes at multiple horizons

Clusters patterns into robust families

Computes OOS stability (walk-forward)

Runs prop-firm Monte Carlo survivability

Writes deterministic parquet artifacts per run

Attaches metadata under pkg.metadata["derived"]["pattern_engine"]

8.2 Where it runs

Pattern Engine runs once per report build (analysis phase), in build_report_from_config(...),
before any section rendering.

Sections only consume artifacts already produced.

8.3 Template registry (how patterns are defined)

Templates are registered in a TemplateRegistry.

Key convention: "{family}::{structure}" (e.g., ORB::orb_break_retest).

Built-ins must be registered in default_template_registry().

8.4 Canonical derived storage layout

Pattern Engine must attach:

pkg.metadata["derived"]["pattern_engine"] = {
  "version": "pe_v1",
  "engine": {...},         # instrument, contract, tf, horizons, etc.
  "artifacts": {...},      # parquet paths + metadata
  "diagnostics": {...},    # counts, validation issues, etc.
}

Artifacts are stored on disk, referenced by path in metadata:

signals

outcomes

patterns

pattern_stats

clusters

cluster_members

cluster_stats

oos_stats

mc_summary

8.5 JSON safety rule

pkg.metadata MUST remain JSON-safe:

Never store DataFrames or non-serializable objects inside metadata.

If you snapshot options, sanitize them (omit DataFrames, callables, registries).

9) Feature Placement Matrix (Decide Before Coding)
Feature type	Correct location	Notes
New file format parser	src/ta_foundation/parsers/...	Register via parser registry/CLI wiring
Ingest grouping/routing	src/ta_foundation/core/pipeline.py	Keep change minimal
Pattern detection logic	src/ta_foundation/analysis/pattern_engine/templates/...	Register template key
Sweep/cluster/CV/MC logic	src/ta_foundation/analysis/pattern_engine/...	Deterministic artifacts; no HTML
New derived metric/helper	src/ta_foundation/analysis/...	Stored under metadata["derived"]
New visual/report block	src/ta_foundation/reports/html/sections/...	Renderer only
Section order/title/options	report.yaml	Runtime config surface
10) Pattern Engine Extension Playbook (How to add to it)
10.1 Add a new pattern template

Create analysis/pattern_engine/templates/<family>.py

Implement detect_<name>(bars_df, ticks_df, params, market_ctx, options) -> DataFrame
Output columns (minimum):

dt (tz-aware)

direction (+1/-1)

entry_ref_price

features_json (string; JSON-safe)

Register it in analysis/pattern_engine/templates/builtins.py

Ensure the YAML references match the template’s (family, structure).

10.2 Add a new analysis artifact

Produce a DataFrame deterministically in analysis

Write to parquet under .ta_artifacts/pattern_engine/<run_id>/...

Attach under:

pkg.metadata["derived"]["pattern_engine"]["artifacts"][artifact_name] = {"type":"parquet","path":...}

Update report sections to read (not compute) and render.

10.3 Add a new report section

Create reports/html/sections/<name>.py

Implement render_<name>(ctx) as pure renderer

Register in reports/html/registry.py

Add to report.yaml sections list

10.4 Add new YAML configuration

Analysis behavior belongs in top-level blocks (e.g., pattern_engine:).

Rendering behavior belongs in section-local sections[].options.

11) Implementation Workflow (For Developers and AI Prompts)
Step 0 — Load mandatory context

Read before implementing:

ARCHITECTURE.md

CONTRIBUTING.md

REPORT_SECTIONS.md

PROJECT_CONTEXT.md

Step 1 — Define change intent

One paragraph stating:

feature goal

target layer

data ownership (run-scoped/shared)

config surface (top-level vs section-local)

Step 2 — Declare impacted files

List exact files to create/modify (smallest set).

Step 3 — Enforce contracts

Preserve architecture.

Keep heavy logic out of sections.

Keep metadata JSON-safe.

Respect tz-aware America/Denver.

Step 4 — Validate

Confirm tz-awareness.

Confirm shared artifacts not copied into packages.

Confirm Pattern Engine runs before rendering.

Confirm sections render even if Pattern Engine disabled (graceful).

Step 5 — Deliver implementation package

Return:

brief plan

exact file paths changed

complete code blocks

dependency changes

run/verify steps

12) Hard Rejection Checklist

Reject and redesign if proposal includes:

Reading files inside a section

Parsing YAML inside a section

Calling ingest/pipeline inside a section

Creating global mutable state

Returning naive datetimes

Duplicating shared artifacts in packages

Storing DataFrames/objects in metadata

13) Prompt Pack for Future ChatGPT Sessions (Paste This)
You are extending ta_foundation, a production analytics/reporting framework.

Before coding, read and follow:
- ARCHITECTURE.md
- CONTRIBUTING.md
- REPORT_SECTIONS.md
- PROJECT_CONTEXT.md
- EXTENSION_DESIGN_SPEC.md

Non-negotiable contracts:
- All canonical datetimes are tz-aware America/Denver.
- Shared artifacts belong in MarketDataStore with run_id=None.
- Run-scoped artifacts belong in AnalysisPackage.
- Derived metrics attach under pkg.metadata["derived"].
- Report flow: report.yaml -> load_report_config -> build_report_from_config -> HtmlReportBuilder.build -> section.render_fn.
- Sections are pure renderers: no disk IO, no YAML parsing, no heavy analytics.

Options semantics (critical):
- ctx["options"] is SECTION-LOCAL options only (sections[].options).
- ctx["all_options"] contains the FULL merged YAML (top-level blocks like pattern_engine).

Pattern Engine rules:
- Pattern Engine runs in analysis phase before rendering (builder/config), once per report.
- Templates must be registered in default_template_registry() using key family::structure.
- Artifacts must be written as parquet and referenced under pkg.metadata["derived"]["pattern_engine"]["artifacts"].
- pkg.metadata must remain JSON-safe (no DataFrames/registries/callables).

When proposing implementation:
1) Brief plan
2) Layer placement (parser/pipeline/analysis/section)
3) Exact file paths to change
4) Full code blocks
5) Verification steps

---

# 2) Replacement: `ARCHITECTURE.md` (updated)

> Copy/paste this whole document over your current `ARCHITECTURE.md`.

```markdown
# ta_foundation — Architecture (Updated)

## Overview
ta_foundation is a reusable analytics and reporting framework designed to:

- Ingest NinjaTrader exports from a folder containing multiple runs.
- Normalize data into a stable internal model (AnalysisPackage).
- Support shared market reference data (minute candles, ticks).
- Generate fully self-contained HTML reports (base64 embedded).
- Emit reproducible manifests with file hashes and parser mappings.
- Support safe extensibility via: new parsers, analysis helpers, report sections.

This version documents the **Pattern Engine** as a first-class analysis subsystem.

---

## Core Non-Negotiable Contracts

### 1) Time Handling
**Trades / Daily / Summary**
- Source timestamps are NinjaTrader local PC time.
- All datetimes are localized on ingest to: `America/Denver`.
- Internal dataframes must use tz-aware datetimes only.
- Naive datetimes are forbidden in canonical models.

**Minute Market Data (`*.Last.txt`)**
- Often UTC timestamps on disk.
- Converted to America/Denver during ingest.
- All canonical bars must be tz-aware America/Denver.

### 2) Multi-Run Ingest Model
A folder may contain many independent strategy runs.

**run_id derivation**
Default:
- strip suffixes:
  - `_Trades.csv`
  - `_Analysis.csv`
  - `_Summery.csv`
  - `_Summary.csv`
  - `_Settings.csv`

Optional override:
- `--run-id-regex` (first capture group becomes run_id).

### 3) Shared vs Run-Scoped Data (Critical)
**Run-scoped artifacts** attach to `AnalysisPackage` (run_id != None).
Examples:
- Trades CSV
- Daily Analysis CSV
- Summary CSV
- Settings CSV

**Shared artifacts** attach to `MarketDataStore` (run_id=None).
Examples:
- Minute bar files (e.g., `NQ 03-26.Last.txt`)
- Tick streams (if present)

Shared artifacts must NEVER be duplicated inside `AnalysisPackage`.

### 4) KPI Normalization
Summary KPIs use normalized keys:
- case-insensitive
- punctuation-insensitive
- spacing-insensitive

### 5) HTML Reporting Rules
- Reports must be fully self-contained.
- All images embedded as base64 PNG data URIs.
- No external CSS/JS/assets.
- Sections are independent and composable.
- `report.yaml` controls:
  - section ordering
  - section titles
  - section options

---

## Repository Layout (src-based packaging)

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
│ ├── model.py
│ ├── registry.py
│ ├── pipeline.py
│ ├── manifest.py
│
├── parsers/
│ ├── base.py
│ └── ninjatrader/
│ ├── trades_csv.py
│ ├── analysis_by_day_csv.py
│ ├── summary_csv.py
│ ├── settings_csv.py
│ └── minute_bars_last_txt.py
│
├── marketdata/
│ └── store.py
│
├── analysis/
│ ├── apex_trailing_model.py
│ └── pattern_engine/
│ ├── orchestrator.py
│ ├── engine.py
│ ├── clustering.py
│ ├── walkforward.py
│ ├── monte_carlo.py
│ └── templates/
│ ├── builtins.py
│ └── <family>.py
│
├── reports/html/
│ ├── builder.py
│ ├── registry.py
│ ├── config.py
│ ├── theme.py
│ ├── embed.py
│ └── sections/
│ ├── ...
│ ├── pattern_engine_overview.py
│ └── pattern_cluster_drilldown.py
│
└── cli/
└── main.py


---

## Runtime Data Flow

### 1) Ingest Layer
`ingest_folder(...)`:
- enumerates files
- selects parser via ParserRegistry
- produces ParsedArtifact
- groups into:
  - `packages: dict[str, AnalysisPackage]`
  - `market: MarketDataStore` (run_id=None artifacts)
  - `unparsed_files: list[Path]`

### 2) Report Construction
Flow:

report.yaml
↓
load_report_config(...)
↓
build_report_from_config(packages, cfg, market)
↓
HtmlReportBuilder.build(context)
↓
section.render_fn(section_ctx)


---

## HTML Section Architecture (Authoritative)

### Section Registry
Defined in: `reports/html/registry.py`

Each section entry:
```python
@dataclass(frozen=True)
class SectionDef:
    id: str
    default_title: str
    render_fn: Callable[[dict], str]

Rules:

id must match report.yaml

render_fn(ctx) must return HTML string

sections are pure renderers

sections must not read files directly

sections must not call ingest

Section Context Contract (Options Semantics)

Each section receives:

ctx["packages"]

ctx["market"]

ctx["report_config"]

ctx["options"] (SECTION-LOCAL options only)

ctx["all_options"] (FULL merged YAML, including top-level blocks)

Renderers must use:

options = ctx.get("options") or {}

all_options = ctx.get("all_options") or {}

Renderers must not parse YAML.

Pattern Engine Architecture
Purpose

Pattern Engine is an analysis subsystem that:

evaluates template patterns on shared market bars/ticks

sweeps parameter grids

computes multi-horizon outcomes

clusters patterns into families

evaluates out-of-sample stability

runs prop-firm Monte Carlo survivability

writes parquet artifacts

attaches references under pkg.metadata["derived"]["pattern_engine"]

Execution

Pattern Engine runs during report build (analysis phase), not in sections:

called once per report build

attaches derived metadata per run package

Template Registry

Templates are registered by key:

{family}::{structure}

Example:

ORB::orb_break_retest

Templates live under:

analysis/pattern_engine/templates/
and are registered via:

templates/builtins.py called by default_template_registry()

Artifacts and Metadata

Derived metadata location:

pkg.metadata["derived"]["pattern_engine"]

Artifacts are parquet written under:

.ta_artifacts/pattern_engine/<run_id>/...

Metadata stores only JSON-safe references to artifacts.

Safe Extension Rules
Adding a Parser

Implement can_parse(path, header)

Implement parse(path, run_id)

Return ParsedArtifact (shared → run_id=None)

Register parser in CLI registry

Adding Analysis Logic

Add module under analysis/

Do not embed heavy logic in sections

Store results under pkg.metadata["derived"] (JSON-safe)

Prefer parquet artifacts referenced by path for heavy tables

Adding a Report Section

Create sections/<name>.py

Implement render_<name>(ctx) (pure HTML)

Register in SECTION_REGISTRY

Enable in report.yaml

Final Architectural Principle

The system has 4 layers:

Parsers → Pipeline → Analysis → Sections

Sections must never collapse layers 1–3 into themselves.

Sections must never collapse layers 1–3 into themselves.

## 11) Anchor Interaction Engine Design
For MA/anchor excursion analysis aligned with current architecture and reporting contracts, see:

- `MA_ANCHOR_INTERACTION_DESIGN.md`