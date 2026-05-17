# Optimizer Known Issues

Status: living list of operational issues and unfixed items in the
NinjaTrader optimizer workstream. Updated 2026-05-16. Keep this short
and dated; close items inline when fixed rather than starting a new
doc.

For architecture, see
[`ninjatrader_optimizer_web_ui.md`](ninjatrader_optimizer_web_ui.md)
and [`pantheon_optimizer_handoff_plan.md`](pantheon_optimizer_handoff_plan.md).
For the operator runbook, see
[`../runbooks/pantheon_web_optimizer_full_run.md`](../runbooks/pantheon_web_optimizer_full_run.md).

---

## Open

### Auto-stage market data alongside optimization sessions (deferred)

**Why this is on the list.** Today the Python side never sees the
bars/ticks an optimization ran against unless the operator manually
exports them and drops the TXT files in the session input folder
(where `minute_bars_last_txt.py` and `tick_last_txt.py` route them
into `MarketDataStore`). All currently-shipped robustness tests work
around this:

- Bootstrap resamples `Trades.csv` directly — no bars needed.
- Walk-forward and the proposed parameter-neighborhood check
  round-trip through NT for fills, because we don't have a Python
  simulator that matches NT execution.

So no current test is blocked. We're documenting this because the
question keeps coming up and the answer is non-obvious.

**When to revisit.** Add this only when a planned Python-side test
genuinely needs bar context — candidates: indicator-on-bar
consistency checks, slippage / fill modeling, regime overlays on the
optimization rows, a Python-side mini-simulator for cheap
neighborhood / sensitivity sweeps that avoids NT round-trips.

**Design sketch (when needed).** Half-day AddOn change: on session
start the NT AddOn dumps the relevant minute (and optionally tick)
range for the session's contract to a known per-session location, so
the Python ingest picks them up automatically without operator
pre-staging. Key parameters: `instrument_root`, `contract`, date
range derived from the session's Backtest window, output format
matching the existing `Last.txt` exports the parsers already
understand. Alternative cheaper path: a `tools\Export-Bars.ps1`
helper that the operator runs once per session and that drops the
files in the session input folder.

### ~~NT custom optimizer DLL ignores bool parameter sweeps~~ — root cause re-diagnosed and fixed 2026-05-16

**Initial misdiagnosis.** The symptom was real (returned
`*_Optimization.csv` contained only one value for the swept bool
param) but it was not a bool-handling bug. The custom optimizer's log
at `C:\Users\Owner\AppData\Local\Temp\nt8_custom_optimizer.log`
showed it correctly enumerated `Parameter space: Reverse has 2
candidate values` and reached `UniqueParameterSets=18` for an
18-combo plan including both `Reverse` values.

**Actual root cause.** With `PopulationSize=50 × Generations=10 = 500`
iterations against only 18 unique combinations, ~482 iterations were
wasted re-running parameter sets the optimizer had already evaluated.
NT's `KeepBestResults=N` then retained the top-N by Performance, and
since each unique combo was run ~28 times (deterministically yielding
identical scores), the top-N over-represented the highest-scoring
combinations — typically pushing the under-performing bool side out
entirely.

**Fix (deployed 2026-05-16).** In
`D:\ninjatraderOptimizer\NinjaTraderOptimizerProject\NinjaTraderOptimizerProject\Optimizers\CustomMultiObjectiveOptimizer.cs`:

- `ComputeUniqueComboCount(valueSpaces)` calculates the Cartesian
  product size of all parameter value spaces.
- `OnOptimize` now branches: if `totalUniqueCombos <= NumberOfIterations`,
  call new `RunExhaustive()` which enumerates each unique combination
  exactly once. Otherwise fall back to the original Halton-sampled
  `RunCoverageSampling()` for large spaces.
- Log line now includes `TotalUniqueCombos=N` so the operator can see
  which branch was taken; exhaustive mode logs
  `Exhaustive mode: enumerating N unique combinations` and
  `Exhaustive mode dispatched N iterations`.

Built with MSBuild, deployed via `tools\Deploy-Optimizer.ps1`.

**Verification (2026-05-16, complete).** Live re-run on session
`opt_0c96561ec6e4` (multi-type sweep: 3 averageSlow × 3 MaxTPRatio ×
2 Reverse = 18 combos).

