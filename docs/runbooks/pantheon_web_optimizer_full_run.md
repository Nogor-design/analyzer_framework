# Pantheon Web Optimizer Full Runbook

Status: repeatable operator runbook for the proven `/optimizer` web flow that
drives the NinjaTrader batch AddOn and produces an end-user deployment
package. Companion to
[`pantheon_custom_optimizer_full_run.md`](pantheon_custom_optimizer_full_run.md)
which covers the equivalent CLI flow.

The reference session that this runbook reproduces lives at:

```text
.ta_artifacts\web_optimizer\sessions\opt_5bab6a5ee1ea\
```

## Scope

Walks the operator from a clean state to a packaged end-user decision
artifact:

1. Start the web UI and NinjaTrader with the AddOn loaded.
2. Configure an optimizer session (strategy, seed, parameters, guardrails,
   chunking).
3. Generate phase-1 chunk templates.
4. Preflight + RunBatch on NinjaTrader.
5. Build the deployment package (drives phase 2, phase 3, and final fixed
   Backtest generation/validation).
6. Hand the package to the operator for the deploy / paper-trade / revise /
   reject decision.

## Repositories

```text
D:\ninjatraderOptimizer                       # AddOn + custom optimizer DLL
D:\Backup\projects\PythonProject\ta_foundation  # web UI + optimizer engine
D:\templateNaming                              # template-namer CLI
```

## Hard Rules

- Build NinjaTrader projects with **MSBuild**, not `dotnet build`.
- Deploy NinjaTrader DLLs by stopping NinjaTrader, copying DLL/PDB to
  `C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom`, then restarting.
- Web UI runs at **`http://127.0.0.1:7738/optimizer`** by convention.
- Do not skip preflight. If preflight fails, fix the underlying template,
  do not bypass it.
- Final fixed Backtest templates must not contain `OptimizerType`,
  `OptimizationFitness`, or `<OptimizationParameters>`.
- Final Backtest templates and returned `Settings.csv` files must force
  `UseTrend=false` and `UseTrendReverse=false`.
- Every generated chunk XML must carry the full seed contract (e.g.
  `NQ 06-26`, not generic `NQ`). The preflight enforces this; if you see
  generic-instrument warnings, regenerate templates.
- Do not revert unrelated dirty files in either repo.

## Preconditions

1. **NinjaTrader 8 running** with the batch optimizer AddOn loaded. The AddOn
   watches `C:\temp\nt8_command.json` and writes `C:\temp\nt8_status.json`.
2. **Optimizer DLL deployed.** If you rebuilt
   `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject` recently, run:

   ```powershell
   .\tools\Build-WithLearningLog.ps1
   .\tools\Deploy-Optimizer.ps1
   ```

   `Deploy-Optimizer.ps1` stops NT, copies the DLL/PDB, and restarts NT.
3. **Seed template saved** in
   `C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\<StrategyName>\`.
   Known good seed:
   `PantheonMasterBotV01TesterV2\Pass1_Breakout_Start00.xml` with
   `<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>`.

## Start The Web UI

From `D:\Backup\projects\PythonProject\ta_foundation`:

```powershell
python -m ta_foundation.web.app --port 7738
```

Open `http://127.0.0.1:7738/optimizer`. The page is a 7-section single-page
flow keyed by `session_id` in the URL.

## Step 1 — Configure The Session

In the UI:

1. **Strategy** — pick `PantheonMasterBotV01TesterV2`.
2. **Seed template** — pick the saved seed (e.g. `Pass1_Breakout_Start00`).
   The contract field auto-fills from the seed's
   `<InstrumentOrInstrumentList>`. Confirm it shows the **full contract**
   (`NQ 06-26`), not generic `NQ`.
3. **Parameter table** — mark which parameters to optimize. For the proven
   phase-1 setup, optimize `DurationTimeH`, `averageSlow`, `MaxStop`,
   `MaxTPRatio`; leave the rest fixed.
4. **Guardrails** — proven defaults:
   - Max drawdown: `2500`
   - Min trades: `10`
   - Min profit factor: `1.5`
   - Min % days traded: `20`
5. **Chunking** — max combinations / chunk: `5000`. Keep best rows / chunk:
   `500`.
6. **Save session.**

Session disk location:

```text
.ta_artifacts\web_optimizer\sessions\opt_<id>\
  session.json
  plan.json   (after preview)
  generated_templates\
  nt_output\
  run.json    (after RunBatch)
  deployment_package\   (after build)
```

