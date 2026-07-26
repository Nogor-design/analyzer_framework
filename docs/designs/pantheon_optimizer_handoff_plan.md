# Pantheon Optimizer Handoff Plan

Status: **CLI workflow proven end to end**; web optimizer now wraps the same
engine. The standalone modules listed in "Current Implementation" below all
exist and are exercised by the proven session
`.ta_artifacts/web_optimizer/sessions/opt_5bab6a5ee1ea/`.

For the operator runbooks:
- CLI flow: [`docs/runbooks/pantheon_custom_optimizer_full_run.md`](../runbooks/pantheon_custom_optimizer_full_run.md)
- Web flow: [`docs/runbooks/pantheon_web_optimizer_full_run.md`](../runbooks/pantheon_web_optimizer_full_run.md)
- Web architecture: [`docs/designs/ninjatrader_optimizer_web_ui.md`](ninjatrader_optimizer_web_ui.md)

## Purpose

Build a standalone optimizer workflow for Pantheon-style NinjaTrader templates
that helps find the best backtests by time period, mode, and risk shape. The
first goal is a team-ready handoff package, not full automation.

The optimizer should support the way the work is currently done by hand:

1. Run broad first-pass optimizations by start hour.
2. Let the custom optimizer pick promising rows from the optimization output.
3. Refine each selected row through second, third, and optional fourth passes.
4. Repeat for Breakout and Regression.
5. Only after the third phase, produce fixed Backtest-mode templates, reports,
   and a clear package the team can run.

This project should remain easy to integrate into the future `/optimizer` web UI
while also being runnable from command line without NinjaTrader automation.

## Current Manual Workflow Captured

Samples live in:

```text
docs/samples/
  OptimizeFirstRunBreakout.xml
  OptimizeSecondRunBreakout.xml
  OptimizethirdRunBreakout.xml
  PantheonMasterBotV01TesterV2.cs
  Finding breakouts.txt
```

Observed pass structure:

### Pass 1: Broad Discovery

- `Reverse = false` for Breakout.
- Later repeat with `Reverse = true` for Regression.
- `StartTimeH` is fixed per run.
- Start hours are stepped in 4-hour increments.
- `DurationTimeH` is swept, currently `2..4` by `2`.
- `averageFast` is fixed around `5`.
- `averageSlow` is swept broadly, currently `50..500` by `50`.
- `MaxStop` is swept broadly, currently `50..350` by `50`.
- `MaxTPRatio` is swept broadly, currently `.5..2` by `.5`.
- First review emphasizes:
  - profit factor
  - total net profit
  - max drawdown dollars
  - trade count
  - whether wins are dropping off recently
  - slow-MA diversity

### Pass 2: Candidate Refinement

- Start from selected first-pass candidates.
- Tighten around chosen `averageSlow`.
- Sweep `averageFast`.
- Tighten `MaxStop`.
- Tighten `MaxTPRatio`.
- Sweep `Long` and `Short` to discover directional specialists.

### Pass 3: Daily Risk Behavior

- Tune `ProfitStop`.
- Tune `LossStop`.
- Tune `MaxTrades`.
- This pass controls daily behavior and prop-firm survivability, not just raw
  leaderboard rank.

### Later Passes

- Optional fourth pass should be treated as a refinement from the current best
  lineage, not a new search from scratch.

## Core Product

The first product is an optimization handoff folder:

```text
optimizer_handoff/
  README_FOR_TEAM.md
  run_plan.csv
  run_plan.json
  templates/
    breakout/
    regression/
  intake/
    drop_results_here/
  reports/
  recommendations.json
  named_templates/
```

This package should tell the team:

- which templates to run
- what order to run them in
- which output folder names to use
- what result files are expected
- which pass and candidate each template belongs to
- how to return results for ingestion

## Standalone Architecture

### 1. Template Reader

Reads NinjaTrader Strategy Analyzer XML templates and extracts:

- strategy type
- optimizer type
- optimization fitness
- optimized parameters
- fixed parameters
- current strategy values
- time window
- `Reverse` mode
- direction flags
- estimated combination count

This allows the optimizer to understand existing hand-made templates before it
generates new ones.

### 2. Workflow Planner

Creates pass plans from a seed template and a workflow config.

Planned config shape:

```yaml
strategy: PantheonMasterBotV01TesterV2
market: NQ
instrument: NQ 06-26
mode: breakout
reverse: false
start_hours: [0, 4, 8, 12, 16, 20]
passes:
  - id: pass_1_broad
    optimize:
      DurationTimeH: {min: 2, max: 4, step: 2}
      averageSlow: {min: 50, max: 500, step: 50}
      MaxStop: {min: 50, max: 350, step: 50}
      MaxTPRatio: {min: 0.5, max: 2.0, step: 0.5}
  - id: pass_2_refine
    derive_from: selected_candidates
  - id: pass_3_risk
    derive_from: selected_candidates
```

