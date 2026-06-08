# Analysis Capability Guide

**Purpose:** make the implemented analysis surface visible to AI agents and
operators before anyone proposes a new analyzer, report, template-quality
feature, tick-data tool, or NinjaTrader backtest post-processor.

**Read this when the question sounds like:**

- "What analysis has already been done on NT backtests?"
- "What do the NT backtest exports tell us?"
- "What exists for tick data, MAE/MFE, exits, anchors, regimes, or templates?"
- "Can we add an analysis/report/predictor feature?"
- "Claude/Codex wants to build something; does it already exist?"

This document is intentionally more concrete than the broad system maps. It
names the code, config blocks, report sections, outputs, and interpretation
rules that future chats should route through first.

## First Rule For AI Agents

Do not create a new analysis module until you have checked these existing
surfaces:

1. `docs/CAPABILITY_CATALOG.md`
2. `docs/AI_CAPABILITY_MAP.md`
3. this file
4. `docs/AI_REPO_INDEX.md`
5. `docs/reference/REPORT_SECTIONS_CATALOG.md`
6. the current code under the paths named below

Docs in `docs/audits/` are point-in-time snapshots. They are useful history,
but they are not the current capability surface unless re-verified against
code. The old `docs/audits/DOCUMENTATION_GAP_ANALYSIS.md` correctly identified
discoverability as a problem, but it predates several current docs and is too
generic for routing implementation work.

## Current Verified Snapshot

As of 2026-06-05, a live import of `SECTION_REGISTRY` reports:

- 129 importable HTML report section keys.
- 26 `strategy_discovery*` section keys.
- 45 `large_candle_excursion*` section keys.
- 8 anchor-related keys, including `strategy_discovery_anchor_confluence`.
- 6 pattern/trade-pattern keys.
- 3 exit-policy keys.
- `tick_data_diagnostics`, `optimization_overview`, and `horizon_overview`.
- 5 report presets in `web/report_catalog.py`: weekly prop dashboard,
  executive cards, core comparison, strategy discovery full, horizon overview.

The June 5 audit notes a duplicate `anchor_interaction_overview` registry key;
that is why older docs may say 130 sections while a Python dict import returns
129 keys.

## Mental Model

The project is not one report generator. Analysis runs through four data lanes:

| Lane | What it consumes | Where results live | Typical output |
|---|---|---|---|
| NT backtest run analysis | `*_Trades.csv`, `*_Analysis.csv`, `*_Summary.csv`, `*_Settings.csv`, optional run images | `AnalysisPackage`, `pkg.metadata["derived"]`, report HTML | KPIs, daily outcomes, drawdown, trade timing, settings, cards |
| NT optimizer analysis | `*_Optimization.csv` | `OptimizationStore`, not `AnalysisPackage` | parameter-combo leaderboard, parse quality, metric distributions |
| Market/tick-path analysis | `*.Last.txt`, `*Tick.Last.txt`, tick parquet cache | `MarketDataStore` shared by all packages | bars, tick streams, exit simulations, overlays, diagnostics |
| Discovery/predictor analysis | backtests plus market data, or market data only for some signal corpora | `pkg.metadata["derived"]`, `pkg.assets`, `.ta_artifacts`, manifests | ranked candidates, IS/OOS, MAE/MFE, templates, prediction/horizon reports |

Important contract: optimization files are deliberately routed to
`OptimizationStore`. They do not create or update an `AnalysisPackage`.

## NT Backtest Export Ingest

The CLI entry point is:

```bash
python -m ta_foundation.cli.main \
  --input "C:/path/to/ninjatrader/exports" \
  --output ./outputs \
  --report-config ./report.yaml \
  --market-data "D:/MarketData" \
  --recursive
```

The canonical local market-data root is `D:\MarketData`. It contains
NinjaTrader minute candle exports (`*.Last.txt`) and tick exports
(`*Tick.Last.txt`) that feed `MarketDataStore` for candle, tick-path,
MA-anchor, exit-simulation, large-candle, pattern, and prediction analysis.

