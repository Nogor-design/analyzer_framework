# TA Foundation Project — Documentation Review

**Date:** May 24, 2026  
**Reviewer:** Claude  
**Scope:** Documentation structure, completeness, and clarity across the ta_foundation project

---

## Executive Summary

The **ta_foundation** project is a sophisticated **4-layer trading analysis and strategy discovery system** that ingests NinjaTrader backtest exports, applies multi-stage analysis pipelines, and renders HTML reports with optional AI-driven strategy discovery and execution bridging. 

The documentation is **well-structured and comprehensive**, with clear separation of concerns, a helpful AI_REPO_INDEX, and explicit architecture contracts. However, there are opportunities to improve clarity for new contributors and reduce cognitive load in key areas.

**Overall Assessment:** ⭐⭐⭐⭐ (4/5)  
- **Strengths:** Clear architecture contracts, layered design, excellent role clarity  
- **Opportunities:** Some dense sections, cross-cutting concerns need better visibility, examples could be more abundant

---

## 1. Architecture & Design Documentation

### ✅ Strengths

**1.1 Four-Layer Architecture is Clear**
- The `CLAUDE.md` file articulates the 4-layer model with strong specificity:
  ```
  1. Parsers      → ParsedArtifact objects
  2. Pipeline     → AnalysisPackage + MarketDataStore
  3. Analysis     → metadata["derived"] derivations
  4. Sections     → Pure HTML renderers
  ```
- This constraint is enforced and well-documented. Non-negotiable contracts are explicit.

**1.2 Non-Negotiable Contracts are a Good Pattern**
- The CLAUDE.md specifies immutable rules:
  - Timestamps always `tz-aware America/Denver`
  - `MarketDataStore` (run_id=None) never duplicated into AnalysisPackage
  - All derived data under `metadata["derived"]` (must be JSON-safe)
  - Sections cannot perform IO, parse YAML, or call pipeline functions
- These prevent common architectural decay patterns.

**1.3 AI_CAPABILITY_MAP.md Disambiguates Product Capabilities**
- Clearly separates:
  - Backtest report generation
  - Strategy discovery
  - Prediction/horizon system
  - Strategy template building
  - Agentic NT loop
  - Execution bridge
- Prevents confusion when AI agents (or humans) ask "how do I do X?"

### 🔶 Opportunities

**1.4 The Pipeline is Implicit, Not Diagrammed**
- CLAUDE.md says "ingest → MA anchor analysis → pattern engine → strategy discovery → regime recommender → report rendering"
- But the actual `cli/main.py` orchestration, condition flags, and skip logic are buried in code
- **Suggestion:** Add a flowchart or ASCII diagram in CLAUDE.md showing which CLI flags enable/disable which stages

**1.5 Capability Boundaries Are Clear in Text, But Could Use Decision Trees**
- AI_CAPABILITY_MAP.md table tells me *which* capability to use, but doesn't show *how* to route a new feature request
- **Suggestion:** Add a section "If I want to add X, which capability should I use?" with decision trees

**1.6 Data Model Conventions Are Scattered**
- `pkg.metadata["derived"]` conventions are in CLAUDE.md
- But what about the shape of `anchor_interaction`, `pattern_engine`, `strategy_discovery` metadata blocks?
- **Suggestion:** Add a "Metadata["derived"] Schema" section with example JSON or Python dicts for each subsystem

---

## 2. Getting Started & Onboarding

### ✅ Strengths

**2.1 GETTING_STARTED.md is Concrete and Task-Oriented**
- Step-by-step walkthrough of the web app
- Clear folder requirements (Input, Output, Market Data)
- One-time setup is explicit
- Practical: "if you change a Python file, restart the server"

**2.2 CLAUDE.md Doubles as a Quick Reference**
- Installation: `pip install -e .`
- Run CLI: exact command with all options
- Run web app: exact command with port
- Run tests: single file and all files

### 🔶 Opportunities