### 3. Optimization Grid Intake

Ingests NinjaTrader Strategy Analyzer optimization exports.

The optimizer CSV is treated as an intermediate phase output, not a final
template selection. Phase 1 rows create phase 2 optimizer templates, phase 2
rows create phase 3 optimizer templates, and phase 3 rows create fixed
Backtest-mode templates for the current batch runner.

Important rule:

- Do not emit final named Backtest templates directly from phase 1 or phase 2.
  Those rows still need to prove out through the remaining optimizer phases.

### 4. Result Intake

Ingests team-returned NinjaTrader output folders and CSVs.

The first version should accept:

- current AddOn per-template exports:
  - `Settings.csv`
  - `Summary.csv`
  - `Analysis.csv`
  - `Trades.csv`
- AddOn per-template backtest exports after fixed templates are run.

### 5. Candidate Evaluator

Ranks candidates inside the correct category rather than one global pile.

Hard filters:

- max drawdown dollars
- minimum trades
- positive net profit
- minimum percent days traded where available
- optional minimum profit factor

Quality score:

- profit factor
- total net profit
- drawdown efficiency
- average trade
- recent 5D, 10D, and 20D behavior
- trade participation
- directional clarity
- parameter-neighborhood stability when available

### 6. Diversity Selector

Prevents the final set from becoming clones.

Categories:

- Breakout vs Regression
- session bucket / time window
- slow-MA family
- Long / Short / Both
- risk shape
- recent momentum status

Slow-MA families:

- fast trend: `50..150`
- middle trend: `200..300`
- slow trend: `350..500`

### 7. Report And Handoff Builder

Creates a report focused on this optimizer workflow:

- pass lineage
- best candidates by time window
- Breakout and Regression sections
- candidate cards
- rejection reasons
- team run plan
- named templates
- report links
- God image matching
- recreated NinjaTrader Summary and Analysis panels from exported CSV data

Actual NinjaTrader screenshots can be added later, but the first version should
recreate the panels from data because it is more reliable.

## Relationship To Existing Web UI Design

The standalone optimizer is the engine behind the future `/optimizer` page.

The web UI should call the same planner, reader, intake, evaluator, and report
builder used by the CLI. The web app adds convenience, but it should not own the
business logic.

## First Implementation Slices

### Slice 1: Read Existing Templates

- Parse NinjaTrader optimization template XML.
- Extract optimized parameter ranges.
- Estimate combinations.
- Classify mode as Breakout or Regression from `Reverse`.
- Extract time window.
- Add tests from small XML fixtures.

### Slice 2: Explain Sample Passes

- Add a CLI command that reads a folder of XML templates.
- Output a CSV/Markdown summary:
  - template name
  - pass guess
  - mode
  - start time
  - duration range
  - optimized params
  - estimated combinations

### Slice 3: Generate Pass 1 Handoff

- Generate first-pass templates from a seed XML.
- Support start hours in 4-hour increments.
- Support Breakout and Regression.
- Write `run_plan.csv`, `run_plan.json`, and `README_FOR_TEAM.md`.

### Slice 4: Intake Results

- Read result folders returned by the team.
- Attach parsed results to the run plan.
- Compute initial metrics and rejection reasons.

### Slice 5: Recommend Next Pass

- Select pass 1 candidates by time bucket and MA family.
- Generate pass 2 templates.
- Preserve candidate lineage.

### Slice 6: Optimizer Handoff Report

- Produce the first HTML/Markdown report for the whole optimization workflow.
- Include links to named templates and existing TA Foundation reports.

## Open Questions

1. Which exact start-hour set should be default after `0` and `4`?
2. Should each 4-hour start run include only `2h` and `4h`, or also `3h`?
3. What is the minimum trade count for a one-month pass 1 candidate?
4. What max NQ drawdown should reject a candidate vs mark it MNQ-only?
5. How many pass 1 candidates should be selected per time window?
6. Should Regression use the exact same pass ranges as Breakout at first?
7. Should final handoff choose one best per bucket or keep multiple alternates?
8. Where should final team handoff folders be written by default?

## Immediate Recommendation

Start by reading and summarizing the existing templates, then generate the first
team handoff package manually from those contracts. Once the handoff shape feels
right, add template generation and result intake.

## Current Implementation

Initial standalone modules:

- `ta_foundation.optimization.nt_template`
  - Parses NinjaTrader Strategy Analyzer optimization XML.
  - Extracts swept parameters, fixed strategy values, mode, time window, and
    estimated combinations.
