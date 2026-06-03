# Report Sections Catalog

**Status:** Complete browsable catalog of 120+ HTML report sections  
**Last Updated:** May 24, 2026  
**Purpose:** Discover what reports you can build, understand each section's role  

---

## Quick Start: Find a Section

### "I want to..."

**...compare multiple backtest runs side-by-side**
→ Start with `comparison_overview`, then add `equity_curve_comparison`, `daily_scoreboard`

**...find the best strategy parameters**
→ `strategy_discovery_ranked_table`, `strategy_discovery_evaluation`

**...understand why a trade entered/exited**
→ `trade_candle_overlay`, `exit_policy_trade_debug`

**...find entry signals I didn't know about**
→ `candle_discovery_overview`, `ma_discovery_overview`, `orb_discovery_overview`

**...analyze large price moves**
→ `large_candle_excursion_summary`, `large_candle_excursion_distributions`

**...see parameter sensitivity**
→ `strategy_discovery_parameter_sensitivity`

**...export a quick snapshot**
→ `run_snapshot_clipboard` (copy-paste ready)

---

## Section Categories

### Category: Core Run Analysis (10 sections)

Display-only analysis of a single backtest run or comparison across runs.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `comparison_overview` | Comparison Overview | Summary of multiple runs side-by-side | 2+ runs |
| `equity_curve_comparison` | Equity Curve Comparison | Plot equity curves for all runs | Trades, daily |
| `run_kpi_cards` | Run KPI Cards | Key metrics as card widgets | Summary block |
| `run_snapshot_clipboard` | Snapshot (Clipboard) | Copy-paste summary | Summary block |
| `run_settings_table` | Run Settings | Strategy parameters used | Settings CSV |
| `run_metadata_cards` | Run Metadata Cards | Run info (dates, version, etc.) | Trade metadata |
| `run_executive_profile_cards` | Executive Strategy Profiles | "Executive summary" cards | Computed profiles |
| `run_card_catalog` | Run Card Catalog | All PNG cards exported during run | PNG assets |
| `drawdown_curve` | Drawdown Analysis | Peak-to-valley drawdown chart | Trades, daily |
| `apex_drawdown_survival_profile` | APEX Trailing Model | Drawdown recovery profile | Trades, daily |

---

### Category: Daily & Session Analysis (8 sections)

Breakdowns by trading day, session, or time of day.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `daily_scoreboard` | Daily Scoreboard | Win/loss, PnL summary per day | Daily CSV |
| `daily_leaderboard_cards` | Daily Leaderboard Cards | Ranked daily performance cards | Daily CSV |
| `daily_winner_spotlight` | Daily Winner Spotlight | The day with best PnL | Daily CSV |
| `deployment_board_insight` | Deployment Board (Insight) | Strategy's session-by-session view | Daily CSV |
| `deployment_board_gods` | Deployment Board (Gods) | Top strategies across all sessions | Daily CSV |
| `deployment_board_poster` | Deployment Board (Poster) | Formatted for printing | Daily CSV |
| `weekly_leaderboard_cards` | Weekly Leaderboard Cards | Weekly performance ranking | Daily CSV |
| `trades_intraday_pnl_by_day` | Intraday PnL by Day | Hourly P&L distribution | Trades |

---

### Category: Execution Diagnostics (6 sections)

Detailed inspection of entries, exits, and trade mechanics.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `trade_candle_overlay` | Trade Candle Overlay | Entry/exit prices on OHLC chart | Trades, minute bars |
| `tick_data_diagnostics` | Tick Data Diagnostics | Slippage, spread analysis | Tick data |
| `exit_policy_simulation` | Exit Policy Simulation (v1) | Test exit rules | Trades, minute bars |
| `exit_policy_simulation2` | Exit Policy Simulation (v2) | Alternative exit tester | Trades, minute bars |
| `exit_policy_trade_debug` | Exit Trade Debug | Why did this trade exit? | Trades, minute bars |
| `trade_pattern_audit` | Trade Pattern Audit | Which patterns drove trades | Trades, patterns |

