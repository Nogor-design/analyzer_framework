# HTML Report System - Comprehensive Capabilities & Sections Guide

Welcome to the canonical reference manual for the **HTML Report Generation System** in the `ta_foundation` framework. 

This document provides a highly detailed, zero-omission capability catalog of all **127 report sections** registered in the reporting engine (under `src/ta_foundation/reports/html/registry.py`). It serves as the single source of truth for traders, developers, and AI agents looking to design, configure, or customize backtest and discovery reports.

---

## 1. System Architecture & Dual-Layer Configuration

The report generator is decoupled into a **dual-layer configuration architecture**, separating operational filesystem inputs from analytical report layouts:

```
                  +----------------------------------------------+
                  |            CLI Execution Layer               |
                  |  - filesystem paths (input, output folders)  |
                  |  - market data & tick store paths            |
                  |  - run-image inclusions, card png exports    |
                  +----------------------+-----------------------+
                                         |
                                         v
                  +----------------------------------------------+
                  |             YAML Configuration               |
                  |  - report metadata (title, output filename)  |
                  |  - enabled compute engines (pattern, regime) |
                  |  - active sections with local options        |
                  +----------------------+-----------------------+
                                         |
                                         v
                  +----------------------------------------------+
                  |         Analysis & Rendering Engine          |
                  |  - load files via registries & parsers       |
                  |  - compute derived metrics (derived folder)  |
                  |  - render sections via HtmlReportBuilder     |
                  +----------------------------------------------+
```

### Core Architecture Components
* **`src/ta_foundation/reports/html/config.py`**: Auto-detects single vs multi-report configs, loads configurations, merges default structures, and runs upstream compute passes (Moving Average Anchor Interaction, Pattern Engine, Regime Recommender).
* **`src/ta_foundation/reports/html/builder.py`**: Manages the `HtmlReportBuilder` and defines sections as `HtmlSection` instances, compiling them into a responsive, single-file HTML package with CSS styling, custom fonts, and inline base64 graphics.
* **`src/ta_foundation/reports/html/registry.py`**: The definitive registry (`SECTION_REGISTRY`) that maps unique string IDs (e.g. `trades_intraday_pnl_by_day`) to their default titles and backend Python rendering functions.

---

## 2. Modularized Standalone Report Suite

Previously, multiple report structures were bundled inside a single file (`myReports.yaml`). They have now been modularized into separate, valid, and immediately runnable configurations inside `docs/reports_documentation/configs/`.

| Config Filename | Report Title | Target Output Filename | Active Report Sections | Primary Use Case & Analytical Focus |
|---|---|---|---|---|
| **`01_comparison_report.yaml`** | Comparison Report | `Comparison_1_3.html` | `comparison_overview`, `equity_curve_comparison`, `run_kpi_cards` | Standard comparative sweep of multiple backtests, ranking them by absolute metrics. |
| **`02_comparison_overview.yaml`** | Comparison Overview | `Comparison_overview_1_3.html` | `comparison_overview` | Minimalist performance grid ranking net profit, profit factors, and win rates across runs. |
| **`03_daily_scoreboard.yaml`** | Daily Scoreboard | `Daily_Scoreboard_1_3.html` | `daily_scoreboard` *(with combo solver)* | Detailed daily Win/Loss outcomes with portfolio optimization solving for best Pairs, Triples, and Quads. |
| **`04_overview_scoreboard.yaml`** | Overview Scoreboard | `Overview_1_3.html` | `comparison_overview`, `equity_curve_comparison`, `run_kpi_cards`, `run_snapshot_clipboard` | High-fidelity comparative review equipped with a clean text snapshot block for Slack or logs sharing. |
| **`05_daily_scoreboard_combos.yaml`** | Daily Scoreboard | `Daily_Scoreboard2_1_3.html` | `daily_scoreboard` *(with beam search)* | Employs beam-search algorithms to identify optimal non-co-losing bot combinations to reduce risk correlation. |
| **`06_run_card_catalog.yaml`** | Daily run_card_catalog | `run_card_catalog_1_3.html` | `run_card_catalog` | Classifies strategy runs into structured visual cards sorted by trading session window and market index. |
| **`07_weekly_leaderboard.yaml`** | weekly_leaderboard_cards | `weekly_leaderboard_cards_3_14.html` | `weekly_leaderboard_cards` | Simulation dashboard evaluating trading runs against prop-firm metrics (starting balance, trailing drawdown limits). |
| **`08_apex_drawdown_survival.yaml`** | apex_drawdown_survival_profile | `apex_drawdown_survival_profile_3_14.html` | `apex_drawdown_survival_profile` | Runs Monte Carlo simulations on trade equity paths to calculate the probability of breaching Apex trailing drawdowns. |
| **`09_daily_scoreboard_snapshot.yaml`** | Daily Scoreboard | `Daily_Scoreboard_3_14.html` | `comparison_overview`, `equity_curve_comparison`, `run_kpi_cards`, `run_snapshot_clipboard` | **[FIXED]** Standard daily scoreboard dashboard with corrected sections tree indentation to prevent runtime parser errors. |
| **`10_market_regime_discovery.yaml`** | Market Regime Discovery 3-14 | `Market_Regime_Discovery_3_14.html` | `market_regime_discovery` | Sweeps and recommends optimal parameter settings, win-rates, and expectancies across segmented market regimes and hours. |
| **`11_executive_strategy_profiles.yaml`** | Executive Strategy Profiles | `Executive_Strategy_Profiles_3_14.html` | `run_executive_profile_cards` | Premium executive dashboard displaying win-loss matrix strips, timeline heatmaps, and high-fidelity strategy profile cards. |

---

## 3. Master Catalog of All 127 Registered Sections

Below is the definitive, comprehensive catalog detailing the purpose, configuration schema, and code binding of every registered report section.

### Category A: Core Reports & Performance Rankings
These sections provide baseline summaries, ranking backtests according to primary trading indicators (profit factor, net profit, drawdown).

#### 1. `comparison_overview`
* **Default Title**: `Comparison Overview`
* **Python Renderer**: `render_comparison_overview` in `comparison_overview.py`
* **Visual Purpose**: A responsive grid containing all backtest runs ranked by net profit. Shows Net Profit, Profit Factor, Max Drawdown, Total Trades, and Win Rate.
* **YAML Options**: None.
* **Data Dependencies**: `_summary.csv` or `_trades.csv` for each backtest.

#### 2. `equity_curve_comparison`
* **Default Title**: `Equity Curve Comparison`
* **Python Renderer**: `render_equity_curve_all_runs` in `equity_curve.py`
* **Visual Purpose**: Renders a dynamic line chart displaying the cumulative growth curves of all ingested runs layered together.
* **YAML Options**: None.
* **Data Dependencies**: Full daily or trade-by-trade equity logs derived from `_trades.csv`.

#### 3. `run_kpi_cards`
* **Default Title**: `Run KPI Cards`
* **Python Renderer**: `render_run_kpis` in `run_kpis.py`
* **Visual Purpose**: Displays premium visual cards for each strategy run, highlighting Net Profit, Profit Factor, Max Drawdown, Win Rate, and Trade Count in bold typography.
* **YAML Options**: None.
* **Data Dependencies**: Primary package outcomes.

#### 4. `analysis_chart_replica`
* **Default Title**: `Analysis (replicated from CSV)`
* **Python Renderer**: `render_analysis_chart_replica` in `analysis_chart_replica.py`
* **Visual Purpose**: Reconstructs strategy metric charts (like Drawdown or Trade Analysis) by reading the replica tables.
* **YAML Options**:
  * `analysis_csv_path` *(string)*: Relative path to the raw analysis CSV data.
* **Data Dependencies**: Strategy-specific CSV outputs.

---

### Category B: Run & Metadata Diagnostics
Designed for tracking configurations, system settings, and metadata to preserve backtest reproducibility and auditing.

#### 1. `run_settings_table`
* **Default Title**: `Run Settings`
* **Python Renderer**: `render_run_settings_table` in `run_settings_table.py`
* **Visual Purpose**: Displays an interactive, searchable table containing all inputs, parameter values, and backtest configurations.
* **YAML Options**:
  * `max_rows` *(integer, default: 500)*: Limit for the rows shown.
  * `show_run_image` *(boolean)*: Set true to display the visual chart associated with the run.
  * `run_image_max_width` *(integer)*: Maximum width in pixels for the inline image.
* **Data Dependencies**: `_settings.csv` and on-disk backtest images.

#### 2. `run_metadata_cards`
* **Default Title**: `Run Metadata Cards`
* **Python Renderer**: `render_run_metadata_cards` in `run_metadata.py`
* **Visual Purpose**: Displays compact cards detailing structural settings, run timestamps, system environment data, and code hashes.
* **YAML Options**:
  * `show_run_image` *(boolean)*: Show run chart.
  * `run_image_max_width` *(integer)*: Pixel width cap.
* **Data Dependencies**: Package metadata logs.

#### 3. `run_snapshot_clipboard`
* **Default Title**: `snapshot`
* **Python Renderer**: `render_run_snapshot_clipboard` in `run_snapshot_clipboard.py`
* **Visual Purpose**: Renders a pre-formatted, highly dense text block containing backtest outcomes, designed for instant copy/pasting.
* **YAML Options**:
  * `style` *(string, choices: `slides`, `minimal`, `contrast`)*: Visual theme for the snapshot block.
  * `density` *(string, choices: `compact`, `comfortable`)*: Text spacing and padding density.
  * `layout` *(string, choices: `grid`, `stack`)*: Layout organization.
  * `columns` *(integer)*: Grid columns layout.
  * `show_hint` *(boolean)*: Display a helper text tip.
  * `emphasize_negatives` *(boolean)*: Set true to highlight negative metrics in red text.
* **Data Dependencies**: Normalized outcome KPIs.