DLL log:
```
Starting coverage-search run: PopulationSize=50, Generations=10,
NumberOfIterations=500, OptimizationParameters=26, TotalUniqueCombos=18, ...
Parameter space: averageSlow has 3 candidate values.
Parameter space: MaxTPRatio has 3 candidate values.
Parameter space: Reverse has 2 candidate values.
Exhaustive mode: enumerating 18 unique combinations (NumberOfIterations=500).
Exhaustive mode dispatched 18 iterations.
Custom optimizer completed. UniqueParameterSets=18, DuplicateParameterSets=0.
```

Result CSV: 18 rows, perfectly covering all 18 unique
(Reverse, averageSlow, MaxTPRatio) tuples. Reverse=False appears 9
times, Reverse=True 9 times. Before the fix the same session had
yielded 100 retained rows over only 5 distinct combos with
Reverse=True missing entirely.

This issue is **closed**. Re-test recipe kept for future regression
detection:
```powershell
$base = "http://127.0.0.1:7738/api/optimizer/sessions/opt_0c96561ec6e4"
Remove-Item -Recurse -Force ".ta_artifacts\web_optimizer\sessions\opt_0c96561ec6e4\nt_output" -ErrorAction SilentlyContinue
Invoke-RestMethod -Method Post -Uri "$base/run"
# Poll $base/run/status until state=finished, then check the DLL log
# for "Exhaustive mode dispatched 18 iterations."
```

---

### AddOn cancel still cannot interrupt the in-flight template (partial)

The IPC half is fixed (2026-05-17, see Resolved below): the web cancel
button now actually reaches the AddOn and stops the batch at the next
template boundary, matching the local CANCEL button's semantics.

The remaining gap is **NT-internal**: once `RunCommand.Execute(null)`
has been issued on a Strategy Analyzer tab, the AddOn cannot stop that
optimization mid-flight — NT exposes no public Stop API on
`StrategyAnalyzerVM.RunCommand`. The currently-running template must
complete (or NT must be killed) before the cancel takes visible
effect.

**Where to look** if a future operator wants to push further:
- `StrategyAnalyzerAutomation.cs` — would need a discovered field like
  `StopCommand` / `CancelCommand` / equivalent on the SA view model.
  Past reflection sweeps (see `tools/inspect_vm_command_fields.ps1`)
  did not surface one. Closing the SA tab via `OnCloseTab` while a
  run is in flight is *possible* but risks leaking handles or
  corrupting export state.

**Workaround for true emergencies.** Stop NinjaTrader directly. The
local CANCEL button and the new IPC cancel both have the same
"finish current template, then stop" semantics; neither can interrupt
a single optimization once NT has started it.

---

### Top-level `tests/` directory has pre-existing failures

Unrelated to the optimizer but documented for context: 10 failures
in `tests/research_ledger/test_backfill.py` and 2 in
`src/ta_foundation/tests/web/test_conditional_promotion.py`
predate today's optimizer work. They consistently appear with or
without the optimizer changes applied. Ignore them when reading
optimizer-related test output; use:

```powershell
python -m pytest src/ta_foundation/tests/web src/ta_foundation/tests/optimization src/ta_foundation/tests/parsers `
  --ignore=src/ta_foundation/tests/web/test_conditional_promotion.py -q
