# Autonomous NinjaTrader Strategy Loop

Status: implementation design for removing the current human babysitting from
NinjaTrader strategy creation, compile repair, optimization, and refinement.

Last reviewed: 2026-05-19.

Related projects:

- `D:\Backup\projects\PythonProject\ta_foundation`
- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper`
- `D:\ninjatraderOptimizer`

Related docs:

- `docs/designs/ninjatrader_optimizer_web_ui.md`
- `docs/designs/optimizer_process_explained.md`
- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\docs\COMPILE_LOOP.md`
- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\README.md`
- `D:\ninjatraderOptimizer\PROJECT_STATUS.md`

## NinjaTrader Startup Runbook

These startup details are part of the automation contract. Future agents should
not rediscover them manually.

1. NinjaTrader may stop at the `Welcome` login window after restart.
2. The login password is stored locally at:

```text
C:\Users\Owner\Downloads\P.txt
```

3. Treat that file as secret material. Read it only in memory, never echo it to
   chat, logs, command output, generated docs, or artifacts.
4. The username currently observed in the NinjaTrader trace is `eirwin`.
5. After login, a NinjaTrader authorization prompt appears quickly for rebuilt
   AddOn DLLs. The window may be titled `Update to Server Definitions?`, but
   the message says NinjaTrader detected new add-on(s), such as
   `BatchStrategyOptimizerAddOn`, and asks whether to authorize them.
6. Click `Yes` on that prompt. The UI Automation id observed for the button is
   `NTMessageBoxYesButton`; the window Automation id is `NTMessageBox`.
7. The prompt can appear on another monitor. Automation should enumerate all
   NinjaTrader-owned top-level windows, not only the main window.
8. After clicking `Yes`, wait 1 to 2 minutes for the Control Center and AddOn
   startup to finish before sending compile or optimizer IPC commands.
9. A ready session usually shows `Control Center - Accounts` or another Control
   Center title, and trace logs should include the third-party AddOn DLL load.

This runbook should be used by deploy/restart helpers before attempting
`ObserveCompile` or `RunBatch`.

## Purpose

Build a full closed-loop system where AI can:

1. Create or modify a NinjaTrader strategy.
2. Install the strategy into the NinjaTrader strategy directory.
3. Let NinjaTrader auto-compile the dropped strategy file.
4. Read compile errors automatically.
5. Repair and reinstall until compile-clean or stopped by policy.
6. Generate optimizer/backtest templates for the compiled strategy.
7. Run NinjaTrader Strategy Analyzer through the existing AddOn.
8. Ingest and analyze optimizer/backtest results.
9. Modify the strategy or optimization plan and continue.

The core goal is to stop requiring the operator to manually copy compiler
errors, manually run every Strategy Analyzer job, or manually decide every next
mechanical step.

Human review remains required for final deployment, live trading, and risky
strategy behavior changes.

## Current State

### What Already Exists

`NinjatraderDocScrapper` already has the strategy-authoring half:

- local NinjaTrader docs/RAG flow
- strategy factory JSON specs
- deterministic NinjaScript generation for known strategy families
- generated NinjaScript strategy files
- generated Strategy Analyzer template XML files
- install helper that copies `.cs` files into:

```text
C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\Strategies
```

- compile-loop folder contract under:

```text
C:\ta_foundation\nt_compile_loop
```

- compiler error parsing/normalization for CSV and text exports
- repair prompts that accept existing code plus compiler errors

`ta_foundation` already has the optimizer control plane:

- durable optimizer sessions
- strategy/template scanning
- parameter metadata and seed-template loading
- plan preview and chunking
- generated optimizer XMLs
- NinjaTrader RunBatch IPC
- status polling
- optimization CSV ingestion
- phase 1 -> phase 2 -> phase 3 -> final Backtest package generation
- final Backtest review and recommendations
- optional robustness checks

`D:\ninjatraderOptimizer` already has the NinjaTrader execution worker:

- `BatchControl.cs` watches `C:\temp\nt8_command.json`
- `RunBatch` loads XML templates into Strategy Analyzer
- AddOn exports Strategy Analyzer results
- AddOn writes status to `C:\temp\nt8_status.json`

### What Is Missing

The missing part is an automated NinjaTrader compile-result observer.

Today the compile loop is documented as semi-automated:

1. Python installs the `.cs` file.
2. NinjaTrader compiles internally and automatically when it sees the changed
   strategy file.
3. The operator exports/copies compiler errors into the compile-loop folder.
4. Python normalizes the errors and the AI repairs.

The desired loop requires step 3 to be automatic.

Important correction: NinjaTrader strategy source should not be treated like
the AddOn/optimizer DLL projects. The AddOn and optimizer projects can be
compiled with MSBuild outside NinjaTrader, then deployed. Normal NinjaScript
strategies are ultimately compiled and validated by NinjaTrader itself. Any
outside compile is only a fast preflight for obvious C# syntax/reference
problems, not the final authority.

## Target Architecture

Use the same control-plane pattern already proven by the optimizer:

```text
TA Foundation / Strategy Factory
  owns intent, memory, repair policy, optimization policy, artifacts