**2.3 New Developer Path is Not Clear**
- If someone is hired to work on ta_foundation, what do they read first?
- Current answer: CLAUDE.md, then AI_REPO_INDEX.md
- But there's no "Developer Orientation" section that says: "Read these three docs in this order, then you're ready to code"
- **Suggestion:** Add a `CONTRIBUTING.md` with a "First Day Checklist"

**2.4 Test Examples Are Light**
- CLAUDE.md shows how to run tests, not how to *write* them
- No link to example test files or test patterns
- **Suggestion:** Add "Writing Tests" section with a pointer to a well-commented test file

**2.5 Common Tasks Are Not Indexed**
- Adding a new report section
- Adding a new parser
- Adding a new analysis subsystem
- Adding a new entry strategy family
- **Suggestion:** Add a quick-reference table in CLAUDE.md with links to the relevant docs/code

---

## 3. Core Data Model Documentation

### ✅ Strengths

**3.1 AnalysisPackage is Well-Defined**
- Fields are named and typed
- Assets dict, metadata dict constraints are clear
- Warnings list is mentioned

**3.2 MarketDataStore is Simple and Clear**
- Keyed by (instrument_root, contract)
- Shared across runs, never duplicated

**3.3 OptimizationStore/Batch is New and Well-Introduced**
- Clear that *_Optimization.csv files do NOT create AnalysisPackage runs
- Parameter name parsing logic (backward-scanning parens) is documented with examples

### 🔶 Opportunities

**3.4 SummaryBlock is Underdocumented**
- CLAUDE.md says "KPIs split into kpis_all, kpis_long, kpis_short + start_dt/end_dt"
- But:
  - What is the key normalization rule? (CLAUDE.md says "lowercase, punctuation-insensitive")
  - How do I know what KPI keys exist for a given strategy?
  - What does kpis_short vs. kpis_all mean semantically? Why split?
- **Suggestion:** Add example SummaryBlock JSON and explain the intent behind each field

**3.5 metadata["derived"] Keys Are Listed But Not Fully Specified**
- anchor_interaction: {...}
- pattern_engine: { artifacts: [...], diagnostics: {...} }
- strategy_discovery: {...}
- regime_recommender: {...}
- daily_outcomes: {...}
- trade_time_profile: {...}
- But internal structure of each is not shown
- **Suggestion:** Link to or embed schema docs for each subsystem (e.g., `analysis/ma_structure/README.md` should define the anchor_interaction block)

**3.6 JSON Safety Constraint Is Critical But Vague**
- "All values must be JSON-safe — no DataFrames, callables, or registry objects"
- Is `numpy.ndarray` JSON-safe? `datetime.datetime`? `Decimal`?
- **Suggestion:** Add a brief Python code example of what is safe: e.g., `{"key": 1.5, "list": [1,2,3], "nested": {...}}`

---

## 4. Report System Documentation

### ✅ Strengths

**4.1 Report Section Contract is Explicit**
- Signature is clear: `render_my_section(ctx: dict[str, Any]) -> str`
- Returns pure HTML, embeds images as base64
- No disk IO, YAML parsing, or pipeline calls allowed
- Must register in registry.py

**4.2 CLI vs. YAML Separation is Clear**
- CLI: ingest behavior (--input, --output, --market-data, --recursive)
- YAML: report behavior (sections, options, analysis feature blocks)
- AI_CAPABILITY_MAP.md reinforces this

**4.3 YAML Schema is Documented**
- Top-level blocks: report, sections, anchor_interaction, pattern_engine, strategy_discovery, regime_recommender
- Section structure: id, options
- Feature blocks have examples

### 🔶 Opportunities

**4.4 100+ Report Sections Exist But Are Not Cataloged**
- CLAUDE.md mentions "~100+ sections" in the registry
- AI_REPO_INDEX.md lists partial names but not a full catalog
- No browsable section directory or quick reference
- **Suggestion:** Generate a `REPORT_SECTIONS_CATALOG.md` that lists each section with 1-line description and required options

