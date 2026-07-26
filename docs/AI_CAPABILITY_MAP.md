# AI Capability Map

This repo is not one monolithic report generator. It has several distinct product capabilities that share parsers, market data, report rendering, and web job infrastructure.

Use this map before changing web UI, RAG, docs, CLI orchestration, or AI-facing prompts.

For analysis-specific routing, especially questions about NinjaTrader backtest
analysis, tick data, MAE/MFE, exit simulation, MA anchors, large candle
excursion, or template-quality features, read
`docs/ANALYSIS_CAPABILITY_GUIDE.md` before proposing new code. That guide is
the concrete inventory of what analysis already exists and how to use it.

Canonical local market data lives in `D:\MarketData` and includes NinjaTrader
minute candle exports (`*.Last.txt`) plus tick exports (`*Tick.Last.txt`) for
multiple contracts. Treat that folder as the first local source for candle,
tick-path, MA-anchor, exit-simulation, large-candle, pattern, and prediction
analysis before asking whether the project has market data.

For NinjaTrader optimizer behavior, Strategy Analyzer result interpretation,
local NT docs RAG, `OptimizationFitness`, `KeepBestResults`, template XML, or
"best template" ranking, read
`docs/runbooks/nt_optimizer_evidence_and_rag_guide.md`. It explains why raw
PF-first results can produce misleading one-trade winners and how to combine
NT evidence with local candle/tick analysis.

## Main Capabilities

| Capability | Primary User Goal | Uses NinjaTrader Backtest Exports? | Uses Market Data? | Primary Entry Points | UI Surface |
|---|---|---:|---:|---|---|
| Backtest report generation | Load exported NinjaTrader strategy runs and render HTML reports | Yes | Optional, depending on selected YAML blocks/sections | `python -m ta_foundation.cli.main` | Backtest Reports tab |
| Strategy discovery reports | Run ranking, validation, entry/filter/exit discovery, and NT template report sections | Usually yes for run ranking; can also use market-discovery signal corpus | Yes | `ta_foundation.cli.main` with `strategy_discovery:` YAML | Strategy Discovery tab |
| Prediction and horizon system | Run daily/horizon prediction jobs, score outcomes, build horizon stores and prediction reports | No | Yes | `ta_foundation.prediction.run_prediction`, `run_multi_agent`, `backtest_horizon_predictions` | Prediction tab |
| Strategy template building | Generate/edit a structured strategy template and run local template backtests | No direct report-package ingest | Yes, for template backtest bars | `analysis.strategy_composer.*`, web `/api/generate`, `/api/backtest`, `/api/validate` | Strategy Templates tab |
| Agentic NT strategy loop | Promote research candidates into deterministic NinjaTrader validation, optimizer runs, shadow monitoring, and supervised execution handoff | Yes, through optimizer/Strategy Analyzer evidence | Yes | `src/ta_foundation/agent/`, `src/ta_foundation/nt_strategy_loop/`, `docs/designs/agentic_nt_strategy_knowledge_base.md`, `docs/designs/autonomous_research_to_paper_trade_loop_build_plan.md`, external `D:\NinjaAccountManager` | Manager/operator workflow |
| Execution bridge | Send/monitor execution messages for NinjaTrader shell/runtime integration | No | Runtime shell/state/log data | `strategies/TaFoundationExecutionBridge/*`, `cli/bridge_operator.py`, external `D:\NinjaAccountManager` | Separate operator/tooling surface |
| Market data dashboard | Inspect market data file freshness and availability | No | Yes | `market_data_dashboard.py` | Separate dashboard |

## Analysis Capability Inventory

The broad map above intentionally stays compact. The detailed analysis surface
is documented in `docs/ANALYSIS_CAPABILITY_GUIDE.md`, including:

- NT backtest export analysis from trades, daily, summary, settings, and
  optimization CSVs.
- Tick data ingest, tick cache, minute-vs-tick diagnostics, tick-derived bars,
  exit-policy simulation, and large-candle tick-path analysis.
- Strategy discovery outputs: MAE/MFE, walk-forward, OOS/holdout evaluation,
  slippage stress, entry/filter/exit discovery, ranking, clustering, and NT
  template generation.
