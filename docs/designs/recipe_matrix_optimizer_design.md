# Recipe Matrix Optimizer Design

Status: proposed design, created 2026-05-21.

Related inputs:

- `docs/samples/OptimizationIdea2.txt`
- `docs/samples/Recipe Optimizer.pdf`
- `docs/designs/ninjatrader_optimizer_web_ui.md`
- `docs/designs/optimizer_process_explained.md`

## Executive Advice

Build this as a higher-level recipe orchestration layer above the shipped web
optimizer, not as a replacement optimizer and not as a separate storage system.

The strongest part of the idea is the parameter role model:

- matrix axes split the search into separate NinjaTrader templates
- fixed values are pinned in every template
- optimized parameters sweep inside each generated template
- child stages inherit selected parent values
- refinement stages create narrow ranges around parent winners
- final validation uses fixed Backtest templates

That model fits the current system well because TA Foundation already owns
planning, template generation, NinjaTrader RunBatch handoff, result ingestion,
candidate selection, phase advancement, and deployment packages. NinjaTrader
should continue to be only the execution worker.

The main design correction is that the recipe should be autonomous by default.
The user should configure the recipe, selection rules, safety caps, and final
candidate target, then click Start Recipe once. Manual controls should exist for
inspection, pause, rerun, branch, and override, but they should not be required
between stages.

The least invasive implementation path is:

1. Add recipe files beside the current `session.json` and `plan.json`.
2. Add new recipe planner/template/result modules that reuse existing helpers.
3. Add a resumable `RecipeRunOrchestrator` that wraps the current runner.
4. Keep `OptimizerSessionDocument` backward-compatible by adding optional mode
   metadata only after the recipe files are stable.
5. Feed the final fixed Backtest stage into the existing deployment package and
   final review code.

Do not start with a large visual editor. Start with JSON import/edit plus a
good preview and run dashboard. The mockup PDF is useful as product direction,
but the engine and saved artifacts matter first.

## Product Shape

The Recipe Matrix Optimizer is an optional mode under `/optimizer`.

Current modes:

- Standard optimizer: the shipped flow remains unchanged.
- Recipe/Matrix optimizer: a stage sequence that generates, runs, ingests,
  selects, refines, and validates automatically.

Core promise:

```text
recipe.json
  -> recipe_plan.json
  -> stage 1 matrix templates
  -> NinjaTrader optimizer results
  -> automatic selection
  -> child stage templates
  -> repeated optimizer stages
  -> final fixed Backtest templates
  -> final validation review
  -> deployment package
```

## Fit With Existing Optimizer

The current optimizer already has most of the primitives this design needs.

| Need | Existing Fit | Extension |
|---|---|---|
| Durable sessions | `optimizer_session.py` stores `.ta_artifacts/web_optimizer/sessions/<id>/` | Add recipe files inside the same folder |
| Standard plan hash | `optimizer_plan.py` creates stable chunk plans | Add recipe-level hash over recipe schema, matrix jobs, and stages |
| XML generation | `optimizer_template_writer.py` patches seed XML safely | Add stage-aware writer that can patch matrix axes, inherited values, and refined ranges |
| NinjaTrader execution | `optimizer_runner.py` writes `RunBatch` command and tracks status | Allow source/dest folders per stage, or wrap with stage-specific generated/output paths |
| Result ingestion | `optimizer_results.py` parses `*_Optimization.csv` recursively | Add bucket metadata, candidate IDs, stage IDs, and selection outputs |
| Phase advancement | `optimizer_deployment_package.py` and `grid_workflow.py` generate phase 2/3/final artifacts | Keep as final-package path, but recipe should own stage-to-stage orchestration |
| Final review | `optimization.review`, `evaluator`, `recommendations` | Reuse for final fixed Backtest validation |

The recipe mode should not duplicate the AddOn or invent a different
NinjaTrader command format unless the current stage-specific source/dest path
needs one small additive field.

## Storage Layout

All recipe artifacts stay inside the current web optimizer session folder.

Recommended layout:

```text
.ta_artifacts/web_optimizer/sessions/<session_id>/
  session.json
  plan.json
  recipe.json
  recipe_plan.json
  recipe_run.json
  recipe_state.json
  recipe_events.jsonl
  recipe_lineage.csv
  recipe_lineage.json
  recipe_selection.csv
  recipe_selection.json
  generated_templates/
    stage_1/
    stage_2/
    stage_3/
    final_backtest/
  nt_output/
    stage_1/
    stage_2/
    stage_3/
    final_backtest/
  parsed_results/
    stage_1/
    stage_2/
    stage_3/
  deployment_package/
```

Notes:

- Keep `session.json` readable by old code.
- Keep `plan.json` available for standard optimizer sessions.
- Store recipe-specific planning in `recipe_plan.json`, not by overloading the
  old chunk shape.
- Use stage folders so the AddOn can still run a normal folder of XML files.
- Keep final package generation under `deployment_package/`.

## Session Compatibility

The standard optimizer should continue to work without any changes to user
behavior.

Recommended session additions after MVP planner is stable:

```json
{
  "optimizer_mode": "standard",
  "recipe_config_path": "",
  "recipe_state_path": ""
}
```

Rules:

- Missing `optimizer_mode` means `standard`.
- Old sessions with `schema_version: 1` remain loadable.
- Recipe files are optional. A session can be standard with no recipe files.
- Do not require recipe fields inside `ParameterConfig`; keep role details in
  `recipe.json`.

## Recipe Schema

The recipe schema should be strategy-generic. It should use parameter names
from the selected seed XML and strategy catalog, but it should not hardcode
Pantheon-specific parameters in the engine.

Minimal schema:

```json
{
  "recipe_version": 1,
  "mode": "matrix_sequence",
  "recipe_id": "rec_8c2a44",
  "recipe_name": "Pantheon NQ - Time x Reverse x Slow MA",
  "strategy_id": "PantheonMasterBotV01TesterV2",
  "target_final_candidates": 4,
  "safety_caps": {
    "max_total_combinations": 250000,
    "max_total_runtime_minutes": 720,
    "max_templates_per_stage": 250,
    "pause_on_boundary_winners": false
  },
  "base_matrix": [
    {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4, 8, 12, 16, 20]},
    {"param": "DurationTimeH", "role": "fixed", "value": 4},
    {"param": "Reverse", "role": "matrix_axis", "values": [false, true]}
  ],
  "stages": []
}
```

Stage schema:

```json
{
  "stage_id": "stage_1",
  "stage_type": "optimizer",
  "description": "Broad bucket search",
  "from": null,
  "pin": ["StartTimeH", "DurationTimeH", "Reverse"],
  "optimize_inside_template": {
    "averageSlow": {"min": 50, "max": 400, "step": 50},
    "averageFast": {"min": 2, "max": 50, "step": 2},
    "MaxStop": {"min": 50, "max": 350, "step": 50},
    "MaxTPRatio": {"min": 0.5, "max": 2.0, "step": 0.5}
  },
  "selection": {
    "group_by": ["StartTimeH", "Reverse"],
    "keep_per_group": 3,
    "target_total_candidates": 12,
    "hard_filters": {
      "min_trades": 20,
      "min_profit_factor": 1.3,
      "max_drawdown": 2500,
      "min_net_profit": 0
    },
    "rank_by": "portfolio_score",
    "tie_breakers": ["lower_drawdown", "higher_trade_count", "higher_net_profit"]
  }
}
```

Child stage schema:

```json
{
  "stage_id": "stage_2",
  "stage_type": "optimizer",
  "description": "Refine around selected parent rows",
  "from": "stage_1.selected_rows",
  "pin": ["StartTimeH", "DurationTimeH", "Reverse"],
  "refine_around_parent_result": {
    "averageSlow": {"source": "parent", "radius": 50, "step": 25},
    "averageFast": {"source": "parent", "radius": 5, "step": 1},
    "MaxStop": {"source": "parent", "radius": 50, "step": 25},
    "MaxTPRatio": {"source": "parent", "radius": 0.5, "step": 0.25}
  },
  "add_optimize": {
    "Long": [true, false],
    "Short": [true, false]
  },
  "selection": {
    "group_by": ["parent_candidate_id"],
    "keep_per_group": 1,
    "rank_by": "portfolio_score"
  }
}
```

Final stage schema:

```json
{
  "stage_id": "final_backtest",
  "stage_type": "fixed_backtest",
  "from": "stage_3.selected_rows",
  "finalists_per_bucket": 2,
  "description": "Generate final fixed Backtest templates and validate through the existing final review flow"
}
```

Final selection is anchored to the original Stage 1 matrix buckets. If Stage 1
creates 12 time/reverse buckets and `finalists_per_bucket` is 2, the expected
final handoff is 24 fixed Backtest templates. Later refinement stages do not
replace Stage 1 winners automatically; every selected row from Stage 1 through
the final parent stage competes inside its original Stage 1 bucket. This means a
Stage 1 template can still be promoted if its score remains better than the
refined descendants for that same bucket.

The final manifest must also explain missing buckets. A bucket may produce fewer
than 2 finalists, or zero finalists, if guardrails filtered its candidates or no
candidate was selected for that original bucket. The UI should show this as a
bucket coverage board rather than hiding the absence.

## Parameter Roles

Parameter roles should live in recipe/stage config rather than replacing the
current `fixed` and `optimize` modes in standard sessions.

Supported roles:

| Role | Meaning |
|---|---|
| `fixed` | Same value in every generated template |
| `matrix_axis` | Expands into separate template jobs |
| `optimize_inside_template` | Swept by NinjaTrader inside each XML |
| `inherited_from_parent_result` | Child stage receives selected parent value |
| `refine_around_parent_result` | Child stage sweeps around selected parent value |
| `validation_only` | Applied only to final fixed Backtest templates |

The UI can show Pantheon examples, but the engine should validate every role
against the selected seed template/catalog.

## Planning

Add a recipe planner module:

```text
src/ta_foundation/web/optimizer_recipe_plan.py
```

Responsibilities:

- Load `recipe.json` and the current session document.
- Validate parameter names against the strategy parameter catalog.
- Expand matrix axes into stable stage-1 jobs.
- Estimate combinations per template and per stage.
- Apply safety caps before any XML is written.
- Produce stable template IDs, bucket IDs, parent IDs, and plan hash.
- Save `recipe_plan.json`.

Example 12-template expansion:

```text
StartTimeH: 0, 4, 8, 12, 16, 20
DurationTimeH: 4
Reverse: false, true

6 x 2 = 12 templates
```

Example 96-template expansion:

```text
StartTimeH: 0, 4, 8, 12, 16, 20
Reverse: false, true
averageSlow: 50, 100, 150, 200, 250, 300, 350, 400

6 x 2 x 8 = 96 templates
```

Stable IDs:

```text
stage_1__start00__dur04__reverse_false
stage_1__start00__dur04__reverse_true
stage_1__start04__dur04__reverse_false
stage_1__start04__dur04__reverse_true
stage_1__start00__dur04__reverse_false__slow050
```

Plan preview should show:

- matrix template count
- combinations per template
- total stage combinations
- total recipe combinations
- estimated runtime
- safety-cap warnings
- boundary behavior policy
- expected stage sequence

## XML Generation

Add a stage-aware recipe writer:

```text
src/ta_foundation/web/optimizer_recipe_templates.py
```

It should reuse or extract helpers from `optimizer_template_writer.py` rather
than reimplementing XML patching.

For each stage job:

1. Read the seed template text.
2. Patch full contract from session or seed.
3. Patch `Category` to `Optimize` for optimizer stages.
4. Preserve `OptimizerType`, `OptimizationFitness`, date range, bars period,
   and contract settings unless explicitly overridden.
5. Patch matrix-axis values into the strategy node.
6. Patch fixed values into the strategy node and clamp optimization parameter
   blocks.
7. Patch `optimize_inside_template`, `add_optimize`, or refined ranges into
   `<OptimizationParameters>`.
8. Write the XML under `generated_templates/<stage_id>/`.
9. Write a sidecar manifest mapping template ID to bucket ID and parent
   candidate ID.

The writer must preserve the full contract, for example `NQ 06-26`, never
silently fall back to generic `NQ`.

For final fixed Backtests, prefer the existing final template generation path
where possible. Recipe mode should contribute selected rows and lineage, then
let the current final validation/review code do the review.

## Autonomous Orchestration