#### 4. `exec_card_god_banner`
* **Default Title**: *(empty)*
* **Python Renderer**: `render_exec_card_god_banner` in `exec_card_god_banner.py`
* **Visual Purpose**: Displays a highly polished, colorful banner representing candidate strategies that passed high-conviction "God" validation criteria.
* **YAML Options**:
  * `run_id` *(string)*: Focus ID.
  * `label` *(string)*: Banner tag description.
  * `template_path` *(string)*: NinjaTrader strategy template link.
  * `images_dir` *(string)*: Path to banner images assets.
  * `market_suffix` *(string)*: Market tag (e.g. "NQ").

---

### Category C: Leaderboards & Performance Boards
These sections support operational evaluation by organizing runs into daily grids, consistency cards, and weekly scores.

#### 1. `daily_scoreboard`
* **Default Title**: `Daily Scoreboard`
* **Python Renderer**: `render_daily_scoreboard` in `daily_scoreboard.py`
* **Visual Purpose**: Displays a calendar-style scoreboard marking Wins, Losses, and No-Trade days for each bot. If configured, it executes a portfolio combo solver.
* **YAML Options**:
  * `max_runs` *(integer)*: Caps the number of displayed runs.
  * `show_individual_equity` *(boolean)*: Show miniature equity lines per bot.
  * `include_summary_table` *(boolean)*: Output aggregate statistics tables.
  * `include_all_bot_charts` *(boolean)*: Render full equity curves for all bots.
  * `combo_sets` *(list)*: Sets of portfolio combinations to solve and render.
* **Data Dependencies**: `_trades.csv` parsed to daily outcomes.

#### 2. `run_card_catalog`
* **Default Title**: `Run Card Catalog`
* **Python Renderer**: `render_run_card_catalog` in `run_card_catalog.py`
* **Visual Purpose**: Group strategies into an interactive grid of trading cards classified by active session time (London, NY, Asia) and underlying instrument.
* **YAML Options**:
  * `metric` *(string, default: `profit_factor`)*: Sorting metric (e.g. `profit_factor`, `total_net_profit`).
  * `sort_desc` *(boolean)*: Sort descending.
  * `max_per_group` *(integer)*: Maximum cards per session/market cell.
  * `hide_missing_cards` *(boolean)*: Hide empty groups.
  * `fallback_session_label` *(string)*: Default label when session is unclassified.
  * `fallback_market_label` *(string)*: Default label for unclassified markets.
  * `session_windows` *(list)*: Defines start/end hours for session groups.
* **Data Dependencies**: Ingested package KPIs and code pattern names.

#### 3. `daily_leaderboard_cards`
* **Default Title**: `Daily Leaders (Session Winners)`
* **Python Renderer**: `render_daily_leaderboard_cards` in `daily_leaderboard_cards.py`
* **Visual Purpose**: Renders a spotlight leaderboard identifying the best-performing bots for a specific day, complete with a lookback consistency heatmap.
* **YAML Options**:
  * `target_date` *(string)*: Target date (YYYY-MM-DD); auto-picks.
  * `top_n` *(integer)*: Leaders count.
  * `lookback_days` *(integer)*: Consistency calculation lookback.
  * `heatmap_top_bots` *(integer)*: Map size.
  * `buffer_chart_top_bots` *(integer)*: Capital buffer chart count.
  * `hide_missing_cards` *(boolean)*: Filter out empty profiles.

#### 4. `weekly_leaderboard_cards`
* **Default Title**: `Weekly Leaders`
* **Python Renderer**: `render_weekly_leaderboard_cards` in `weekly_leaderboard_cards.py`
* **Visual Purpose**: Renders prop-firm leaderboards simulating capital draws against target rules on a weekly basis. Shows account buffer decay curves.
* **YAML Options**:
  * `week_ending` *(string)*: Target week end date.
  * `top_n` *(integer)*: Ranks count.
  * `starting_balance` *(number, default: 50000)*: Balance base.
  * `trailing_dd` *(number, default: 2500)*: Drawdown floor.
  * `baseline_mode` *(string, choices: `fresh_week`, `continuous`)*: Tracking mode.
  * `show_card_image` *(boolean)*: Profile cards image visibility.
  * `show_chart` *(boolean)*: Equity curves visibility.
  * `warn_buffer` *(number)*: Drawdown warning line limit.
  * `compact_noimg` *(boolean)*: Renders without profile pictures.
  * `bot_columns` *(integer)*: Multi-column count grid.

#### 5. `daily_winner_spotlight`
* **Default Title**: `Daily Winner Insight`
* **Python Renderer**: `render_daily_winner_spotlight` in `daily_winner_spotlight.py`
* **Visual Purpose**: Spotlights the single highest-performing bot of the day, detailing its win streak, execution quality, and setup metrics.
* **YAML Options**:
  * `top_n` *(integer)*: Spotlight count.
  * `strip_days` *(integer)*: Trailing days.
  * `target_date` *(string)*: Target date.

#### 6. `run_executive_profile_cards`
* **Default Title**: `Executive Strategy Profiles`
* **Python Renderer**: `render_run_executive_profile_cards` in `run_executive_profile_cards.py`
* **Visual Purpose**: High-fidelity dashboard overlaying profile cards, win-loss matrix strips, and intraday timeline timeline cells.
* **YAML Options**:
  * `show_hint` *(boolean)*: Tooltip helper.
  * `show_run_image` *(boolean)*: Show strategy backtest charts.
  * `background_style` *(string)*: Options are `solid`, `image-cover`, `image-soft-overlay`, `image-dark-overlay`.
  * `card_width_px` *(integer)*: Overall card width.
  * `card_padding_px` *(integer)*: Card inner margins.
  * `image_width_px` *(integer)*: Strategy chart width.
  * `wlr_days_back` *(integer)*: Length of the win-loss strip.
  * `wlr_gap_px` *(integer)*: Spacing inside win-loss strip.
  * `show_detail_charts` *(boolean)*: Show detail excursion charts.
  * `detail_chart_layout` *(string)*: Layout model (`stack` or `two-up`).
  * `timeline_render_bin_minutes` *(integer)*: Minutes width for timeline boxes.
  * `timeline_cell_h_px` *(integer)*: Timeline cell height.
  * `timeline_show_hours` *(boolean)*: Overlay hour markers.
  * `timeline_show_summary` *(boolean)*: Include timeline summary data.

---

### Category D: Operations & Deployment Boards
Designed for active system operators to inspect account postures, bot allocation, and short-term momentum.

#### 1. `deployment_board_insight`
* **Default Title**: `Deployment Board Insight`
* **Python Renderer**: `render_deployment_board_insight` in `deployment_board_insight.py`
* **Visual Purpose**: Renders a master dashboard displaying live bot deployment states, active hours, risk postures, and recent day-by-day Win/Loss grids.
* **YAML Options**:
  * `board_text_path` *(string)*: Path to the raw text deployment board log.
  * `as_of_date` *(string)*: Target date.
  * `strip_days` *(integer)*: History strip length.

#### 2. `deployment_board_gods`
* **Default Title**: `Deployment Board Pantheon`
* **Python Renderer**: `render_deployment_board_gods` in `deployment_board_gods.py`
* **Visual Purpose**: Displays elite, high-conviction "God" strategy allocations across active sessions with interactive visual cards.
* **YAML Options**: Similar to `deployment_board_insight` options.

#### 3. `deployment_board_poster`
* **Default Title**: `Ares Deployment Card`
* **Python Renderer**: `render_deployment_board_poster` in `deployment_board_poster.py`
* **Visual Purpose**: A high-impact, visual "poster card" for a specific market strategy, showing its core setup filters, session windows, and active state.
* **YAML Options**:
  * `headline` *(string)*: Title header.
  * `subtitle` *(string)*: Card subheader.
  * `market` *(string)*: Renders market details (e.g. "NQ").
  * `timeframe_label` *(string)*: Strategy resolution label (e.g. "5m").

#### 4. `strategy_momentum_board`
* **Default Title**: `Strategy Momentum Board`
* **Python Renderer**: `render_strategy_momentum_board` in `strategy_momentum_board.py`
* **Visual Purpose**: Trailing win/loss momentum analyzer, ranking bots by their trailing profit factor and streak metrics over a rolling day window.
* **YAML Options**:
  * `top_n` *(integer)*: Rank limit.
  * `strip_days` *(integer)*: Days of trailing performance strips.

#### 5. `strategy_session_momentum_board`
* **Default Title**: `Strategy Session Momentum Board`
* **Python Renderer**: `render_strategy_session_momentum_board` in `strategy_session_momentum_board.py`
* **Visual Purpose**: Segmented momentum tracker, evaluating and ranking bots within their dedicated trading session times (London vs NY vs Asia).
* **YAML Options**:
  * `overall_top_n` *(integer)*: Caps overall leaders.
  * `top_n_per_session` *(integer)*: Caps per-session leaders.
  * `strip_days` *(integer)*: Timeline strip length.

---

### Category E: Trade Diagnostics & Risk Analytics
These sections dissect trade execution down to specific entry candles, intraday timing windows, and capital risk exposures.

#### 1. `trades_intraday_pnl_by_day`
* **Default Title**: `Intraday Trade PnL by Day (MFE Overlay)`
* **Python Renderer**: `render_trades_intraday_pnl_by_day` in `trades_intraday_pnl_by_day.py`
* **Visual Purpose**: Renders a detailed, daily trade execution timeline showing intraday PnL bars, Maximum Favorable Excursion (MFE) overlays, and drawdown limits.
* **YAML Options**:
  * `show_cum_line` *(boolean)*: Layer the cumulative session P&L curve over the bars.
  * `show_trade_charts` *(boolean)*: Show miniature charts for individual trade excursions.
  * `show_hourly_totals` *(boolean)*: Render aggregate hourly performance profiles.
  * `hourly_totals_mode` *(string, choices: `direction_split`, `net`)*: Display mode.
  * `show_mae_bar` *(boolean)*: Include Maximum Adverse Excursion (MAE) risk markers.
  * `mae_alpha` *(number)*: Transparency for MAE markers.
  * `sessions` *(list)*: Define custom intraday session boundaries.
  * `show_ledger` *(boolean)*: Include a color-coded trade ledger table.