## Step 2 — Preview Plan And Generate Templates

1. Click **Preview plan**. Confirm:
   - Combinations per chunk is within budget.
   - Number of chunks is reasonable (4 for the reference session).
2. Click **Generate optimizer XMLs**.
3. Spot-check the generated files. Every chunk XML must contain the full
   contract:

   ```powershell
   Get-Content .ta_artifacts\web_optimizer\sessions\opt_<id>\generated_templates\chunk_001.xml |
     Select-String "InstrumentOrInstrumentList"
   ```

   Expected: `<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>`.

## Step 3 — Preflight And RunBatch

1. The page shows preflight status. It must report **OK** before RunBatch is
   enabled. If it blocks, the error names the offending chunk file — fix
   either the seed contract or regenerate templates.
2. Click **Run on NinjaTrader**. This writes:

   ```text
   C:\temp\nt8_command.json
   ```

   with the session's `sourceFolder` (generated templates) and `destFolder`
   (session `nt_output/`).
3. The AddOn picks up the command and runs every `*.xml` in
   `sourceFolder`. Progress bar reads from `C:\temp\nt8_status.json`
   heartbeat, with a folder-watch fallback that counts completed
   `Summary.csv` exports.
4. Expected phase-1 result: 4 chunks complete, ~2,000 optimization rows
   parsed across 4 `*_Optimization.csv` exports under `nt_output/`.

Troubleshooting:

- **No `*_Optimization.csv` exported and AddOn says finished.** Most common
  cause is Windows MAX_PATH truncation of the export filename. The fixed
  AddOn (`BatchControl.cs`, 2026-05-16 patch) shrinks the export name
  based on destination path depth — confirm the DLL is current via
  `Deploy-Optimizer.ps1`.
- **AddOn shows `Performance 0.00` but rows have trades/PF/net.** This is
  normal. `Performance` is the optimizer fitness column; per-row metrics
  in `*_Optimization.csv` are still valid.
- **Generic instrument warning.** Regenerate templates; do not bypass
  preflight.

## Step 4 — Results And Deployment Package

1. Click **Refresh results** in the Results section. The page shows the
   top-N rows filtered by available guardrails.
2. POST `/api/optimizer/sessions/<id>/deployment-package` (UI button:
   **Build deployment package**) to assemble the full operator handoff.

The package is written to:

```text
.ta_artifacts\web_optimizer\sessions\opt_<id>\deployment_package\
  DECISION_SUMMARY.md          # phase-1 guardrail candidates summary
  END_USER_DECISION.md         # operator-facing decision summary
  manifest.json                # machine-readable package metadata
  analysis\
    batch_summary.csv
    guardrail_candidates.csv
    top_optimizer_rows.csv
  templates_to_run\            # phase-1 chunk templates + run plan
  phase2_refinement_handoff\
    generated_phase2_templates\   # 8 refinement XMLs
    optimization_grid_candidates.{csv,json}
    optimization_phase_lineage.{csv,json}
  phase3_risk_handoff\         # populated after phase-2 results come back
  final_backtest_handoff\      # populated after phase-3 results come back
```

## Step 5 — Phase 2 → Phase 3 → Final Backtests

The package drives the multi-phase loop. For each phase:

1. Use the most recent `generated_phase<N>_templates` folder as the next
   AddOn `sourceFolder`.
2. Drop the matching `nt8_command.json` (the package writes one ready to
   use) or trigger the run from the UI (future: dedicated buttons; for
   now, the CLI fallback below works).
3. Return result CSVs to the corresponding `nt_output*/` folder.
4. Re-run the deployment package builder. It detects the new returned
   results and advances to the next phase folder.

CLI fallback (equivalent to the web flow once the optimizer rows are
returned):

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase phase3 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizethirdRunBreakout.xml" `
  --optimization-csv-dir "<session>\deployment_package\phase2_refinement_handoff\nt_output" `
  --output-dir "<session>\deployment_package\phase3_risk_handoff" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

Final fixed Backtest generation **requires** explicit out-of-sample dates:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase final `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\strategies\PantheonMasterBotV01TesterV2\templates\sampleTemplate.xml" `
  --optimization-csv-dir "<session>\deployment_package\phase3_risk_handoff\nt_output_short_path_success" `
  --output-dir "<session>\deployment_package\final_backtest_handoff" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --from-date 2026-04-14 `
  --to-date 2026-05-14