NinjaTrader AddOn
  owns compile, Strategy Analyzer execution, raw NT errors/results

AI agent
  owns code/spec repair and strategy refinement inside policy limits
```

The central rule is:

```text
NinjaTrader is the worker. TA Foundation is the memory and conductor.
```

## Session Folder Contract

Autonomous strategy-loop sessions should live under:

```text
.ta_artifacts/nt_strategy_lab/sessions/<session_id>/
  session.json
  strategy_spec.json
  source_request.md
  attempts/
    attempt_001/
      Strategy.cs
      StrategyTemplate.xml
      static_review.md
      install_manifest.json
      compile_command.json
      compile_status.json
      compiler_errors.csv
      compiler_errors.json
      repair_prompt.md
      repair_summary.md
    attempt_002/
      ...
  compile_clean/
    Strategy.cs
    StrategyTemplate.xml
    compile_status.json
  optimizer/
    optimizer_session_id.txt
    seed_templates/
    generated_templates/
    nt_output/
    deployment_package/
  decisions/
    STRATEGY_LOOP_SUMMARY.md
    NEXT_ACTION.md
  manifest.json
```

The folder should be append-only by default. Each repair attempt gets its own
folder so bad loops can be debugged later.

## NinjaTrader IPC Contract

The existing AddOn currently watches:

```text
C:\temp\nt8_command.json
```

and writes:

```text
C:\temp\nt8_status.json
```

The new compile-result observer should extend that same IPC channel rather
than creating a parallel watcher. It does not need to force compilation in the
normal case. The Python side installs the `.cs` file; NinjaTrader auto-compiles;
the AddOn is asked to inspect/export the latest compile result.

### Observe Compile Command

```json
{
  "action": "ObserveCompile",
  "runId": "compile_20260519_001",
  "sourceFile": "C:\\Users\\Owner\\Documents\\NinjaTrader 8\\bin\\Custom\\Strategies\\MyStrategy.cs",
  "strategyName": "MyStrategy",
  "outputDir": "C:\\ta_foundation\\nt_compile_loop\\compiler_errors",
  "installedSha256": "<sha256 written by Python installer>",
  "waitForQuietSeconds": 3,
  "timeoutSeconds": 120,
  "exportErrors": true
}
```

Required fields:

- `action`
- `runId`
- `sourceFile`
- `outputDir`

Optional fields:

- `strategyName`
- `installedSha256`
- `waitForQuietSeconds`
- `timeoutSeconds`
- `exportErrors`

### Observe Compile Status

The same status file can be extended with `workerKind`:

```json
{
  "runId": "compile_20260519_001",
  "workerKind": "compile_observer",
  "state": "failed",
  "strategyName": "MyStrategy",
  "sourceFile": "C:\\Users\\Owner\\Documents\\NinjaTrader 8\\bin\\Custom\\Strategies\\MyStrategy.cs",
  "compiled": false,
  "errorCount": 3,
  "errorsCsv": "C:\\ta_foundation\\nt_compile_loop\\compiler_errors\\compile_20260519_001_errors.csv",
  "errorsText": "C:\\ta_foundation\\nt_compile_loop\\compiler_errors\\compile_20260519_001_errors.txt",
  "lastError": "CS0103: The name 'CrossAboveFast' does not exist in the current context",
  "heartbeatUtc": "2026-05-19T18:20:00.0000000Z"
}
```

States:

- `starting`
- `waiting_for_auto_compile`
- `observing`
- `succeeded`
- `failed`
- `timed_out`
- `worker_error`

### Compile Result Files

When possible, the AddOn should write:

```text
<outputDir>/<runId>_errors.csv
<outputDir>/<runId>_errors.txt
<outputDir>/<runId>_compile_result.json
```

CSV columns should match the existing parser expectations in
`NinjatraderDocScrapper.strategy_factory.compile_loop.errors`:

```text
NinjaScript File,Error,Code,Line,Column
```

If exact NinjaTrader compiler grid export is not available, the AddOn should
write best-effort rows from any accessible log, output window, or reflected
compiler error collection. The Python side already accepts both CSV and text.

## Compile Worker Implementation Options

NinjaTrader compile-result observation is the main technical uncertainty.

Preferred path:

1. Add an `ObserveCompile` branch to `BatchControl.OnCommandFileChanged`.
2. Python installs the strategy file and records its hash.
3. The AddOn waits for NinjaTrader's auto-compile cycle to settle.
4. Extract compiler errors from the NinjaScript Editor/compiler service.
4. Write result files and status JSON.

Fallback path:

1. Install `.cs` file into the strategy folder.
2. Wait for file-system quiet time plus NinjaTrader trace/log changes.
3. Read errors from known NinjaTrader trace/log/output locations.
4. Normalize to the same CSV/text contract.

Last-resort path:

1. Detect file changes and wait for NinjaTrader auto-compile.
2. Watch trace/log files for compile errors.
3. Treat absence of new errors plus discoverable strategy metadata as
   provisional compile success.

The preferred path is worth investigating first, but the fallback contract
should be supported because NinjaTrader internals are version-sensitive.

### Optional External Preflight Compile

An outside compile may still be useful before copying into the live NinjaTrader
strategy folder, but it must be treated as a preflight only.

Possible use:

1. Generate `Strategy.cs`.
2. Run a lightweight external C# syntax/reference check against available
   NinjaTrader assemblies.
3. If it fails on obvious C# syntax errors, repair before touching the NT
   strategy folder.
4. If it passes, install into NT and let NT auto-compile.
5. Use NT's result as the source of truth.

This can reduce noisy NT iterations, but it cannot replace NinjaTrader's own
compile because NinjaScript generation, partial classes, generated wrappers,
attributes, resources, and platform-specific checks happen inside NT.

## Python Orchestrator

Add a new Python package under `ta_foundation`, for example:

```text
src/ta_foundation/nt_strategy_loop/
  __init__.py
  session.py
  authoring.py
  installer.py
  compile_worker.py
  repair.py
  optimizer_bridge.py
  analyzer.py
  policy.py
  cli.py