#### 2. `trade_candle_overlay`
* **Default Title**: `Trades on Candles`
* **Python Renderer**: `render_trade_candle_overlay` in `trade_candle_overlay.py`
* **Visual Purpose**: Renders a candle-by-candle chart showing exactly where trades were entered and exited relative to price action.
* **YAML Options**:
  * `window_minutes` *(integer, default: 480)*: Chart lookback window size.
  * `padding_minutes` *(integer, default: 30)*: Spacing padding.
  * `max_trades` *(integer, default: 300)*: Trade count cap.

#### 3. `tick_data_diagnostics`
* **Default Title**: `Tick Data Diagnostics`
* **Python Renderer**: `render_tick_data_diagnostics` in `tick_data_diagnostics.py`
* **Visual Purpose**: Inspects tick data files to verify data quality, tick density, and bid/ask spread availability.
* **YAML Options**:
  * `instrument` *(string)*: e.g. "NQ".
  * `contract` *(string)*: e.g. "03-26".
  * `show_sample_rows` *(integer)*: Render raw sample table size.

#### 4. `apex_drawdown_survival_profile`
* **Default Title**: `Apex Drawdown Survival`
* **Python Renderer**: `render_apex_drawdown_survival_profile` in `apex_drawdown_survival_profile.py`
* **Visual Purpose**: Generates thousands of randomized Monte Carlo paths based on trade metrics to calculate portfolio survival rates under Apex funding limits.
* **YAML Options**:
  * `apex` *(dict)*: Account settings (starting balance, trailing drawdown, lock profit).
  * `portfolio_mc` *(dict)*: Monte Carlo parameters (seed, path count, correlation mode).
  * `instruments` *(dict)*: Tick specification mapping (tick values and sizes for NQ, ES, etc.).

#### 5. `strategy_lifecycle_board`
* **Default Title**: `Strategy Lifecycle Board`
* **Python Renderer**: `render_strategy_lifecycle_board` in `strategy_lifecycle_board.py`
* **Visual Purpose**: Evaluates active bots against pre-registered risk budgets, highlighting strategies near drawdown boundaries or performance decay.
* **YAML Options**:
  * `risk_budget` *(number)*: Maximum drawdown budget.
  * `top_n` *(integer)*: Display ranks.

#### 6. `strategy_parameter_matrix`
* **Default Title**: `Executive Parameter Matrix`
* **Python Renderer**: `render_strategy_parameter_matrix` in `strategy_parameter_matrix.py`
* **Visual Purpose**: Displays a highly structured, interactive grid cross-referencing multiple bots and parameter combinations against target outcomes.
* **YAML Options**:
  * `sort_by` *(string)*: Target sorting metric.

---

### Category F: Moving Average Anchor Interaction
These sections analyze strategy performance relative to higher-timeframe trend lines and anchor structures, showing optimal TP/SL bounds.

#### 1. `anchor_interaction_overview`
* **Default Title**: `MA Anchor Overview`
* **Python Renderer**: `render_anchor_interaction_overview` in `anchor_interaction_overview.py`
* **Visual Purpose**: Provides a high-level summary of moving-average trend anchors, indicating if they are correctly resolved and active.
* **YAML Options**:
  * `show_presence` *(boolean)*: Show anchor verification tables.
  * `show_notes` *(boolean)*: Include structural research notes.

#### 2. `anchor_interaction_config`
* **Default Title**: `MA Anchor Configuration`
* **Python Renderer**: `render_anchor_interaction_config` in `anchor_interaction_config.py`
* **Visual Purpose**: Renders details about moving-average parameters (EMA vs SMA, lookbacks, prices) mapped to strategy definitions.
* **YAML Options**:
  * `show_anchor_table` *(boolean)*: Show details.
  * `show_tp_sl` *(boolean)*: Show targeted TP/SL specifications.

#### 3. `anchor_interaction_anchor_matrix`
* **Default Title**: `MA Anchor Matrix`
* **Python Renderer**: `render_anchor_interaction_anchor_matrix` in `anchor_interaction_anchor_matrix.py`
* **Visual Purpose**: Displays a performance matrix indicating how bots perform when entering trades above, below, or near specific moving averages.
* **YAML Options**:
  * `show_entry_exit` *(boolean)*: Render entry/exit details.
  * `show_role` *(boolean)*: Include trend-role markers.

#### 4. `anchor_interaction_tp_sl_spec`
* **Default Title**: `MA Anchor TP/SL Specification`
* **Python Renderer**: `render_anchor_interaction_tp_sl_spec` in `anchor_interaction_tp_sl_spec.py`
* **Visual Purpose**: Renders a comprehensive, color-coded grid mapping different Target Profit (TP) and Stop Loss (SL) values to expectancies.
* **YAML Options**:
  * `max_candidate_rows_per_run` *(integer)*: Limits the recommendations output.

#### 5. `anchor_interaction_diagnostics`
* **Default Title**: `MA Anchor Diagnostics`
* **Python Renderer**: `render_anchor_interaction_diagnostics` in `anchor_interaction_diagnostics.py`
* **Visual Purpose**: Diagnostic center showing parsing logs, data gaps, or calculation warnings in the anchor resolution pipeline.
* **YAML Options**:
  * `include_issue_list` *(boolean)*: Render a detailed list of data warning events.
  * `show_only_failures` *(boolean)*: Filter list to failures.

#### 6. `anchor_tp_sl_recommendations`
* **Default Title**: `MA Anchor TP/SL Recommendations`
* **Python Renderer**: `render_anchor_tp_sl_recommendations` in `anchor_tp_sl_recommendations.py`
* **Visual Purpose**: Recommends the mathematically optimal TP and SL values (in ATR multiples) for each bot, maximizing profit factor and stability.
* **YAML Options**:
  * `conservative_min_stability` *(number)*: Stability threshold for conservative recommendations.
  * `conservative_max_tail_dependency` *(number)*: Risk cap.
  * `balanced_min_stability` *(number)*: Threshold for balanced portfolios.
  * `show_candidate_grid` *(boolean)*: Renders the candidate options grid.
  * `show_trade_alignment` *(boolean)*: Shows trade-by-trade alignment matrix.

#### 7. `anchor_interaction_hourly_profile`
* **Default Title**: `TP/SL by Hour of Day`
* **Python Renderer**: `render_anchor_interaction_hourly_profile` in `anchor_interaction_hourly_profile.py`
* **Visual Purpose**: Renders a 2D heatmap showing how anchor expectancies change according to the hour of day, highlighting high-probability execution windows.
* **YAML Options**:
  * `min_decisive` *(integer)*: Minimum trade count per hour.

---

### Category G: Pattern Discovery Engine
Designed to evaluate trade quality, calculate patterns (VWAP, Bollinger Bands, RSI) active at trade entry, and check robustness.

#### 1. `pattern_engine_overview`
* **Default Title**: `Pattern Engine Overview`
* **Python Renderer**: `render_pattern_engine_overview` in `pattern_engine_overview.py`
* **Visual Purpose**: Displays global statistics for market pattern sweeps, reporting total combinations executed and top pattern performance.
* **YAML Options**: None.

#### 2. `pattern_engine_diagnostics`
* **Default Title**: `Pattern Diagnostic`
* **Python Renderer**: `render_pattern_engine_diagnostics` in `pattern_engine_diagnostics.py`
* **Visual Purpose**: Diagnostic logs detailing pattern calculation coverage, missing market bars, or structural mismatches.
* **YAML Options**: None.

#### 3. `pattern_market_discovery`
* **Default Title**: `Pattern Discovery`
* **Python Renderer**: `render_pattern_market_discovery` in `pattern_market_discovery.py`
* **Visual Purpose**: Displays a 2D heatmap of Signal vs Timeframe, indicating the average profit factors obtained during market sweeps.
* **YAML Options**: None.

#### 4. `pattern_engine_mc_regime`
* **Default Title**: `Pattern Engine MC`
* **Python Renderer**: `render_pattern_engine_mc_regime` in `pattern_engine_mc_regime.py`
* **Visual Purpose**: Renders a 3D surface mapping parameter sensitivity and Monte Carlo outcomes conditioned on specific market regimes.
* **YAML Options**:
  * `show_surface` *(boolean)*: Set true to render the 3D surface graphic.
  * `top_n` *(integer)*: Limits candidates rendered.

#### 5. `pattern_cluster_drilldown`
* **Default Title**: `Pattern Cluster Drilldown`
* **Python Renderer**: `render_pattern_cluster_drilldown` in `pattern_cluster_drilldown.py`
* **Visual Purpose**: Drills down into identified clusters of similar market patterns, comparing their Win Rates and expectancies.
* **YAML Options**: None.

#### 6. `trade_pattern_audit`
* **Default Title**: `Trade Pattern Audit`
* **Python Renderer**: `render_trade_pattern_audit` in `trade_pattern_audit.py`
* **Visual Purpose**: Audits every executed trade against concurrent market patterns, assigning a verdict: **CONFIRMED** (pattern supported), **PARTIAL**, or **REJECTED** (no pattern alignment). Displays per-run summary cards and trade ledger.
* **YAML Options**:
  * `run_id` *(string)*: Focus target.
  * `top_n_runs` *(integer)*: Maximum runs to audit.
  * `show_per_trade` *(boolean)*: Renders the collapsible trade-by-trade audit table.
  * `min_trades` *(integer)*: Skip runs with fewer audited trades.

