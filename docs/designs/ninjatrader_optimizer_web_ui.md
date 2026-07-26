# NinjaTrader Optimizer Web UI

Status: **shipped end-to-end through Phase 5** as of 2026-05-16. Phases 1–4 of the
implementation plan and most of Phase 5 (recommendation engine + deployment
package) are live. The first proven session is
`opt_5bab6a5ee1ea` — phase 1→2→3→final Backtest validation completed,
8/8 final candidates passed hard filters, top candidate `F_001` produced
$19,880 net / PF 5.01 / DD $1,500 / 17 trades on contract `NQ 06-26`.

For the operator runbook covering the web flow, see
[`docs/runbooks/pantheon_web_optimizer_full_run.md`](../runbooks/pantheon_web_optimizer_full_run.md).
This document is now the **architecture reference**, not a forward plan — see
the "Current Implementation Status" section below for what is built vs open.

## Purpose

Build a local TA Foundation web page that lets a user run NinjaTrader strategy
optimization without manually preparing many Strategy Analyzer jobs.

The intended workflow:

1. User opens NinjaTrader and leaves Strategy Analyzer available.
2. User opens TA Foundation's local web UI.
3. User selects a NinjaTrader strategy, starting with
   `PantheonMasterBotV01TesterV2`.
4. The page populates strategy parameters from the NinjaTrader strategy source
   and/or saved template XML, similar to NinjaTrader Strategy Analyzer.
5. User marks parameters as fixed or optimized, sets guardrails, and chooses
   chunk limits.
6. TA Foundation generates smaller optimization templates instead of one giant
   optimization.
7. The NinjaTrader AddOn runs the generated templates.
8. TA Foundation ingests the results, saves a durable optimization session,
   generates reports, runs the template naming workflow, and recommends a
   curated set of templates.

The product goal is an easy-to-ship package for users, not an internal research
script collection.

## Key User Requirements Captured

- Strategy templates currently of interest:
  `C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\PantheonMasterBotV01TesterV2`
- Strategy source folder:
  `C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\Strategies`
- Existing NinjaTrader automation project:
  `D:\ninjatraderOptimizer`
- Existing template naming project:
  `D:\templateNaming`
- Template naming can run from command line.
- Max drawdown should be expressed in dollars.
- Chunking should support max combinations and/or max runtime.
- The system must support later retrieval and reuse of previous optimization
  results.
- One-button report should recommend 8 templates spanning the main trading time
  areas.
- The current answer to "use NinjaTrader default optimizer or custom optimizer
  DLL first?" is undecided and should be thought through. This design recommends
  default optimizer first for the shippable MVP.

## Current Related Capabilities

### TA Foundation

Relevant existing components:

- `src/ta_foundation/web/app.py`
  Existing local web app routes and API patterns.
- `src/ta_foundation/web/jobs.py`
  Background job manager with logs, status, cancel, and artifact support.
- `src/ta_foundation/web/discovery_session.py`
  On-disk JSON session persistence pattern.
- `src/ta_foundation/parsers/ninjatrader/optimization_csv.py`
  Parser for NinjaTrader optimization CSV exports.
- `src/ta_foundation/optimization/model.py`
  `OptimizationStore` and `OptimizationBatch` data model.
- `src/ta_foundation/reports/html/sections/optimization_overview.py`
  Existing optimization overview report section.
- `src/ta_foundation/persistence/db.py`
  DuckDB experiment registry with a lightweight `optimization_batches` table.
- `src/ta_foundation/analysis/strategy_metadata/extractor.py`
  Existing strategy metadata extractor that can be extended for NinjaTrader
  folders.
- `src/ta_foundation/strategies/LargeCandleReversal/generate_nt8_template.py`
  Useful seed-template patching pattern for NinjaTrader XML.

The discovery web UI design is a good sibling pattern:
`docs/designs/discovery_web_ui.md`.

### NinjaTrader Automation

The project at `D:\ninjatraderOptimizer` already includes a working AddOn
workflow for Strategy Analyzer batch runs.

Observed capabilities:

- Adds a `BATCH STRATEGY ANALYZER` panel inside Strategy Analyzer settings.
- Detects the selected Strategy Analyzer type and strategy.
- Can batch-run XML strategy templates from a folder.
- Can export settings, summary, analysis, trades, orders, and executions.
- Uses an IPC command file:
  `C:\temp\nt8_command.json`
- Existing IPC command shape:

```json
{
  "action": "RunBatch",
  "sourceFolder": "...",
  "destFolder": "..."
}
```

Important current gap:

- The inspected AddOn code appears to apply strategy template values and
  `BacktestType`, but does not fully apply top-level optimization fields such
  as `OptimizerType`, `OptimizerParameters`, `OptimizationFitness`, and
  `OptimizationParameters`.
- True web-driven optimizer templates will likely require extending the AddOn
  loader to apply those fields reliably.

### Template Naming

Project:
`D:\templateNaming`

CLI entrypoint:

```powershell
template-namer rename --input-dir <input_dir> --output-dir <output_dir> --market NQ
```

Equivalent module form:

```powershell
python -m template_naming.cli rename --input-dir <input_dir> --output-dir <output_dir> --market NQ
```

Naming rules live in:
`D:\templateNaming\naming_rules.json`

The naming guide defines the 8 time buckets needed by the one-button
recommendation feature:

| Session | Single-trade form | Multi-trade form |
|---|---|---|
| Asia | Dawn | Dawning |
| London Early | Rise | Rising |
| London Late | Prime | Priming |
| Pre-Market | Coil | Coiling |
| Overlap | War | Warring |
| NY Open | Rage | Raging |
| Midday | Drift | Drifting |
| Power Hour | Close | Closing |

The recommender should select one strong template per session bucket rather
than simply the top 8 by profit factor.

## Recommended Architecture

Think of the system as four layers:

1. Strategy catalog and UI.
2. Optimization plan builder.
3. NinjaTrader runner bridge.
4. Result library, reporting, and recommendation engine.

### 1. Strategy Catalog and UI

New web page:

```text
/optimizer
```

Responsibilities:

- Scan NinjaTrader strategy source and template folders.
- Let the user select a strategy.
- Let the user select a seed template.
- Populate fields from strategy metadata and the seed template.
- Group fields like NinjaTrader Strategy Analyzer when possible.
- Allow each parameter to be fixed or optimized.
- Support beginner-friendly controls while keeping an expert JSON/YAML escape
  hatch.

Metadata sources, in priority order:

1. Strategy `.cs` file:
   `[NinjaScriptProperty]`, `[Range]`, `[Display]`, type, group, order, and
   description.
2. Selected seed template XML:
   current values, instrument, dates, bars period, and optimizer seed fields.
3. Fallback XML-only mode:
   if source parsing fails, build fields from the template's `<Strategy>` node.

MVP strategy:

```text
PantheonMasterBotV01TesterV2
```

### 2. Optimization Plan Builder

The planner converts one user intent into multiple small optimization chunks.

Inputs:

- strategy name
- seed template path
- instrument and market suffix
- date range
- bars period
- fixed parameter values
- optimized parameter ranges
- guardrails
- max combinations per chunk
- optional max runtime per chunk
- output folder/session label

Guardrails:

- max drawdown dollars
- min trades
- min percent days traded
- optional min profit factor
- optional min net profit
- optional max trades per day
- optional session coverage requirement

Chunking rules:

- Estimate combination count before execution.
- Split large parameter grids into smaller generated optimizer templates.
- Prefer meaningful chunks:
  - by session/time bucket
  - by small groups of parameters
  - by coarse-to-fine ranges
  - by date regime if needed
- Store the chunk plan before running anything.

Chunk metadata should include:

```json
{
  "chunk_id": "chunk_001",
  "strategy": "PantheonMasterBotV01TesterV2",
  "session_bucket": "London Early",
  "combination_count_estimate": 4800,
  "max_runtime_minutes": 20,
  "parameters_optimized": ["averageFast", "averageSlow", "MaxStop"],
  "template_path": "...",
  "status": "planned"
}
```

### 3. NinjaTrader Runner Bridge

TA Foundation should treat NinjaTrader as an execution worker.

TA Foundation owns:

- what to run
- how to chunk it
- result storage
- scoring
- rejection reasons
- reports
- recommendations

