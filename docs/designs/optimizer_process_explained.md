# Optimizer Process Explained

Status: future-use reference for understanding the current NinjaTrader web
optimizer process before changing it. Based on
`docs/designs/ninjatrader_optimizer_web_ui.md`,
`docs/runbooks/pantheon_web_optimizer_full_run.md`,
`docs/designs/pantheon_optimizer_handoff_plan.md`, and the current
implementation in `src/ta_foundation/web/optimizer_*.py` plus
`src/ta_foundation/optimization/*.py`.

Last reviewed: 2026-05-19.

## Short Version

The optimizer is a staged search-and-validation workflow.

TA Foundation is the control plane. It stores session state, builds parameter
plans, generates NinjaTrader XML templates, ingests NinjaTrader output, filters
and scores candidates, advances the run through later phases, and builds the
operator package.

NinjaTrader is the execution worker. It loads generated XML templates through
the batch AddOn, runs Strategy Analyzer, and exports CSV results.

The final result is not the best row from the first optimizer grid. The final
result is an operator-facing deployment package containing fixed Backtest
templates, final Backtest validation results, recommendations, lineage files,
and decision summaries.

## Main Artifacts

Each web optimizer run is stored under:

```text
.ta_artifacts/web_optimizer/sessions/<session_id>/
  session.json
  plan.json
  generated_templates/
  nt_output/
  run.json
  deployment_package/
```

The most important generated package is:

```text
deployment_package/
  DECISION_SUMMARY.md
  END_USER_DECISION.md
  manifest.json
  analysis/
    batch_summary.csv
    top_optimizer_rows.csv
    guardrail_candidates.csv
  templates_to_run/
  phase2_refinement_handoff/
  phase3_risk_handoff/
  final_backtest_handoff/
    named_backtest_templates/
    nt8_backtest_results/
    final_backtest_review/
```

The proven reference session is:

```text
.ta_artifacts/web_optimizer/sessions/opt_5bab6a5ee1ea/
```

## Roles In The System

### TA Foundation Web UI

The `/optimizer` page collects the strategy, seed template, instrument,
parameter modes, guardrails, and chunking rules. It also exposes buttons for
plan preview, XML generation, NinjaTrader execution, result refresh, prior-run
matching, cloning, and deployment-package generation.

The UI is backed by session files, so a run can be resumed later.

### TA Foundation Optimizer Engine

The engine is spread across two layers:

- `src/ta_foundation/web/optimizer_*.py` handles web session state, plan
  preview, generated templates, preflight, run status, and deployment packages.
- `src/ta_foundation/optimization/*.py` handles phase-specific candidate
  evaluation, next-phase template generation, final Backtest template creation,
  result review, and recommendations.

### NinjaTrader AddOn

The AddOn watches `C:\temp\nt8_command.json`. When TA Foundation writes a
`RunBatch` command, the AddOn loads each XML template from the source folder,
runs Strategy Analyzer, and exports results under the destination folder. It
writes progress to `C:\temp\nt8_status.json`.

The AddOn is expected to preserve important XML settings such as optimizer type,
optimization fitness, optimization parameter ranges, and the full contract such
as `NQ 06-26`.

## End-To-End Flow

## Phase 0: Session Setup

The operator starts by choosing:

- strategy, usually `PantheonMasterBotV01TesterV2`
- seed Strategy Analyzer template
- full contract, for example `NQ 06-26`
- fixed and optimized parameters
- guardrails
- chunking settings
- optional OOS dates and Backtest seed template for final validation

The selected seed template is critical. TA Foundation patches this template
instead of building XML from scratch because NinjaTrader XML contains type and
serialization details that are safer to preserve.

The session is saved to `session.json`. It includes the strategy id, seed path,
instrument, market suffix, parameter configs, guardrails, chunking config, OOS
dates, and related paths.

## Phase 1: Plan Preview And Chunking

Implementation: `src/ta_foundation/web/optimizer_plan.py`.

The plan builder converts the user's parameter choices into a deterministic
chunk plan.

For each parameter:

- fixed parameters count as one value
- numeric optimized parameters count values from min to max by increment
- bool optimized parameters count as two values unless pinned
- invalid ranges create warnings

The total combination estimate is the product of all optimized parameter step
counts.