---

### Category: Pattern Engine (7 sections)

Pattern discovery, clustering, and robustness analysis.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `pattern_engine_overview` | Pattern Engine Overview | Summary of all patterns found | Pattern artifacts |
| `pattern_engine_diagnostics` | Pattern Engine Diagnostics | Detailed pattern statistics | Pattern artifacts |
| `pattern_engine_mc_regime` | Pattern Engine (Monte Carlo) | Robustness by regime | Pattern artifacts |
| `pattern_market_discovery` | Pattern Market Discovery | Which patterns exist in market | Minute bars, patterns |
| `pattern_cluster_drilldown` | Pattern Cluster Drilldown | Zoom into a pattern cluster | Pattern artifacts |
| `filter_discovery` | Filter Discovery | Trade filtering rules | Trades, market data |
| `drawdown_curve` | Drawdown Curve | Ongoing drawdown | Trades, daily |

---

### Category: Market Regime Analysis (3 sections)

Market condition classification and recommendations.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `market_regime_discovery` | Market Regime Discovery | ADX, volatility regimes | Minute bars |
| `anchor_interaction_hourly_profile` | Hourly Profile by Regime | Trade distribution per regime | Trades, hour-of-day |
| `regime_parameter_recommendation` | Regime Parameter Recommendation | Suggested params per regime | Regime classification |

---

### Category: Moving Average Anchor (6 sections)

Entry/exit signals based on MA confluence.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `anchor_interaction_config` | Anchor Config | What MAs are configured | YAML (anchor_interaction) |
| `anchor_interaction_overview` | Anchor Overview | MA alignment & confluence | Minute bars, trades |
| `anchor_interaction_anchor_matrix` | Anchor Matrix | Which MAs are confluent | Minute bars |
| `anchor_tp_sl_recommendations` | TP/SL Recommendations | Suggested profit targets/stops | Minute bars, MA levels |
| `anchor_interaction_tp_sl_spec` | TP/SL Spec | Detailed TP/SL analysis | Minute bars |
| `anchor_interaction_diagnostics` | Anchor Diagnostics | Detailed MA interaction stats | Minute bars, trades |

---

### Category: Strategy Discovery (25 sections)

The largest category — discovering and validating entry/exit rules.

**Subcategory: Discovery Overview & Ranking**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `strategy_discovery_overview` | Strategy Discovery Overview | Summary of all discovered signals | Discovery results |
| `strategy_discovery_ranked_table` | Ranked Entry Signals | Top signals by profit factor | Discovery results |
| `strategy_discovery_comparison` | Strategy Comparison | Compare 2+ discovered strategies | Discovery results |
| `strategy_discovery_unified` | Unified Discovery | All signal families on one view | Discovery results |

**Subcategory: Entry & Exit Rules**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `strategy_discovery_entry_rules` | Entry Rules | Discovered entry conditions | Discovery results |
| `strategy_discovery_filter_rules` | Filter Rules | Pre-entry filters | Discovery results |
| `strategy_discovery_exit_policies` | Exit Policies | Discovered exit rules | Discovery results |
| `strategy_discovery_signal_entries` | Signal Entries (8 families) | Entry signals from 8 discovery families | Discovery results |
| `strategy_discovery_signal_validation` | Signal Validation | Entry signal testing | Discovery results |
| `strategy_discovery_signal_exit_sweep` | Exit Sweep | Test exit parameter combinations | Discovery results |
| `strategy_discovery_signal_simulation` | Signal Simulation | Forward-test signal performance | Discovery results |

**Subcategory: Evaluation & Metrics**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `strategy_discovery_evaluation` | Evaluation | Sharpe, Sortino, MAE/MFE | Discovery results |
| `strategy_discovery_mae_mfe` | MAE/MFE Analysis | Max adverse/favorable excursion | Discovery results |
| `strategy_discovery_risk_metrics` | Risk Metrics | Max DD, calmar, recovery | Discovery results |
| `strategy_discovery_drawdown` | Drawdown Analysis | Peak-to-valley per strategy | Discovery results |
| `strategy_discovery_validation` | Validation (IS/OOS) | In-sample vs out-of-sample | Discovery results |