```

### Responsibilities

`session.py`

- create/load strategy-loop sessions
- write attempt folders
- maintain `manifest.json`
- keep all paths durable

`authoring.py`

- call or wrap `NinjatraderDocScrapper` strategy factory
- generate `.cs` and initial template XML
- record the original strategy intent/spec

`installer.py`

- install `.cs` into NinjaTrader strategy folder
- preserve previous target file by hash/backups
- write install manifests

`compile_worker.py`

- write `ObserveCompile` IPC command after installing a strategy file
- poll status
- ingest result files
- normalize compiler errors

`repair.py`

- build repair prompts from:
  - strategy spec
  - current code
  - normalized compiler errors
  - previous failed attempts
  - relevant RAG docs or module cards
- produce the next `.cs` attempt
- detect repeated error signatures

`optimizer_bridge.py`

- once compile-clean, create optimizer seed templates
- create or reuse a web optimizer session
- run optimizer phases through existing `optimizer_runner` and
  `optimizer_deployment_package`

`analyzer.py`

- summarize final optimizer/backtest results
- identify weak areas:
  - too few trades
  - drawdown too high
  - PF below threshold
  - one-session overfit
  - poor walk-forward/neighborhood stability

`policy.py`

- stop runaway loops
- define max repair attempts
- define max optimization iterations
- define when code changes are allowed
- define human approval gates

`cli.py`

- expose the loop as a scriptable command

### CLI Shape

First useful command:

```powershell
python -m ta_foundation.nt_strategy_loop.cli auto-compile-repair `
  --source "D:\path\GeneratedStrategy.cs" `
  --strategy-name GeneratedStrategy `
  --max-attempts 5 `
  --compile-root "C:\ta_foundation\nt_compile_loop" `
  --nt-documents-dir "C:\Users\Owner\Documents\NinjaTrader 8"
```