#### 7. `market_regime_discovery`
* **Default Title**: `Market Regime Discovery`
* **Python Renderer**: `render_market_regime_discovery` in `market_regime_discovery.py`
* **Visual Purpose**: The master regime section evaluating setups under specific market contexts (trend flat, high/low volatility). Recommends time-of-day filters, stop sizing, and risk parameters. Supports high-fidelity styling configs.
* **YAML Options**:
  * `bar_tf`, `htf_tf` *(string)*: Timeframe specifications.
  * `ema_period`, `atr_period` *(int)*: Indicator parameters.
  * `include_micro` *(bool)*: Incorporate micro contracts.
  * `top_n_runs` *(int)*: Maximum runs output.
  * `style_preset` *(string, choices: `classic_dark`, `executive_dark`, `compact_dark`, `clean_light`)*: Color theme preset.
  * `style_density` *(string, choices: `compact`, `normal`, `comfortable`)*: Grid sizing density.
  * `style_accent` *(string, choices: `blue`, `green`, `amber`, `violet`, `red`)*: Accent highlights.

---

### Category H: Strategy Discovery Engine & Rule Swapping
Sweeps rules, checks walk-forward validation (IS/OOS), features, risk matrices, and generates NinjaTrader templates.

#### 1. `strategy_discovery_overview`
* **Default Title**: `Strategy Discovery Overview`
* **Python Renderer**: `render_strategy_discovery_overview` in `strategy_discovery_overview.py`
* **Visual Purpose**: Core overview panel displaying walk-forward IS/OOS validation results, MAE/MFE profiles, and risk matrix outcomes.
* **YAML Options**: See Category H.1 in summary block.

#### 2. `strategy_discovery_ranked_table`
* **Default Title**: `Strategy Discovery — Ranked Table`
* **Python Renderer**: `render_strategy_discovery_ranked_table` in `strategy_discovery_ranked_table.py`
* **Visual Purpose**: Renders the master candidate grid, ranking all discovered strategy variations based on their walk-forward OOS expectancies.
* **YAML Options**:
  * `show_sensitivity` *(boolean)*: Show parameter sensitivity grids.
  * `show_components` *(boolean)*: Show individual filter component scores.
  * `show_clusters` *(boolean)*: Renders parameter stability clusters.

#### 3. `strategy_discovery_validation`
* **Default Title**: `Strategy Discovery — Walk-Forward Validation`
* **Python Renderer**: `render_strategy_discovery_validation` in `strategy_discovery_validation.py`
* **Visual Purpose**: Renders interactive OOS growth curves layered over In-Sample results, detailing degradation profiles.
* **YAML Options**:
  * `show_cross_run` *(boolean)*: Combine multiple runs in validation curves.
  * `max_runs` *(integer)*: Caps runs evaluated.

#### 4. `strategy_discovery_nt_template`
* **Default Title**: `Strategy Discovery — NinjaTrader Template`
* **Python Renderer**: `render_strategy_discovery_nt_template` in `strategy_discovery_nt_template.py`
* **Visual Purpose**: Renders a pre-formatted C# configuration block or a JSON strategy template matching the winning candidates.
* **YAML Options**:
  * `max_per_rule_templates` *(integer)*: Caps templates generated.
  * `show_reasoning` *(boolean)*: Renders explanation cards.
  * `tick_size` *(number)*: Instrument tick resolution.
  * `tick_value` *(number)*: Tick cash value.

#### 5. `strategy_discovery_comparison`
* **Default Title**: `Strategy Discovery — Cross-Run Comparison`
* **Python Renderer**: `render_strategy_discovery_comparison` in `strategy_discovery_comparison.py`
* **Visual Purpose**: Renders a high-density comparative ledger cross-referencing metrics, drawdown behaviors, and contract scaling sizes across multiple runs.
* **YAML Options**:
  * `max_runs` *(int)*: Caps runs displayed.
  * `group_columns` *(list)*: Columns grouping model.
  * `show_drawdown`, `show_sizing`, `show_risk_adj`, `show_stability` *(bool)*: Section inclusion metrics.

#### 6. `strategy_discovery_entry_rules`
* **Default Title**: `Strategy Discovery — Entry Rules`
* **Python Renderer**: `render_strategy_discovery_entry_rules` in `strategy_discovery_entry_rules.py`
* **Visual Purpose**: Outputs the exact price-action triggering conditions derived for the winning candidates.
* **YAML Options**:
  * `show_all_runs` *(bool)*: Display all series.
  * `max_runs` *(int)*: Caps runs.

#### 7. `strategy_discovery_filter_rules`
* **Default Title**: `Strategy Discovery — Filter Rules`
* **Python Renderer**: `render_strategy_discovery_filter_rules` in `strategy_discovery_filter_rules.py`
* **Visual Purpose**: Maps the downside avoidance rules (like EMA trend filters or news calendars) and their positive statistical impact.
* **YAML Options**: Similar to `strategy_discovery_entry_rules`.

#### 8. `strategy_discovery_exit_policies`
* **Default Title**: `Strategy Discovery — Exit Policies`
* **Python Renderer**: `render_strategy_discovery_exit_policies` in `strategy_discovery_exit_policies.py`
* **Visual Purpose**: Evaluates expectancies obtained when replacing executed strategy exits with optimized target and stop models.
* **YAML Options**:
  * `top_n_policies` *(int)*: Limit policies printed.

#### 9. `strategy_discovery_feature_importance`
* **Default Title**: `Strategy Discovery — Feature Importance`
* **Python Renderer**: `render_strategy_discovery_feature_importance` in `strategy_discovery_feature_importance.py`
* **Visual Purpose**: Plots the normalized feature weight contributions derived from classification algorithms.
* **YAML Options**:
  * `show_rf` *(bool)*: Include Random Forest models.
  * `top_n_features` *(int)*: Limit features plotted.

#### 10. `strategy_discovery_mae_mfe`
* **Default Title**: `Strategy Discovery — MAE/MFE Profile`
* **Python Renderer**: `render_strategy_discovery_mae_mfe` in `strategy_discovery_mae_mfe.py`
* **Visual Purpose**: Profiles entry-efficiency by plotting trade distribution density across adverse and favorable excursion axes.
* **YAML Options**:
  * `show_direction` *(bool)*: Segments profiles into Long vs Short.

#### 11. `strategy_discovery_position_sizing`
* **Default Title**: `Strategy Discovery — Position Sizing`
* **Python Renderer**: `render_strategy_discovery_position_sizing` in `strategy_discovery_position_sizing.py`
* **Visual Purpose**: Renders a volatility-scaled allocation model suggesting exact contract sizes per strategy run.
* **YAML Options**:
  * `show_regime_conditional` *(bool)*: Sets if sizing changes based on current regime.

#### 12. `strategy_discovery_risk_metrics`
* **Default Title**: `Strategy Discovery — Risk-Adjusted Metrics`
* **Python Renderer**: `render_strategy_discovery_risk_metrics` in `strategy_discovery_risk_metrics.py`
* **Visual Purpose**: Compiles Sharpe, Sortino, MAR, and tail CVaR ratios to help select risk-stable strategies.
* **YAML Options**: None.

#### 13. `strategy_discovery_drawdown`
* **Default Title**: `Strategy Discovery — Drawdown Analysis`
* **Python Renderer**: `render_strategy_discovery_drawdown` in `strategy_discovery_drawdown.py`
* **Visual Purpose**: Plots the history of rolling portfolio drawdowns, peak-to-valley durations, and recovery curves.
* **YAML Options**:
  * `show_rolling` *(bool)*: Render the rolling drawdown timeline.
  * `top_n_rolling` *(int)*: Cap series rendered.

#### 14. `strategy_discovery_cohort`
* **Default Title**: `Strategy Discovery — Cohort Analysis (Drift / Decay)`
* **Python Renderer**: `render_strategy_discovery_cohort` in `strategy_discovery_cohort.py`
* **Visual Purpose**: Analyzes the stability and degradation of strategy performance by tracking outcomes across distinct temporal trade cohorts.
* **YAML Options**:
  * `show_early_vs_late` *(bool)*: Compare first-half trades vs second-half trades.

#### 15. `strategy_discovery_classification`
* **Default Title**: `Strategy Discovery — Automation Classification`
* **Python Renderer**: `render_strategy_discovery_classification` in `strategy_discovery_classification.py`
* **Visual Purpose**: Renders an automated grading system classifying strategies into shadow-promotion, optimization-ready, or archive-bound states.
* **YAML Options**: None.

#### 16. `strategy_discovery_evaluation`
* **Default Title**: `Strategy Discovery — Trade Evaluation`
* **Python Renderer**: `render_strategy_discovery_evaluation` in `strategy_discovery_evaluation.py`
* **Visual Purpose**: Drills down to raw trade outcome evaluations, segmenting metrics across entry triggers, trading sessions, and directions.
* **YAML Options**:
  * `show_equity_curve` *(bool)*: Render the execution P&L line.

#### 17. `strategy_discovery_regime`
* **Default Title**: `Strategy Discovery — Market Regime`
* **Python Renderer**: `render_strategy_discovery_regime` in `strategy_discovery_regime.py`
* **Visual Purpose**: Renders a cross-tab mapping strategy metrics to active regime states (trend, flat, high/low vol).
* **YAML Options**:
  * `max_recent_days` *(int)*: Caps active evaluation window.

#### 18. `strategy_discovery_parameter_sensitivity`
* **Default Title**: `Strategy Discovery — Parameter Sensitivity`
* **Python Renderer**: `render_strategy_discovery_parameter_sensitivity` in `strategy_discovery_parameter_sensitivity.py`
* **Visual Purpose**: Visualizes the stability of strategy candidates by plotting expectancies across parameter step changes.
* **YAML Options**:
  * `max_rules_shown` *(int)*: Caps parameter variations printed.