**Subcategory: Advanced Analysis**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `strategy_discovery_feature_importance` | Feature Importance | Which features matter | Discovery results, model |
| `strategy_discovery_parameter_sensitivity` | Parameter Sensitivity | How sensitive to parameters | Discovery sweep |
| `strategy_discovery_position_sizing` | Position Sizing | Kelly, fixed, volume-based | Discovery results |
| `strategy_discovery_cohort` | Cohort Analysis | Group strategies by family | Discovery results |
| `strategy_discovery_classification` | Classification | Cluster/label strategies | Discovery results |
| `strategy_discovery_regime` | Regime Conditional | Performance per regime | Market regime + results |
| `strategy_discovery_anchor_confluence` | Anchor Confluence | Entry confluence with MAs | MA anchors + discovery |
| `strategy_discovery_nt_template` | NT Template | Generated NinjaScript code | Discovery results |
| `strategy_discovery_decision_ledger` | Decision Ledger | Approval/rejection record | HITL decisions |
| `strategy_discovery_combo_basket` | Combo Basket | Multi-signal combinations | Discovery results |

---

### Category: Entry Strategy Families (7 sections)

Specific entry signal types from the 8-family discovery system.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `candle_discovery_overview` | Candle Discovery Overview | Candle pattern summary | Minute bars, discovery results |
| `candle_discovery_ranking` | Candle Pattern Ranking | Top candle patterns | Discovery results |
| `ma_discovery_overview` | MA Discovery Overview | Moving average crossover summary | Minute bars, discovery results |
| `orb_discovery_overview` | ORB Discovery Overview | Opening Range Breakout summary | Minute bars, discovery results |
| `bb_discovery_overview` | Bollinger Band Discovery | BB touch/break summary | Minute bars, discovery results |
| `lcr_discovery_overview` | Level Discovery (LCR) | Support/resistance level summary | Minute bars, discovery results |
| `premarket_discovery_overview` | Premarket Discovery Overview | Pre-open signals | Premarket data, discovery |

---

### Category: Optimization (1 section)

Parameter sweep results from NinjaTrader optimizer.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `optimization_overview` | Optimization Results | Leaderboard of parameter combos | *_Optimization.csv |

---

### Category: Large Candle Excursion (45 sections)

Deep analysis of large price moves (> 1 ATR or > threshold). The most comprehensive subsystem.

**Subcategory: Summary & Context**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_summary` | LCE Summary | Overview of large moves | Minute bars, LCE config |
| `large_candle_excursion_session_context` | Session Context | Time-of-day when moves occur | LCE results |
| `large_candle_excursion_signal_context` | Signal Context | Market structure before move | LCE results, minute bars |

**Subcategory: Statistical Distributions**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_distributions` | Return Distributions | Price move statistics | LCE results |
| `large_candle_excursion_threshold_hits` | Threshold Hits | Prob. of hitting levels | LCE results |
| `large_candle_excursion_event_table` | Event Table | Large moves as table | LCE results |

**Subcategory: Market Context**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_volume_context` | Volume Context | Volume during large moves | LCE results, tick volume |
| `large_candle_excursion_structure_context` | Structure Context | Support/resistance before move | LCE results, minute bars |
| `large_candle_excursion_volatility_context` | Volatility Context | ATR, momentum before move | LCE results, minute bars |
| `large_candle_excursion_regime_discovery` | Regime Discovery | Which regimes have large moves | LCE results, regime |
| `large_candle_excursion_regime_findings_explainer` | Regime Findings | Per-regime move analysis | LCE results |
| `large_candle_excursion_context_diagnostics` | Context Diagnostics | Feature summary | LCE results |
| `large_candle_excursion_context_families` | Context by Family | Context per trade family | LCE results, families |

**Subcategory: Trade Analysis**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_trade_summary` | Trade Summary | Trades during large moves | Trades, LCE results |
| `large_candle_excursion_trade_comparison` | Trade Comparison | Trade performance in moves | Trades, LCE results |
| `large_candle_excursion_trade_event_table` | Trade Event Table | Detailed trade list | Trades, LCE results |