- `ta_foundation.optimization.handoff`
  - Builds `run_plan.csv`, `run_plan.json`, `run_plan.md`, and
    `README_FOR_TEAM.md`.
  - Copies templates into `templates/<mode>/<pass>/`.
- `ta_foundation.optimization.template_generator`
  - Generates broad pass-1 discovery XML templates from a seed template.
  - Defaults to start hours `0,4,8,12,16,20` and modes
    `breakout,regression`.
  - Generates pass-2 candidate refinement templates.
  - Generates pass-3 daily risk behavior templates.
  - Prunes fixed parameters out of `<OptimizationParameters>` so each phase's
    custom optimizer run only randomizes the intended swept controls. Fixed
    settings are still written into the strategy body.
- `ta_foundation.optimization.result_intake`
  - Ingests returned NinjaTrader CSV result folders.
  - Supports `Summary/Summery`, `Settings`, `Trades`, and `_Trades_keep.csv`.
  - Computes percent days traded and recent trade momentum.
- `ta_foundation.optimization.evaluator`
  - Applies first hard filters and scoring.
  - Writes transparent pass/reject reasons.
- `ta_foundation.optimization.recommendations`
  - Selects a diverse set of passing candidates.
  - Prefers coverage across mode, session bucket, slow-MA family, risk shape,
    and direction.
  - Writes `recommendations.csv`, `recommendations.json`, and
    `recommendations.md`.
- `ta_foundation.optimization.review`
  - One-command returned-result review.
  - Writes intake, evaluation, and recommendation artifacts into one folder.
- `ta_foundation.optimization.workflow`
  - One-command pass-1 template generation plus team handoff packaging.
- `ta_foundation.optimization.next_pass`
  - Creates pass-2 or pass-3 templates from passing returned result folders.
- `ta_foundation.optimization.grid_workflow`
  - Reads NinjaTrader `*_Optimization.csv` exports.
  - Scores and selects optimization rows as intermediate phase candidates.
  - Generates phase-2 templates from phase-1 optimizer rows.
  - Generates phase-3 templates from phase-2 optimizer rows.
  - Generates fixed Backtest-mode templates only from phase-3 optimizer rows.

Useful commands:

```powershell
python -m ta_foundation.optimization.handoff `
  --input-dir "D:\Backup\projects\PythonProject\ta_foundation\docs\samples" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_handoff_samples"
```

```powershell
python -m ta_foundation.optimization.workflow `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeFirstRunBreakout.xml" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_pass1_workflow"
```

```powershell
python -m ta_foundation.optimization.result_intake `
  --input-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_result_intake_MAGods"
```

```powershell
python -m ta_foundation.optimization.evaluator `
  --input-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_eval_MAGods" `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

```powershell
python -m ta_foundation.optimization.recommendations `
  --input-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_recommend_MAGods" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

```powershell
python -m ta_foundation.optimization.review `
  --input-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_review_MAGods" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

```powershell
python -m ta_foundation.optimization.next_pass `
  --target-pass pass2 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeSecondRunBreakout.xml" `
  --results-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_pass2_from_MAGods" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

To generate a next pass only for manually selected winners:

```powershell
python -m ta_foundation.optimization.next_pass `
  --target-pass pass2 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeSecondRunBreakout.xml" `
  --results-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_pass2_selected_MAGods" `
  --include-run-ids "RisePoseidonHunterB-NQ" `
  --average-fast-min 2 `
  --average-fast-max 10 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

Current pass-2 defaults:

- `averageFast`: `2..10` by `1`
- `averageSlow`: selected slow MA +/- `20`, by `10`
- `MaxStop`: selected stop +/- `20`, by `20`
- `MaxTPRatio`: selected TP ratio +/- `0.3`, by `0.1`
- `Long`: `false..true`
- `Short`: `false..true`

`averageFast` bounds can be overridden with `--average-fast-min` and
`--average-fast-max` when a selected result is already outside the original
second-pass fast-MA range.

```powershell
python -m ta_foundation.optimization.next_pass `
  --target-pass pass3 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizethirdRunBreakout.xml" `
  --results-dir "C:\Users\Owner\Downloads\MAGods" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_pass3_from_MAGods" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --min-percent-days-traded 20