Use `--no-tick-data` for faster minute-bar-only workflows such as LCR, candle,
MA, BB, and many discovery passes.

| Export | Parser | Data lane | What it tells you |
|---|---|---|---|
| `*_Trades.csv` or `Trades.csv` | `parsers/ninjatrader/trades_csv.py` | run package | trade-level PnL, entry/exit time and price, direction, MAE/MFE/ETD when exported |
| `*_Analysis.csv` or `Analysis.csv` | `analysis_by_day_csv.py` | run package | daily Strategy Analyzer outcomes, used by daily/weekly reports |
| summary CSV | `summary_csv.py` | run package | normalized KPI blocks in `SummaryBlock` |
| settings CSV | `settings_csv.py` | run package | strategy parameters, used by settings reports and anchor extraction |
| `*_Optimization.csv` | `optimization_csv.py` | `OptimizationStore` | one row per parameter combo, `param_*` columns, parse quality, metric columns |
| `*.Last.txt` | `minute_bars_last_txt.py` | shared market data | canonical 1-minute OHLCV bars |
| `*Tick.Last.txt` | `tick_last_txt.py` | shared market data | tick stream with `dt` and `last`, used for tick-path simulation |

Core assembly lives in `core/pipeline.py`. It derives run ids, attaches run
assets, computes daily outcomes, derives trade-time profiles, routes shared
market data into `MarketDataStore`, and routes optimizer artifacts into
`OptimizationStore`.

## NT Backtest Analysis Inventory

### Core Run Analytics

Use these before building any generic "backtest analyzer."

| Capability | Code | Report sections | What it tells you | How to use it |
|---|---|---|---|---|
| KPI and run metadata | `core/model.py`, `core/derived_metrics.py`, `utils/kpi.py` | `run_kpi_cards`, `run_metadata_cards`, `run_snapshot_clipboard` | normalized profit, PF, win rate, drawdown, configured run identity | include run sections in `sections:` |
| Daily outcomes | `core/daily_outcomes.py` | `daily_scoreboard`, `daily_leaderboard_cards`, `daily_winner_spotlight`, `weekly_leaderboard_cards` | which days were green/red/no-trade, daily PnL, recent progress | use daily/weekly report sections or weekly package reports |
| Drawdown | `analysis/drawdown.py`, `core/derived_metrics.py` | `drawdown_curve`, `apex_drawdown_survival_profile` | drawdown curve, recovery, prop-account survival view | include drawdown/APEX sections |
| Trade time profile | `core/market_time_profile.py` | `trades_intraday_pnl_by_day`, session/momentum boards | hour/session concentration, PnL by time bucket | default derived metric; render relevant sections |
| Settings and parameter display | `parsers/ninjatrader/settings_csv.py`, `reports/html/sections/run_settings_table.py` | `run_settings_table`, `strategy_parameter_matrix`, `recipe_parameter_trend` | actual parameters used by the run | include settings/parameter sections |
| Executive/profile cards | `reports/html/sections/run_executive_profile_cards.py`, `reports/html/export_cards.py` | `run_executive_profile_cards`, `run_card_catalog` | team-facing concise strategy card | use `executive_cards_report` preset or candidate report builder |

### Optimization CSV Analysis

Use this before writing a new optimizer-result parser or leaderboard.

| Capability | Code | Report section | What it tells you |
|---|---|---|---|
| Parse Strategy Analyzer optimization grids | `parsers/ninjatrader/optimization_csv.py` | `optimization_overview` | row count, parse failures, detected parameters, metric distributions |
| Parameter extraction | `parse_parameters()` in `optimization_csv.py` | `optimization_overview` | normalized `param_<Name>` columns, including parameter names with parentheses |
| Leaderboards | `reports/html/sections/optimization_overview.py` | `optimization_overview` | top rows by net profit, PF, lowest drawdown with trade guards |