NinjaTrader owns:

- loading generated templates
- running Strategy Analyzer
- exporting raw results

Recommended bridge additions:

#### Rich Command File

Extend `C:\temp\nt8_command.json`:

```json
{
  "action": "RunBatch",
  "runId": "opt_20260514_001",
  "sourceFolder": "D:\\...\\generated_optimizer_templates",
  "destFolder": "D:\\...\\nt_output",
  "closeTempTabs": true,
  "overwrite": true,
  "maxRuntimeMinutesPerTemplate": 20,
  "rollingDays": null
}
```

#### Status File

Add `C:\temp\nt8_status.json`:

```json
{
  "runId": "opt_20260514_001",
  "state": "running",
  "currentTemplate": "chunk_014.xml",
  "completed": 13,
  "total": 160,
  "lastError": null,
  "heartbeatUtc": "2026-05-14T18:20:00Z",
  "outputRoot": "D:\\...\\nt_output"
}
```

The web UI can poll this file to show progress and detect a stale NinjaTrader
worker.

#### Optimization Result Export

Best path:

- Make the AddOn export a `*_Optimization.csv` compatible with
  `ta_foundation.parsers.ninjatrader.optimization_csv`.

Fallback path:

- Add a TA Foundation parser for the AddOn's current per-template result
  folders.

The compatible `*_Optimization.csv` path is preferred because TA Foundation
already has parser and report support.

### 4. Result Library, Reporting, and Recommendation

Optimization sessions must be durable and reusable.

Recommended disk layout:

```text
.ta_artifacts/
  optimizer/
    sessions/
      <session_id>/
        session.json
        plan.json
        chunks.json
        generated_templates/
        nt_command.json
        nt_status_history.jsonl
        nt_output/
        parsed_results/
        reports/
        recommended_templates/
        renamed_templates/
        recommendations.json
        manifest.json
```

Recommended database index:

Use DuckDB or SQLite. DuckDB fits the existing project, but SQLite would also
work for simple metadata.

Tables to add or extend:

- `optimizer_sessions`
- `optimizer_chunks`
- `optimizer_artifacts`
- `optimizer_recommendations`
- possibly extend existing `optimization_batches`

Index fields:

- session id
- strategy name
- instrument
- market suffix
- source template path and hash
- strategy source path and hash
- date range
- bars period
- parameter ranges hash
- guardrail config hash
- max combinations per chunk
- max runtime per chunk
- output paths
- run status
- created and updated timestamps
- recommended template names
- report paths

Reuse behavior:

- Before launching a run, compute a stable plan hash.
- If matching results already exist, offer:
  - reuse results
  - refine from results
  - run missing chunks only
  - rerun everything
- Store rejected parameter regions so the user does not keep rerunning poor
  areas.

## Recommendation Engine

The recommendation engine should optimize a portfolio, not rank a leaderboard.

Hard filters:

- drawdown dollars <= user max
- trades >= user minimum
- percent days traded >= user minimum
- positive net profit
- optional profit factor minimum

Quality scoring:

- profit factor
- net profit
- drawdown efficiency
- average trade
- recent performance
- session consistency
- day participation
- trade count sufficiency
- parameter neighborhood stability
- rejection of one-day wonders

Diversity rules:

- choose one candidate per naming session bucket where possible
- avoid eight templates that are the same strategy shape with tiny parameter
  changes
- balance long, short, and both if the result pool supports it
- avoid all recommendations coming from the same MA tier or risk descriptor

Output:

```json
{
  "schema_version": 1,
  "session_id": "opt_20260514_001",
  "recommended_count": 8,
  "templates": [
    {
      "rank": 1,
      "session_bucket": "London Early",
      "template_name": "RisingApolloBoltB-NQ.xml",
      "source_chunk_id": "chunk_014",
      "reason": "Best London Early candidate after drawdown, trade count, participation, and stability filters.",
      "metrics": {
        "net_profit": 12345.0,
        "profit_factor": 1.62,
        "max_drawdown": 850.0,
        "trades": 142,
        "percent_days_traded": 68.5
      }
    }
  ]
}
```

Report views:

- Executive summary.
- Recommended 8 templates.
- Per-session bucket winners and alternates.
- Rejected candidates with reasons.
- Parameter stability heatmaps.
- Prior run comparison.
- Generated template download/open links.

## Template Generation Strategy

Use seed-template patching, not hand-built XML from scratch.

Reason:

- NinjaTrader XML can include machine-specific type serialization details.
- Existing `LargeCandleReversal` template generator shows the safer approach:
  preserve the seed template and patch specific values.

Template generation modes:

1. Optimization template mode:
   patch top-level optimizer fields and `<OptimizationParameters>`.
2. Fixed backtest template mode:
   generate fixed-value templates from winning parameter combinations.
3. Naming mode:
   pass generated fixed templates through `template-namer`.

Required seed behavior:

- Prefer a user-saved Strategy Analyzer optimization seed template per
  strategy.
- If no optimization seed exists, prompt the user to save one, or fall back to
  fixed-template generation for backtest batching.

## Product Experience

Primary pages:

- `/optimizer`
  Main optimization setup and runner.
- `/optimizer/sessions`
  Retrieve prior runs.
- `/optimizer/sessions/<session_id>`
  Run detail, results, reports, recommendations, and refinement actions.

Core buttons:

- Scan Strategies
- Load Strategy
- Estimate Combinations
- Generate Plan
- Run Optimization
- Reuse Previous Results
- Generate Report
- Recommend 8 Templates
- Rename Templates
- Refine Selected Area

Progress UI:

- current NinjaTrader worker state
- current chunk/template
- completed / total
- elapsed time
- stale worker warning
- latest exported file
- cancel/stop affordance if supported by the AddOn

## Implementation Phases

### Phase 0: Confirm Golden Path

- Confirm the exact Pantheon seed template to use.
- Confirm market suffix default, likely `NQ`.
- Confirm where final renamed templates should be written.
- Confirm whether NinjaTrader can export optimization result CSVs from the
  AddOn in a parser-compatible format.

### Phase 1: Strategy Catalog and Plan Preview

- Add backend scanner for NinjaTrader strategies and templates.
- Extend metadata extraction for real NinjaTrader `.cs` files.
- Add `/optimizer` shell page.
- Populate fields from Pantheon source and selected template.
- Let user define optimize ranges and guardrails.
- Estimate combinations and show chunk plan.
- Persist the session and plan on disk.

No NinjaTrader execution required in this phase.

### Phase 2: Template Generation and Naming

- Implement seed-based optimizer template generation.
- Implement fixed winning-template generation.
- Wire command-line call to `template-namer`.
- Save generated and renamed templates in the optimizer session folder.

### Phase 3: NinjaTrader Bridge

- Extend `nt8_command.json`.
- Add `nt8_status.json`.
- Extend AddOn loading for optimizer template fields.
- Add compatible optimization result export.
- Web UI sends batch commands and watches progress.

### Phase 4: Ingestion and Reports

- Ingest exported optimization CSVs into `OptimizationStore`.
- Store session metadata and result paths in a durable index.
- Generate HTML reports.
- Add retrieval/reuse page.

### Phase 5: Recommendation Engine

- Implement hard filters.
- Compute percent days traded.
- Add scoring and diversity selection.
- Generate `recommendations.json`.
- Generate 8 renamed templates across the naming guide session buckets.

### Phase 6: Refinement Loop

- Let user select winners, alternates, or rejected zones.
- Create follow-up optimization plans from selected areas.
- Reuse prior result library to avoid duplicate work.
- Track stale results and recommend refreshes.

## Current Implementation Status (2026-05-16)

### Backend modules (src/ta_foundation/web/)

