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

### AddOn cancel cannot abort an in-flight template

**Symptom.** Calling
`POST /api/optimizer/sessions/<id>/run/cancel` writes the cancel
state to `run.json` and removes `C:\temp\nt8_command.json`, but the
NinjaTrader AddOn finishes the current template before it sees the
absence of the command file. Long-running phase-3 chunks can keep
running for the full template duration after a cancel click.

**Where to look.** `BatchControl.cs` in
`D:\ninjatraderOptimizer\NinjaTraderAddOnProject`. The batch loop
checks the command file between templates, not within a single
Strategy Analyzer optimization run.

**Workaround.** Stop NinjaTrader directly if you need to abort a
long-running optimization mid-template. Otherwise wait for the
current template to finish; the cancel will take effect at the next
template boundary.

---

### `[2,]` range display in the parameter table

**Symptom.** The "Range" column in the optimizer parameter table
renders as `[2,]` when the strategy's `[Range]` attribute has a min
but no explicit max (e.g. `[Range(2, int.MaxValue)]`). Cosmetic only;
the sweep still validates fine because Min/Max are taken from the
optimize fields, not the [Range] hint.

**Where to fix.** `src/ta_foundation/web/templates/optimizer.html`,
the `rangeText` ternary inside `makeParamRow`. Render `[2, ∞]` or
just `≥ 2` instead of `[2, ]`.

---

### `min_percent_days_traded` not applied at the optimizer-row stage

**Symptom.** The session's `min_percent_days_traded` guardrail
filters final-Backtest review rows correctly, but `optimizer_results`
does not compute or filter on percent-days-traded for raw optimizer
rows because that field is not in `*_Optimization.csv`. The web UI
allows setting the guardrail and shows it on the deployment package
notes, but it has no effect until the final-Backtest stage.

**Where to fix.** Either compute percent-days-traded from the
Settings / Trades exports during result intake, or surface this
clearly in the UI as "applied at final review only". The latter is
the cheaper option.

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
