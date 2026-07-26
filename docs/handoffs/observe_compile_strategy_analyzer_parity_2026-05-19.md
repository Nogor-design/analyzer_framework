# ObserveCompile vs. Strategy Analyzer Compile Parity — Handoff (2026-05-19)

## Status: RESOLVED (2026-05-19)

**AddOn side** — `NinjaTraderAddOnProject\CompileObserverService.cs`:
- `FindEditorCompileErrors` no longer drops peer-strategy errors; non-owned records are tagged with `Source` suffix `[peer]`.
- `ObserveCompile` succeeded branch now requires a fresh `NinjaTrader.Custom.dll` (mtime ≥ source mtime − 2s) in addition to type-visible — kills the cached-type false positive.
- `RunNinjaScriptCompileCheck` (reflective `LegacyCompiler.Compile`) **removed** — it defaulted to an older C# language version and emitted ~3000 false positives against NT's own `@`-prefixed bin\Custom files and user code with modern syntax. The editor's `CompileErrors` collection is the source of truth; trace/log scan remains as fallback when no editor instance is open. `System.CodeDom.Compiler` using directive removed.
- Status / result JSON gained `compileBlockReason` field: `strategy_compile_errors` | `peer_strategy_errors` | `stale_assembly`. Schema bumped to version 2 (backward compatible — field omitted when not applicable).

**Python side**:
- `CompileObservation.compile_block_reason` (new field, optional).
- `policy.evaluate_stop` branches: `peer_strategy_errors` → terminal `peer_compile_block`, `stale_assembly` → terminal `stale_assembly`. `strategy_compile_errors` keeps the repair-eligible path.
- Regression tests in `test_compile_observer.py` and `test_policy.py`.