**Subcategory: Discovery & Findings**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_discovery_summary` | Discovery Summary | What we learned | LCE discovery results |
| `large_candle_excursion_discovery_broad_scan` | Discovery Broad Scan | Initial pattern sweep | LCE discovery results |
| `large_candle_excursion_discovery_refinement` | Discovery Refinement | Refined hypotheses | LCE discovery results |
| `large_candle_excursion_discovery_chains` | Discovery Chains | Cause-effect chains | LCE discovery results |
| `large_candle_excursion_discovery_robustness` | Discovery Robustness | Stress testing patterns | LCE discovery results |
| `large_candle_excursion_discovery_diagnostics` | Discovery Diagnostics | Technical details | LCE discovery results |
| `large_candle_excursion_discovery_next_steps` | Next Steps | Recommended follow-ups | LCE discovery results |
| `large_candle_excursion_discovery_methodology` | Discovery Methodology | How discovery works | Methodology doc |

**Subcategory: Findings (Executive)**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_findings_executive_summary` | Findings Executive Summary | Key conclusions | LCE findings |
| `large_candle_excursion_findings_top_discoveries` | Top Discoveries | Most important findings | LCE findings |
| `large_candle_excursion_findings_interactions` | Interaction Findings | Multi-factor effects | LCE findings |
| `large_candle_excursion_findings_fragility` | Fragility Analysis | Which findings are fragile | LCE findings |
| `large_candle_excursion_findings_interpretation` | Interpretation | What do findings mean | LCE findings |
| `large_candle_excursion_findings_methodology` | Findings Methodology | How findings are derived | Methodology doc |

**Subcategory: Advanced Strategy Building**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_reversal_leaders` | Reversal Leaders | Fastest reversion patterns | LCE results |
| `large_candle_excursion_target_curves` | Target Curves | Probability of reaching prices | LCE results |
| `large_candle_excursion_elite_reversal_setup_extractor` | Elite Reversal Setup | High-probability setups | LCE discovery |
| `large_candle_excursion_recursive_edge_search` | Recursive Edge Search | Nested pattern discovery | LCE discovery |
| `large_candle_excursion_edge_validation_engine` | Edge Validation | Test edge robustness | LCE validation |
| `large_candle_excursion_strategy_construction_engine` | Strategy Constructor | Build strategies from findings | LCE findings |
| `large_candle_excursion_strategy_blueprints` | Strategy Blueprints | Recommended strategy designs | LCE blueprints |
| `large_candle_excursion_strategy_cards` | Strategy Cards | Visual strategy summary | LCE blueprints |
| `large_candle_excursion_session_large_moves` | Session Large Moves | Which sessions have big moves | LCE results |
| `large_candle_excursion_time_segments` | Time Segments | Performance by hour | LCE results |

**Subcategory: Metadata & Navigation**

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `large_candle_excursion_methodology` | LCE Methodology | How LCE analysis works | Methodology doc |
| `large_candle_excursion_interactions` | LCE Interactions | Feature combinations | LCE results |

---

### Category: Extra/Specialized (5 sections)

One-off or specialized renderers.

| Section ID | Title | Purpose | Data Needed |
|---|---|---|---|
| `strategy_parameter_matrix` | Strategy Parameter Matrix | Heatmap of param combos | Optimization results |
| `strategy_momentum_board` | Strategy Momentum Board | Equity curve momentum | Daily CSV |
| `strategy_lifecycle_board` | Strategy Lifecycle Board | Performance over time | Daily CSV |
| `strategy_session_momentum_board` | Session Momentum Board | Intra-session performance | Trades, session times |
| `horizon_overview` | Horizon Prediction Overview | Multi-horizon forecast results | Horizon predictions |

---

## How to Use Sections in YAML

Every section needs an entry in your `report.yaml` config:

```yaml
report:
  title: "My Backtest Report"
  output_filename: "backtest_report.html"