Because optimization batches are not run packages, report sections that expect
`pkg.trades` will not see optimizer rows. Use `ctx["optimization_store"]` and
`optimization_overview`.

### Strategy Discovery Over NT Backtests

The main entry is the top-level YAML block:

```yaml
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "06-26"
  timeframe: "5m"
  tick_size: 0.25
  cost_model:
    commission_per_side: 2.09
    slippage_ticks: 1
    tick_value: 5.00
```

Implementation: `analysis/strategy_discovery/orchestrator.py`.

Do not trust only the orchestrator's opening docstring; it is behind the code.
The current body already wires:

- Regime labeling and daily regime summaries.
- Locked holdout partitioning.
- Entry-search vs exit-search dev split to reduce co-fitting.
- MAE/MFE profile from NT trade exports.
- Walk-forward validation and OOS pool evaluation.
- Locked holdout evaluation.
- Slippage and latency stress.
- Exit discovery.
- Feature matrix creation.
- Pattern-engine signal feature bridge.
- Session risk summary.
- Candidate scorecard.
- Signal entry discovery.
- Feature importance.
- Classification.
- Entry discovery.
- Filter discovery.
- Position sizing.
- Cohort analysis.
- Drawdown analysis.
- Risk metrics.
- Pure market-data discovery.
- Parameter sensitivity.
- Cross-run ranking and clustering.
- Portfolio combo basket selection.
- Market corpus signal-entry discovery for synthetic `__market_discovery__`
  packages.

Key report sections:

- `strategy_discovery_ranked_table`
- `strategy_discovery_validation`
- `strategy_discovery_mae_mfe`
- `strategy_discovery_evaluation`
- `strategy_discovery_risk_metrics`
- `strategy_discovery_drawdown`
- `strategy_discovery_entry_rules`
- `strategy_discovery_filter_rules`
- `strategy_discovery_exit_policies`
- `strategy_discovery_feature_importance`
- `strategy_discovery_parameter_sensitivity`
- `strategy_discovery_signal_entries`
- `strategy_discovery_signal_exit_sweep`
- `strategy_discovery_signal_validation`
- `strategy_discovery_nt_template`
- `strategy_discovery_combo_basket`

Use the report preset `strategy_discovery_full` in `web/report_catalog.py` or
the repo-root `strategy_discovery_report.yaml` for a comprehensive config.

What it tells you:

- Whether a candidate survives costs, rolling walk-forward, OOS, and holdout.
- Whether performance is concentrated in one regime/session/time slice.
- Which entry/filter/exit rules explain the edge.
- Whether MAE/MFE supports proposed stops and targets.
- Which signals from a market corpus can become entry candidates.
- Which NT template parameters can be generated from the evidence.

### MAE/MFE Analysis

Use this before creating a stop/target recommender.

| Capability | Code | Output |
|---|---|---|
| NT exported MAE/MFE profile | `analysis/strategy_discovery/mae_mfe.py` | `mae_coverage`, `mfe_coverage`, percentile distributions, exit bounds |
| Exit discovery bounds | `analysis/strategy_discovery/exit_discovery.py` | stop/target and policy candidates derived from MAE/MFE |
| NT template stop/target mapping | `analysis/strategy_discovery/nt_template_generator.py`, `pantheon_master_template.py` | stop and target ticks, with warnings and reasons |
| Predictor manifest template quality | `web/optimizer_template_quality_features.py` | `mae_mfe_ratio_median`, `mfe_giveback_median` |

Interpretation:

- MAE is risk demand. Winner MAE percentiles are useful stop guides.
- MFE is opportunity. Median or percentile MFE is a target/runner guide.
- MFE giveback is how much open profit tends to be surrendered.
- Coverage fields matter: if NT did not export MAE/MFE columns, the derived
  recommendations may fall back or be unavailable.

### Exit Policy Simulation

Use this before building a new trailing-stop or breakeven simulator.

