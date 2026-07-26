# NT Optimizer Evidence And RAG Guide

Read this before changing NinjaTrader optimizer templates, Deployment Matrix
selection, Strategy Analyzer XML, custom optimization fitness, or any "best
template" ranking. This guide exists because the optimizer can return rows that
look excellent by one metric while being useless as deployable templates.

## First Sources

Use these local sources before asking the user where NinjaTrader docs or
optimizer behavior live:

| Source | What It Answers |
|---|---|
| `D:\Backup\projects\PythonProject\NinjatraderDocScrapper` | Local NinjaTrader docs RAG, Strategy Factory, NinjaScript generation, compile repair, template-generation guidance |
| `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\README.md` | RAG build/search/generation commands and project overview |
| `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\docs\PROJECT_HANDOFF.md` | What the RAG/factory currently contains |
| `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\LLM_DOCUMENTATION_GUIDE.md` | LLM rules for using retrieved NT docs, especially optimization docs |
| `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\strategy_factory\modules\templates\LLM_TEMPLATE_CREATION_GUIDE.md` | Strategy Analyzer template XML structure, including `KeepBestResults` and `OptimizationFitness` |
| `D:\ninjatraderOptimizer\PROJECT_STATUS.md` | Local AddOn behavior, version sensitivity, Strategy Analyzer automation quirks |
| `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject` | Standalone custom optimizer and custom optimization-fitness project; this is the active home for optimizer/fitness code |
| `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject\NinjaTraderOptimizerProject\OptimizationFitnesses\CustomMultiObjectiveFitness.cs` | Current buildable custom fitness implementation |
| `D:\ninjatraderOptimizer\NinjaTraderOptimizerProject\NinjaTraderOptimizerProject\Optimizers\CustomMultiObjectiveOptimizer.cs` | Current buildable coverage-search optimizer implementation |
| `D:\ninjatraderOptimizer\NinjaTraderAddOnProject\OptimizationFitnesses\CustomMultiObjectiveFitness.cs` | Older on-disk AddOn copy; do not treat as the active compiled source unless the project files prove it is compiled |
| `D:\ninjatraderOptimizer\NinjaTraderAddOnProject\Optimizers\CustomMultiObjectiveOptimizer.cs` | Older on-disk AddOn copy; the standalone optimizer project should own these classes to avoid duplicate NinjaScript types |
| `docs/designs/optimizer_known_issues.md` | Optimizer quirks, resolved failures, cancellation and export caveats |
| `docs/runbooks/NINJATRADER_INTEGRATION_RUNBOOK.md` | ta_foundation to NT control loop and IPC files |
| `docs/runbooks/deployment_matrix_technical_guide.md` | How staged optimization becomes the 252-cell deployment manifest |
| `docs/designs/ma_pool_enrichment_and_pantheonmaster_migration.md` | Current plan for using exit simulation, regime fit, and manifest features to improve MA-cross pools |
| `docs/designs/nt_fitness_parameter_control_plan.md` | Implementation plan for controlling custom optimization-fitness plugin properties from the web optimizer |
| `docs/designs/deployment_matrix_optimization_redesign.md` | Current experiment ladder for matrix quality, MA-cross scout, validators, and custom-fitness A/B testing |

Official NinjaTrader anchors:

- Strategy Analyzer Optimization:
  `https://ninjatrader.com/it/support/helpGuides/nt8/optimize_a_strategy.htm`
- Optimization Fitness Metrics:
  `https://ninjatrader.com/support/helpguides/nt8/optimization_fitness_metrics.htm`

## Local RAG Commands

From `D:\Backup\projects\PythonProject\NinjatraderDocScrapper`:

```powershell
.\.venv\Scripts\python.exe .\build_ollama_index.py `
  --chunks .\ninjatrader_docs\chunks.jsonl `
  --db .\ninjatrader_docs\rag_index.sqlite `
  --embed-model nomic-embed-text `
  --reset

.\.venv\Scripts\python.exe .\generate_ninjascript.py `
  --db .\ninjatrader_docs\rag_index.sqlite `
  --model qwen3-coder:30b `
  --embed-model nomic-embed-text `
  --task "Explain the NT8 Strategy Analyzer OptimizationFitness and KeepBestResults behavior for template ranking."

.\.venv\Scripts\python.exe .\chat_ninjascript.py `
  --db .\ninjatrader_docs\rag_index.sqlite
