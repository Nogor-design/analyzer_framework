# New Chat Starter Prompt (ta_foundation)

You are extending an existing production-grade Python reporting framework (ta_foundation).

Before writing code:
1) Respect ARCHITECTURE.md and REPORT_SECTIONS.md.
2) Do not change pipeline flow unless explicitly requested.
3) Sections are pure render functions.
4) All section options come from report.yaml via ctx["options"].
5) Use AnalysisPackage objects only — do not reload files.
6) Market data is shared in ctx["market"], not inside AnalysisPackage.

When adding functionality:
- Identify layer: parser, analysis helper, pipeline, or report section.
- Modify the smallest number of files possible.
- Do not bypass registry or builder.
- Do not assume new CLI flags unless requested.
- Always show exact file paths and full code blocks.



You are helping me extend an existing Python foundation library named `ta_foundation`.

Non-negotiable contracts:
- All timestamps are NinjaTrader local PC time localized on ingest to America/Denver (tz-aware).
- Folder ingest supports many runs; run_id is derived from filename suffix stripping OR overridden by --run-id-regex.
- Summary KPIs use normalized keys (case/punct/spacing tolerant).
- HTML reports must be self-contained with embedded base64 images (no external assets).
- New functionality should be added as reusable modules (parsers, report sections, analysis helpers), not one-off scripts.

Current capabilities:
- Parsers: *_Trades.csv, *_Analysis.csv (daily), *_Summery.csv / *_Summary.csv
- Pipeline: ingest_folder(...) returns packages: dict[run_id, AnalysisPackage]
- Reports: HTML comparison report built via report.yaml section registry
  - comparison_overview
  - equity_curve_comparison
  - run_metadata_cards (start/end/duration)
  - run_kpi_cards
- Manifest: outputs/manifest.json with hashes and run mappings

When I ask for a change, respond with:
1) brief plan
2) exact file paths to create/modify
3) complete code blocks for each file change
4) any required pip dependencies
5) how to run and verify

My request for this chat:
[DESCRIBE THE FEATURE TO ADD]


---

## What to put in the “ChatGPT project folder” (minimum set)

If that project folder is meant to support new chats, store these files there (or keep them in repo root and attach them):

1) `PROJECT_CONTEXT.md`  
2) `ARCHITECTURE.md`  
3) `REPORT_SECTIONS.md`  
4) `report.yaml` (the active config)  
5) `CHAT_STARTER_PROMPT.md` (the paste-into-new-chat template)

With those five, a new chat will “snap to” your established contracts quickly and will stop suggesting rewrites.

---

You are helping me extend an existing Python foundation library named ta_foundation.

First, read these project docs (they are authoritative):
- ARCHITECTURE.md
- REPORT_SECTIONS.md
- PROJECT_CONTEXT.md

Non-negotiable contracts:
- All run timestamps are NinjaTrader local PC time localized on ingest to America/Denver (tz-aware).
- Minute bar files (*.Last.txt) are typically UTC and are converted to America/Denver on ingest.
- Folder ingest supports many runs; run_id derived from filename suffix stripping OR overridden by --run-id-regex.
- Summary KPIs use normalized keys (case/punct/spacing tolerant).
- HTML reports must be self-contained with embedded base64 images (no external assets).
- New functionality must be added as reusable modules (parsers, analysis helpers, report sections), not one-off scripts.

Current architecture requirements (do not assume otherwise):
- Parsers return ParsedArtifact(kind, run_id|None, source_path, df, summary, warnings).
- run_id=None means “shared/global” artifact (ex: market minute bars), stored in MarketDataStore (NOT duplicated into AnalysisPackage).
- ingest_folder(...) builds dict[run_id, AnalysisPackage] + optional MarketDataStore and returns IngestResult(packages, unparsed_files, market).
- report.yaml controls which sections render and provides per-section options.
- Section render signature: render_x(ctx) where ctx must support:
  packages = ctx.get("packages", {}) or {}
  options = ctx.get("options") or ctx.get("section_options") or {}
  market  = ctx.get("market")
  report_config = ctx.get("report_config")

When I request a change, respond with:
1) brief plan
2) exact file paths to create/modify
3) complete code blocks for each file change
4) any required pip dependencies
5) how to run and verify

Before writing code:
- Identify the exact existing file(s) to edit.
- Confirm how the pipeline discovers/attaches data.
- Confirm how report.yaml options reach the section ctx.

standardize this pattern in every section:
packages = ctx.get("packages", {}) or {}
options = ctx.get("options") or ctx.get("section_options") or {}
market = ctx.get("market")


#If you want to be extra strict
Never introduce new CLI flags for report styling/behavior; use report.yaml section options.
Never read data files directly inside a report section; only use ctx["packages"], ctx["market"], and embedded assets/helpers.
