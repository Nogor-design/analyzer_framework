ta_foundation — Architecture
Overview

ta_foundation is a reusable analytics and reporting framework designed to:

Ingest NinjaTrader exports from a folder containing multiple runs.

Normalize data into a stable internal model (AnalysisPackage).

Support shared market reference data (minute candles).

Generate fully self-contained HTML reports (base64 embedded).

Emit a reproducible manifest with file hashes and parser mappings.

The system is designed for safe extensibility via:

New parsers

New analysis helpers

New report sections

Without rewriting the pipeline.

Core Non-Negotiable Contracts
1. Time Handling
Trades / Daily / Summary

Source timestamps are NinjaTrader local PC time

All datetimes are localized on ingest to:

America/Denver


Internal dataframes must use timezone-aware datetimes only.

Naive datetimes are forbidden in canonical models.

Minute Market Data (*.Last.txt)

Typically UTC timestamps

Converted to America/Denver during ingest

All canonical bars must be tz-aware America/Denver

This rule applies globally.

2. Multi-Run Ingest Model

A folder may contain many independent strategy runs.

run_id Derivation

Default:

Strip known suffixes:

_Trades.csv

_Analysis.csv

_Summery.csv

_Summary.csv

_Settings.csv

Optional override:

--run-id-regex


First capture group becomes run_id.

3. Shared vs Run-Scoped Data

This is critical.

Run-Scoped Artifacts

Attached to:

AnalysisPackage


Examples:

Trades CSV

Daily Analysis CSV

Summary CSV

Settings CSV

These always have:

run_id != None

Shared / Global Artifacts

Attached to:

MarketDataStore


Examples:

Minute bar files (NQ 03-26.Last.txt)

These return:

ParsedArtifact(run_id=None)


Shared artifacts must NEVER be duplicated inside AnalysisPackage.

4. KPI Normalization

Summary KPIs use normalized keys:

Case-insensitive

Punctuation-insensitive

Spacing-insensitive

Consumers must use tolerant lookup patterns.

5. HTML Reporting Rules

Reports must be fully self-contained.

All images embedded as base64 PNG data URIs.

No external CSS/JS/assets.

Sections are independent and composable.

report.yaml controls:

Section ordering

Section titles

Section options

Repository Layout (src-based packaging)
ta_foundation/
│
├── ARCHITECTURE.md
├── PROJECT_CONTEXT.md
├── REPORT_SECTIONS.md
├── report.yaml
│
└── src/ta_foundation/
    ├── core/
    │   ├── model.py              # AnalysisPackage, SummaryBlock
    │   ├── registry.py           # ParserRegistry
    │   ├── pipeline.py           # ingest_folder
    │   ├── manifest.py
    │
    ├── parsers/
    │   ├── base.py               # ParsedArtifact + Parser protocol
    │   └── ninjatrader/
    │       ├── trades_csv.py
    │       ├── analysis_by_day_csv.py
    │       ├── summary_csv.py
    │       └── minute_bars_last_txt.py
    │
    ├── marketdata/
    │   └── store.py              # MarketDataStore
    │
    ├── analysis/
    │   └── apex_trailing_model.py
    │
    ├── reports/html/
    │   ├── builder.py            # HtmlReportBuilder
    │   ├── registry.py           # SECTION_REGISTRY
    │   ├── config.py             # build_report_from_config
    │   ├── theme.py
    │   ├── embed.py
    │   └── sections/
    │
    └── cli/
        └── main.py

Runtime Data Flow
1. Ingest Layer
ingest_folder(...)


Enumerates CSV files

Selects parser via ParserRegistry

Produces ParsedArtifact

Groups into:

dict[str, AnalysisPackage]


Shared artifacts (run_id=None) attach to:

MarketDataStore


Pipeline returns:

IngestResult(
    packages: dict[str, AnalysisPackage],
    market: Optional[MarketDataStore],
    unparsed_files: list[Path]
)

2. Report Construction

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

HTML Section Architecture (Authoritative Contract)
Section Registry

Defined in:

reports/html/registry.py


Each section entry must use:

@dataclass(frozen=True)
class SectionDef:
    id: str
    default_title: str
    render_fn: Callable[[dict], str]


Rules:

id must match report.yaml

render_fn(ctx) must return HTML string

Sections are pure renderers

Sections must not read files directly

Sections must not call ingest

Section Context Contract

Injected by HtmlReportBuilder:

Each section receives:

ctx["packages"]
ctx["market"]
ctx["options"]
ctx["section_id"]
ctx["section"]
ctx["report_config"]


All sections must follow this pattern:

def render_x(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")


Never assume keys exist.

Always guard.

ctx["options"] is canonical.
section_options is legacy.

AnalysisPackage Contract

Sections operate only on:

pkg.trades
pkg.daily
pkg.summary
pkg.settings
pkg.metadata
pkg.assets
pkg.warnings


Derived metrics must attach under:

pkg.metadata["derived"][...]


Never attach arbitrary new top-level attributes dynamically.

Safe Extension Rules
Adding a Parser

Implement can_parse(path, header)

Implement parse(path, run_id)

Return ParsedArtifact

If shared → run_id=None

Register parser in CLI registry

Adding a Report Section

Create sections/<name>.py

Implement render_<name>(ctx)

Register in SECTION_REGISTRY

Enable in report.yaml

Use ctx["options"] only

Adding Analysis Logic

Add module under analysis/

Do not embed heavy logic inside sections

Store results under:

pkg.metadata["derived"]

Operational Conventions

Never mix naive and tz-aware datetimes

Never duplicate shared data into run packages

Never bypass registry

Never bypass builder

Never parse YAML inside a section

Never access disk inside a section

Final Architectural Principle

The system has 4 layers:

Parsers → produce normalized artifacts

Pipeline → assemble run packages + shared store

Analysis → derive reusable metrics

Sections → render pure HTML from ctx

Sections must never collapse layers 1–3 into themselves.