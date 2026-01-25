# New Chat Starter Prompt (ta_foundation)

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