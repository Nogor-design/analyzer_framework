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

### NT custom optimizer DLL ignores bool parameter sweeps

**Symptom.** A generated optimizer XML with
`<Min xsi:type="xsd:boolean">false</Min>`,
`<Max xsi:type="xsd:boolean">true</Max>`, `<Increment>1</Increment>`
on a bool parameter (e.g. `Reverse`) executes only the strategy's
fixed value, not both `false` and `true`. The returned
`*_Optimization.csv` contains a single distinct value for that param,
even when multiple combinations were planned.

**Web side is correct.** As of 2026-05-16, the bool-sweep `Increment`
serializer fix in `optimizer_template_writer.py` emits integer `1`
(was `true`, which the default NT optimizer used to collapse the sweep
to a single value). Production parser tests confirm Min/Max/Increment
appear correctly in the generated XML.

**Where to look.** The seed templates we use force
`<OptimizerType>NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer</OptimizerType>`,
which is the custom optimizer DLL living at
`D:\ninjatraderOptimizer\NinjaTraderOptimizerProject`. That DLL is
likely sampling around the strategy's fixed `<Reverse>` value rather
than treating the bool Parameter block as a discrete domain. Verify
in `BatchOptimizerLoader` / the custom optimizer's parameter handling.

**Workaround.** Pin `Reverse` as `fixed` and use **Clone & refine**
on the session list page to create a second session with the opposite
value. The deployment package, recommendations engine, and final
review treat each session independently and the bucket-diverse
recommendation logic handles both modes naturally.

**Verification session.** `opt_0c96561ec6e4` (2026-05-16,
multi-type sweep smoke). Planned 18 combinations (3 averageSlow x
3 MaxTPRatio x 2 Reverse); NT exported 100 rows representing 5
distinct `(Reverse, averageSlow, MaxTPRatio)` tuples — Reverse only
ever False.

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