| Capability | Code | Report sections | Requires ticks? |
|---|---|---|---|
| Policy definitions | `analysis/exits/policies.py` | rendered by exit sections | no |
| Tick-path simulation | `analysis/exits/simulate.py` | `exit_policy_simulation`, `exit_policy_trade_debug`, `exit_policy_simulation2` | yes for full tick-path |
| Report policy factory | `reports/html/sections/exit_policy_simulation.py` | `exit_policy_simulation` | yes |
| Template-quality feature export | `web/optimizer_template_quality_features.py` | manifest feature block | yes for exit robustness |

Implemented policies include fixed stop/target, ATR trail, trail-stop target,
breakeven ATR trail, chandelier ATR trail, time stop with no progress,
giveback after MFE, fixed-ATR-then-trail, and fixed-ATR-then-chandelier.

The simulator returns per-trade rows with policy, entry/exit, `pnl_ticks`,
`mae_ticks`, `mfe_ticks`, exit reason, ATR at entry, best favorable/worst
adverse ticks, breakeven arming diagnostics, and detail rows when inputs are
unavailable.

Interpretation:

- Compare net ticks by policy, not just win rate.
- `exit_rank_spread` in template-quality features shows how sensitive a
  template is to exit-policy choice.
- `exit_robustness_margin` compares the best simulated policy to current net.
- Diagnostic rows usually mean missing trade columns or missing ticks for the
  requested instrument/contract.

### Prop/Firm Risk Analysis

Use this before rebuilding APEX trailing drawdown math.

| Capability | Code | Report/consumer | What it tells you |
|---|---|---|---|
| APEX trailing model | `analysis/apex_trailing_model.py` | `apex_drawdown_survival_profile` | trailing drawdown survival for a run |
| Prop evaluation simulator | `analysis/prop_evaluation/simulation.py` | current audit marks shipped | trailing DD, daily loss, profit target, MC/stress |
| Portfolio MC | `analysis/apex_portfolio_mc.py` | analysis consumers | portfolio-level drawdown risk |
| Template risk features | `web/optimizer_template_quality_features.py` | deployment matrix manifest | true/effective max loss and effective trades |

## Tick Data And Market Data Inventory

### MarketDataStore

Implementation: `marketdata/store.py`.

It stores:

- `minute_bars[(instrument_root, contract)]`
- `ticks[(instrument_root, contract)]`
- derived `bars_cache[(instrument_root, contract, timeframe, source)]`

It can:

- fall back from a specific contract to merged contract `""`;
- merge all loaded contracts per instrument in `finalize()`;
- derive timeframe bars from minute bars or ticks;
- force tick-derived bars with `source="ticks"`;
- compare provided 1-minute bars against tick-derived 1-minute bars with
  `validate_minute_vs_ticks()`.

### Tick Cache

Implementation: `marketdata/tick_cache.py`.

It recognizes NinjaTrader tick files such as `NQ 03-26 Tick.Last.txt`, writes
parquet cache files when pyarrow is available, and can load tick cache parquet
even if the original text file is gone. The CLI skips tick loading entirely
when `--no-tick-data` is passed.

### Tick Report Sections

| Section | Code | What it tells you |
|---|---|---|
| `tick_data_diagnostics` | `reports/html/sections/tick_data_diagnostics.py` | which tick streams loaded, row counts, min/max timestamps, sample rows, minute-vs-tick-derived close/high/low diagnostics |
| `trade_candle_overlay` | `reports/html/sections/trade_candle_overlay.py` | trade entries and exits over market bars |
| `exit_policy_simulation` | `reports/html/sections/exit_policy_simulation.py` | tick-path policy performance across runs |
| `exit_policy_trade_debug` | `reports/html/sections/exit_policy_trade_debug.py` | why one trade exited under a policy |

### Large Candle Tick-Path Analysis

Implementation: `analysis/large_candle_excursion/tick_analyzer.py`.

For each qualifying large-candle event it traces tick prices inside the
forward window and appends:

- `tick_pre_reversal_fav_ticks`
- `tick_pre_reversal_adv_ticks`
- `tick_reversal_occurred`
- `tick_n_ticks_in_window`