**Validated end-to-end (2026-05-19)** with two fresh NT restarts against the smoke session:
- Pre-fix: `state=succeeded / errorCount=0` despite SA refusing to run.
- Post-AddOn-fix #1 (filter dropped, fresh-assembly check, new field): `state=failed / errorCount=3134 / compileBlockReason=peer_strategy_errors / lastError="peer strategy compile error blocking SA: Unexpected character '$'"` — Python policy returns `stop.code=peer_compile_block` with actionable message. Root cause: `Documents\NinjaTrader 8\bin\Custom\AddOns\LocalTradeCopier.cs` uses C# 6 interpolated strings (`$"..."`) at lines 138/587/617 that NinjaScript's older compiler rejects, blocking the entire assembly rebuild.
- Operator quarantined LocalTradeCopier.cs to `.ta_artifacts\quarantine\nt_addons_2026-05-19\`.
- Post-quarantine ObserveCompile then surfaced 3123 *new* false-positive errors from `RunNinjaScriptCompileCheck` against NT's own `@RegressionChannel.cs`, `@LineBreakBarsType.cs`, etc. — exposed a third bug the per-strategy filter had been hiding.
- Post-AddOn-fix #2 (reflective probe removed): `state=succeeded / errorCount=0 / compileBlockReason=None` — Python policy returns `compile_clean`. Loop fully unblocked.

## Original TL;DR

The Python compile observer (`ta_foundation.nt_strategy_loop.compile_observer`)
reports `state=succeeded` and `errorCount=0` for a strategy that NinjaTrader's
Strategy Analyzer subsequently refuses to run with the modal text:

> The following programming errors must be resolved before compiling.

The receiving side (the C# `BatchStrategyOptimizerAddOn`, specifically the
`ObserveCompile` handler in `BatchControl.cs`) is reading from a different
source than the pre-run validator that Strategy Analyzer consults. Until that
discrepancy is closed, the autonomous repair loop can't tell broken strategies
from clean ones and will silently stall in the SA modal.

This handoff exists so the AddOn-side change can be done in isolation.

## Reproduction evidence

Session: `.ta_artifacts/nt_strategy_lab/sessions/loop_20260519_194414_autonomousloopsmokecodex/`

Key artifacts:

- `decisions/STRATEGY_LOOP_SUMMARY.md`, lines 33–35:

  > Follow-up `ObserveCompile` for `AutonomousLoopSmokeCodex` still returned
  > `succeeded` with `0` errors, so this is a remaining worker gap: RunBatch /
  > Strategy Analyzer can see compile-blocking state that the current compile
  > observer does not export.

- `attempts/attempt_001/live_compile_status.json` — `state: succeeded`,
  `compiled: true`, `error_count: 0`.
- `attempts/attempt_001/live_compile_status_after_runbatch_error.json` —
  same `succeeded`/`0` outcome, recorded *after* SA refused to run with the
  programming-error modal.

## What the autonomous loop assumes

`evaluate_stop` in `ta_foundation.nt_strategy_loop.policy` treats a
`CompileObservation` with `compiled=True` and `state="succeeded"` as a
terminal `compile_clean` stop. That decision then triggers
`optimizer_bridge.run_optimizer_for_strategy`, which submits a RunBatch
command via IPC. If Strategy Analyzer rejects the strategy at pre-run, the
optimizer session sits with no result and `get_status` reports `stale`.

## Root-cause hypotheses to investigate (C# side)

The `ObserveCompile` handler currently inspects whatever surface returns a
"clean" answer fastest. Strategy Analyzer almost certainly enumerates a
richer compile/load-time state. Things to check in `BatchStrategyOptimizerAddOn`:

1. **Strategy Analyzer's pre-run validator.** SA shows the
   *"following programming errors must be resolved before compiling"* modal
   from somewhere other than the NinjaScript Editor compile output. Identify
   that source (likely a property/method on the SA viewmodel or a static
   `NinjaScriptCompiler` helper) and have `ObserveCompile` query it after
   the NinjaTrader auto-compile pass settles.
2. **Cached vs. fresh compile state.** It is possible the observer is reading
   a cached result from the previous compile and not waiting for NT to
   finish the rebuild triggered by the new `.cs` install. The
   `waitForQuietSeconds` argument should be honored on the AddOn side, not
   only in Python.
3. **Class-name and assembly load failures.** A strategy can compile but
   fail to load into Strategy Analyzer if its `StrategyType` token doesn't
   resolve. SA shows that as a compile-blocking modal; the AddOn currently
   doesn't probe the loader.

## Acceptance criteria for the AddOn change

The fix is correct when all of the following hold against a fresh strategy:

1. Install a `.cs` that compiles in the editor but cannot run in SA (for
   example: missing `[NinjaScriptProperty]` decoration, or a class-name vs.
   filename mismatch). `ObserveCompile` must report `state=failed` (or a new
   `state=blocked_by_strategy_analyzer`) with a non-empty error list.
2. Install a genuinely clean `.cs`. `ObserveCompile` must continue to report
   `state=succeeded` with `errorCount=0`.
3. The exported CSV/text error files must keep matching the column schema in
   `parse_compile_errors_csv` and `parse_compile_errors_text`.
4. The auto-archived test session
   `loop_20260519_194414_autonomousloopsmokecodex` can be replayed and now
   surfaces the missing diagnostic.

## Suggested IPC shape if a new state is needed

Backwards-compatible option: keep `state` ∈ {`succeeded`, `failed`,
`timed_out`, `worker_error`} and use `failed` with new
`compileBlockReason: "strategy_analyzer_validator"` when the editor compile
passed but SA's pre-run check failed. `compile_observer.observation_from_status`
already tolerates extra fields.

Slightly more explicit option: add `blocked_by_strategy_analyzer` to the
state enum and update `policy._TERMINAL_FAILURE_STATES` to include it.
Either is fine; pick whichever is easier on the AddOn side.

## Where the Python side will need to follow up

- Once the AddOn reports the new failure mode, extend `policy.evaluate_stop`
  to treat it as repair-eligible (it's still a code problem, the repair
  pipeline can have a shot at it) or terminal (if SA tells us nothing
  actionable). Default: repair-eligible.
- Add a regression test using a synthetic status payload with the new field
  to `test_compile_observer.py`.

## Files touched on the Python side already

These are the modules consuming the observer's output today; no changes
required for them when the AddOn change lands, but they're the reading list:

- `src/ta_foundation/nt_strategy_loop/compile_observer.py`
- `src/ta_foundation/nt_strategy_loop/policy.py`
- `src/ta_foundation/nt_strategy_loop/repair_loop.py`
- `src/ta_foundation/nt_strategy_loop/optimizer_bridge.py`

## Not in scope for this handoff

- Strategy authoring/refactor (autonomous loop slice 5)
- Optimizer bridge plumbing (already shipped in
  `optimizer_bridge.run_optimizer_for_strategy`)
- The full repair loop (already shipped in `repair_loop.run_repair_loop`)