The chunker then splits the parameter grid so each chunk stays near
`max_combinations_per_chunk`. Its current strategy is greedy and
human-readable:

1. Walk optimized parameters in declaration order.
2. Keep adding full sweeps while the chunk stays under the cap.
3. When adding the next parameter would exceed the cap, slice that parameter's
   numeric range into legal increment-aligned pieces.
4. Emit chunks such as `chunk_001`, `chunk_002`, etc.

This produces `plan.json`. Each chunk records:

- chunk id
- estimated combination count
- optimized sweep ranges for that chunk
- fixed parameters

The plan also gets a stable `plan_hash`. That hash is used to detect matching
prior runs with the same strategy, seed, instrument, parameters, guardrails,
chunking, and generated chunk shape.

Important limitation: chunking is mostly a mechanical grid splitter. It does
not yet implement a deep adaptive search by itself. The staged refinement
happens later through phase 2 and phase 3 template generation.

## Phase 1 XML Generation

Implementation: `src/ta_foundation/web/optimizer_template_writer.py`.

After the plan is saved, TA Foundation creates one NinjaTrader optimizer XML
per chunk under:

```text
<session>/generated_templates/chunk_001.xml
<session>/generated_templates/chunk_002.xml
...
```

For each chunk, the writer:

1. Reads the seed XML as text.
2. Optionally patches `OptimizerType` and `OptimizationFitness`.
3. Patches `<InstrumentOrInstrumentList>` to the full contract from the
   session or seed.
4. Patches optimizer `KeepBestResults` from the chunking config.
5. Writes optimized chunk parameters into `<OptimizationParameters>`.
6. Writes fixed parameters into the strategy node.
7. Clamps fixed parameters in their `<Parameter>` block so NinjaTrader does not
   accidentally sweep them.

This is seed-template patching, not clean-room XML creation.

For the proven Pantheon broad pass, the expected phase-1 optimized parameters
are:

- `DurationTimeH`
- `averageSlow`
- `MaxStop`
- `MaxTPRatio`

The broad pass is meant to find promising time windows, slow-MA families, stop
sizes, and target ratios.

## Preflight

Implementation: `src/ta_foundation/web/optimizer_preflight.py`.

Before RunBatch is allowed, preflight checks the generated templates and
runtime assumptions. The most important gate is contract correctness: every
generated XML must carry the full contract, such as `NQ 06-26`, not generic
`NQ`.

Preflight also surfaces useful run context such as the seed/session/command
contract, chunk count, and NinjaTrader heartbeat age.

If preflight fails, the correct fix is to regenerate or repair the templates.
The intended process is not to bypass preflight.

## Phase 1 Execution In NinjaTrader

Implementation: `src/ta_foundation/web/optimizer_runner.py` plus the external
AddOn in `D:\ninjatraderOptimizer`.

When the operator clicks Run, TA Foundation writes:

```text
C:\temp\nt8_command.json
```

The command points the AddOn at:

- `sourceFolder`: the session's `generated_templates/`
- `destFolder`: the session's `nt_output/`

The AddOn loads each chunk XML, runs Strategy Analyzer, and exports
`*_Optimization.csv` files plus other per-template outputs. TA Foundation polls
`C:\temp\nt8_status.json` for progress. If status is unavailable, it can fall
back to counting exported files in `nt_output/`.

The key output from phase 1 is a set of NinjaTrader optimization grid CSVs.
These are retained optimizer rows, not final deployment proof.

## Phase 1 Result Ingestion

Implementation: `src/ta_foundation/web/optimizer_results.py`.

TA Foundation ingests `nt_output/` using the NinjaTrader optimization CSV
parser. Parsed data goes into an `OptimizationStore`.

The result loader creates:

- batch summaries
- combined optimizer rows
- top rows
- guardrail-filtered rows

Available phase-1 guardrails include:

- max drawdown dollars
- min trades
- min profit factor
- min net profit

Percent days traded is not available from raw `*_Optimization.csv` rows, so it
is only enforced later during fixed Backtest review when trade-level data is
available.

The quick web ranking computes:

```text
optimizer_score =
  profit_factor * 1000
  + total_net_profit / 10
  - drawdown_abs / 2
```

This score is used for display and initial sorting. Later phase selection uses
a separate scoring model.

## Deployment Package Build

