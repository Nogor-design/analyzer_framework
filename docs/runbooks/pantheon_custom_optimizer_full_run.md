# Pantheon Custom Optimizer Full Runbook

Status: repeatable operator runbook for the proven standalone custom optimizer workflow.

## Scope

This runbook repeats the NinjaTrader/Pantheon optimizer flow that has been
proven end to end:

1. Generate phase-1 custom optimizer templates.
2. Run them in NinjaTrader with the batch Strategy Analyzer AddOn.
3. Generate phase-2 templates from phase-1 `*_Optimization.csv` exports.
4. Run phase 2.
5. Generate phase-3 daily-risk templates from phase-2 exports.
6. Run phase 3.
7. Generate final fixed Backtest templates from phase-3 exports.
8. Run final Backtests.
9. Review final Backtest outputs and validate the settings contract.

## Repositories

```text
D:\ninjatraderOptimizer
D:\Backup\projects\PythonProject\ta_foundation
```

## Hard Rules

- Build NinjaTrader projects with MSBuild, not `dotnet build`.
- Deploy NinjaTrader DLLs by stopping NinjaTrader, copying DLL/PDB to
  `C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom`, then restarting
  NinjaTrader.
- Do not use phase-1 or phase-2 optimizer rows as final deploy candidates.
  Final fixed Backtest templates are generated only after phase 3.
- Final Backtest templates must not contain `OptimizerType`,
  `OptimizationFitness`, or `OptimizationParameters`.
- Final Backtest templates and returned `Settings.csv` files must force:
  `UseTrend=false` and `UseTrendReverse=false`.

## Build And Deploy Optimizer DLL

From `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject`:

```powershell
.\tools\Build-WithLearningLog.ps1
.\tools\Deploy-Optimizer.ps1
```

Expected build result:

```text
Build succeeded.
0 Warning(s)
0 Error(s)
```

The deploy script stops NinjaTrader, copies:

```text
NinjaTraderOptimizerProject.dll
NinjaTraderOptimizerProject.pdb
```

to:

```text
C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom
```

and restarts NinjaTrader.

## Phase-Specific Optimizer Contract

The generated optimizer templates keep only true swept controls in
`<OptimizationParameters>`.

Expected parameter sets:

- Phase 1 broad discovery: `DurationTimeH`, `averageSlow`, `MaxStop`, `MaxTPRatio`.
- Phase 2 refinement: `averageFast`, `averageSlow`, `MaxStop`, `MaxTPRatio`, `Long`, `Short`.
- Phase 3 daily risk: `ProfitStop`, `LossStop`, `MaxTrades`.

Fixed parameters remain in the strategy body and should load as fixed values in
NinjaTrader.

## Phase 1: Custom Optimizer Smoke

From `D:\Backup\projects\PythonProject\ta_foundation`:

```powershell
python -m ta_foundation.optimization.template_generator `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeFirstRunBreakout.xml" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase1_nt_templates" `
  --start-hours "0" `
  --modes "breakout" `
  --optimizer-type "NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer" `
  --optimization-fitness "NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness"
```

Run the generated template folder through the NinjaTrader batch AddOn. Use an
output folder similar to:

```text
D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase1_nt_output
```

Expected proof:

- One `*_Optimization.csv` export.
- 500 parser-clean optimization rows if using `PopulationSize=50` and
  `Generations=10`.
- NinjaTrader Output tab or temp log includes `Starting coverage-search run`.

Useful log:

```text
C:\Users\Owner\AppData\Local\Temp\nt8_custom_optimizer.log
```

## Phase 2: Generate Refinement Templates

Use phase-1 NinjaTrader output as the optimization CSV directory:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase phase2 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizeSecondRunBreakout.xml" `
  --optimization-csv-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase1_nt_output" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase2_from_nt" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

Run:

```text
D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase2_from_nt\generated_phase2_templates
```

through NinjaTrader. A proven phase-2 batch produced 8,000 parser-clean rows
across 8 templates.

## Phase 3: Generate Daily-Risk Templates

Use phase-2 NinjaTrader output as the optimization CSV directory:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase phase3 `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\docs\samples\OptimizethirdRunBreakout.xml" `
  --optimization-csv-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase2_nt_output" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase3_from_nt" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5
```

Run:

```text
D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_phase3_from_nt\generated_phase3_templates
```

through NinjaTrader. A proven phase-3 batch produced 640 parser-clean rows
across 8 templates.

## Final Fixed Backtest Templates

Use phase-3 NinjaTrader output as the optimization CSV directory:

```powershell
python -m ta_foundation.optimization.grid_workflow `
  --target-phase final `
  --seed-template "D:\Backup\projects\PythonProject\ta_foundation\src\ta_foundation\strategies\PantheonMasterBotV01TesterV2\templates\sampleTemplate.xml" `
  --optimization-csv-dir "D:\nt_phase3_out" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_final_from_nt" `
  --count 8 `
  --max-drawdown 2500 `
  --min-trades 10 `
  --min-profit-factor 1.5 `
  --from-date 2026-04-14 `
  --to-date 2026-05-14
```

Template folder:

```text
D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_final_from_nt\named_backtest_templates
```

Before running final Backtests, verify the final template contract:

```powershell
rg -n "<UseTrend>true</UseTrend>|<UseTrendReverse>true</UseTrendReverse>|<OptimizerType>|<OptimizationFitness>|<OptimizationParameters>" "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_final_from_nt\named_backtest_templates"
```

Expected: no matches.

Run the final fixed templates through NinjaTrader Backtest mode. Proven final
output folder:

```text
D:\nt_final_backtest_out
```

## Final Review

From `D:\Backup\projects\PythonProject\ta_foundation`:

```powershell
python -m ta_foundation.optimization.review `
  --input-dir "D:\nt_final_backtest_out" `
  --output-dir "D:\Backup\projects\PythonProject\ta_foundation\output\custom_optimizer_smoke_final_review"
```

Expected manifest:

```text
validation_status = valid
candidate_count = 8
passed_count = 8
settings_contract_violation_count = 0
```

Expected `settings_contract_violations.csv` content: header only.

Every returned final `Settings.csv` must include:

```text
UseTrend,False
UseTrendReverse,False
```

## Focused Verification

```powershell
python -m pytest src/ta_foundation/tests/optimization -q
```

Expected:

```text
26 passed
```

## Known Good Final Candidates

The last proven run produced these top final candidates:

| Rank | Candidate | Net | PF | DD | Trades |
|---:|---|---:|---:|---:|---:|
| 1 | `01_Regression_PantheonMasterBotV01TesterV2` | `$19,555` | `99` | `$0` | `11` |
| 2 | `03_Breakout_PantheonMasterBotV01TesterV2` | `$9,785` | `7.06` | `$735` | `15` |
| 3 | `07_Regression_PantheonMasterBotV01TesterV2` | `$10,770` | `49.95` | `$95` | `12` |

## Troubleshooting

If the optimizer DLL fails to copy during build, NinjaTrader is probably
holding the DLL open. Run:

```powershell
.\tools\Deploy-Optimizer.ps1
```

If NinjaTrader runs but no optimization CSV is exported, check:

```text
C:\Users\Owner\AppData\Local\Temp\nt8_custom_optimizer.log
C:\Users\Owner\AppData\Local\Temp\nt8_batch_optimizer_loader.log
```

If final review reports settings contract violations, discard that final run,
regenerate fixed Backtest templates, confirm the `rg` contract check returns no
matches, and rerun final Backtests.