Interpretation:

- Favorable pre-reversal excursion answers "how far did this move go before
  giving back by the configured reversal threshold?"
- Adverse pre-reversal excursion answers "how much pain happened before the
  first meaningful favorable reversal?"
- `tick_n_ticks_in_window` is a data-quality signal; low or zero counts mean
  the tick-path conclusion is weak or unavailable.

## Higher-Order Analysis Engines

### MA Anchor Interaction

Entry: `analysis/ma_structure/orchestrator.py` via top-level
`anchor_interaction:` YAML.

It attaches `pkg.metadata["derived"]["anchor_interaction"]` and artifact refs
for:

- `anchors`
- `segments`
- `segment_path_stats`
- `summary_by_anchor`
- `summary_by_anchor_regime`
- `tp_sl_candidates`
- `recommendations`
- `validation_folds`
- `trade_recommendation_alignment`
- `trade_time_candidates`
- `trade_time_per_trade`

Report sections:

- `anchor_interaction_config`
- `anchor_interaction_overview`
- `anchor_interaction_anchor_matrix`
- `anchor_interaction_tp_sl_spec`
- `anchor_interaction_diagnostics`
- `anchor_tp_sl_recommendations`
- `anchor_interaction_hourly_profile`
- `strategy_discovery_anchor_confluence`

What it tells you:

- Which MA anchors were detected or configured.
- How price path segments behave around those anchors.
- TP/SL candidates and fold validation.
- Structural stop/target recommendations.
- Whether actual trades align with the recommendations.
- Whether performance changes by anchor/regime/hour.

### Pattern Engine

Entry: `analysis/pattern_engine/engine.py` via top-level `pattern_engine:`
YAML. The CLI runs it before strategy discovery so discovery can consume
pattern-engine artifacts.

Code paths:

- `analysis/pattern_engine/engine.py`: sweeps templates.
- `analysis/pattern_engine/discovery.py`: market signal corpus, including
  forward returns and MAE/MFE in ticks.
- `analysis/pattern_engine/cluster.py`: clusters similar patterns.
- `analysis/pattern_engine/robustness_cv.py`: cross-validation robustness.
- `analysis/pattern_engine/trade_pattern_audit.py`: maps pattern matches to
  executed trades.
- `analysis/pattern_engine/monte_carlo.py`: currently an empty stub according
  to the June 5 audit; do not cite it as a working Monte Carlo engine unless
  it has been implemented.

Report sections:

- `pattern_engine_overview`
- `pattern_cluster_drilldown`
- `pattern_engine_diagnostics`
- `pattern_market_discovery`
- `pattern_engine_mc_regime`
- `trade_pattern_audit`

What it tells you:

- Which market patterns occurred.
- Which patterns match executed trades.
- Forward return, MAE, and MFE for signal corpora.
- Which pattern clusters are stable enough to bridge into strategy discovery.

### Entry Strategy Families

Entry: `analysis/entry_strategies/*`, invoked by `cli/main.py` through
top-level discovery blocks such as `candle_discovery:`, `ma_discovery:`,
`bb_discovery:`, `orb_discovery:`, `breakout_discovery:`,
`pullback_discovery:`, `level_discovery:`, `lcr_discovery:`, and
`premarket_discovery:`.

Families present in code:

- candle
- moving average
- Bollinger Band
- ORB
- breakout
- pullback
- level
- LCR
- premarket

The June 5 audit notes `premarket/` is incomplete compared with the others.
Verify before depending on that family.

Report sections include:

- `candle_discovery_overview`
- `candle_discovery_ranking`
- `ma_discovery_overview`
- `orb_discovery_overview`
- `bb_discovery_overview`
- `breakout_discovery_overview`
- `pullback_discovery_overview`
- `level_discovery_overview`
- `lcr_discovery_overview`
- `premarket_discovery_overview`
- `strategy_discovery_unified`

What it tells you:

- Which raw entry signal families worked on a market bar set.
- How candidates behave by session/regime/context.
- Which candidates should feed the deeper strategy discovery or NT template
  generation layer.

### Large Candle Excursion

Entry/config examples live under
`docs/reports_documentation/configs/entry_discovery/large_candle_excursion*.yaml`.

Important code:

- `analysis/large_candle_excursion/detector.py`
- `forward_window.py`
- `tick_analyzer.py`
- `trade_analyzer.py`
- `target_curve.py`
- `downstream_reports.py`
- `reversal_decision_engine.py`
- `elite_reversal_setup_extractor.py`
- `recursive_edge_search.py`
- `edge_validation_engine.py`
- `strategy_construction_engine.py`
- `strategy_blueprint_exporter.py`

Report sections: 45 keys starting with `large_candle_excursion`.

What it tells you:

- Which large candles occur by timeframe, session, size bucket, and context.
- How far they tend to continue or reverse.
- Target curves: scalp vs runner vs mixed behavior.
- Continuation vs reverse trade-mode outcomes.
- Context families and interaction effects.
- Fragility warnings, next tests, and validated edge candidates.
- Strategy construction and blueprint export candidates.

### Horizon Prediction Reports

The prediction/horizon system is separate from NT backtest packages. It uses
market data and horizon stores.

Code:

- `prediction/backtest_horizon_predictions.py`
- `prediction/horizon_reports.py`
- `prediction/horizon_store.py`
- `prediction/horizon_calibrator.py`
- `prediction/horizon_scorer.py`
- `reports/html/sections/horizon_overview.py`

Report preset: `horizon_overview` in `web/report_catalog.py`.

What it tells you:

- Agent leaderboard.
- Timeframe by horizon matrix.
- Session matrix.
- Best-edge cells.
- Calibration and ECE.
- Recent drift.

Use it for prediction quality and model calibration, not for interpreting one
NT backtest run.

## Optimizer And Final Template Analysis Surfaces

The web optimizer already consumes many of the same analysis outputs. Check
these before adding a new template-review or predictor-pool feature.

| Surface | Code | Output |
|---|---|---|
| Per-candidate reports | `web/optimizer_candidate_report.py` | `deployment_package/per_candidate_reports/<run_id>.html`, session candidate reports |
| Report section picker | `web/optimizer_candidate_report.py`, `optimizer_candidate_report_builder.html` | lets operator select any registry section, grouped by safe/market-dependent/multi-candidate |
| Weekly coverage package | `web/optimizer_weekly_coverage_package.py` | validated/review/fallback template folders, CSVs, report, ZIP, manifest |
| Weekly daily update | `build_weekly_daily_update_report()` | lightweight post-run progress report from shipped package manifest |
| Deployment matrix manifest | `web/optimizer_deployment_matrix_manifest.py` | fixed 252-cell manifest for daily-prediction pool, with fallback |
| Template quality features | `web/optimizer_template_quality_features.py` | MAE/MFE, daily green/worst day, risk, exit robustness |

The deployment matrix manifest is the interface to the daily-prediction pool.
The weekly coverage package is a shipping/diversity package. They solve related
but different problems.

## What The Main Analysis Results Mean

| Result | Meaning | Common misuse |
|---|---|---|
| Profit factor | gross wins divided by gross losses | trusting PF with too few trades |
| Walk-forward passed | OOS folds met gates after costs | treating it as live proof |
| `evaluation_oos` | metrics on the OOS fold pool from dev slice | confusing it with locked holdout |
| `evaluation_holdout` | one-shot locked holdout evaluation | tuning after seeing it |
| MAE percentile | adverse movement distribution | using all-trade MAE when winner MAE is the stop question |
| MFE percentile | favorable movement distribution | assuming target should equal max MFE |
| Exit robustness margin | best simulated exit policy minus current net | using it when ticks were missing |
| Tick diagnostics `bad_minutes` | minute bars disagree with tick-derived bars | ignoring timezone/contract mismatch |
| Anchor recommendation | structural TP/SL candidate around MA anchors | assuming it applies without trade alignment |
| LCE target curve | win-rate by target percentage | picking the single peak without plateau/stability |
| Horizon ECE | calibration error by bucket | using it as a directional edge score |