Implementation: `src/ta_foundation/web/optimizer_deployment_package.py`.

The deployment package builder is the coordinator for the remaining phases.
It is safe to run repeatedly. It preserves returned NinjaTrader result folders
and regenerates derived files.

On each run it:

1. Copies the original generated phase-1 templates into `templates_to_run/`.
2. Writes phase-1 analysis CSVs.
3. Builds phase 2 templates from phase-1 optimizer rows when possible.
4. Detects returned phase-2 results and builds phase 3 templates.
5. Detects returned phase-3 results and builds final fixed Backtest templates.
6. Detects final Backtest results and builds final review/recommendations.
7. Writes `DECISION_SUMMARY.md`, `END_USER_DECISION.md`, and `manifest.json`.

Its `decision_state` explains what the operator should do next:

- `blocked`: preflight failed
- `no_results`: no phase-1 optimizer rows parsed
- `no_guardrail_candidates`: no phase-1 rows passed guardrails
- `needs_phase2_run`: phase-2 templates are ready to run
- `needs_phase3_run`: phase-3 templates are ready to run
- `needs_phase3_run_then_oos_dates`: phase-3 templates are ready, but final
  OOS dates still need to be set
- `needs_final_backtest_run`: final fixed Backtest templates are ready
- `candidate_ready_for_operator_review`: final review is valid
- `settings_contract_warning`: final Backtest settings violated the required
  contract
- `final_review_inconclusive`: final review ran but did not produce a clean
  valid state

## Candidate Evaluation From Optimizer Grids

Implementation: `src/ta_foundation/optimization/grid_workflow.py`.

For phase advancement, TA Foundation reads `*_Optimization.csv` files and
normalizes parameter columns. It then evaluates each retained optimizer row.

A row passes only if it clears hard filters:

- total net profit is positive
- profit factor is at least the configured minimum
- absolute max drawdown is at or below the configured maximum
- trade count is at or above the configured minimum

Each row is also classified:

- mode: `breakout` when `Reverse=false`, `regression` when `Reverse=true`
- session bucket from `StartTimeH`
- slow-MA family from `averageSlow`
- risk shape from `MaxStop` and `MaxTPRatio`
- direction from `Long` and `Short`

Optimizer-grid phase score is:

```text
profit_score = min(40, net_profit / 500)
pf_score     = min(30, profit_factor * 8)
dd_score     = 20 * (1 - min(drawdown_abs, max_drawdown) / max_drawdown)
trade_score  = min(10, trades / min_trades * 5)
total_score  = profit_score + pf_score + dd_score + trade_score
```

The selector tries to choose up to 8 candidates while preserving diversity:

1. First, prefer unique combinations of mode, session bucket, and slow-MA
   family.
2. Then add candidates that avoid exact clones across mode, session bucket,
   slow-MA family, risk shape, and direction.
3. Finally, fill remaining slots with the strongest passing candidates.

This means the system is intentionally selecting a portfolio of candidates,
not simply the top 8 rows by profit factor.

## Phase 2: Candidate Refinement

Implementation:
`create_next_phase_from_optimization_csv(..., target_phase="phase2")`.

Phase 2 starts from selected passing phase-1 optimizer rows. It generates
refinement optimizer templates under:

```text
deployment_package/phase2_refinement_handoff/generated_phase2_templates/
```

Current phase-2 sweep intent:

- keep the selected time window
- keep the selected `Reverse` mode
- tighten around selected `averageSlow`
- sweep `averageFast`
- tighten `MaxStop`
- tighten `MaxTPRatio`
- sweep `Long`
- sweep `Short`

Current default `averageFast` range is `2..10` by `1`.

The phase-2 output includes:

- generated phase-2 XML templates
- `team_handoff/`
- `optimization_grid_candidates.csv/json`
- `optimization_phase_lineage.csv/json`
- `OPTIMIZATION_PHASE_SUMMARY.md`

The operator then runs these phase-2 templates in NinjaTrader and returns the
exported optimization CSVs into a folder named like:

```text
deployment_package/phase2_refinement_handoff/nt_output/
```

After those results exist, rebuilding the deployment package advances to
phase 3.

## Phase 3: Daily Risk Behavior

Implementation:
`create_next_phase_from_optimization_csv(..., target_phase="phase3")`.

