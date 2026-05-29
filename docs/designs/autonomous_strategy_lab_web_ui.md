# Autonomous Strategy Lab Web UI

Status: draft design for a new web page that preserves the shipped
`/optimizer` operator flow while adding an autonomous compile/repair/optimize
workflow.

Related docs:

- `docs/designs/autonomous_ninjatrader_strategy_loop.md`
- `docs/designs/ninjatrader_optimizer_web_ui.md`
- `src/ta_foundation/nt_strategy_loop/README.md`
- `docs/handoffs/observe_compile_strategy_analyzer_parity_2026-05-19.md`

## Purpose

Create a new TA Foundation web page for the autonomous NinjaTrader strategy
loop.

The existing `/optimizer` page should remain the manual/operator optimizer
workspace. The new page should be the conductor view for:

1. Strategy spec or source intake.
2. NinjaTrader startup/readiness checks.
3. Strategy authoring or source install.
4. ObserveCompile.
5. Compile repair attempts.
6. Compile-clean promotion.
7. Optimizer session creation.
8. NinjaTrader RunBatch dispatch.
9. Result ingestion and guardrail analysis.
10. Final decision: candidate, archive, incomplete, or halted.

The page should not copy the dense optimizer parameter table. When the loop
creates a child optimizer session, link into the existing optimizer pages for
deep optimizer inspection.

## Recommended Route

Use a distinct product surface:

```text
/strategy-lab
/strategy-lab/sessions
/strategy-lab/sessions/<loop_session_id>
```

Alternative names considered:

- `/autonomous-loop`: accurate, but too implementation-centric.
- `/optimizer/autonomous`: makes the page feel like another optimizer tab, even
  though it also owns compile and repair.

Recommended name in UI: **Strategy Lab**.

Reason: the page is about autonomous strategy development, not only
optimization. It can still show optimizer state as one stage of the larger
loop.

## Relationship To Existing `/optimizer`

Keep the boundary crisp:

| Existing page | New page |
|---|---|
| `/optimizer` | `/strategy-lab` |
| Operator configures a known strategy optimization by hand. | Operator starts or supervises a closed loop. |
| Primary artifact root: `.ta_artifacts/web_optimizer/sessions/<id>/` | Primary artifact root: `.ta_artifacts/nt_strategy_lab/sessions/<id>/` |
| Parameter table, guardrails, chunking, RunBatch, results. | Stage timeline, attempts, policy, gates, child optimizer session, decision. |
| Best for Pantheon and other known Strategy Analyzer sessions. | Best for generated or modified strategies that need compile/repair before optimization. |

When the autonomous loop reaches optimization, it should show:

```text
Child optimizer session: opt_<id>
Open optimizer detail
Open optimizer decision dashboard
```

Those links should point to:

```text
/optimizer/sessions/<opt_id>
/optimizer/sessions/<opt_id>/decision
```

## Page Model

The page should be state-first. The operator should be able to answer:

- What is the loop doing right now?
- Is NinjaTrader ready?
- Which strategy/spec is being tested?
- Which attempt failed, and why?
- Did compile repair repeat the same error signature?
- Did optimization run or stall?
- What decision did the loop reach?
- What human gate is blocking the next action?
- Where are the artifacts?

The page should avoid visible instructional prose once the controls are
self-explanatory. Use labels, state pills, timelines, tables, and tooltips.

## Primary Screens

### 1. Strategy Lab Dashboard

Route:

```text
/strategy-lab
```

This is both the launcher and session list.

Top bar:

- Workbench back link.
- Product title: `Strategy Lab`.
- NinjaTrader worker status pill:
  - `unknown`
  - `starting`
  - `ready`
  - `busy`
  - `stale`
  - `blocked`
- Buttons:
  - `Ensure NT ready`
  - `New loop`
  - `Refresh`

Launcher band:

- Source mode segmented control:
  - `Strategy spec`
  - `Existing .cs`
  - `Smoke test`