| Module | Phase | Status |
|---|---|---|
| `optimizer_strategy_catalog.py` | 1 | scans NT strategy `.cs` + seed templates; returns parameter metadata, seed templates, swept params, instrument from `<InstrumentOrInstrumentList>` |
| `optimizer_session.py` | 1 | atomic-write durable session model (`OptimizerSessionDocument`, `Guardrails`, `ChunkingConfig`, `ParameterConfig`); on-disk at `.ta_artifacts/web_optimizer/sessions/<id>/` |
| `optimizer_plan.py` | 1 | combination estimation + chunking; persisted as `plan.json` |
| `optimizer_template_writer.py` | 2 | seed-patched chunk XML generation; explicitly patches `<InstrumentOrInstrumentList>` from session contract or seed when the other side is generic |
| `optimizer_namer.py` | 2 | shells out to `template-namer` for renamed bucket templates |
| `optimizer_preflight.py` | 3 | server-side gate: confirms generated chunks carry full contract before allowing RunBatch; surfaces seed/session/command contract + chunk count + NT heartbeat age |
| `optimizer_runner.py` | 3 | writes `C:\temp\nt8_command.json`, polls `nt8_status.json` heartbeat, falls back to counting `Summary.csv` files under `nt_output/`; refuses RunBatch when preflight blocks |
| `optimizer_results.py` | 4 | parses `*_Optimization.csv` exports into `OptimizationStore`; returns top-N + guardrail-filtered rows |
| `optimizer_deployment_package.py` | 5 | builds the full end-user package under `<session>/deployment_package/` — phase2/phase3/final handoffs, named backtest templates, decision summary, recommendations, manifest |

### API routes (`/api/optimizer/*`)

18 routes: session CRUD, strategy catalog list/detail, plan preview, template generate, template rename, preflight, run start/status/cancel, results, deployment package.

### UI (`templates/optimizer.html`)

Single page with 7 sections: strategy/seed picker → parameter table → guardrails/chunking → plan preview → generate XMLs → run on NinjaTrader (progress + cancel) → results.

### NinjaTrader AddOn (D:\ninjatraderOptimizer)

- AddOn loader applies `OptimizerType`, `OptimizationFitness`, and `<OptimizationParameters>` ranges from generated XMLs. Custom multi-objective optimizer smoke template verified.
- 2026-05-16 patch: `BatchControl.cs` shrinks per-template export filenames based on destination path depth to avoid Windows MAX_PATH truncation of `*_Optimization.csv` exports. This was the root cause of "phase finished but no result CSV" symptoms.
- Build with MSBuild only. Deploy via `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject\tools\Deploy-Optimizer.ps1` (stop NT → copy DLL/PDB → restart NT).

### Proven session

`.ta_artifacts/web_optimizer/sessions/opt_5bab6a5ee1ea/` is the canonical reference session. Its `deployment_package/` contains:

- `END_USER_DECISION.md` — operator-facing decision summary (state: `candidate_ready_for_operator_review`)
- `DECISION_SUMMARY.md` — phase-1 guardrail candidates summary
- `manifest.json` — machine-readable package metadata
- `analysis/` — `batch_summary.csv`, `guardrail_candidates.csv`, `top_optimizer_rows.csv`
- `templates_to_run/` — phase-1 chunk templates + run plan
- `phase2_refinement_handoff/` — 8 generated phase-2 templates, optimizer rows, lineage
- `phase3_risk_handoff/` — 8 phase-3 templates (`nt_output_short_path_success/` is the rerun after the path-length fix)
- `final_backtest_handoff/named_backtest_templates/breakout/` — 8 fixed Backtest XMLs ready to deploy
- `final_backtest_handoff/final_backtest_review/` — validation status `valid`, 8/8 passed, ranked recommendations

### What is NOT yet built

| Item | Notes |
|---|---|
| `/optimizer/sessions/<id>` detail page | API exists; UI is single-page-per-session only |
| AddOn cancel mid-template | `cancel_run` only stops between templates |
| Phase 6 deep refinement | "Clone & refine" exists (carries config to a new session); UI for selecting specific winners/rejected zones to drive narrowed sweeps is not yet wired |

### Recent updates (2026-05-16)

Shipped together with the design-doc refresh:

- **Multi-phase auto-advance in `optimizer_deployment_package`** — when phase-2 or phase-3 nt_output appears, the next phase's templates auto-generate. When `final_backtest_handoff/nt8_backtest_results/` arrives, `optimization.review` runs and writes bucket-diverse recommendations into `END_USER_DECISION.md`. Decision states now include `needs_phase2_run`, `needs_phase3_run`, `needs_phase3_run_then_oos_dates`, `needs_final_backtest_run`, `candidate_ready_for_operator_review`, and `settings_contract_warning`.
- **`/optimizer/sessions` list page** with strategy / contract / decision state / final validation status / recs count per row, plus Resume / Clone & refine / Delete actions.
- **Plan-hash reuse** — `OptimizerSessionDocument.plan_hash()` computes a stable SHA over strategy + seed + parameter config + guardrails + instrument. `GET /api/optimizer/sessions/<id>/matches` returns other sessions with the same hash. UI "Check for prior runs" button surfaces them.
- **Clone & refine** — `clone_session()` and `POST /api/optimizer/sessions/<id>/clone` create a fresh session that inherits config but not outputs.
- **OOS dates and Backtest seed** persistable on the session doc (`oos_from_date`, `oos_to_date`, `backtest_seed_template_path`).
- **Parameter UX cleanup** in the parameter table: flipping a row to `optimize` hides fixed_value, auto-seeds bool sweeps to `false..true` step `1`, and seeds numeric min/max from the fixed default. Flipping to `fixed` hides min/max/increment.
- **Bool-sweep `Increment` bug fixed** — was serialized as `true` and collapsed the sweep. Now serialized as integer `1`. Regression test in place.
- **Parser bool-coercion fixed** — `_coerce_scalar` no longer treats `"1"`/`"0"` as bool, so numeric params like `Contracts=1` no longer become `True`.
- **`_top_rows` no longer truncates parameters** — was clipping to `param_cols[:12]`, which dropped `param_Reverse` and other late-position params. Full param set surfaces now.

Reference live test session: `opt_967c2dbf2660` (regression-mode smoke, `Reverse=true`, deployment package state `no_guardrail_candidates` because drawdown $3180 exceeded the $2500 guardrail — correct behavior).

## MVP Recommendation

Before committing the web MVP to either optimizer path, prove the custom
optimizer smoke path in NinjaTrader.

The immediate proof should be deliberately small:

1. Generate one Pantheon optimizer template that uses
   `CustomMultiObjectiveOptimizer` and `CustomMultiObjectiveFitness`.
2. Load and run it through the batch AddOn.
3. Confirm the AddOn log shows the custom optimizer, custom fitness, and a
   non-zero optimization parameter count after template load.
4. Confirm NinjaTrader exports a parser-compatible optimization CSV.

If that proof is clean, the custom optimizer can be used as the default
execution engine for staged optimizer templates. If it is not clean, fall back
to NinjaTrader's default optimizer for the first shippable web loop while the
custom optimizer DLL continues separately.

The custom optimizer remains the path for smarter search:

- random sampling
- staged narrowing
- Pareto scoring
- robustness-aware fitness
- parameter neighborhood exploration

## Open Questions

1. Which exact Pantheon template should be the first seed template?
2. Should final renamed templates be copied directly into the NinjaTrader
   strategy template folder, or into a review/export folder first?
3. What default max combinations per chunk should ship?
4. What default max runtime per chunk should ship?
5. Should percent days traded be computed from trade exports, optimization
   exports, or a follow-up fixed backtest pass?
6. Should the AddOn attempt to cancel a long-running optimization, or only stop
   between templates/chunks?
7. What exact report should be shown for the "next day prediction" view?
8. Should the first version support only Pantheon, or scan all strategies with
   a warning that non-Pantheon strategies are experimental?

## Future Chat Handoff Summary

If continuing this work in a new chat, start here:

- Read this document.
- Read `docs/designs/discovery_web_ui.md` for the web/session pattern.
- Inspect `D:\ninjatraderOptimizer\NinjaTraderAddOnProject\BatchControl.cs`.
- Inspect `D:\ninjatraderOptimizer\NinjaTraderAddOnProject\StrategyAnalyzerAutomation.cs`.
- Inspect `D:\templateNaming\README.md`.
- Inspect `D:\templateNaming\naming_rules.json`.
- Start with `PantheonMasterBotV01TesterV2`.

The core design decision is:

- TA Foundation is the control plane and memory.
- NinjaTrader is the execution worker.
- Template naming is a post-processing step.
- Previous runs must be first-class reusable evidence, not disposable output
  folders.