```

The canonical local RAG artifacts are:

- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\ninjatrader_docs\chunks.jsonl`
- `D:\Backup\projects\PythonProject\NinjatraderDocScrapper\ninjatrader_docs\rag_index.sqlite`

For optimization or template XML work, prioritize retrieved docs under
References Optimizer and References Optimization Fitness, then cross-check
against the template creation guide above.

## Custom Optimizer And Fitness State

The custom optimizer/fitness work is real, but easy to miss because it lives
outside this repo.

Active source of truth:

```text
D:\ninjatraderOptimizer\NinjaTraderOptimizerProject
```

The standalone project exists to avoid duplicate NinjaScript class definitions
between the batch AddOn and custom optimizer assembly. Its status docs say the
main AddOn project no longer compiles the optimizer/fitness classes; the AddOn
files remain on disk but should be treated as historical unless project files
prove otherwise.

Current active fitness:

```text
NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness
```

What it currently does:

- exposes `Min Trades` with default `30`
- exposes `Max Drawdown %` with default `20.0`
- returns `Value = 0` below the min-trade gate
- returns `Value = 0` above the drawdown gate
- records net profit as `Objective1`
- records finite profit factor as `Objective2`
- still ranks with `Value = Objective2`, so the surviving rows are still
  profit-factor-ranked

Current active optimizer:

```text
NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer
```

What it currently does:

- samples parameter ranges with a Halton-style coverage sequence
- tracks duplicate parameter signatures
- short-circuits to exhaustive enumeration when the whole parameter space fits
  inside the configured iteration budget
- logs unique and duplicate parameter-set counts

This is useful, but it is not yet a full "best template" fitness. It solves
some of the PF-one-trade problem through hard gates. It does not yet rank by
daily consistency, holdout retention, MAE/MFE shape, giveback, exit robustness,
or parameter-neighborhood stability. Those are still best handled by the
ta_foundation post-export validators unless the NT API access is proven in the
optimizer project's RAG learning log.

To control fitness plugin settings from the web optimizer, use
`docs/designs/nt_fitness_parameter_control_plan.md`. That plan defines the
proposed `<OptimizationFitnessParameters>` XML block, recipe schema changes,
AddOn hook, UI controls, and smoke tests.

## Optimizer Evidence Traps

These are not theoretical. They have already caused confusing or misleading
results in this project.

1. `Performance` is not a universal score. In NT Strategy Analyzer results it
   is the selected `OptimizationFitness`. If the template uses Max Profit
   Factor, the displayed best rows are best by profit factor.
2. `KeepBestResults` limits the displayed or retained top rows. If it is too
   low and the fitness is PF-first, robust candidates can be hidden behind
   small-sample outliers.
3. PF 99 on one trade is not edge. Treat one-trade and tiny-sample rows as
   scouting evidence only unless they survive min-trades, min-days, drawdown,
   holdout, and robustness gates.
4. Optimization result rows are retained evidence, not the complete universe.
   The parser sees what NT exported, which is controlled by optimizer settings.
5. In 32-bit NT, trade data is not stored for each optimization backtest. In
   64-bit NT, trade results are kept for kept backtests. Either way, a low
   `KeepBestResults` setting can limit which rows have convenient trade-level
   follow-up evidence.
6. The local custom optimizer had a duplicate-replay failure mode where repeated
   parameter sets could over-represent the apparent best rows. The local AddOn
   project fixed this with exhaustive enumeration when the Cartesian product is
   smaller than the requested iteration count, but future agents should know
   the failure pattern.
7. Native Strategy Analyzer grid export was found unreliable in the local AddOn
   project. Trust the current result-object exports and `BatchRunSummary.csv`
   lineage bridge over raw grid scraping.
8. NT and Windows path shortening can truncate output folder names. Use
   `BatchRunSummary.csv` to map truncated folder basenames back to full
   template names before grouping or selecting candidates.
9. A cancel request stops the next template, not necessarily the current
   in-flight Strategy Analyzer job. Do not assume a cancel is immediate.
10. A full Deployment Matrix grid can contain fallbacks. `covered`,
   `fallback`, and `missing` statuses must be read separately; fallback-filled
   cells are not the same as directly proven winners.

## What MA-Cross Backtests Tell You