- Strategy spec input:
  - file path input
  - paste/edit JSON drawer
  - validate button
- Existing source input:
  - `.cs` path
  - strategy name
- Compile mode:
  - `live`
  - `fixture`
- Policy controls:
  - max repair attempts
  - overwrite existing strategy checkbox
  - LLM repair checkbox
  - model/url inputs shown only when LLM repair is enabled
- Optimizer controls:
  - instrument
  - market suffix
  - max drawdown
  - min trades
  - min profit factor
  - max combinations per chunk
  - keep best rows
  - optimizer timeout
- Primary action:
  - `Start full loop`

Session list:

Columns:

- Strategy
- Decision
- Current stage
- Attempts
- Compile
- Optimizer
- Best row / passing rows
- Updated
- Actions

Actions:

- Open
- Resume optimizer
- Open decision
- Archive/hide

### 2. Loop Session Detail

Route:

```text
/strategy-lab/sessions/<loop_session_id>
```

Header summary:

- Strategy name
- Decision pill:
  - `running`
  - `candidate`
  - `archive`
  - `incomplete`
  - `halted`
- Stage pill:
  - `authoring`
  - `installing`
  - `observing_compile`
  - `repairing`
  - `compile_clean`
  - `generating_seed`
  - `optimizer_running`
  - `ingesting_results`
  - `analyzing`
  - `done`
- Compile mode
- Session id
- Created/updated time

Primary action row:

- `Open folder`
- `Open latest source`
- `Open child optimizer`
- `Open optimizer decision`
- `Rerun optimizer bridge`
- `Stop job` when a background job is active

Main layout:

```text
+--------------------------------------------------------------+
| Status strip: decision, active stage, NT worker, active job   |
+----------------------------+---------------------------------+
| Stage timeline             | Active stage detail             |
|                            |                                 |
| Author                     | Logs/status for selected stage  |
| Install                    | Current command/status JSON     |
| ObserveCompile             | Last error / heartbeat age      |
| Repair                     |                                 |
| Compile clean              |                                 |
| Optimize                   |                                 |
| Analyze                    |                                 |
+----------------------------+---------------------------------+
| Attempts table                                               |
+--------------------------------------------------------------+
| Optimizer child session                                      |
+--------------------------------------------------------------+
| Decision and next action                                     |
+--------------------------------------------------------------+
| Artifacts                                                    |
+--------------------------------------------------------------+
```

#### Stage Timeline

Use a vertical or horizontal stepper. Each stage has:

- state icon:
  - pending
  - running
  - ok
  - warning
  - failed
  - skipped
- start/end time if known
- concise status text

Stages:

1. Author source.
2. Install into NinjaTrader.
3. Observe compile.
4. Repair.
5. Compile-clean.
6. Generate seed template.
7. Create optimizer session.
8. Generate optimizer templates.
9. RunBatch.
10. Ingest results.
11. Analyze guardrails.
12. Decide.

#### Active Stage Detail

Render different details depending on selected/current stage:

Compile stage:

- compile run id
- state
- compiled true/false
- error count
- compile block reason
- last error
- errors table with file, code, line, column, message
- links to `compiler_errors.csv`, `compiler_errors.txt`,
  `compile_status.json`

Repair stage:

- attempt number
- repair source:
  - heuristic
  - LLM
  - none
- repaired files
- repeated signature warning if applicable
- repair prompt link
- repair summary link

Optimizer stage:

- child optimizer session id
- generated template count
- RunBatch state
- CSV path
- row count
- passing row count
- warnings
- links into `/optimizer`

Decision stage:

- decision
- stop reason
- guardrail verdict
- best row summary
- next action markdown preview

#### Attempts Table

Rows come from:

```text
.ta_artifacts/nt_strategy_lab/sessions/<id>/attempts/attempt_NNN/
```

Columns:

- Attempt
- Source file
- Compile state
- Error count
- Stop/repair action
- Signature
- Artifacts

Actions:

- View source
- View compile errors
- View repair prompt
- View repair summary