```

When working from NinjaTrader optimization grid exports instead of returned
single-backtest folders, advance the workflow one phase at a time:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase phase2 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeSecondRunBreakout.xml" `
  --optimization-csv-dir "C:\Users\Owner\Downloads\OptimizerPhase1Exports" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_grid_phase2" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase phase3 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizethirdRunBreakout.xml" `
  --optimization-csv-dir "C:\Users\Owner\Downloads\OptimizerPhase2Exports" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_grid_phase3" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

After phase 3 returns its optimization grid, create the fixed Backtest-mode
templates for the existing batch runner:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase final `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\strategies\PantheonMasterBotV01TesterV2\templates\sampleTemplate.xml" `
  --optimization-csv-dir "C:\Users\Owner\Downloads\OptimizerPhase3Exports" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\optimizer_grid_final_backtests" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --from-date 2026-04-14 `
  --to-date 2026-05-14
```

For final Backtest-mode generation, always set `--from-date` and `--to-date`
to the same out-of-sample date range used by the optimizer phases. Otherwise
the generated fixed templates inherit the seed Backtest template's date range.

Current focused verification:

```powershell
python -m pytest src/ta_foundation/tests/optimization -q
```

Custom optimizer smoke template generation:

```powershell
python -m ta_foundation.optimization.template_generator `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeFirstRunBreakout.xml" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase1" `
  --start-hours "0" `
  --modes "breakout" `
  --optimizer-type "NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer" `
  --optimization-fitness "NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness"
```

Use the generated `breakout\Pass1_Breakout_Start00.xml` as the first runtime
proof that the NinjaTrader AddOn restores optimizer type, optimization fitness,
and optimization parameter ranges from XML before attempting the full staged
phase-1 through phase-3 workflow.

Expected phase-specific optimizer parameters after generation:

- Phase 1 broad discovery: `DurationTimeH`, `averageSlow`, `MaxStop`, `MaxTPRatio`.
- Phase 2 candidate refinement: `averageFast`, `averageSlow`, `MaxStop`, `MaxTPRatio`, `Long`, `Short`.
- Phase 3 daily risk: `ProfitStop`, `LossStop`, `MaxTrades`.

## Web Optimizer Additions (2026-05-16)

The web layer at `src/ta_foundation/web/optimizer_*` now wraps this engine:

- `optimizer_strategy_catalog` scans NT strategy `.cs` files and seed templates,
  extracts `<InstrumentOrInstrumentList>` so the contract reaches generated
  XMLs (e.g. `NQ 06-26`, not just `NQ`).
- `optimizer_session` writes durable session state under
  `.ta_artifacts/web_optimizer/sessions/<id>/`.
- `optimizer_plan` estimates combinations and chunks them.
- `optimizer_template_writer` patches `<InstrumentOrInstrumentList>` from the
  session contract or the seed when the other side is generic — this hardened
  a real bug where the last chunk used generic `NQ` and NinjaTrader produced
  zero results.
- `optimizer_preflight` blocks `RunBatch` if any generated chunk XML contains
  a generic instrument; surfaces seed/session/command contract, chunk count,
  and NT heartbeat age.
- `optimizer_runner` writes `C:\temp\nt8_command.json`, polls
  `nt8_status.json` with a folder-watching fallback.
- `optimizer_results` parses `*_Optimization.csv` exports back into the
  existing `OptimizationStore`.
- `optimizer_deployment_package` builds the operator-facing package:
  `END_USER_DECISION.md`, `manifest.json`, phase2/phase3/final handoff
  subfolders, named backtest templates, recommendations.

## NinjaTrader AddOn (D:\ninjatraderOptimizer)

The AddOn loader applies `OptimizerType`, `OptimizationFitness`, and
`<OptimizationParameters>` ranges from generated XMLs. The custom
multi-objective optimizer smoke template has been verified.

2026-05-16 patch in `BatchControl.cs`: per-template export filenames now
shrink based on destination path depth. This avoids Windows MAX_PATH
truncating `*_Optimization.csv` exports, which was the root cause of "phase
finished but no result CSV" symptoms during the phase-3 rerun. The proven
short-path rerun is in
`opt_5bab6a5ee1ea/deployment_package/phase3_risk_handoff/nt_output_short_path_success/`.

## Proven End-to-End Session

`.ta_artifacts/web_optimizer/sessions/opt_5bab6a5ee1ea/deployment_package/`
contains a full, validated run:

- Contract: `NQ 06-26` from selected seed template.
- Phase 1: 4 chunks, 2,000 optimizer rows parsed.
- Phase 2: 8 templates, 4,000 optimizer rows parsed.
- Phase 3: 8 templates, 640 optimizer rows parsed (after short-path rerun).
- Final fixed Backtests: 8 templates, all 8 exported clean.
- Final review: `validation_status = valid`, 8/8 passed, 0 settings
  contract violations.
- Top candidate: `F_001`, Pre-Market, net $19,880, PF 5.01, DD $1,500,
  17 trades, 54.84% days traded.