## Routing Examples

### "What analysis is done on NT backtests?"

Start with:

- `core/pipeline.py`
- `core/derived_metrics.py`
- `core/daily_outcomes.py`
- `analysis/drawdown.py`
- `analysis/strategy_discovery/orchestrator.py`
- `analysis/strategy_discovery/mae_mfe.py`
- `analysis/exits/simulate.py`
- `analysis/ma_structure/orchestrator.py`
- `reports/html/registry.py`
- `docs/reference/REPORT_SECTIONS_CATALOG.md`

Then render or inspect sections: core run, daily/weekly, drawdown, strategy
discovery, anchor, exit policy, and optimization sections.

### "What is done on tick data?"

Start with:

- `parsers/ninjatrader/tick_last_txt.py`
- `marketdata/tick_cache.py`
- `marketdata/store.py`
- `marketdata/resample.py`
- `analysis/exits/simulate.py`
- `reports/html/sections/tick_data_diagnostics.py`
- `reports/html/sections/exit_policy_simulation.py`
- `analysis/large_candle_excursion/tick_analyzer.py`

Then verify whether the run used `--no-tick-data`, whether `MarketDataStore`
has the requested instrument/contract, and whether diagnostics show tick rows.

### "Can we create a new stop/target recommender?"

Check first:

- `analysis/strategy_discovery/mae_mfe.py`
- `analysis/strategy_discovery/exit_discovery.py`
- `analysis/exits/policies.py`
- `analysis/exits/simulate.py`
- `analysis/ma_structure/tp_sl_engine.py`
- `analysis/ma_structure/trade_time_tp_sl.py`
- `web/optimizer_template_quality_features.py`

Most likely the new work is wiring or surfacing existing signals, not a new
engine.

### "Can we improve the daily prediction template pool?"

Check first:

- `docs/designs/deployment_matrix_252_capability.md`
- `docs/runbooks/deployment_matrix_technical_guide.md`
- `web/optimizer_deployment_matrix_manifest.py`
- `web/optimizer_template_quality_features.py`
- `prediction/horizon_reports.py`
- `reports/html/sections/horizon_overview.py`

The likely gap is selection UI/manifest feature surfacing, not prediction math.

## Existing Docs To Use With This Guide

| Doc | Use |
|---|---|
| `docs/CAPABILITY_CATALOG.md` | cheapest load-first router |
| `docs/AI_CAPABILITY_MAP.md` | broad capability semantics |
| `docs/AI_REPO_INDEX.md` | generated file map; regenerate after structural changes |
| `docs/reference/REPORT_SECTIONS_CATALOG.md` | report section catalog, but verify counts against registry |
| `docs/reference/DATA_MODEL_SCHEMA.md` | data model and metadata conventions |
| `docs/reference/COMPLETE_SYSTEM_MAP.md` | broad map, but verify because older prose may be aspirational |
| `docs/audits/capability_and_cleanup_audit_2026-06-05.md` | latest cleanup/correction audit |
| `docs/reports_documentation/configs/entry_discovery/` | split YAML examples for discovery and LCE |
| `strategy_discovery_report.yaml` | full strategy discovery YAML example |
| `report.yaml` | broad report config with commented advanced sections |

## Maintenance Checklist

When a new analysis capability ships:

1. Add the code path and report ids here.
2. Link this file from any new operator/runbook docs that touch the feature.
3. Update `docs/AI_CAPABILITY_MAP.md` if the capability changes routing.
4. Update `docs/CAPABILITY_CATALOG.md` if the capability becomes a new top-level
   row or changes status.
5. Update `docs/DOCS_INDEX.md` if a new doc becomes canonical.
6. Re-run `python scripts/build_ai_index.py` when file structure changes.