#### Optimizer Child Session Panel

Data comes from:

```text
manifest.json artifacts.optimizer_session_id
optimizer/optimizer_analysis.json
optimizer/nt_output/
```

Show:

- optimizer session id
- optimizer session directory
- seed template
- generated templates count
- run state
- optimizer CSV
- row count
- passing rows
- decision
- warnings

Buttons:

- `Open optimizer detail`
- `Open optimizer decision`
- `Resume optimizer`
- `Build deployment package` if the child optimizer is far enough along

Do not duplicate `/optimizer/sessions/<id>` optional validation cards here.
This panel should be a launchpad, not a second optimizer detail page.

#### Human Gates Panel

Show gates as explicit blocked/ready rows.

Initial gates:

- NinjaTrader ready.
- AddOn authorized.
- Existing strategy overwrite allowed.
- Peer compile block resolved.
- Compile-clean source available.
- Optimizer seed generated.
- RunBatch terminal.
- Human review required before deployment.

Gate states:

- `ready`
- `blocked`
- `needs_review`
- `not_applicable`

The page may run mechanical stages automatically, but it must not hide risk
gates behind optimistic wording.

#### Artifact Browser

Read from the known session layout:

```text
strategy_spec.json
source_request.md
attempts/
compile_clean/
optimizer/
decisions/STRATEGY_LOOP_SUMMARY.md
decisions/NEXT_ACTION.md
manifest.json
```

Render a compact tree with links/downloads where the existing web app supports
serving local artifacts. A first version can show absolute paths and markdown
previews for summary files.

## State And Data Sources

Existing durable inputs:

- `session.json` / `manifest.json`
- `strategy_spec.json`
- `source_request.md`
- `attempts/attempt_NNN/compile_status.json`
- `attempts/attempt_NNN/repair_prompt.md`
- `attempts/attempt_NNN/repair_summary.md`
- `compile_clean/`
- `optimizer/optimizer_analysis.json`
- `decisions/STRATEGY_LOOP_SUMMARY.md`
- `decisions/NEXT_ACTION.md`

Recommended additions for web friendliness:

```text
.ta_artifacts/nt_strategy_lab/sessions/<id>/
  run.json
  events.jsonl
  stage_state.json
```

`run.json`:

```json
{
  "job_id": "job_...",
  "state": "running",
  "command": "full-loop",
  "started_at": "2026-05-20T16:00:00Z",
  "finished_at": null,
  "exit_code": null
}
```

`events.jsonl`:

```json
{"ts":"2026-05-20T16:00:01Z","stage":"observe_compile","level":"info","message":"ObserveCompile command written."}
{"ts":"2026-05-20T16:00:14Z","stage":"observe_compile","level":"error","message":"CS0103 CrossAboveFast not found."}
```

`stage_state.json`:

```json
{
  "active_stage": "optimizer_running",
  "stages": {
    "observe_compile": {"state": "ok", "summary": "Compile succeeded on attempt 2."},
    "optimizer_running": {"state": "running", "summary": "RunBatch active."}
  }
}
```

The first implementation can infer these states from existing files. The
additional files make polling and UI rendering simpler once the page grows.

## API Design

New routes:

```text
GET  /strategy-lab
GET  /strategy-lab/sessions
GET  /strategy-lab/sessions/<session_id>
```

New API namespace:

```text
GET  /api/strategy-lab/sessions
GET  /api/strategy-lab/sessions/<session_id>
POST /api/strategy-lab/full-loop
POST /api/strategy-lab/repair-loop
POST /api/strategy-lab/sessions/<session_id>/optimizer-bridge
POST /api/strategy-lab/ensure-nt-ready
GET  /api/strategy-lab/jobs/<job_id>
POST /api/strategy-lab/jobs/<job_id>/cancel
```

`POST /api/strategy-lab/full-loop` payload:

```json
{
  "spec_path": "D:\\path\\strategy_spec.json",
  "compile_mode": "live",
  "max_repair_attempts": 5,
  "overwrite": false,
  "repair_llm": false,
  "repair_llm_model": "qwen3-coder:30b",
  "instrument": "NQ 06-26",
  "market_suffix": "NQ",
  "max_drawdown": 2500,
  "min_trades": 10,
  "min_profit_factor": 1.5,
  "max_combinations_per_chunk": 5000,
  "keep_best_results": 500,
  "optimizer_timeout_seconds": 3600
}
```

Response:

```json
{
  "job_id": "job_...",
  "session_id": "loop_20260520_160001_mystrategy",
  "session_url": "/strategy-lab/sessions/loop_20260520_160001_mystrategy"
}
```

Implementation note: the current `full-loop` is a blocking function/CLI. The
web page should dispatch it through the existing web background job pattern
first. Progress can be polled from `jobs.py` plus the session folder. A later
version can call Python functions directly with stage callbacks.

## Visual Design

This should be a quiet operational tool, not a landing page.

Use:

- Dense tables for sessions and attempts.
- Status pills for decisions and gate states.
- A single stage timeline for the loop.
- Compact metric rows for optimizer summary.
- Monospace path text for artifacts.
- Tooltips for risky toggles and worker states.

Avoid:

- A second full optimizer parameter table.
- Marketing-style hero sections.
- Large decorative cards.
- Hiding human gates below the fold.

Color/state mapping should match the existing optimizer pages:

- amber: active/manual action
- green: succeeded/candidate
- blue: running/needs next phase
- orange: warning/incomplete
- red: halted/blocked
- gray: pending/not applicable

## First Implementation Slice

Build the smallest useful page:

1. Add `/strategy-lab` and `/strategy-lab/sessions/<id>` templates.
2. Add a session index reader for `.ta_artifacts/nt_strategy_lab/sessions`.
3. Add a session summary builder that reads existing manifest, attempts,
   compile status, optimizer analysis, and decision markdown.
4. Add `POST /api/strategy-lab/full-loop` that launches the existing CLI or
   Python function through the background job manager.
5. Poll job status and refresh the session summary.
6. Link child optimizer sessions into existing `/optimizer/sessions/<id>` and
   `/optimizer/sessions/<id>/decision`.

Do not add autonomous strategy mutation beyond what `nt_strategy_loop` already
does. The first web page should make the existing loop visible and controllable.

## Later Slices

Slice 2:

- Add `Ensure NT ready` action.
- Add live AddOn heartbeat/status card from `C:\temp\nt8_status.json`.
- Add cancel/stop action for active jobs where safe.

Slice 3:

- Add attempt source viewer and compile error table.
- Add side-by-side attempt diff.
- Add repair prompt/summary previews.

Slice 4:

- Add policy presets:
  - smoke
  - conservative research
  - broad search
  - quick compile only

Slice 5:

- Add controlled strategy modification loop after optimizer analysis.
- Require human approval for major behavior changes.
- Track refinement lineage from parent loop session to child loop session.

## Open Design Questions

1. Should the route be `/strategy-lab` or `/autonomous-loop`?
2. Should the launcher accept pasted JSON specs in v1, or only spec file paths?
3. Should `Ensure NT ready` be a global worker action on this page, or live on
   a separate worker status page?
4. Should loop jobs be run via CLI subprocess first, or direct Python function
   call with callbacks?
5. Should the page support existing `.cs` source in v1, or only `StrategySpec`
   full-loop starts?
6. How aggressive should the LLM repair toggle be by default? Recommended:
   off by default, explicit opt-in.

## Recommended Default

For the first build, make `/strategy-lab` a supervisor over the existing
autonomous loop:

- Start from a `StrategySpec` JSON file.
- Live compile mode by default.
- LLM repair off by default.
- Max repair attempts: 5.
- Guardrails: drawdown 2500, min trades 10, min PF 1.5.
- Full loop launched as a background job.
- Session detail reads artifacts and links into the child optimizer session.

This gives the operator a true autonomous workflow without disturbing the
proven `/optimizer` page.