Phase 3 starts from selected passing phase-2 optimizer rows. It generates
daily-risk optimizer templates under:

```text
deployment_package/phase3_risk_handoff/generated_phase3_templates/
```

Current phase-3 sweep intent:

- keep the selected time window
- keep selected `Reverse`
- keep selected fast/slow MA and trade direction settings
- keep selected `MaxStop`
- keep selected `MaxTPRatio`
- tune `ProfitStop`
- tune `LossStop`
- tune `MaxTrades`

This phase is about daily behavior and survivability, not just raw leaderboard
rank.

The operator runs these templates in NinjaTrader and returns optimization CSVs
into a folder named like:

```text
deployment_package/phase3_risk_handoff/nt_output/
```

The package builder looks for the newest `nt_output*` folder that contains
`*_Optimization.csv`. This supports recovery folders such as
`nt_output_short_path_success/`.

After phase-3 results exist, rebuilding the deployment package advances to
final fixed Backtest template generation.

## Final Template Generation

Implementation:
`create_final_backtest_templates_from_phase3_csv(...)` and
`generate_fixed_backtest_template(...)`.

Final templates are created only from phase-3 optimizer rows. They are fixed
Backtest-mode templates, not optimizer templates.

The generator:

1. Reads a Backtest seed template.
2. Removes optimizer sections:
   - `<OptimizerType>`
   - `<OptimizerParameters>`
   - `<OptimizationFitness>`
   - `<OptimizationParameters>`
3. Forces the template category to `Backtest`.
4. Applies OOS `From` and `To` dates when provided.
5. Patches selected strategy values from the phase-3 candidate.
6. Forces final validation contract values:
   - `UseTrend=false`
   - `UseTrendReverse=false`
7. Writes named fixed templates under:

```text
deployment_package/final_backtest_handoff/named_backtest_templates/
```

It also writes:

```text
README_FINAL_BACKTEST_TEMPLATES.md
RUN_FINAL_BACKTESTS.md
nt8_run_batch_command.json
optimization_phase_lineage.csv/json
optimization_grid_candidates.csv/json
```

The final templates must be run in NinjaTrader as Backtests. Their output goes
to:

```text
deployment_package/final_backtest_handoff/nt8_backtest_results/
```

## Final Backtest Review

Implementation: `src/ta_foundation/optimization/review.py`,
`evaluator.py`, `recommendations.py`, and `result_intake.py`.

When final Backtest results are present, the deployment package builder runs a
full review and writes:

```text
deployment_package/final_backtest_handoff/final_backtest_review/
  REVIEW_SUMMARY.md
  review_summary.json
  review_manifest.json
  result_intake.csv/json
  evaluated_candidates.csv/json
  recommendations.csv/json/md
  settings_contract_violations.csv
```

Final review ingests NinjaTrader Backtest output folders, including Summary,
Settings, and Trades files. This gives it information that raw optimizer grids
do not have, especially percent days traded and recent trade behavior.

Final hard filters:

- total net profit must be positive
- profit factor must meet the configured minimum
- max drawdown must be at or below the configured maximum
- trades must meet the configured minimum
- percent days traded must meet the configured minimum when available

Final review also notes recent trade fading when recent trade delta is
negative.

Final Backtest score is:

```text
profit_score = min(35, net_profit / 500)
pf_score     = min(25, profit_factor * 7)
dd_score     = 20 * (1 - min(drawdown_abs, max_drawdown) / max_drawdown)
trade_score  = min(10, trades / min_trades * 5)
days_score   = min(10, percent_days_traded / 10)
recent_score = clamp(recent_trade_delta / 500, -10, 10)
total_score  = profit_score + pf_score + dd_score + trade_score
             + days_score + recent_score
```

The final recommendations selector again favors diversity:

1. Prefer one candidate per mode/session bucket.
2. Add candidates that avoid exact shape clones.
3. Fill remaining slots with strongest passing candidates.

The final validation status is:

- `valid`: candidates exist, recommendations exist, and settings contract is
  clean
- `settings_warning`: final Backtest results returned forbidden settings such
  as `UseTrend=true` or `UseTrendReverse=true`
- `no_results`: no candidates were ingested
- `no_passing_runs`: candidates were ingested, but no recommendations passed

When status is `valid`, the deployment package decision state becomes
`candidate_ready_for_operator_review`.