#### 19. `strategy_discovery_signal_entries`
* **Default Title**: `Strategy Discovery — Signal Entry Discovery`
* **Python Renderer**: `render_strategy_discovery_signal_entries` in `strategy_discovery_signal_entries.py`
* **Visual Purpose**: Renders entry signals compiled from off-line signal databases.
* **YAML Options**:
  * `top_n_rules` *(int)*: Caps signals shown.

#### 20. `strategy_discovery_signal_validation`
* **Default Title**: `Strategy Discovery — Signal Rule Walk-Forward Validation`
* **Python Renderer**: `render_strategy_discovery_signal_validation` in `strategy_discovery_signal_validation.py`
* **Visual Purpose**: Maps In-Sample vs Out-of-Sample validation growth for resampled signals.
* **YAML Options**: None.

#### 21. `strategy_discovery_signal_exit_sweep`
* **Default Title**: `Strategy Discovery — Signal Corpus Exit Parameter Sweep`
* **Python Renderer**: `render_strategy_discovery_signal_exit_sweep` in `strategy_discovery_signal_exit_sweep.py`
* **Visual Purpose**: Renders parameters obtained when sweeping exits over the resampled signal corpus.
* **YAML Options**: None.

#### 22. `strategy_discovery_signal_simulation`
* **Default Title**: `Strategy Discovery — Signal Corpus P&L Simulation`
* **Python Renderer**: `render_strategy_discovery_signal_simulation` in `strategy_discovery_signal_simulation.py`
* **Visual Purpose**: Compiles simulated growth paths using signal entry triggers mapped directly to historic bars.
* **YAML Options**: None.

#### 23. `strategy_discovery_anchor_confluence`
* **Default Title**: `Strategy Discovery × Anchor Confluence`
* **Python Renderer**: `render_strategy_discovery_anchor_confluence` in `strategy_discovery_anchor_confluence.py`
* **Visual Purpose**: Checks confluence between winning rules and moving-average alignments to confirm higher timeframe support.
* **YAML Options**: None.

#### 24. `strategy_discovery_decision_ledger`
* **Default Title**: `Strategy Discovery — Decision Ledger`
* **Python Renderer**: `render_strategy_discovery_decision_ledger` in `strategy_discovery_decision_ledger.py`
* **Visual Purpose**: Auditor ledger tracking rule pre-registrations, validation parameters, and candidate promotion decisions.
* **YAML Options**: None.

#### 25. `strategy_discovery_combo_basket`
* **Default Title**: `Strategy Discovery — Combo Basket`
* **Python Renderer**: `render_strategy_discovery_combo_basket` in `strategy_discovery_combo_basket.py`
* **Visual Purpose**: Portfolio composer solving for the best combinations of candidate rules to form a balanced portfolio.
* **YAML Options**:
  * `top_n_k2`, `top_n_k3` *(int)*: Limits combinations evaluated.

#### 26. `final_template_bundle_basket`
* **Default Title**: `Final Template Bundle Basket`
* **Python Renderer**: `render_final_template_bundle_basket` in `final_template_bundle_basket.py`
* **Visual Purpose**: Final optimizer deployment helper that builds runnable bundles with exactly one template from each time bucket, ranking them by shared losing-day risk and combined P&L. Useful when a final optimizer stage produces several candidates per time frame and the operator needs one exportable set to run together.
* **YAML Options**:
  * `bucket_param` *(string, default: `StartTimeH`)*: Settings parameter used to group candidates into time buckets.
  * `top_n` *(int, default: 12)*: Maximum bundle rows shown.
  * `max_per_bucket` *(int, default: 12)*: Candidate cap per bucket before bundle enumeration.
  * `show_chart` *(bool, default: true)*: Shows cumulative P&L for the top bundle.

#### 27. `strategy_discovery_unified`
* **Default Title**: `Unified Strategy Discovery — Cross-Strategy Ranking`
* **Python Renderer**: `render_strategy_discovery_unified` in `strategy_discovery_unified.py`
* **Visual Purpose**: High-level ranking matrix compiling candidates from diverse entry setups into a unified leader grid.
* **YAML Options**: None.

---

### Category I: Entry Discovery Sweeps
Designed to scan raw price action and resampled signals to isolate high-expectancy entry rules.