- MA anchor interaction TP/SL recommendations and trade alignment.
- Pattern engine and entry-strategy families.
- Large candle excursion event studies, target curves, reversal decision
  engine, validation, strategy construction, and blueprints; plus
  strategy-parity adaptive time-window analysis with fixed-bracket walk-forward
  replay (`analysis.large_candle_excursion.adaptive_window`).
- Optimizer final-template reports, weekly coverage package, deployment matrix
  manifest, and template-quality feature export.

## Backtest Reports

The normal report workflow has two configuration layers:

1. CLI parameters: filesystem/runtime inputs to `ta_foundation.cli.main`.
   Examples: `--input`, `--output`, `--market-data`, `--recursive`, `--include-run-images`, `--export-exec-cards-png`, `--no-tick-data`.
2. YAML parameters: report behavior and analysis configuration.
   Examples: `sections:`, section `options:`, `strategy_discovery:`, `pattern_engine:`, `anchor_interaction:`.

Keep this separation in UI and code. Do not move report behavior into CLI flags. New report behavior belongs in YAML and section options.

Example CLI shape:

```bash
python -m ta_foundation.cli.main \
  --input "C:/Users/Owner/Downloads/B99" \
  --output ./outputs5-1 \
  --report-config ./report.yaml \
  --include-run-images \
  --export-exec-cards-png \
  --market-data "D:/MarketData" \
  --recursive
```

The web report UI should let users select the base CLI parameters and a report preset, then show editable YAML parameters with descriptions before saving and running the job.

## Prediction

Prediction is its own capability under `src/ta_foundation/prediction`. It does not depend on `AnalysisPackage` backtest runs. It loads market data and prediction YAML, then persists predictions/outcomes to prediction stores.

Reuse these entry points:

- `python -m ta_foundation.prediction.run_prediction --config <prediction.yaml>`
- `python -m ta_foundation.prediction.run_multi_agent --config <multi_agent.yaml>`
- `python -m ta_foundation.prediction.backtest_horizon_predictions --minute-bars-file <file> --store-dir <dir>`

Prediction YAML controls agent/operator behavior. The web app may expose common fields, but should not duplicate prediction agent logic.

## Strategy Template Builder

The strategy template builder is the LLM-assisted workflow in the web app and `analysis/strategy_composer`. It produces structured strategy templates and can run local template backtests against loaded bars. It is not the same as report generation and should not be mixed into the report-builder UI except through shared job/status components.

## Strategy Discovery

`analysis/strategy_discovery` is a major analysis capability. In the current CLI flow it is enabled through `strategy_discovery:` YAML and rendered through `strategy_discovery_*` report sections.

Important distinction:

- Run-ranking and validation use ingested backtest packages.
- Market-discovery signal corpus behavior can use market data and pattern-engine artifacts to find signal rules beyond executed trades.

Use the existing CLI/report orchestration. Do not duplicate strategy discovery internals in Flask.

## Web UI Direction

The web app should expose main capabilities as separate navigation areas:

- Backtest Reports
- Prediction
- Strategy Templates
- Strategy Discovery
- System Map / capabilities

Shared infrastructure is acceptable:

- background job dispatch/status
- command construction
- YAML save/validation
- generated artifact links

But keep capability semantics separate in the UI and docs so users and AI agents do not confuse report generation with prediction, template composition, or discovery engines.

## AI/RAG Guidance

When a user asks about “the web app,” first determine which capability tab/surface they mean.

Search terms that usually route correctly:

- Backtest reports: `cli main report yaml sections NinjaTrader exports`
- Prediction: `prediction run_prediction horizon config agent store`
- Strategy templates: `strategy_composer template generate backtest validate`
- Strategy discovery: `strategy_discovery orchestrator report yaml entry filter exit validation`
- Agentic NT strategy loop: `agentic nt strategy loop Strategy Factory StrategyDiscoveryFilter optimizer bridge shadow execution NinjaAccountManager`
- Web orchestration: `web app report_builder report_catalog jobs prediction_jobs`