## How The Final Result Is Produced

The final result is the combination of:

1. `END_USER_DECISION.md`
2. `DECISION_SUMMARY.md`
3. `manifest.json`
4. final fixed Backtest templates
5. final Backtest validation review
6. recommendation files
7. lineage files showing which optimizer rows produced which later templates

The operator-facing result lives primarily in:

```text
deployment_package/END_USER_DECISION.md
deployment_package/final_backtest_handoff/named_backtest_templates/
deployment_package/final_backtest_handoff/final_backtest_review/
```

The operator then decides whether to deploy, paper-trade, revise, or reject.
The software does not automatically approve live trading.

## Proven Reference Numbers

The canonical session `opt_5bab6a5ee1ea` proved the process end to end:

- phase 1: 4 chunks, about 2,000 optimizer rows parsed
- phase 2: 8 templates, about 4,000 optimizer rows parsed
- phase 3: 8 templates, about 640 optimizer rows parsed after the short-path
  rerun
- final fixed Backtests: 8 templates
- final validation: `valid`
- final pass count: 8/8
- settings contract violations: 0
- top candidate: `F_001`, Pre-Market, `$19,880` net, `PF 5.01`,
  `$1,500` drawdown, 17 trades, 54.84% days traded

## Important Current Limits

- The `/optimizer/sessions/<id>` detail page is not a separate deep detail UI;
  the current web flow is mostly single-page-per-session plus the sessions
  list.
- AddOn cancel can stop between templates, but cannot interrupt a currently
  running Strategy Analyzer optimization.
- Percent days traded is not enforced on raw optimizer grid rows because it is
  not present in `*_Optimization.csv`; it is enforced during final Backtest
  review when trade-level files exist.
- Phase 6 deep refinement is not fully wired. Clone and refine exists, but a
  UI for selecting specific winners or rejected zones to drive narrowed sweeps
  is still open.
- Optional robustness checks exist separately, including bootstrap,
  walk-forward, and parameter-neighborhood validation. They are not part of the
  core phase 1 -> 2 -> 3 -> final decision state unless explicitly run.

## Places To Modify Carefully

Use these code areas when changing the optimizer process:

- Session shape and persisted config:
  `src/ta_foundation/web/optimizer_session.py`
- Combination math and chunking:
  `src/ta_foundation/web/optimizer_plan.py`
- Phase-1 XML generation:
  `src/ta_foundation/web/optimizer_template_writer.py`
- Run command and status polling:
  `src/ta_foundation/web/optimizer_runner.py`
- Raw optimizer result ingestion:
  `src/ta_foundation/web/optimizer_results.py`
- Phase auto-advance and package decision state:
  `src/ta_foundation/web/optimizer_deployment_package.py`
- Phase 2/3/final template generation from optimizer grids:
  `src/ta_foundation/optimization/grid_workflow.py`
- Final Backtest result intake:
  `src/ta_foundation/optimization/result_intake.py`
- Final scoring:
  `src/ta_foundation/optimization/evaluator.py`
- Final recommendation diversity:
  `src/ta_foundation/optimization/recommendations.py`
- Final review summary and settings contract:
  `src/ta_foundation/optimization/review.py`
- NinjaTrader AddOn execution behavior:
  `D:\ninjatraderOptimizer\NinjaTraderAddOnProject\BatchControl.cs`

## Mental Model For Future Changes

Think of the optimizer as a funnel:

```text
User intent
  -> saved session
  -> chunked phase-1 optimizer XMLs
  -> NinjaTrader optimization CSVs
  -> guardrail-filtered phase-1 candidates
  -> phase-2 refinement XMLs
  -> phase-2 optimization CSVs
  -> phase-3 daily-risk XMLs
  -> phase-3 optimization CSVs
  -> fixed final Backtest XMLs
  -> final Backtest CSV exports
  -> validated, diverse recommendations
  -> operator decision package
```

The safest process updates preserve that separation:

- Planning should not execute NinjaTrader.
- XML generation should patch a seed and keep lineage.
- Optimizer rows should drive next optimizer phases, not final deployment.
- Final deployment candidates should come from fixed Backtest validation.
- Recommendations should remain portfolio-aware, not a simple leaderboard.
- Every phase should leave enough artifacts to explain where each final
  template came from.