Add:

```text
src/ta_foundation/web/optimizer_recipe_orchestrator.py
```

Primary class:

```text
RecipeRunOrchestrator
```

Responsibilities:

- Load `session.json`, `recipe.json`, `recipe_plan.json`, and
  `recipe_state.json`.
- Determine current durable state from files, not memory.
- Generate current-stage templates when needed.
- Submit current stage to NinjaTrader using the existing AddOn workflow.
- Monitor `nt8_status.json`, stage output folders, and batch summaries.
- Detect completed or failed templates.
- Ingest stage results with the existing parser.
- Attach bucket and parent metadata to result rows.
- Apply selection rules automatically.
- Save selected and rejected rows with scoring details.
- Generate child-stage templates from selected parents.
- Continue until final validation, completion, pause, stop, or failure.

The orchestrator should write every transition to `recipe_events.jsonl`.

State machine:

```text
draft
planned
ready_to_generate_stage
generating_stage_templates
ready_to_run_stage
running_stage
waiting_for_results
ingesting_results
selecting_candidates
generating_child_stage
ready_for_final_backtest
running_final_backtest
reviewing_final_backtest
complete
paused
failed
stopped
```

Pause and stop behavior:

- The AddOn may not be able to interrupt the active Strategy Analyzer job.
- Pause should mean "pause after current template" or "pause after current
  stage", depending on user selection.
- Stop should prevent further template dispatch and persist state for resume or
  clone.

## Runner Integration

The current `optimizer_runner.start_run()` assumes:

```text
generated_templates/
nt_output/
run.json
```

Recipe mode needs stage-specific equivalents:

```text
generated_templates/stage_1/
nt_output/stage_1/
recipe_run.json
```

Two implementation choices:

1. Add optional `source_folder`, `dest_folder`, and `run_filename` arguments to
   `optimizer_runner.start_run()`, `get_status()`, and `cancel_run()`.
2. Add a thin recipe runner wrapper that writes the same command shape but
   persists recipe run records separately.

Recommendation: start with option 2 for lower risk. If duplication grows, then
extract a generic batch runner.

## Result Grouping And Selection

Add:

```text
src/ta_foundation/web/optimizer_recipe_results.py
src/ta_foundation/web/optimizer_recipe_selection.py
```

Result enrichment:

- Parse stage `nt_output/<stage_id>/` recursively.
- Join each optimizer CSV back to a template manifest.
- Add `recipe_id`, `stage_id`, `template_id`, `bucket_id`, and
  `parent_candidate_id`.
- Normalize result row IDs so every row has a stable candidate ID.

Selection:

- Apply hard filters first.
- Group by configured fields such as `StartTimeH`, `Reverse`, and
  `averageSlow`.
- Rank by `profit_factor`, `total_net_profit`, drawdown-adjusted score, or
  `portfolio_score`.
- Apply tie breakers in order.
- Apply diversity rules when requested.
- Save selected rows and rejected rows.

Selection outputs:

```text
recipe_selection.csv
recipe_selection.json
parsed_results/<stage_id>/selected.csv
parsed_results/<stage_id>/rejected.csv
parsed_results/<stage_id>/scored_rows.csv
```

Ranking should reuse the existing evaluator/scoring ideas where possible, but
it needs to remain configurable because recipe stages may group by different
fields.

## Boundary Detection

Boundary detection is essential. If a selected row wins at the edge of a range,
that is evidence that the search may be truncated.

Detect:

- winner value equals previous sweep min
- winner value equals previous sweep max
- winner value equals first or last matrix-axis value, when the axis is ordered

Persist flags:

```json
{
  "boundary_flags": [
    {
      "param": "averageSlow",
      "value": 400,
      "boundary": "upper",
      "previous_range": {"min": 50, "max": 400, "step": 50},
      "recommendation": "expand_upper"
    }
  ]
}
```

Default behavior:

- Flag boundary winners in lineage and UI.
- If the recipe stage says `auto_expand_boundary: true`, widen child ranges by
  the configured percentage.
- Never silently assume the boundary value is optimal.

## Lineage

Every final candidate must be explainable.

Required lineage fields:

```text
recipe_id
stage_id
template_id
bucket_id
initial_bucket_key
initial_bucket_values
parent_candidate_id
optimizer_csv_file
optimizer_row_id
candidate_id
selected_status
selection_reason
rejection_reason
generated_child_template_id
final_backtest_template_id
boundary_flags
```

Write both CSV and JSON:

```text
recipe_lineage.csv
recipe_lineage.json
```

JSON should keep nested details for UI inspection. CSV should be flat enough to
open in a spreadsheet.

## UI Design

Use the PDF mockups as product direction, but ship the engine in smaller steps.

MVP UI:

- Mode switch: Standard optimizer / Recipe optimizer.
- Recipe JSON editor/import.
- Plan preview:
  - stage count
  - template count
  - combination estimate
  - safety warnings
  - generated artifact paths
- Start Recipe.
- Run dashboard:
  - current state
  - current stage
  - current template
  - completed templates
  - rows parsed
  - selected candidates
  - latest event log entries
  - pause, stop, resume controls
- Stage results:
  - original Stage 1 bucket coverage
  - best PF
  - best net
  - best score
  - selected and rejected rows
  - final template count per bucket
  - reason when a bucket has fewer finalists than requested
- Lineage inspector:
  - parent row
  - child template
  - child result
  - final Backtest template/result

Recommended final-results flow:

1. Show Candidate Results as a Stage 1 bucket board first.
2. Let the user inspect the flat candidate table inside a bucket.
3. Auto-select up to `finalists_per_bucket` rows per original bucket across
   Stage 1 and all refinement stages.
4. Show a Final Template Coverage board with expected, produced, and missing
   counts by bucket.
5. Provide one obvious path to rename final templates, run the final Backtest
   package, and open the final review/recommendations.

Later visual editor:

- Matrix-axis editor.
- Parameter role table.
- Stage sequence editor.
- Selection-rule builder.
- Branch/clone controls.

## API Routes

Add recipe-specific routes beside existing `/api/optimizer/sessions/<id>/...`
routes.

Recommended routes:

```text
GET    /api/optimizer/sessions/<id>/recipe
PUT    /api/optimizer/sessions/<id>/recipe
POST   /api/optimizer/sessions/<id>/recipe/plan
POST   /api/optimizer/sessions/<id>/recipe/start
POST   /api/optimizer/sessions/<id>/recipe/pause
POST   /api/optimizer/sessions/<id>/recipe/resume
POST   /api/optimizer/sessions/<id>/recipe/stop
GET    /api/optimizer/sessions/<id>/recipe/status
GET    /api/optimizer/sessions/<id>/recipe/events
GET    /api/optimizer/sessions/<id>/recipe/stages/<stage_id>/results
POST   /api/optimizer/sessions/<id>/recipe/stages/<stage_id>/rerun
POST   /api/optimizer/sessions/<id>/recipe/templates/<template_id>/rerun
POST   /api/optimizer/sessions/<id>/recipe/candidates/<candidate_id>/promote
POST   /api/optimizer/sessions/<id>/recipe/candidates/<candidate_id>/reject
```

Long-running orchestration should use the existing web job pattern if it fits,
but state must still be durable in recipe files so app restarts can resume.

## Failure Handling

Failures should stop or pause the autonomous loop safely and write both state
and event records.

Failure conditions:

- NinjaTrader AddOn not responding.
- Heartbeat stale beyond threshold.
- Expected output files missing.
- XML generation fails.
- Result parsing fails.
- No candidates pass guardrails.
- Template count exceeds cap.
- Combination estimate exceeds cap.
- Runtime exceeds cap.
- Contract mismatch detected.
- Seed template missing required values.
- Final Backtest dates missing when required.

Each failure event should include:

```json
{
  "event_type": "failure",
  "stage_id": "stage_1",
  "template_id": "stage_1__start08__reverse_false",
  "code": "no_candidates_passed_guardrails",
  "message": "No candidates passed min_profit_factor=1.3 and max_drawdown=2500.",
  "recoverable": true
}
```

Resume should work after the user fixes the problem or adjusts the recipe.

## Implementation Plan

Phase A - Design and schema:

- Add this design doc.
- Add JSON schema/dataclasses for recipe, stages, roles, selection, safety caps.
- Add recipe save/load helpers.
- Add tests for backward-compatible standard session loading.