> [!NOTE]
> For a comprehensive, in-depth guide on the Entry Discovery Sweeps architectures, data flows, math models, and detailed parameters for all 11 sections in this category, see the [Entry Discovery Sweeps Reference Manual](file:///d:/Backup/projects/PythonProject/ta_foundation/docs/reports_documentation/entry_discovery_sweeps.md).
>
> Operational YAML configurations representing each stage of the discovery funnel are organized under the [Entry Discovery YAML Subcategory](file:///d:/Backup/projects/PythonProject/ta_foundation/docs/reports_documentation/configs/entry_discovery/).

#### 1. `candle_discovery_overview`
* **Default Title**: `Candle Discovery — Pattern × TF × Regime Overview`
* **Python Renderer**: `render_candle_discovery_overview` in `candle_discovery_overview.py`
* **Visual Purpose**: Compiles statistical scans of candlestick patterns (Pinbars, Engulfing, Inside bars) across multiple timeframes.
* **YAML Options**: None.

#### 2. `candle_discovery_ranking`
* **Default Title**: `Candle Discovery — 5-Tier Ranking`
* **Python Renderer**: `render_candle_discovery_ranking` in `candle_discovery_ranking.py`
* **Visual Purpose**: Renders candlestick candidates classified into a 5-Tier quality ranking structure.
* **YAML Options**: None.

#### 3. `ma_discovery_overview`
* **Default Title**: `MA Discovery — Cross & Pullback Overview`
* **Python Renderer**: `render_ma_discovery_overview` in `ma_discovery_overview.py`
* **Visual Purpose**: Summarizes moving average crossover and pullback signals.
* **YAML Options**: None.

#### 4. `orb_discovery_overview`
* **Default Title**: `ORB Discovery — Opening Range Breakout Overview`
* **Python Renderer**: `render_orb_discovery_overview` in `orb_discovery_overview.py`
* **Visual Purpose**: Displays statistics for Opening Range Breakout setups, checking expectancies of first 15-minute and 30-minute breaks.
* **YAML Options**: None.

#### 5. `premarket_discovery_overview`
* **Default Title**: `Pre-Market Predictor — Does the Premarket Signal the Open Direction?`
* **Python Renderer**: `render_premarket_discovery_overview` in `premarket_discovery_overview.py`
* **Visual Purpose**: Maps pre-market high/low breaks to NY session open directionality.
* **YAML Options**: None.

#### 6. `bb_discovery_overview`
* **Default Title**: `BB Discovery — Bollinger Band Strategies Overview`
* **Python Renderer**: `render_bb_discovery_overview` in `bb_discovery_overview.py`
* **Visual Purpose**: Maps Bollinger Band squeeze and mean-reversion expectancies.
* **YAML Options**: None.

#### 7. `breakout_discovery_overview`
* **Default Title**: `Breakout Discovery — N-Bar & Volatility Breakouts`
* **Python Renderer**: `render_breakout_discovery_overview` in `registry.py` (via `_make_generic_overview`)
* **Visual Purpose**: Displays average profit factors of breakouts across multiple timeframes.
* **YAML Options**: None.

#### 8. `pullback_discovery_overview`
* **Default Title**: `Pullback Discovery — Trend Pullback & Continuation`
* **Python Renderer**: `render_pullback_discovery_overview` in `registry.py` (via `_make_generic_overview`)
* **Visual Purpose**: Compiles average profit factors for trend pullbacks and continuation entries.
* **YAML Options**: None.

#### 9. `level_discovery_overview`
* **Default Title**: `Level Discovery — Swing Levels, Consolidation & Round Numbers`
* **Python Renderer**: `render_level_discovery_overview` in `registry.py` (via `_make_generic_overview`)
* **Visual Purpose**: Displays average profit factors for entries reacting to swing highs/lows or round prices.
* **YAML Options**: None.

#### 10. `lcr_discovery_overview`
* **Default Title**: `LCR Discovery — Large Candle Region Analysis`
* **Python Renderer**: `render_lcr_discovery_overview` in `lcr_discovery_overview.py`
* **Visual Purpose**: Reviews retraces, break times, and statistics in large candle zones.
* **YAML Options**:
  * `show_break_time_of_day_stats` *(bool)*: Shows entry timing statistics.
  * `min_breaks_per_hour` *(int)*: Filter threshold.
  * `show_retrace_stats`, `show_r2r_stats` *(bool)*: Visual grid visibility.

#### 11. `filter_discovery`
* **Default Title**: `Filter Discovery`
* **Python Renderer**: `render_filter_discovery` in `filter_discovery.py`
* **Visual Purpose**: Sweeps and evaluates downside filters (like EMA trend rules) across resampled price bars and tick data, mapping positive statistical impacts.
* **YAML Options**:
  * `bar_tf` *(string)*: Resolution of the bar timeframe (e.g. `5m`).
  * `htf_tf` *(string)*: Timeframe of the higher timeframe filter (e.g. `15m`).
  * `ema_period` *(integer)*: Lookback period for the EMA trend filter (e.g. `50`).
  * `atr_period` *(integer)*: Volatility period for the ATR (e.g. `14`).
  * `include_micro` *(boolean)*: True to include micro contracts.
  * `top_n_runs` *(integer)*: Caps the number of top runs evaluated.
  * `min_trades` *(integer)*: Trade count floor required to include the run.
  * `sort_by` *(string, choices: `net_pnl`, `trades`, `win_rate`)*: KPI for sorting results.
  * `include_run_id_regex`, `exclude_run_id_regex` *(string)*: Regex filters.

---

### Category J: Exit Policy Optimization
Simulates and optimizes trade exits, trailing stop behaviors, and time stops.

#### 1. `exit_policy_simulation`
* **Default Title**: `Exit Policy Simulation`
* **Python Renderer**: `render_exit_policy_simulation` in `exit_policy_simulation.py`
* **Visual Purpose**: Simulates alternative trailing stop, Breakeven, Chandelier, and Time stop rules on executed trades to calculate potential PnL improvements.
* **YAML Options**:
  * `top_n_runs` *(integer)*: Maximum runs evaluated.
  * `min_trades` *(integer)*: Minimum trade counts required.
  * `tick_size` *(number)*: Target resolution.
  * `atr_tf` *(string)*: ATR timeframe (e.g. "5m").
  * `use_bid_ask_triggers` *(boolean)*: Sets trigger type.

#### 2. `exit_policy_trade_debug`
* **Default Title**: `Exit Policy Debug`
* **Python Renderer**: `render_exit_policy_trade_debug` in `exit_policy_trade_debug.py`
* **Visual Purpose**: Drills down into individual trade paths, plotting price candles alongside step-by-step trailing stop levels.
* **YAML Options**:
  * `run_id` *(string)*: Target strategy run.
  * `trade_idx` *(integer)*: Specific trade index.
  * `run_all_trades` *(boolean)*: Simulates all trades.
  * `max_trades` *(integer)*: Limit for simulated trade counts.
  * `show_pnl_chart` *(boolean)*: Renders inline PnL charts.

#### 3. `exit_policy_simulation2`
* **Default Title**: `Exit Policy Simulation 2`
* **Python Renderer**: `render_exit_policy_simulation2` in `exit_policy_simulation2.py`
* **Visual Purpose**: Enhanced exit policy simulator incorporating risk-profile metrics and downside cvars to verify exit safety.
* **YAML Options**: Similar to `exit_policy_simulation` options.

---

### Category K: Regime Recommender & Prediction
Designed for forward-horizon predictions and optimizer sweeps.

#### 1. `horizon_overview`
* **Default Title**: `Horizon Prediction Overview`
* **Python Renderer**: `render_horizon_overview` in `horizon_overview.py`
* **Visual Purpose**: Evaluates active prediction models, plotting expected edge values, directional ECE scores, and calibration metrics.
* **YAML Options**:
  * `store_dir` *(string)*: Path to the prediction database.
  * `instrument` *(string)*: e.g. "NQ".
  * `contract` *(string)*: e.g. "06-26".
  * `drift_recent_n` *(integer)*: History window size for drift detection.

#### 2. `optimization_overview`
* **Default Title**: `Optimization Results Overview`
* **Python Renderer**: `render_optimization_overview` in `optimization_overview.py`
* **Visual Purpose**: Renders a comprehensive review of parameter sweeps, plotting net profit against parameter axes.
* **YAML Options**:
  * `min_trades` *(integer)*: Minimum trades floor.
  * `top_n` *(integer)*: Renders top N combinations.

#### 3. `regime_parameter_recommendation`
* **Default Title**: `Regime Parameter Recommendation`
* **Python Renderer**: `render_regime_parameter_recommendation` in `regime_parameter_recommendation.py`
* **Visual Purpose**: Recommends optimized parameter settings mapped to active regime states.
* **YAML Options**: None.

---

### Category L: Large Candle Excursion Research
Dissects price behavior and setups following massive directional bars (1m, 5m, 15m).

#### 1. `large_candle_excursion_summary`
* **Default Title**: `Large Candle Excursion — Summary`
* **Python Renderer**: `render_large_candle_excursion_summary` in `large_candle_excursion_summary.py`
* **Visual Purpose**: Master dashboard summarizing price action and excursion rates following outsized price movements.
* **YAML Options**: None.

#### 2. `large_candle_excursion_distributions`
* **Default Title**: `Large Candle Excursion — Distributions & Breakdowns`
* **Python Renderer**: `render_large_candle_excursion_distributions` in `large_candle_excursion_distributions.py`
* **Visual Purpose**: Compiles detailed histograms of price excursions, broken down by timeframe, trend direction, and candle size.
* **YAML Options**:
  * `include_distribution_tables` *(boolean)*: Render distribution tables.
  * `include_timeframe_breakdown` *(boolean)*: Resample outputs.

#### 3. `large_candle_excursion_target_curves`
* **Default Title**: `Large Candle Excursion — Target Curve Analysis`
* **Python Renderer**: `render_large_candle_excursion_target_curves` in `large_candle_excursion_target_curves.py`
* **Visual Purpose**: Plots the probability curve of hitting various Target Profit levels, assisting in optimal reward/risk ratio selection.
* **YAML Options**:
  * `top_n` *(integer)*: Caps series rendered.
  * `min_n` *(integer)*: Minimum sample filter.

#### 4. `large_candle_excursion_threshold_hits`
* **Default Title**: `Large Candle Excursion — Threshold Hit Rates`
* **Python Renderer**: `render_large_candle_excursion_threshold_hits` in `large_candle_excursion_threshold_hits.py`
* **Visual Purpose**: Compiles percentage probabilities that price will reach specific tick threshold levels after large onset candles.
* **YAML Options**: None.

#### 5. `large_candle_excursion_event_table`
* **Default Title**: `Large Candle Excursion — Event Detail Table`
* **Python Renderer**: `render_large_candle_excursion_event_table` in `large_candle_excursion_event_table.py`
* **Visual Purpose**: Renders a comprehensive, sortable grid listing every large candle onset event with its local details (volume, range).
* **YAML Options**:
  * `top_n` *(integer)*: Caps rows printed.

#### 6. `large_candle_excursion_methodology`
* **Default Title**: `Large Candle Excursion — Methodology`
* **Python Renderer**: `render_large_candle_excursion_methodology` in `large_candle_excursion_methodology.py`
* **Visual Purpose**: Displays theoretical documentation and calculation guides explaining mathematical onset and excursion logic.
* **YAML Options**: None.

#### 7. `large_candle_excursion_trade_summary`
* **Default Title**: `Large Candle Excursion — Trade Analysis Summary`
* **Python Renderer**: `render_large_candle_excursion_trade_summary` in `large_candle_excursion_trade_summary.py`
* **Visual Purpose**: Summarizes backtest trades that executed within large candle regions, comparing metrics to non-regional trades.
* **YAML Options**: None.

#### 8. `large_candle_excursion_trade_comparison`
* **Default Title**: `Large Candle Excursion — Continuation vs Reverse Comparison`
* **Python Renderer**: `render_large_candle_excursion_trade_comparison` in `large_candle_excursion_trade_comparison.py`
* **Visual Purpose**: Evaluates trading expectancies for strategies attempting to follow continuation vs strategies fading the moves (reversal).
* **YAML Options**: None.

#### 9. `large_candle_excursion_trade_event_table`
* **Default Title**: `Large Candle Excursion — Trade Event Detail Table`
* **Python Renderer**: `render_large_candle_excursion_trade_event_table` in `large_candle_excursion_trade_event_table.py`
* **Visual Purpose**: Details every single executed trade in large candle regions, listing entry times, prices, and realized adverse/favorable excursions.
* **YAML Options**:
  * `top_n` *(integer)*: Caps rows.

#### 10. `large_candle_excursion_volume_context`
* **Default Title**: `Large Candle Excursion — Volume Context`
* **Python Renderer**: `render_large_candle_excursion_volume_context` in `large_candle_excursion_volume_context.py`
* **Visual Purpose**: Cross-tabs excursion probability based on volume surge multipliers at onset times (conviction checks).
* **YAML Options**: None.

#### 11. `large_candle_excursion_structure_context`
* **Default Title**: `Large Candle Excursion — Candle Structure Context`
* **Python Renderer**: `render_large_candle_excursion_structure_context` in `large_candle_excursion_structure_context.py`
* **Visual Purpose**: Maps expectancies to the candle body-to-range ratios (wick sizes) of onset signals.
* **YAML Options**: None.

#### 12. `large_candle_excursion_volatility_context`
* **Default Title**: `Large Candle Excursion — Volatility Context`
* **Python Renderer**: `render_large_candle_excursion_volatility_context` in `large_candle_excursion_volatility_context.py`
* **Visual Purpose**: Conditions excursion hit rates on rolling ATR percentiles at the time of large candle onset.
* **YAML Options**: None.

#### 13. `large_candle_excursion_interactions`
* **Default Title**: `Large Candle Excursion — Context Interaction Tables`
* **Python Renderer**: `render_large_candle_excursion_interactions` in `large_candle_excursion_interactions.py`
* **Visual Purpose**: Displays joint interaction matrices (e.g. Volume × Volatility) to identify maximum probability confluence zones.
* **YAML Options**: None.

#### 14. `large_candle_excursion_findings_executive_summary`
* **Default Title**: `Large Candle Excursion Findings — Executive Summary`
* **Python Renderer**: `render_large_candle_excursion_findings_executive_summary` in `large_candle_excursion_findings_executive_summary.py`
* **Visual Purpose**: High-level summary of core research findings derived from large candle sweeps.
* **YAML Options**: None.

#### 15. `large_candle_excursion_findings_top_discoveries`
* **Default Title**: `Large Candle Excursion Findings — Top Discoveries`
* **Python Renderer**: `render_large_candle_excursion_findings_top_discoveries` in `large_candle_excursion_findings_top_discoveries.py`
* **Visual Purpose**: Displays the highest-fidelity, most robust reversal/continuation candidates found.
* **YAML Options**:
  * `top_n` *(integer)*: Caps rows printed.

#### 16. `large_candle_excursion_findings_interactions`
* **Default Title**: `Large Candle Excursion Findings — Strongest Interaction Effects`
* **Python Renderer**: `render_large_candle_excursion_findings_interactions` in `large_candle_excursion_findings_interactions.py`
* **Visual Purpose**: Summarizes the best multi-context confluence setups.
* **YAML Options**: None.

#### 17. `large_candle_excursion_findings_fragility`
* **Default Title**: `Large Candle Excursion Findings — Fragility Warnings`
* **Python Renderer**: `render_large_candle_excursion_findings_fragility` in `large_candle_excursion_findings_fragility.py`
* **Visual Purpose**: Lists key robustness warnings, highlighting setups vulnerable to spread friction or parameter decay.
* **YAML Options**: None.

#### 18. `large_candle_excursion_findings_next_tests`
* **Default Title**: `Large Candle Excursion Findings — Suggested Next Tests`
* **Python Renderer**: `render_large_candle_excursion_findings_next_tests` in `large_candle_excursion_findings_next_tests.py`
* **Visual Purpose**: Lists recommended next-step hypotheses and validation parameters.
* **YAML Options**: None.

#### 19. `large_candle_excursion_findings_methodology`
* **Default Title**: `Large Candle Excursion Findings — Methodology`
* **Python Renderer**: `render_large_candle_excursion_findings_methodology` in `large_candle_excursion_findings_methodology.py`
* **Visual Purpose**: Documentation on calculations and robust validation thresholds.
* **YAML Options**: None.

#### 20. `large_candle_excursion_findings_interpretation`
* **Default Title**: `Large Candle Excursion Findings — Research Interpretation Guide`
* **Python Renderer**: `render_large_candle_excursion_findings_interpretation` in `large_candle_excursion_findings_interpretation.py`
* **Visual Purpose**: Structural interpretation manual explaining how to read the statistical matrices.
* **YAML Options**: None.

#### 21. `large_candle_excursion_findings_families`
* **Default Title**: `Large Candle Excursion — Top Setup Families`
* **Python Renderer**: `render_large_candle_excursion_findings_families` in `large_candle_excursion_findings_families.py`
* **Visual Purpose**: Renders setup rules grouped into standardized structural families.
* **YAML Options**:
  * `top_n` *(integer)*: Caps rows.

#### 22. `large_candle_excursion_findings_decision_engine`
* **Default Title**: `Large Candle Excursion Findings — Reversal Decision Engine`
* **Python Renderer**: `render_large_candle_excursion_findings_decision_engine` in `large_candle_excursion_findings_decision_engine.py`
* **Visual Purpose**: Grid showing trade-execution verdicts generated by the automated decision model.
* **YAML Options**: None.

#### 23. `large_candle_excursion_elite_reversal_setup_extractor`
* **Default Title**: `Large Candle Excursion Findings — Elite Reversal Setup Extractor`
* **Python Renderer**: `render_large_candle_excursion_elite_reversal_setup_extractor` in `large_candle_excursion_elite_reversal_setup_extractor.py`
* **Visual Purpose**: Displays elite reversal rules passing extreme quality checks.
* **YAML Options**: None.

#### 24. `large_candle_excursion_recursive_edge_search`
* **Default Title**: `Large Candle Excursion Findings — Recursive Edge Search`
* **Python Renderer**: `render_large_candle_excursion_recursive_edge_search` in `large_candle_excursion_recursive_edge_search.py`
* **Visual Purpose**: Renders rules derived from recursive multidimensional edge scans.
* **YAML Options**: None.

#### 25. `large_candle_excursion_edge_validation_engine`
* **Default Title**: `Large Candle Excursion Findings — Edge Validation Engine`
* **Python Renderer**: `render_large_candle_excursion_edge_validation_engine` in `large_candle_excursion_edge_validation_engine.py`
* **Visual Purpose**: Displays walk-forward validation parameters and quality criteria for excursion edges.
* **YAML Options**: None.

#### 26. `large_candle_excursion_strategy_construction_engine`
* **Default Title**: `Large Candle Excursion Findings — Strategy Construction Engine`
* **Python Renderer**: `render_large_candle_excursion_strategy_construction_engine` in `large_candle_excursion_strategy_construction_engine.py`
* **Visual Purpose**: Compiles rules into standardized C#/JSON deployment templates.
* **YAML Options**: None.

#### 27. `large_candle_excursion_strategy_blueprints`
* **Default Title**: `Large Candle Excursion Findings — Strategy Blueprints`
* **Python Renderer**: `render_large_candle_excursion_strategy_blueprints` in `large_candle_excursion_strategy_blueprints.py`
* **Visual Purpose**: Renders high-fidelity architectural blueprints mapping setup conditions to entry execution rules.
* **YAML Options**: None.

#### 28. `large_candle_excursion_session_context`
* **Default Title**: `Large Candle Excursion — Session Context`
* **Python Renderer**: `render_large_candle_excursion_session_context` in `large_candle_excursion_session_context.py`
* **Visual Purpose**: Conditions excursion probability on active session windows.
* **YAML Options**: None.

#### 29. `large_candle_excursion_time_segments`
* **Default Title**: `Large Candle Excursion — Time Segment Analysis`
* **Python Renderer**: `render_large_candle_excursion_time_segments` in `large_candle_excursion_time_segments.py`
* **Visual Purpose**: Maps excursion expectancies to intraday half-hour time segments.
* **YAML Options**: None.

#### 30. `large_candle_excursion_signal_context`
* **Default Title**: `Large Candle Excursion — Signal Candle Context Intelligence`
* **Python Renderer**: `render_large_candle_excursion_signal_context` in `large_candle_excursion_signal_context.py`
* **Visual Purpose**: Highlights the candle attributes of winning signals.
* **YAML Options**: None.

#### 31. `large_candle_excursion_context_diagnostics`
* **Default Title**: `Large Candle Excursion — Signal Context Coverage Diagnostics`
* **Python Renderer**: `render_large_candle_excursion_context_diagnostics` in `large_candle_excursion_context_diagnostics.py`
* **Visual Purpose**: Verifies statistical sample size sufficiency across contextual dimensions.
* **YAML Options**: None.

#### 32. `large_candle_excursion_context_families`
* **Default Title**: `Large Candle Excursion — Top Context-Conditioned Setup Families`
* **Python Renderer**: `render_large_candle_excursion_context_families` in `large_candle_excursion_context_families.py`
* **Visual Purpose**: Lists winning setup families conditioned on multi-context attributes.
* **YAML Options**: None.

#### 33. `large_candle_excursion_reversal_leaders`
* **Default Title**: `Large Candle Excursion — Large Reversal Move Leaders`
* **Python Renderer**: `render_large_candle_excursion_reversal_leaders` in `large_candle_excursion_reversal_leaders.py`
* **Visual Purpose**: Focuses on large outsized reversal moves following onset signals.
* **YAML Options**: None.

#### 34. `large_candle_excursion_session_large_moves`
* **Default Title**: `Large Candle Excursion — Large Move Probability by Session`
* **Python Renderer**: `render_large_candle_excursion_session_large_moves` in `large_candle_excursion_session_large_moves.py`
* **Visual Purpose**: Renders a session probability cross-tab for outsized trades.
* **YAML Options**: None.

#### 35. `large_candle_excursion_strategy_cards`
* **Default Title**: `Large Candle Excursion — Strategy Cards`
* **Python Renderer**: `render_large_candle_excursion_strategy_cards` in `large_candle_excursion_strategy_cards.py`
* **Visual Purpose**: Premium visual trading cards outlining the setup conditions of winning candidates.
* **YAML Options**:
  * `top_n` *(integer)*: Caps cards printed.

#### 36. `large_candle_excursion_regime_discovery`
* **Default Title**: `Large Candle Excursion Findings - Regime Discovery`
* **Python Renderer**: `render_large_candle_excursion_regime_discovery` in `large_candle_excursion_regime_discovery.py`
* **Visual Purpose**: Sweeps and documents optimal parameters mapped to discovered regime structures.
* **YAML Options**: None.

#### 37. `large_candle_excursion_regime_findings_explainer`
* **Default Title**: `Large Candle Excursion - Regime Findings Explainer`
* **Python Renderer**: `render_large_candle_excursion_regime_findings_explainer` in `large_candle_excursion_regime_findings_explainer.py`
* **Visual Purpose**: Theoretical documentation detailing calculated regime stability metrics.
* **YAML Options**:
  * `top_n` *(integer)*: Ranks count limit.

#### 38. `large_candle_excursion_discovery_summary`
* **Default Title**: `Large Candle Excursion Discovery — Executive Summary`
* **Python Renderer**: `render_large_candle_excursion_discovery_summary` in `large_candle_excursion_discovery_summary.py`
* **Visual Purpose**: Executive overview page compiling statistical outcomes from excursion sweeps.
* **YAML Options**: None.

#### 39. `large_candle_excursion_discovery_broad_scan`
* **Default Title**: `Large Candle Excursion Discovery — Broad Scan Results`
* **Python Renderer**: `render_large_candle_excursion_discovery_broad_scan` in `large_candle_excursion_discovery_broad_scan.py`
* **Visual Purpose**: Displays results of broad parameter scans.
* **YAML Options**:
  * `top_n` *(integer)*: Caps rows.

#### 40. `large_candle_excursion_discovery_refinement`
* **Default Title**: `Large Candle Excursion Discovery — Refinement Results`
* **Python Renderer**: `render_large_candle_excursion_discovery_refinement` in `large_candle_excursion_discovery_refinement.py`
* **Visual Purpose**: Displays statistical results of refined parameter scans.
* **YAML Options**: None.

#### 41. `large_candle_excursion_discovery_chains`
* **Default Title**: `Large Candle Excursion Discovery — Chained Discoveries`
* **Python Renderer**: `render_large_candle_excursion_discovery_chains` in `large_candle_excursion_discovery_chains.py`
* **Visual Purpose**: Maps parameter relationships between consecutive large candle excursions.
* **YAML Options**: None.

#### 42. `large_candle_excursion_discovery_robustness`
* **Default Title**: `Large Candle Excursion Discovery — Robustness Validation`
* **Python Renderer**: `render_large_candle_excursion_discovery_robustness` in `large_candle_excursion_discovery_robustness.py`
* **Visual Purpose**: Validates that excursion edges hold up to alternative random walk distributions.
* **YAML Options**: None.

#### 43. `large_candle_excursion_discovery_diagnostics`
* **Default Title**: `Large Candle Excursion Discovery — Diagnostics`
* **Python Renderer**: `render_large_candle_excursion_discovery_diagnostics` in `large_candle_excursion_discovery_diagnostics.py`
* **Visual Purpose**: System diagnostic center mapping file integrity across sweeps.
* **YAML Options**: None.

#### 44. `large_candle_excursion_discovery_next_steps`
* **Default Title**: `Large Candle Excursion Discovery — Suggested Next Steps`
* **Python Renderer**: `render_large_candle_excursion_discovery_next_steps` in `large_candle_excursion_discovery_next_steps.py`
* **Visual Purpose**: Recommends further forward observation tests.
* **YAML Options**: None.

#### 45. `large_candle_excursion_discovery_methodology`
* **Default Title**: `Large Candle Excursion Discovery — Methodology`
* **Python Renderer**: `render_large_candle_excursion_discovery_methodology` in `large_candle_excursion_discovery_methodology.py`
* **Visual Purpose**: Calculations documentation explaining math and metrics foundations.
* **YAML Options**: None.

---

## 4. Visual Presets & UI Customization Guide

Several report sections (like `market_regime_discovery` and `run_executive_profile_cards`) support advanced layout controls, density settings, and color palettes to create premium, high-impact HTML reports.

### HSL Style Presets
The `style_preset` property customizes the report theme:
* `executive_dark`: A sleek, state-of-the-art dark mode utilizing a highly curated charcoal background with HARMONIOUS HSL highlights. (Highly Recommended).
* `classic_dark`: Default retro black theme with high-contrast text layers.
* `clean_light`: A vibrant light theme with harmonized styling, custom fonts, and subtle shadows.
* `compact_dark`: A highly condensed, low-padding dark mode designed for dense multi-monitor tracking.

### Density and Layout Controls
* `style_density`:
  * `compact`: Minimizes cell padding and margins to pack maximum statistics onto a single screen.
  * `normal`: Balanced, comfortable readability.
  * `comfortable`: Larger typography, spacious layouts, and visual breathing room.
* `style_accent`:
  * Sets the principal highlights across tables and headers: `blue`, `green`, `amber`, `violet`, `red`.

### Background Overlay Modes (`background_style` in Exec cards)
* `solid`: Plain black card background.
* `image-cover`: Displays the strategy's backtest image fully across the card.
* `image-soft-overlay`: Renders a translucent dark layer over the background image (ideal for light strategy charts).
* `image-dark-overlay`: Renders a high-contrast dark overlay to ensure maximum text readability (ideal for complex or busy charts).

---

## 5. Lineage of Other Root YAML Files

In addition to our modularized report configs, the repo contains other standalone YAML files. Each addresses a specific phase of the workflow:

```
+------------------+     +--------------------+     +-------------------+
|   Ingestion &    |     |  Regime Sweeping   |     |    Simulation &   |
|   Comparison     |     |    & Discovery     |     |   Apex Auditing   |
|                  |     |                    |     |                   |
| - report.yaml    | --> | - strat.yaml       | --> | - PatternE.yaml   |
| - report55.yaml  |     | - regime_disc.yaml |     | - exitPolicy.yaml |
| - myReport.yaml  |     | - signalDiscovery  |     | - pattern_engine  |
+------------------+     +--------------------+     +-------------------+
```

### Ingestion & Classic Comparison
* **`report.yaml`**: The standard root config rendering `run_executive_profile_cards` and `weekly_leaderboard_cards` to evaluate backtests.
* **`report55.yaml`**: A lightweight scoreboard rendering weekly leaderboards.
* **`myReport.yaml`**: A minimalist configuration rendering the ranked `comparison_overview` grid.

### Discovery & Regime Sweeping
* **`strat.yaml` & `candle.yaml`**: Sweeps candlestick signals, rendering `strategy_discovery_unified` and `candle_discovery_overview`.
* **`regime_disc.yaml` & `regime_disc2.yaml`**: Sweeps market regimes, outputting `market_regime_discovery` sections.
* **`signalDiscovery.yaml` & `strategy_discovery_report.yaml`**: Standard configurations executing the entire walk-forward Strategy Discovery pipeline across all 22+ sections.

### Simulation & Auditing
* **`pattern.yaml` & `PatternE.yaml`**: Parameterized pattern sweeps, rendering `pattern_engine_overview` and MC regimes.
* **`exitPolicyReport.yaml`**: Runs advanced tick simulators, rendering `exit_policy_simulation2` summaries.
* **`pattern_engine.yaml`**: Audits trades against concurrent indicators, outputting `trade_pattern_audit` ledgers.
* **`lcr_discovery.yaml`**: Executes the Large Candle Region analysis pipeline.

---

## 6. Guidelines for Adding New Report Sections

To add a new HTML report section, always follow these rules:

1. **Create the Python Module**: Write the rendering function inside `src/ta_foundation/reports/html/sections/<section_id>.py`. Use the signature `def render_<section_id>(ctx: dict) -> str:`.
2. **Access Configuration Safely**: Read options via `ctx.get("options", {}).get(...)` rather than catching KeyErrors. Keep defaults standard.
3. **Register the Section**: Import your renderer in `src/ta_foundation/reports/html/registry.py` and register it inside `SECTION_REGISTRY` as a `SectionDef`:
   ```python
   "my_new_section": SectionDef(
       id="my_new_section",
       default_title="My New Section Title",
       render_fn=render_my_new_section,
   )
   ```
4. **Define Metadata in Capabilities**: Update `src/ta_foundation/web/capabilities.py` by mapping your section ID to its category inside `_category_for_section()` and configuration block inside `_config_blocks_for_section()`.
5. **Document the Section**: Keep documentation updated inline in this guide.

---

## 7. Discovered NinjaTrader 8 Strategy Templates

I have successfully generated NinjaTrader 8 strategy parameter templates representing the three high-expectancy quantitative edges discovered on continuous NQ futures. These templates are formatted specifically for the framework's C# `StrategyDiscoveryFilter` strategy.

The templates are saved under `docs/reports_documentation/ninja_trader_templates/`:

### 1. Volatility Breakout (High Vol Expansion Scoped)
* **File Path**: [`Volatility_Breakout_High_Vol_Edge.xml`](file:///d:/Backup/projects/PythonProject/ta_foundation/docs/reports_documentation/ninja_trader_templates/Volatility_Breakout_High_Vol_Edge.xml)
* **Key Configuration**:
  * `RegimeMode`: `HighVolOnly` — strictly scoped to high-volatility days to maximize breakout momentum.
  * `AdxThreshold`: `25` — requires clear momentum confirmation.
  * `ExitPolicy`: `AtrTrail` — trails a standard volatility-adjusted ATR stop.
  * `StopTicks`: `534` ($2,670) — p75 MAE of winners under high volatility expansion.
  * `TargetTicks`: `938` ($4,690) — p50 MFE of winners.
  * `MaxDailyLossUsd`: `2670` — capped at one full trade loss value.
  * `MaxDailyTrades`: `3`

### 2. Swing Level Breakout (Ranging Wide Scoped)
* **File Path**: [`Swing_Level_Breakout_Ranging_Edge.xml`](file:///d:/Backup/projects/PythonProject/ta_foundation/docs/reports_documentation/ninja_trader_templates/Swing_Level_Breakout_Ranging_Edge.xml)
* **Key Configuration**:
  * `RegimeMode`: `RangingOnly` — scoped strictly to ranging days to capture clean pivot tests and avoid trend overrun.
  * `ExitPolicy`: `AtrTrail` — trails a standard volatility-adjusted ATR stop.
  * `StopTicks`: `245` ($1,225) — p75 MAE of winners during pivot extreme test.
  * `TargetTicks`: `536` ($2,680) — p50 MFE of winners.
  * `MaxDailyLossUsd`: `1227` — capped at one full trade loss.
  * `MaxDailyTrades`: `5`

### 3. Prior Session Reaction Close Scalp
* **File Path**: [`Prior_Session_Reaction_Close_Scalp.xml`](file:///d:/Backup/projects/PythonProject/ta_foundation/docs/reports_documentation/ninja_trader_templates/Prior_Session_Reaction_Close_Scalp.xml)
* **Key Configuration**:
  * `RegimeMode`: `Any` — trades prior close reactions universally under main intraday hours.
  * `AllowRTH`: `True`, `AllowONH`: `False`, `AllowETH`: `False` — restricted strictly to US RTH main session to leverage volume liquidity.
  * `ExitPolicy`: `FixedRR` — tight scalp setup with fixed TP/SL.
  * `StopTicks`: `8` ($40) — high-conviction tight scalp stop to capture precise pivot bounce.
  * `TargetTicks`: `20` ($100) — quick profit target on prior close rejections.
  * `MaxDailyLossUsd`: `150` — protects from multi-stop chop.
  * `MaxDailyTrades`: `6`

### How to Install in NinjaTrader 8
1. Copy the desired `.xml` template file from `docs/reports_documentation/ninja_trader_templates/`.
2. Save it to your local NinjaTrader 8 templates folder:
   `Documents\NinjaTrader 8\templates\Strategy\StrategyDiscoveryFilter\`
3. Open the `StrategyDiscoveryFilter` strategy in NinjaTrader.
4. Right-click, select **Templates** -> **Load**, and choose the template you saved.
5. All discovered quantitative parameters, session filters, and risk controls will load automatically.