An open-ended MA-cross backtest with no stop or target is useful because each
cross becomes a market-movement measurement:

- MFE says how far the market moved in favor after the cross.
- MAE says how much adverse movement had to be tolerated.
- ETD/giveback says how much of the favorable move was lost before exit.
- Cross timing shows when signals cluster, chop, or align with session regimes.
- Slow-MA sweeps show which structural anchors are responsive enough to catch
  movement without overreacting to noise.

For example, a 5-fast / 100-slow long cross can tell you that at that time of
day and regime, the market commonly moved a certain number of ticks before
giving back. That is directly useful for estimating stop distance, profit
target, trailing behavior, time stop, and whether a session/side should be
eligible for optimization at all.

The caution: this is entry-event geometry, not final deployability. A strong
MFE profile can still fail if adverse excursion is too large, giveback is high,
the signal only appears in one market month, or the final NT strategy cannot
execute the same assumptions.

## Best-Template Selection Stack

Use NT as an evidence generator, not as the final judge.

1. Scout in Python first when possible.
   Use `D:\MarketData` minute and tick exports to measure MA-cross timing,
   forward excursion, chop, session behavior, large-candle context,
   regime/classifier labels, and exit-policy simulations before spending NT
   time on broad sweeps.
2. Run NT optimizations with enough retained rows.
   Use multiple objectives or multiple lanes, set `KeepBestResults` high enough
   to preserve alternatives, and set a real min-trades floor when the goal is
   deployable templates rather than raw scouting.
3. Preserve lineage.
   Keep the selected CSVs, rejected CSVs, coverage lanes, batch summary, XML
   template names, and final backtest handoff together. If candidate IDs or
   parent names are lost, selection quality degrades quickly.
4. Final-rank with gates before scores.
   First exclude rows that fail trade count, days traded, drawdown, direction,
   data availability, or final backtest requirements. Then score the survivors.
5. Prefer robust plateaus over isolated peaks.
   A template surrounded by nearby working parameter values is usually more
   valuable than a single PF spike.
6. Treat holdout retention as a first-class signal.
   A template that keeps acceptable rank on a different month/session/contract
   deserves more trust than one that only wins the optimization window.
7. Feed the predictor a feature-rich manifest.
   Final templates should carry quality features such as MAE/MFE ratios,
   giveback, daily consistency, true max loss, effective trade count, exit
   robustness, market-data coverage, and fallback status.

## Practical Ranking Shape

For deployable pools, avoid a raw sort like "PF descending". Use this shape:

```text
eligible =
  total_trades >= floor
  and days_traded >= floor
  and max_drawdown <= cap
  and total_net_profit > 0
  and final_backtest_exists

score =
  weighted net profit
  + weighted profit factor after sample-size cap
  + trade-count confidence
  + daily consistency
  + holdout rank retention
  + parameter-neighborhood stability
  + favorable MAE/MFE and giveback profile
  + exit-policy robustness on ticks
  - drawdown and true max-loss penalties
  - fallback-cell penalty
```

For scouting, allow weak rows through but label them as hypotheses. For final
template grids, demand evidence.

## Where Current Code Implements Pieces

| Evidence Piece | Current Code |
|---|---|
| NT optimization CSV ingest | `src/ta_foundation/parsers/ninjatrader/optimization_csv.py` |
| Recipe selection and hard filters | `src/ta_foundation/web/optimizer_recipe_selection.py` |
| Batch summary lineage bridge | `src/ta_foundation/web/optimizer_recipe_results.py` |
| Deployment Matrix manifest and fallback status | `src/ta_foundation/web/optimizer_deployment_matrix_manifest.py` |
| Session manifest from final rows | `src/ta_foundation/web/optimizer_deployment_matrix_session.py` |
| Template quality feature export | `src/ta_foundation/web/optimizer_template_quality_features.py` |
| Exit-policy simulation | `src/ta_foundation/analysis/exits/simulate.py` |
| MA anchor analysis | `src/ta_foundation/analysis/ma_structure/` |
| Large candle excursion | `src/ta_foundation/analysis/large_candle_excursion/` |

Known current gap: `optimizer_template_quality_features.load_market_for_session`
uses an explicitly configured session market-data folder. It does not
automatically scan `D:\MarketData`. If a session has no staged market-data
folder, agents should check whether the market-data path needs to be configured
before assuming tick/candle features are unavailable.