sections:
  - id: comparison_overview
    title: "Comparison Overview"  # optional: override default
    
  - id: daily_scoreboard
    options:
      top_n: 10                   # section-specific options
  
  - id: strategy_discovery_ranked_table
    options:
      min_pf: 1.2                 # only show PF >= 1.2
```

### Common Options

Most sections accept these options:

| Option | Type | Default | Meaning |
|---|---|---|---|
| `title` | string | (auto) | Override section title in report |
| `top_n` | int | 10 | Show top N results (ranking sections) |
| `min_pf` | float | 0.0 | Minimum profit factor filter |
| `min_trades` | int | 1 | Minimum trade count |
| `max_dd` | float | infinity | Maximum drawdown filter |

---

## Finding Sections by Feature

### "I want to analyze..."

| Feature | Section IDs |
|---|---|
| Entry signals | `candle_discovery_overview`, `ma_discovery_overview`, `orb_discovery_overview`, `bb_discovery_overview`, `strategy_discovery_signal_entries` |
| Exit timing | `exit_policy_simulation`, `exit_policy_trade_debug`, `strategy_discovery_exit_policies` |
| Regime performance | `market_regime_discovery`, `strategy_discovery_regime`, `anchor_interaction_hourly_profile` |
| Large price moves | All `large_candle_excursion_*` sections (45 total) |
| Parameter sensitivity | `strategy_discovery_parameter_sensitivity`, `strategy_discovery_evaluation` |
| Daily performance | `daily_scoreboard`, `daily_leaderboard_cards`, `deployment_board_insight` |
| Backtest quality | `strategy_discovery_validation`, `large_candle_excursion_discovery_robustness` |
| Position sizing | `strategy_discovery_position_sizing` |
| Risk metrics | `strategy_discovery_risk_metrics`, `strategy_discovery_drawdown`, `apex_drawdown_survival_profile` |
| Pattern discovery | `pattern_engine_overview`, `pattern_cluster_drilldown`, `pattern_market_discovery` |
| MA confluence | `anchor_interaction_overview`, `anchor_interaction_anchor_matrix`, `strategy_discovery_anchor_confluence` |

---

## Section Readiness Matrix

| Section ID | Status | Data Required | Notes |
|---|---|---|---|
| `comparison_overview` | ✅ Stable | 2+ runs | Most popular starter section |
| `daily_scoreboard` | ✅ Stable | Daily CSV | Essential for daily analysis |
| `strategy_discovery_ranked_table` | ✅ Stable | Discovery results | Top N results by PF |
| `large_candle_excursion_summary` | ✅ Stable | Minute bars, LCE config | LCE is the deepest subsystem |
| `equity_curve_comparison` | ✅ Stable | Trades, daily | Required for visual comparison |
| `pattern_engine_overview` | ⚠️ Beta | Pattern artifacts | Requires pattern_engine: enabled |
| `strategy_discovery_unified` | ⚠️ Beta | All discovery families | Newest unified view |
| `horizon_overview` | ⏳ New | Horizon predictions | Multi-horizon forecasts |

---

## Example Report Configs

### Config 1: Quick Run Analysis

```yaml
report:
  title: "Single Run Summary"
  output_filename: "summary.html"

sections:
  - id: comparison_overview
  - id: run_kpi_cards
  - id: daily_scoreboard
  - id: equity_curve_comparison
  - id: run_snapshot_clipboard
```

### Config 2: Entry Signal Discovery

```yaml
report:
  title: "Signal Discovery"
  output_filename: "signals.html"

sections:
  - id: strategy_discovery_overview
  - id: strategy_discovery_ranked_table
  - id: candle_discovery_overview
  - id: ma_discovery_overview
  - id: orb_discovery_overview
  - id: strategy_discovery_entry_rules
  - id: strategy_discovery_validation
```

### Config 3: Deep Market Analysis

```yaml
report:
  title: "Large Move Analysis"
  output_filename: "lce_deep_dive.html"