Phase B - Planner:

- Implement matrix expansion.
- Implement combination estimates.
- Implement stable bucket/template IDs.
- Implement recipe plan hash.
- Save `recipe_plan.json`.
- Test 12-template and 96-template examples.

Phase C - Stage template generation:

- Extract reusable XML patch helpers from `optimizer_template_writer.py` if
  needed.
- Generate `generated_templates/<stage_id>/*.xml`.
- Write template manifest with bucket and parent metadata.
- Test matrix-axis fixed values and optimized ranges.

Phase D - Stage result ingestion and selection:

- Parse `nt_output/<stage_id>/`.
- Join results to template manifest.
- Apply hard filters and ranking.
- Save selected/rejected rows.
- Detect boundary winners.
- Test top-N per bucket and boundary flags.

Phase E - Orchestrator:

- Implement durable state machine.
- Submit stage folders through existing RunBatch path.
- Monitor stage completion.
- Auto-ingest and auto-select.
- Generate next stage.
- Support pause/resume/stop between templates or stages.

Phase F - Final Backtest and deployment package:

- Convert final selected rows into existing final fixed Backtest flow.
- Preserve lineage into deployment package manifest.
- Reuse existing final review/recommendation generation.

Phase G - UI:

- Add Recipe mode shell.
- Add JSON editor/import and plan preview.
- Add run dashboard and event log.
- Add stage bucket view.
- Add lineage inspector.

## Test Plan

Required tests:

- Standard optimizer sessions still load with no recipe files.
- Recipe session save/load round-trips.
- Matrix expansion count:
  - 6 start hours x 2 reverse states = 12
  - 6 start hours x 2 reverse states x 8 slow MA values = 96
- Stable template IDs for the same recipe.
- Stable recipe plan hash.
- Combination estimates per template and total.
- Safety caps block oversized recipes.
- XML patches matrix-axis values into strategy node.
- XML patches fixed values and clamps parameter blocks.
- XML patches optimized ranges into `<OptimizationParameters>`.
- Stage output folders are used instead of overwriting standard
  `generated_templates/*.xml`.
- Result ingestion attaches bucket and parent metadata.
- Selection keeps top N per bucket.
- Tie breakers apply in configured order.
- Boundary winners are flagged.
- Child-stage generation uses parent values.
- Refined ranges clamp to known parameter bounds when available.
- Final fixed Backtest templates preserve contract and validation settings.
- Pause/resume reconstructs state from files.
- Existing standard optimizer behavior remains unchanged.

## Acceptance Criteria

The design is successfully implemented when:

- The current Standard optimizer still works as-is.
- A user can configure a 12-template time/reverse recipe.
- A user can configure a 96-template time/reverse/slow-MA recipe.
- The user can click Start Recipe once.
- The system generates stage-1 templates automatically.
- The system runs templates through the existing NinjaTrader AddOn.
- The system detects completion and ingests results.
- The system selects candidates per configured bucket rules.
- The system generates child-stage templates automatically.
- The system continues through configured stages.
- Pause/resume works between templates or stages.
- Every artifact stays under the current optimizer session folder.
- Every final candidate traces back to recipe, stage, bucket, template,
  optimizer row, child template, and final Backtest template.
- The existing deployment package/final review flow can produce
  operator-facing validation artifacts.

## Open Questions

1. Should recipe sessions use the existing `opt_` prefix or a visible `rec_`
   prefix while still living under `web_optimizer/sessions/`?
2. Should stage-specific AddOn runs share `run.json` history or use only
   `recipe_run.json`?
3. Should the first MVP allow only one active recipe run at a time because the
   AddOn has one command/status file?
4. Should automatic boundary expansion be enabled by default, or only flag and
   continue?
5. Which scoring formula should be the default `portfolio_score` for optimizer
   stages: current quick UI score, `grid_workflow` score, or a new named
   recipe score?
6. How should recipe-level prior-run reuse work when only some stage templates
   match a previous session?
7. Should final fixed Backtest execution be fully autonomous in the first
   implementation, or should it stop at generated final templates until the
   final Backtest seed/OOS settings are confirmed?
