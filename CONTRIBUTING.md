Contributing to ta_foundation

This project is a structured analytics and reporting framework.
Architectural consistency is more important than feature velocity.

Before making changes, read:

ARCHITECTURE.md

REPORT_SECTIONS.md

PROJECT_CONTEXT.md

Core Architectural Rules (Do Not Break)
1. Layer Separation

The system has four layers:

Parsers → produce normalized artifacts

Pipeline → assemble AnalysisPackage + shared MarketDataStore

Analysis modules → compute reusable derived metrics

HTML sections → render from context only

Sections must never:

Read files from disk

Call ingest

Parse YAML

Compute heavy analytics inline

2. Time Handling (Strict)

All canonical timestamps must be:

tz-aware America/Denver


Never mix naive and tz-aware datetimes.

Minute bar files may be UTC on disk, but must be converted during ingest.

3. Shared vs Run-Scoped Data
Run-Scoped

Attached to:

AnalysisPackage


Examples:

Trades

Daily analysis

Summary

Settings

Shared

Attached to:

MarketDataStore


Examples:

Minute candles (*.Last.txt)

Shared artifacts must have:

run_id = None


They must NEVER be duplicated into each run.

Adding New Functionality
A. Adding a New Parser

Location:

src/ta_foundation/parsers/<vendor>/...


Requirements:

Implement:

can_parse(path, header) -> bool

parse(path, run_id) -> ParsedArtifact

Normalize:

Datetimes → America/Denver (tz-aware)

Money fields → numeric

Return:

run_id != None for run-scoped

run_id = None for shared artifacts

Register in CLI registry

Never:

Attach directly to AnalysisPackage

Perform cross-run logic in parser

B. Adding an Analysis Module

Location:

src/ta_foundation/analysis/


Rules:

Pure Python module

No HTML rendering

No file IO

Operates on:

AnalysisPackage

pd.DataFrame

MarketDataStore

Results must attach under:

pkg.metadata["derived"][...]


Never create new top-level attributes dynamically.

C. Adding a Report Section

Location:

src/ta_foundation/reports/html/sections/

Required Signature
def render_<name>(ctx: dict[str, Any]) -> str:

Always Start With
packages = ctx.get("packages", {}) or {}
options = ctx.get("options") or {}
market = ctx.get("market")
report_config = ctx.get("report_config")


Never assume keys exist.

Register Section

In:

reports/html/registry.py


Add:

SECTION_REGISTRY["my_section"] = SectionDef(
    id="my_section",
    default_title="My Section",
    render_fn=render_my_section,
)


Enable in report.yaml.

D. Adding Configurable Behavior

All runtime behavior must come from:

report.yaml


Not CLI flags (unless it affects ingest behavior).

Section-specific configuration lives under:

sections:
  - id: my_section
    options:
      key: value


Access via:

options = ctx.get("options") or {}


Sections must never parse YAML directly.

Report Builder Context Contract

Each section receives:

ctx["packages"]
ctx["market"]
ctx["options"]
ctx["section_id"]
ctx["section"]
ctx["report_config"]


Sections must only use these objects.

If new data is required:

Add it in pipeline

Or compute it in analysis module

Then pass via ctx

What NOT To Do

❌ Read files inside a section
❌ Modify pipeline logic from a section
❌ Attach shared data to AnalysisPackage
❌ Introduce new global state
❌ Mix timezones
❌ Break registry-based section lookup
❌ Hardcode section order

Testing Checklist Before Committing

When adding new functionality:

Parser

 Timezone correct?

 Numeric fields normalized?

 run_id correct?

 Shared artifacts use run_id=None?

Analysis

 Derived data stored under metadata["derived"]?

 No file IO?

 No section imports?

Section

 Uses ctx.get(...) guards?

 Uses options from YAML?

 No disk reads?

 No ingest calls?

 Images embedded via base64?

AI-Assisted Development Guidelines

When using AI to extend this project, always include:

- Respect ARCHITECTURE.md.
- Respect CONTRIBUTING.md.
- Do not change pipeline flow unless explicitly requested.
- Sections are pure renderers.
- All section options come from report.yaml via ctx["options"].
- Market data is shared in ctx["market"], not inside AnalysisPackage.
- Modify the smallest number of files necessary.


If AI suggests:

New CLI flags for report behavior → reject.

Direct file access in section → reject.

Bypassing registry → reject.

Creating new data loading logic in section → reject.

Design Philosophy

This project prioritizes:

Deterministic reproducibility

Clean separation of concerns

Extensibility without structural drift

Self-contained reporting

Prop-firm risk modeling integrity

If a change violates these principles, it must be redesigned.

Long-Term Stability Principle

Every new feature must fit into one of these categories:

New parser

New analysis helper

New report section

Minor pipeline extension

If it doesn’t clearly fit, rethink the implementation.