sections:
  - id: large_candle_excursion_summary
  - id: large_candle_excursion_distributions
  - id: large_candle_excursion_trade_summary
  - id: large_candle_excursion_findings_executive_summary
  - id: large_candle_excursion_strategy_blueprints
```

### Config 4: Regime & Anchor Analysis

```yaml
report:
  title: "Regime-Aware Strategy"
  output_filename: "regime_analysis.html"

sections:
  - id: market_regime_discovery
  - id: anchor_interaction_overview
  - id: strategy_discovery_regime
  - id: regime_parameter_recommendation
  - id: anchor_interaction_hourly_profile
```

---

## Troubleshooting: Section Not Showing

### "My section appears blank"

**Cause:** Section's required data wasn't enabled in YAML.

**Fix:**
1. Check which top-level YAML block the section needs (e.g., `pattern_engine:`, `strategy_discovery:`)
2. Set `enabled: true` for that block
3. Re-run CLI with updated YAML

**Example:**
```yaml
# For pattern_engine sections:
pattern_engine:
  enabled: true

# For strategy_discovery sections:
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
```

### "I can't find the section I need"

**Solution:**
1. Search this catalog by feature (see "Finding Sections by Feature" above)
2. Scan the category that matches your analysis
3. Read the "Purpose" and "Data Needed" columns

---

## Section Dependencies

Some sections depend on others being enabled:

```
pattern_engine_*
  ↑
  requires: pattern_engine: enabled: true

strategy_discovery_*
  ↑
  requires: strategy_discovery: enabled: true
  
large_candle_excursion_*
  ↑
  requires: minute bars (--market-data)

anchor_interaction_*
  ↑
  requires: anchor_interaction: enabled: true
```

---

## Adding Custom Sections

Extend the section registry by:

1. Create a renderer in `src/ta_foundation/reports/html/sections/my_section.py`:
```python
def render_my_section(ctx: dict[str, Any]) -> str:
    # ctx contains: packages, options, all_options, market, etc.
    return "<div>My section HTML</div>"
```

2. Register in `registry.py`:
```python
SECTION_REGISTRY["my_section"] = SectionDef(
    id="my_section",
    default_title="My Section Title",
    render_fn=render_my_section,
)
```

3. Use in YAML:
```yaml
sections:
  - id: my_section
    options:
      param1: value1
```

---

## Performance Notes

### Fastest sections (< 100ms)
- `run_snapshot_clipboard`
- `run_settings_table`
- `daily_scoreboard` (unless 1000+ days)

### Medium sections (100ms–1s)
- `strategy_discovery_ranked_table`
- `pattern_engine_overview`
- `anchor_interaction_overview`

### Slowest sections (1s+)
- `large_candle_excursion_*` (many sections = slow)
- `trade_candle_overlay` (renders many candles)
- `equity_curve_comparison` (multiple runs)

**Tip:** If your report is slow, remove the `large_candle_excursion_*` sections (they're comprehensive but expensive).

---

## See Also

- **AI_CAPABILITY_MAP.md** — Report capability overview
- **COMPLETE_SYSTEM_MAP.md** — Reporting system architecture
- **reports/html/registry.py** — Source of truth (programmatic section list)

---

## Summary

**120+ sections** organized into **10 categories**:

1. **Core Run Analysis** (10) — Single-run or comparative overviews
2. **Daily & Session** (8) — Time-based breakdowns
3. **Execution Diagnostics** (6) — Trade mechanics detail
4. **Pattern Engine** (7) — Pattern discovery & robustness
5. **Market Regime** (3) — Condition-based analysis
6. **MA Anchor** (6) — Moving average confluence
7. **Strategy Discovery** (25) — The signal-finding subsystem
8. **Entry Families** (7) — Specific signal types
9. **Optimization** (1) — Parameter sweeps
10. **Large Candle Excursion** (45) — Deep move analysis
11. **Extra/Specialized** (5) — One-off sections

**Pick sections that match your analysis goal.** Combine them in YAML to build custom reports without writing code.

