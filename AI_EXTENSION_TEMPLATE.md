AI Extension Template for ta_foundation

Use this template at the beginning of every new AI-assisted development conversation.

This ensures architectural consistency and prevents drift.

Context: You Are Extending an Existing Production Framework

You are helping extend an existing Python analytics/reporting framework named:

ta_foundation


Before writing any code:

Read and respect:

ARCHITECTURE.md

CONTRIBUTING.md

REPORT_SECTIONS.md

PROJECT_CONTEXT.md

Do not assume generic project structure.

Do not redesign pipeline unless explicitly requested.

Do not invent new architectural layers.

Non-Negotiable Contracts
Time Handling

All canonical timestamps must be tz-aware.

Strategy exports are localized to: America/Denver

Minute candle data may be UTC on disk but must be converted to America/Denver on ingest.

Naive datetimes are forbidden.

Data Ownership Rules
Run-Scoped Data

Lives inside:

AnalysisPackage


Includes:

trades

daily

summary

settings

metadata

assets

warnings

Shared Data

Lives inside:

MarketDataStore


Examples:

Minute candles (*.Last.txt)

Shared artifacts must use:

run_id = None


Shared data must never be duplicated into AnalysisPackage.

Report System Contract

Report flow:

report.yaml
    ↓
load_report_config
    ↓
build_report_from_config(packages, cfg, market)
    ↓
HtmlReportBuilder.build(context)
    ↓
section.render_fn(section_ctx)


Sections must not:

Read files

Call ingest

Parse YAML

Compute heavy analytics inline

Section Contract

Every section must follow this pattern:

def render_my_section(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")


Rules:

Use ctx["options"] (from report.yaml).

Never read disk.

Never bypass registry.

Never call pipeline.

Render pure HTML only.

Embed images as base64.

When Adding a Feature

First determine which layer it belongs to:

Feature Type	Correct Location
New file format	parsers/
New derived metrics	analysis/
Market model logic	analysis/
HTML visualization	reports/html/sections/
Ingest behavior	core/pipeline.py
Section ordering or options	report.yaml

If unclear → ask before coding.

Required Response Format

When implementing a change, respond with:

Brief plan

Exact file paths to create/modify

Complete code blocks for each file change

Any required pip dependencies

How to run and verify

Do not summarize code.
Do not provide partial snippets.
Provide complete, ready-to-paste blocks.

Hard Rejection Rules

If you suggest any of the following, you must stop and redesign:

Reading CSV inside a section

Bypassing ParserRegistry

Creating new global state

Hardcoding config instead of using report.yaml

Returning naive datetimes

Attaching shared data to AnalysisPackage

Introducing CLI flags for report display behavior

Market Data Rules

Minute bars are shared.

Access via:

market = ctx.get("market")
bars = market.get(instrument, contract)


Bars must have:

dt, open, high, low, close, volume


dt must be tz-aware America/Denver.

Derived Metrics Rules

All derived data must attach under:

pkg.metadata["derived"][...]


Never attach dynamic attributes directly to pkg.

Style Expectations

Smallest possible change set

No refactors unless explicitly requested

Preserve existing behavior

Respect existing naming patterns

Match existing code style

If Uncertain

Before writing code, ask:

Which layer should this live in?

Is this run-scoped or shared?

Should this be configurable in report.yaml?

Does this belong in analysis instead of section?

Do not guess.

Stability Principle

The framework is mature.

All extensions must fit into:

Parser

Analysis helper

Report section

Minor pipeline enhancement

If a request does not clearly fit one of those, rethink the design.