**4.5 Output Artifacts Are Mentioned But Not Detailed**
- "<output_filename>.html — self-contained HTML"
- "manifest.json — metadata, file hashes, parse warnings"
- "unparsed_files.txt — files not matched by any parser"
- ".ta_artifacts/ — parquet artifacts from pattern engine"
- But what does manifest.json contain exactly? What is unparsed_files.txt format?
- **Suggestion:** Add a "Report Outputs Reference" section with file formats

**4.6 Section Context (ctx dict) Is Not Fully Documented**
- Sections receive: packages, options, all_options, market, report_config
- But what are the actual shapes of each?
- **Suggestion:** Add a schema or example for `ctx` keys

---

## 5. Analysis Subsystems Documentation

### ✅ Strengths

**5.1 Each Subsystem Has Clear Entry Points**
- MA Anchor Interaction: orchestrator.run_anchor_interaction_analysis(...)
- Pattern Engine: engine.py (sweep), templates registered via builtins.py
- Entry Strategies: 8 families, each with *_sweep.py
- Strategy Discovery: orchestrator.py
- Regime Recommender: orchestrator.py

**5.2 Entry Strategies Are Well-Organized**
- 8 distinct families: candle, ma, bb, orb, breakout, pullback, level, lcr
- Shared infrastructure: _sweep_base.py for breakout/pullback/level
- Clear that each family has features.py, signals.py pattern

### 🔶 Opportunities

**5.3 Analysis Subsystem READMEs Are Missing**
- CLAUDE.md lists subsystems but does not link to README docs in each analysis/ subdirectory
- `analysis/ma_structure/`, `analysis/pattern_engine/`, etc. may have local docs, but unclear
- **Suggestion:** Ensure each analysis subsystem has a local README.md that:
  - Explains the problem it solves
  - Shows the main entry point
  - Lists the metadata["derived"] keys it produces
  - Links to key classes/functions

**5.4 Pattern Engine Template System is Powerful But Under-Explained**
- "Templates registered by key {family}::{structure} (e.g., ORB::orb_break_retest)"
- "Add new templates in templates/ and register via builtins.py:default_template_registry()"
- But what is the template structure? What fields must a template have?
- **Suggestion:** Add `analysis/pattern_engine/TEMPLATE_GUIDE.md` with:
  - Template JSON schema
  - Example template (ORB::orb_break_retest)
  - How to parameterize and sweep

**5.5 Entry Strategy Discovery is Complex but Linear Path is Not Clear**
- Multiple families, outcome simulation, ranking, validation, IS/OOS checks
- But how do they compose? Is it: sweep → ranking → validation → template generation?
- **Suggestion:** Add a `analysis/entry_strategies/DISCOVERY_PIPELINE.md` with:
  - ASCII flowchart of the pipeline
  - Entry points for each stage
  - How to add a new family

---

## 6. Execution Bridge & NinjaScript Documentation

### ✅ Strengths

**6.1 Execution Bridge is Separate and Well-Bounded**
- Clear entry points: bridge_sender.py, execution_runtime_client.py
- Two NinjaScript components are listed: TaFoundationExecutionShell, TaFoundationMinuteBarExporter
- Runbooks exist: RUNBOOK.md, RUNBOOK_PHASE2.md

**6.2 Test Harness is Comprehensive**
- Acceptance spec evaluator, evidence bundles, state parsers
- Phase 2 test matrix documented

### 🔶 Opportunities

**6.3 NinjaScript Integration is Dense**
- 61 indexed files under Execution Bridge
- But for a Python developer, the C# code is a black box
- **Suggestion:** Add a `EXECUTION_BRIDGE_OVERVIEW.md` that explains:
  - What each C# component does (in plain language)
  - How the inbox/outbox file-system protocol works
  - How to troubleshoot common issues (e.g., "signal not being picked up")