Later full command:

```powershell
python -m ta_foundation.nt_strategy_loop.cli run `
  --spec "D:\path\strategy_spec.json" `
  --strategy-name GeneratedStrategy `
  --max-repair-attempts 5 `
  --max-optimization-rounds 3 `
  --instrument "NQ 06-26" `
  --from-date 2026-04-14 `
  --to-date 2026-05-14 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

## Repair Loop Logic

Pseudocode:

```text
create session
generate or accept initial Strategy.cs

for attempt in 1..max_attempts:
    write attempt/Strategy.cs
    optionally run external preflight compile
    install Strategy.cs into NT strategy folder
    wait for NinjaTrader auto-compile observation status

    if compile succeeded:
        copy Strategy.cs to compile_clean/
        break

    normalize compiler errors
    if error signature repeated:
        stop with repeated_error_signature

    repair code with AI using current code + errors + spec + docs

if no compile-clean:
    write failure summary and stop
```

Stop conditions:

- compile succeeds
- max repair attempts reached
- same compiler error signature repeats
- NinjaTrader compile observer is stale
- source file disappears or target overwrite is blocked
- strategy name/class name mismatch cannot be repaired automatically

## Optimization Loop Logic

After compile-clean:

```text
generate seed Strategy Analyzer template
create optimizer session
preview plan
generate optimizer XMLs
preflight
run NinjaTrader RunBatch
ingest results
build deployment package
if phase2 templates ready:
    run phase2
    rebuild package
if phase3 templates ready:
    run phase3
    rebuild package
if final Backtest templates ready:
    run final fixed Backtests
    rebuild package
review recommendations
```

For newly generated strategies, there may not be a good seed Strategy Analyzer
template yet. The system needs one of these paths:

1. Generate a Strategy Analyzer XML seed from a known-good template and patch
   `StrategyType`.
2. Ask the AddOn to export current Strategy Analyzer settings as a seed.
3. Require one human-created seed for a new strategy family, then automate all
   subsequent loops.

Path 1 is ideal for known strategy-factory outputs. Path 3 is acceptable as an
early MVP.

## Strategy Modification Loop

Optimization analysis can trigger controlled modifications.

Allowed automatic modifications in early versions:

- add or adjust exposed `[NinjaScriptProperty]` parameters
- widen or narrow parameter ranges in templates
- add simple guard filters already represented in Strategy Factory modules
- fix obvious runtime issues
- add instrumentation/logging for parity

Human approval should be required before:

- changing order-management style
- switching managed/unmanaged order APIs
- adding live trading behavior
- changing account/risk assumptions
- deploying to a live or paper environment outside Strategy Analyzer

The strategy modification loop should always create a new attempt and preserve
the previous compile-clean version.

## Result Interpretation

The loop should not use one optimizer score as truth.

Minimum analysis before recommending a generated strategy:

- compile clean
- Strategy Analyzer run completed
- optimizer rows parsed
- final fixed Backtests generated and run
- final review status is `valid`
- no settings contract violations
- drawdown, trades, PF, net profit pass configured filters
- recommendation diversity is acceptable

Optional but strongly recommended before serious use:

- bootstrap robustness
- walk-forward validation
- parameter-neighborhood stability
- shadow rerun against final templates

## Human Gates

The loop should be autonomous for mechanical work, but not for final risk
decisions.

No human gate needed:

- generate code
- install into NT strategy folder when overwrite policy allows
- compile
- repair compile errors
- run Strategy Analyzer optimizations/backtests
- ingest/analyze results
- create reports

Human gate required:

- overwrite an existing non-loop-owned strategy
- accept a major behavioral rewrite after optimization analysis
- enable real-time/paper/live trading
- mark a strategy as approved