```

Verify the final Backtest contract before running them:

```powershell
rg -n "<UseTrend>true</UseTrend>|<UseTrendReverse>true</UseTrendReverse>|<OptimizerType>|<OptimizationFitness>|<OptimizationParameters>" `
  "<session>\deployment_package\final_backtest_handoff\named_backtest_templates"
```

Expected: no matches.

## Step 6 — Final Review And Operator Handoff

After NinjaTrader returns the final fixed Backtest results:

```powershell
python -m ta_foundation.optimization.review `
  --input-dir "<session>\deployment_package\final_backtest_handoff\nt8_backtest_results" `
  --output-dir "<session>\deployment_package\final_backtest_handoff\final_backtest_review"
```

Expected outputs in `final_backtest_review/`:

- `REVIEW_SUMMARY.md` — validation status, rank table, daily-risk knobs.
- `recommendations.md` / `recommendations.csv` / `recommendations.json` —
  bucket-spanning final candidates.
- `review_summary.json` — UI-facing machine summary.
- `settings_contract_violations.csv` — must be header-only.
- `evaluated_candidates.*` and `result_intake.*` — full traceability.

Reference proven values for the `opt_5bab6a5ee1ea` session:

```text
validation_status        = valid
candidate_count          = 8
passed_count             = 8
settings_contract_violations = 0
top_candidate            = F_001 (Pre-Market, $19,880, PF 5.01, DD $1,500, 17 trades)
```

The `END_USER_DECISION.md` and the named templates under
`final_backtest_handoff\named_backtest_templates\breakout\` are the
operator-facing artifacts. The operator chooses: deploy, paper-trade,
revise, or reject.

## Focused Verification

```powershell
python -m pytest src\ta_foundation\tests\web -q
python -m pytest src\ta_foundation\tests\optimization -q
```

The web optimizer suite covers session model, plan preview, template
writer (including contract patching), preflight, runner, results, and
deployment package. Last green run: 47 passed.

## Reference Implementations

- Web architecture: [`docs/designs/ninjatrader_optimizer_web_ui.md`](../designs/ninjatrader_optimizer_web_ui.md)
- Engine design: [`docs/designs/pantheon_optimizer_handoff_plan.md`](../designs/pantheon_optimizer_handoff_plan.md)
- CLI flow: [`pantheon_custom_optimizer_full_run.md`](pantheon_custom_optimizer_full_run.md)
- Source modules: `src/ta_foundation/web/optimizer_*.py` and
  `src/ta_foundation/optimization/*.py`
- Reference session: `.ta_artifacts/web_optimizer/sessions/opt_5bab6a5ee1ea/`

## Known Gaps / Next Targets

- `/optimizer/sessions/<id>` detail page is the single-page-per-session view
  only; no cross-session reuse UI.
- AddOn cancel mid-template is not supported; only between templates.
- Phase 6 deep refinement UI: "Clone & refine" exists on the sessions
  list page (copies config to a new session), but a UI for selecting
  specific final-recommendation rows or rejected zones to drive narrowed
  sweeps is not yet wired.
- Custom optimizer DLL (`D:\ninjatraderOptimizer`) behavior: a numeric
  sweep with `KeepBestResults=50` can return 50 identical "best" rows
  when the optimizer samples a near-zero parameter neighborhood. The web
  flow handles this correctly (guardrails reject or accept based on the
  returned rows) but operators should be aware that single-chunk
  micro-sweeps may not produce meaningful parameter variation.

## Shipped 2026-05-16

- Multi-phase auto-advance in `optimizer_deployment_package` (phase 2 → 3 →
  final → bucket-diverse recommendations).
- `/optimizer/sessions` list page with Resume / Clone & refine / Delete.
- Plan-hash reuse: `GET /api/optimizer/sessions/<id>/matches`.
- Clone & refine: `POST /api/optimizer/sessions/<id>/clone`.
- OOS dates and Backtest seed persisted on the session doc.
- Parameter table UX cleaned up: fixed_value hides when optimize is
  selected; bool sweeps auto-seed to false..true / step 1.
- Bool-sweep `Increment` bug fixed (was emitting `true`, now emits `1`).
- Parser bool-coercion fixed: `"1"`/`"0"` stay integers (Contracts,
  ProfitStop, etc. now report as numbers, not booleans).
- `_top_rows` no longer truncates parameter columns; `param_Reverse` and
  other late-position params now surface in results.