**6.4 Bridge Signal Contract is Not in CLAUDE.md**
- `signal_contract.md` exists but is not referenced
- **Suggestion:** Add a reference in CLAUDE.md: "See `src/ta_foundation/strategies/TaFoundationExecutionBridge/signal_contract.md` for the message format"

---

## 7. Web App Documentation

### ✅ Strengths

**7.1 GETTING_STARTED.md Covers the Web App Well**
- Clear walkthrough of each tab
- Folder requirements are explicit

**7.2 Capability Routing is Clear**
- "Backtest Reports" tab is the common path
- "Strategy Discovery" tab is for discovery workflows
- "Prediction" tab is separate

### 🔶 Opportunities

**7.3 API Documentation is Missing**
- Flask routes exist (e.g., /api/generate, /api/backtest, /api/validate mentioned in AI_CAPABILITY_MAP)
- But no OpenAPI spec or route listing
- **Suggestion:** Add a `WEB_API_REFERENCE.md` with:
  - List of all Flask routes
  - Request/response shapes
  - Example curl commands

**7.4 Job Infrastructure is Mentioned But Not Documented**
- "Background job dispatch/status" is listed as shared infrastructure
- But how do I check job status? What is the job lifecycle?
- **Suggestion:** Add a "Job System" section explaining:
  - How jobs are queued
  - Where logs are written
  - How to monitor from the UI vs. CLI

**7.5 Discovery UI is Separate but Not Well-Integrated**
- "Discovery UI →" link opens a separate page
- But docs don't explain how it differs from the Strategy Discovery tab
- **Suggestion:** Clarify in GETTING_STARTED.md or add a dedicated DISCOVERY_UI.md

---

## 8. Testing & Quality Assurance

### ✅ Strengths

**8.1 Test Suite is Comprehensive**
- 143 test files across analysis, agent, entry strategies, etc.
- Execution shell tests have phase-based organization

### 🔶 Opportunities

**8.2 Testing Guide is Minimal**
- CLAUDE.md shows how to run tests, not how to write them
- No style guide or test patterns documented
- **Suggestion:** Add `TESTING.md` with:
  - Example test file (well-commented)
  - Fixtures and setup patterns
  - How to mock external dependencies
  - CI/CD expectations

**8.3 Test Failures and Edge Cases Are Not Cataloged**
- When does the pipeline fail? What are common breakpoints?
- **Suggestion:** Add a "Common Issues and Troubleshooting" section in CLAUDE.md

---

## 9. Configuration Files & Prompts

### ✅ Strengths

**9.1 YAML Configuration System is Powerful**
- report.yaml controls all report behavior
- Separate top-level feature blocks for analysis modules
- Options are section-local or global

### 🔶 Opportunities