```

(359 passing as of 2026-05-16.)

---

## Resolved (kept for history)

### 2026-05-17 — Web-app cancel now reaches the AddOn (IPC half)
The `commandWatcher` in
`D:\ninjatraderOptimizer\NinjaTraderAddOnProject\BatchControl.cs`
previously only listened for `Changed/Created/Renamed` on
`C:\temp\nt8_command.json`. The web UI's cancel path unlinks that
file (`optimizer_runner.cancel_run`), so the AddOn never saw the
signal and the batch ran to completion regardless. Fix:

- `SetupIPC()` now also subscribes to `Deleted`. New handler
  `OnCommandFileDeleted` flips `cancelRequested` when the file
  disappears mid-batch (`isRunning && !cancelRequested` guard so
  idle deletions are ignored).
- `OnCommandFileChanged` now recognizes an explicit
  `{"action":"Cancel"}` payload as an alternative cancel trigger
  (future-proof for callers that don't want to delete the file).
- New helper `RequestCancelFromIpc(reason)` flips the flag on the
  WPF dispatcher and logs which path fired it.

Effect: web-app cancel now has the same semantics as clicking the
local CANCEL button — the batch stops at the next template boundary.
NT's RunCommand has no public Stop API, so the in-flight template
itself still has to finish; see the remaining Open item for context.

Built with MSBuild on `NinjaTraderAddOnProject.sln`; the PostBuildEvent
xcopied the DLL/PDB to
`%USERPROFILE%\Documents\NinjaTrader 8\bin\Custom\` (verified
LastWriteTime 2026-05-17 08:29:58 UTC). NT restarted.

**Live smoke test (2026-05-17 08:38).** Staged a single-template
RunBatch payload (`smoke_W00.xml` copied from
`opt_5bab6a5ee1ea/walkforward/templates/F_001__W00.xml`) to a
disposable `%TEMP%\nt_cancel_smoke` folder, wrote
`nt8_command.json`, watched `nt8_status.json` until
`state=running, currentTemplate=smoke_W00`, then deleted the
command file. The AddOn flipped `state` to `cancelled` ~570 ms
after the deletion and aborted before NT exported anything.
Confirms the IPC contract is live in the deployed DLL.

### 2026-05-17 — `[2,]` range display fixed
`makeParamRow` in `src/ta_foundation/web/templates/optimizer.html`
was using `??` to fall back to `−∞`/`+∞` for absent bounds, but the
backing values arrive as `""` rather than `null/undefined` so the
fallback never triggered. Replaced the ternary with explicit
`hasMin`/`hasMax` checks (empty-string-aware). Cosmetic-only change;
the Range column now renders `[2, +∞]` when a `[Range(2, int.MaxValue)]`
attribute supplies only a minimum.

### 2026-05-17 — `min_percent_days_traded` UI clarified as final-review only
Took the cheaper UI-clarification path rather than back-computing
percent-days-traded from Trades exports. The guardrail input on the
configure page and the readout on the session detail page now carry
an amber `⚑ final-review only` indicator with a tooltip explaining
that the field is not present in `*_Optimization.csv` and therefore
does not filter raw optimizer rows — only the final-Backtest review
stage applies it.

### 2026-05-17 — Parameter-neighborhood validation (opt-in)
Added `src/ta_foundation/optimization/neighborhood.py` (pure cell
planner) + `src/ta_foundation/web/optimizer_neighborhood.py` (engine) +
4 API routes (`/neighborhood/generate`, `/run`, `/status`, `/ingest`) +
"Parameter neighborhood (optional)" card on the session detail page.
For each final candidate, generates a small set of fixed-Backtest
templates with one (or — in `full_cube` mode — several) numeric
parameter shifted by ±pct% around the candidate's value, honoring the
original parameter increment so generated values land on the same grid
the optimizer used. Dispatches through the existing AddOn IPC. Bool /
enum / fixed parameters are skipped automatically.

Ingest produces a per-candidate stability summary (PF min/median/max,
net min/median/max, CoV of net profit, count of cells with PF>1, plus
per-parameter sensitivity bucket) and flags needle peaks (`center
PF >> neighborhood median`) and degenerate sweeps. Writes
`stability.json` + `stability.md` to `<session>/deployment_package/neighborhood/`.

`parameter_neighborhood_check` in
`src/ta_foundation/optimization/robustness.py` is now a redirect shim
to the new engine. The "Parameter neighborhood (deferred)" checkbox in
the robustness card is replaced by a note pointing to the new card.

Test coverage: 8 planner tests (`tests/optimization/test_neighborhood.py`)
+ 9 engine tests (`tests/web/test_optimizer_neighborhood.py`). 412
passing across the optimizer/web/parsers suites with the
`test_conditional_promotion` ignore in place.

### 2026-05-16 — AddOn dropped contract suffix on Backtest template re-runs
The BatchControl AddOn unconditionally overrode every loaded template's
`<InstrumentOrInstrumentList>` with either the IPC payload's
`instrument` field or the currently-selected tab's instrument.
When the IPC omitted the instrument and the operator's tab had `NQ`
(the root), the AddOn loaded an `NQ 06-26` template as `NQ` and
the strategy produced zero trades. Discovered via the new shadow
execution feature when the same named template that originally
produced 17 trades produced 0 trades on a re-run.

Fixed in two places:

- **NT AddOn** (`D:\ninjatraderOptimizer\NinjaTraderAddOnProject\BatchControl.cs`):
  introduced `bool instrumentExplicit = !string.IsNullOrWhiteSpace(requestedInstrument);`
  and made the `SetSelectedInstrumentOrInstrumentList` override conditional on
  `instrumentExplicit`. When the IPC payload doesn't specify an instrument,
  the template's own `<InstrumentOrInstrumentList>` value (already
  applied by `LoadTemplate`) is preserved. New log line "Batch instrument
  source: ..." reports which path was taken. Commit `c41dc77` in
  `D:\ninjatraderOptimizer` (master branch, not pushed).
- **Web** (`src/ta_foundation/web/optimizer_shadow.py`):
  `trigger_shadow_run` now reads `session.doc.instrument` and includes
  it as `"instrument"` in the IPC payload. The optimizer phase runner
  already did this; shadow was the omission.

### 2026-05-16 — Walk-forward validation (opt-in, fixed-parameter variant)
Added `src/ta_foundation/optimization/walkforward.py` (pure window
planner) + `src/ta_foundation/web/optimizer_walkforward.py` (engine) +
4 API routes (`/walkforward/generate`, `/run`, `/status`, `/ingest`) +
"Walk-forward validation (optional)" card on the session detail page.
Generates N fixed-Backtest templates per candidate, one per rolling
historical window (anchor + window_days + count + gap), with optional
"skip windows overlapping the OOS range" filter. Dispatches via the
existing AddOn IPC. Ingests per-window Trades.csv / Summary.csv and
produces a per-candidate stability summary (PF min/median/max, mean &
CoV of net profit, count of windows with PF>1 and with trades, flags
for "PF median collapsed", "highly variable net", etc.). Writes
`stability.json` + `stability.md` to `<session>/deployment_package/walkforward/`.

First live run on `opt_5bab6a5ee1ea` / `F_001` (3 x 7-day windows
ending 2026-04-13, immediately before the OOS) produced a dramatic
result: F_001's IS PF of 5.01 collapsed to a 3-window PF median of
**0.00** with net **-$6,970** across 13 trades — strong evidence the
strategy is curve-fit to its specific OOS window. Walk-forward flags
fired correctly. The same live run also confirmed the AddOn
contract-drop fix from commit c41dc77 — the templates produced actual
trades (not zeros), proving the new DLL correctly preserves the
template's `NQ 06-26` contract.

Note: this is the cheaper *fixed-parameter* variant of walk-forward.
A future enhancement would re-OPTIMIZE the parameters on each IS
window via the full phase-1→2→3 pipeline before validating on the
next OOS window. That's still deferred.

### 2026-05-16 — Bootstrap trade-sequence robustness check (opt-in)
Added `src/ta_foundation/optimization/robustness.py` +
`src/ta_foundation/web/optimizer_robustness.py` +
`POST /api/optimizer/sessions/<id>/robustness`. UI: "Robustness
checks (optional)" card on the session detail page. For each final
candidate, reads its `Trades.csv` and resamples with replacement
(default 1000 samples). Reports PF / net profit / max DD bootstrap
percentiles + p(stat ≥ observed). First live run on
`opt_5bab6a5ee1ea` exposed that `F_001`'s observed PF 5.01 sits at
the median (p=0.532) of a 2.22..15.88 bootstrap range — the
strategy is plausibly real, but the headline PF carries huge
uncertainty at only 17 trades. Walk-forward and
parameter-neighborhood checks are stubbed in the same module and
raise `NotImplementedError` (see Open above).

### 2026-05-16 — Bool-sweep `Increment` serialized as `true`
Fixed by switching to a numeric-only `_serialize_increment` helper
in `optimizer_template_writer.py`. The XML now emits `<Increment>1</Increment>`
for bool sweeps. Note: NT's *default* optimizer respects this; the
*custom* optimizer DLL still has its own bool-sweep bug — see Open
section above.

### 2026-05-16 — Parser bool-coercion of `"1"` and `"0"`
`_coerce_scalar` in `optimization_csv.py` was coercing `"1"` to
`True` and `"0"` to `False`, corrupting numeric params like
`Contracts=1` and `ProfitStop=10000`. Fixed by limiting bool
recognition to the literal text `"true"/"false"/"yes"/"no"`.

### 2026-05-16 — `param_Reverse` missing from `top_rows`
`_top_rows` clipped to `param_cols[:12]`, dropping `param_Reverse`
(position 21 in the Pantheon parameter list). Now includes every
`param_*` column.

### 2026-05-16 — Phase-3 export path-length truncation
`BatchControl.cs` shrinks per-template export filenames based on
destination depth so Windows MAX_PATH doesn't silently truncate
`*_Optimization.csv` exports. Deployed earlier today.

### 2026-05-16 — Generic instrument leak in chunk XMLs
`optimizer_template_writer.py` now patches
`<InstrumentOrInstrumentList>` from the session contract (or seed
fallback) so generated chunks always carry the full contract.
`optimizer_preflight` blocks `RunBatch` if any chunk still has a
generic instrument.