## MVP Build Plan

### Slice 1: Add Auto-Compile Observation IPC Contract To AddOn

Goal: Python can install a strategy, let NinjaTrader auto-compile it, and
receive status/errors without the operator copying files.

Work:

- Add `ObserveCompile` action parsing in `BatchControl.cs`.
- Add compile-observer-specific status writing.
- Discover/implement compiler error extraction.
- Export compiler errors to CSV/text/JSON.
- Add a smoke command file for manual testing.

Done when:

- Installing a broken `.cs` file produces NT auto-compile errors, and writing
  an `ObserveCompile` command produces a failed status with parsed compiler
  errors.
- Installing a clean `.cs` file produces `state=succeeded`.

### Slice 2: Python Compile Worker Client

Goal: TA Foundation can run the compile command and normalize results.

Work:

- Add `ta_foundation.nt_strategy_loop.compile_worker`.
- Write compile command JSON.
- Poll status with stale-worker timeout.
- Load errors through the existing normalized error format.
- Write attempt artifacts.

Done when:

- A test can run against fixture status/error files.
- A live smoke can compile one clean and one broken strategy.

### Slice 3: Compile Repair Runner

Goal: Generate/install/compile/repair until clean.

Work:

- Add session and attempt model.
- Wrap existing NinjatraderDocScrapper repair path.
- Detect repeated compiler error signatures.
- Preserve all attempts.

Done when:

- A deliberately broken generated strategy repairs to compile-clean without
  manual error copying.

### Slice 4: Optimizer Bridge For Compile-Clean Strategy

Goal: Start optimizer run automatically after compile-clean.

Work:

- Generate or choose seed template.
- Create optimizer session using existing optimizer APIs.
- Run phase 1 through existing RunBatch IPC.
- Build deployment package.

Done when:

- A compile-clean strategy runs at least phase 1 optimization and produces
  parsed optimizer rows without operator action.

### Slice 5: Full Strategy Refinement Loop

Goal: Let optimization analysis propose a controlled strategy or parameter-plan
change, then repeat.

Work:

- Add result analyzer.
- Add policy gates.
- Add max optimization rounds.
- Add summaries and next-action files.

Done when:

- The loop can run compile -> optimize -> analyze -> refine plan/code -> rerun,
  with durable artifacts and clear stop reasons.

## First Live Smoke Test

Use a tiny strategy, not Pantheon, for the compile-observer proof.

Suggested smoke:

1. Create `BrokenCompileSmoke.cs` with one obvious compile error.
2. Install it to the NT strategy folder.
3. Write:

```json
{
  "action": "ObserveCompile",
  "runId": "compile_smoke_001",
  "sourceFile": "C:\\Users\\Owner\\Documents\\NinjaTrader 8\\bin\\Custom\\Strategies\\BrokenCompileSmoke.cs",
  "strategyName": "BrokenCompileSmoke",
  "outputDir": "C:\\ta_foundation\\nt_compile_loop\\compiler_errors",
  "installedSha256": "<sha256 written by Python installer>",
  "waitForQuietSeconds": 3,
  "timeoutSeconds": 120,
  "exportErrors": true
}
```

4. Confirm:
   - `nt8_status.json` reports `workerKind=compile_observer`
   - state becomes `failed`
   - error count is non-zero
   - CSV/text errors are written
5. Fix the error and rerun.
6. Confirm state becomes `succeeded`.

## Key Risks

- NinjaTrader compile APIs may require reflection and can change by version.
- Compile errors may live in UI-only collections rather than a clean API.
- File watcher events can double-fire; commands need run-id de-duping.
- NinjaTrader can hang or stay busy; stale heartbeat handling is mandatory.
- Class name and file name mismatches can create confusing failures.
- Installing into `bin\Custom\Strategies` can overwrite user files if policy is
  too loose.

## Recommended Immediate Next Step

Implement Slice 1 only.

Do not start by building the whole AI loop. First prove this exact statement:

```text
TA Foundation can install a strategy file, write an observation command, and
NinjaTrader can export machine-readable auto-compile errors without a human
touching the NinjaScript Editor.
```

Once that is true, the rest of the loop is mostly orchestration over pieces
that already exist.