**9.2 YAML Schema is Not Formally Specified**
- AI_REPO_INDEX mentions many .yaml files (candle.yaml, discovery/*.yaml)
- But no JSON Schema or Pydantic model docs
- **Suggestion:** Add a `CONFIG_SCHEMA.md` or generate docs from YAML parsing code (e.g., `reports/html/config.py`)

**9.3 Prompt Templates Are Scattered**
- AI_REPO_INDEX lists discovery/claude_code_prompt_lcr_alignment.md, cli_report_prompts.txt, etc.
- But their relationship to agent workflows is unclear
- **Suggestion:** Consolidate prompt docs under a single `AGENT_PROMPTS.md` or keep them near their usage site with clear backlinks

---

## 10. Missing Documentation

### High Priority

1. **CONTRIBUTING.md** — First-day checklist, how to add features
2. **Analysis Subsystem READMEs** — One per analysis/ subdirectory
3. **REPORT_SECTIONS_CATALOG.md** — Browsable list of 100+ sections
4. **TESTING.md** — How to write tests, common patterns
5. **WEB_API_REFERENCE.md** — Flask routes, request/response shapes

### Medium Priority

6. **Metadata["derived"] Schema** — Full structure for each subsystem
7. **EXECUTION_BRIDGE_OVERVIEW.md** — Plain-language C# integration guide
8. **CONFIG_SCHEMA.md** — YAML configuration reference
9. **Common Issues & Troubleshooting** — Pipeline failure modes
10. **Data Model Examples** — JSON examples for SummaryBlock, OptimizationBatch, etc.

### Lower Priority

11. Performance tuning guide
12. Scaling considerations
13. Benchmarking patterns
14. Historical design decisions (decision log)

---

## 11. Documentation Strengths to Preserve

1. **Non-negotiable contracts** — The explicit, immutable design constraints prevent architectural decay
2. **Layered architecture** — The 4-layer model is clear and well-enforced
3. **Capability boundaries** — AI_CAPABILITY_MAP.md prevents feature confusion
4. **CLAUDE.md as a reference** — Quick-look architecture, commands, and contracts
5. **AI_REPO_INDEX.md as a routing tool** — New agents/humans can find code fast

---

## 12. Summary of Recommendations

### Tier 1: Critical (Do Soon)

| Item | Why | Owner | Effort |
|---|---|---|---|
| Add CONTRIBUTING.md with first-day checklist | Onboarding friction for new developers | DevOps/Lead | 2-3 hours |
| Create subsystem READMEs (ma_structure/, pattern_engine/, etc.) | Readers can't find problem statements or entry points | Technical leads | 4-6 hours |
| Document REPORT_SECTIONS_CATALOG.md | Users can't discover/browse the 100+ report sections | Docs/PM | 3-4 hours |

### Tier 2: High Value (Next Sprint)

| Item | Why | Owner | Effort |
|---|---|---|---|
| Write TESTING.md with patterns | Tests are hard to write without examples | QA/Tech Lead | 3-4 hours |
| Add WEB_API_REFERENCE.md | Flask routes/shapes are not discoverable | Backend engineer | 2-3 hours |
| Document metadata["derived"] schema with examples | Writers need to know exact shape of derived data | Architects | 3-4 hours |
| Add Pipeline Flowchart to CLAUDE.md | Orchestration logic is implicit in code | Architects | 1-2 hours |

### Tier 3: Nice to Have (Future)

| Item | Why | Owner | Effort |
|---|---|---|---|
| EXECUTION_BRIDGE_OVERVIEW.md | Helps non-C# developers understand NinjaScript | Systems engineer | 3-4 hours |
| CONFIG_SCHEMA.md | Formal YAML spec for validation | DevOps/Docs | 2-3 hours |
| Troubleshooting guide | Common failure modes documented | QA/Support | 2-3 hours |

---

## 13. Final Score by Category

| Category | Score | Notes |
|---|---|---|
| Architecture & Design | ⭐⭐⭐⭐⭐ | Clear 4-layer model, explicit contracts |
| Onboarding & Getting Started | ⭐⭐⭐⭐ | Good walkthrough, missing first-day checklist |
| API & Integration Docs | ⭐⭐⭐ | Web API not documented; sections not cataloged |
| Data Model & Schema | ⭐⭐⭐⭐ | Core models well-defined; derived metadata under-specified |
| Analysis Subsystems | ⭐⭐⭐ | Entry points clear; internal logic needs explanations |
| Testing & QA | ⭐⭐⭐ | Test suite exists; guide is missing |
| Execution & Deployment | ⭐⭐⭐⭐ | Clear entry points; C# integration underdocumented |

**Overall: ⭐⭐⭐⭐ (4/5)**

---

## Conclusion

The ta_foundation project has **excellent architectural documentation** and strong design discipline. The CLAUDE.md and AI_REPO_INDEX.md files are well-executed quick references. However, the project is entering a phase where **onboarding new contributors, discovering features, and integrating with external systems** will become bottlenecks if documentation is not expanded.

**The highest ROI improvements** are:
1. CONTRIBUTING.md + first-day checklist
2. Subsystem READMEs with problem statements
3. REPORT_SECTIONS_CATALOG.md for feature discovery
4. TESTING.md with patterns

These four documents would unlock faster onboarding and reduce repeated questions.

