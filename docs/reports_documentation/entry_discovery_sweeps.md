# Category I: Entry Discovery Sweeps Manual

Designed to scan raw price action and resampled signals to isolate high-expectancy entry rules.

---

## 1. `candle_discovery_overview`

### Operational Purpose
Identifies which candlestick patterns, resampled timeframes, and market regimes have structural edge. It maps a wide, cheap grid of patterns to see where the core signal resides, serving as the top of the candle pattern discovery funnel.

### Data Inputs & Pre-requisites
- **Timeframes**: Standard tick resampled bars (e.g., `1m`, `5m`).
- **Candle Features**: Average bar body size over lookback windows (`5`, `10` bars), ATR (`14` period), tick size.
- **Patterns**: Evaluates large body, clean breakout bars, pinbars (bullish/bearish), engulfing (bullish/bearish), inside/outside bars, and dojis.
- **Market State Context**: Actively tracks intraday regime data and session boundary states.

### Internal Logic & Calculation Steps
1. Computes rolling average candle body sizes and ATR.
2. Identifies instances where specified candle pattern criteria are met.
3. Simulates trade entries on either the `next_open` or a breakout of the signal candle's high/low extreme.
4. Tallies the PnL of each simulated trade to evaluate a standard outcome metric (e.g., hitting a fixed tick TP or SL before a bar timeout).
5. Aggregates results (combining all parameter combinations, timing modes, and directions) into average Profit Factor groups.

### HTML Visual Elements
- **Summary Metrics Strip**: Displays total combinations run, valid results, average Profit Factor, and counts of setups with PF $\ge 1.0$ and $\ge 1.3$.
- **Pattern × Timeframe Heatmap**: A colored grid representing average Profit Factor per pattern per timeframe (1m vs 5m). Uses a semantic color scale (Green for high edge, Yellow for breakeven, Red for negative expectancy).
- **Pattern × Regime Heatmap**: Shows average PF mapped across trending, choppy, high-volatility, or low-volatility regimes.
- **Pattern × Session Heatmap**: Maps performance across standard trading sessions (Asia, London, NY Morning, NY Afternoon).
- **MTF Mode Comparison Table**: Compares statistical results for independent timeframes vs confluence triggers vs hierarchical bias sweeps.

### Config Options & YAML Parameters
```yaml
candle_discovery:
  enabled: true                     # Activates the scan
  session_filter:
    hour_from: 7
    minute_from: 30
    hour_to: 16
  timeframes: [1, 5]                # Timeframes to scan
  min_trades: 20                    # Filter results with low sample sizes
  candle_features:
    size_lookbacks: [5, 10]         # Lookbacks for rolling average size
    atr_period: 14
    tick_size: 0.25
  patterns:                         # Configure specific pattern logic
    large_body:
      enabled: true
      body_multiplier: [1.5, 2.0]
      wick_to_body_max: [0.3, 0.5]
```

### Expert Interpretation & Actionable Advice
- **Target Zones**: Filter the heatmap to locate cells where the average Profit Factor $\ge 1.25$.
- **Tuning**: If a pattern performs well in NY Morning but fails in NY Afternoon, use a session filter rather than disabling the pattern entirely.
- **Micro vs Macro**: Lock the timeframe (`timeframes: [5]`) if the 1m scan shows high decay rates, indicating noise.

---

## 2. `candle_discovery_ranking`

### Operational Purpose
Classifies individual candlestick signal combinations (pattern, timeframe, multiplier, entry timing, TP, SL) into a 5-Tier quality ranking. It isolates the most robust trade setups, weeding out overfit noise.

### Data Inputs & Pre-requisites
- **Sweep Results**: Output database from `candle_discovery_overview` containing individual KPI metrics.
- **In-Sample (IS) / Out-of-Sample (OOS) Splits**: Requires walk-forward performance data (traditionally 70% IS, 30% OOS).

### Internal Logic & Calculation Steps
1. Consumes the granular results of the candle pattern sweep.
2. Filters out setups failing the minimum trade count floor (`min_trades`).
3. Computes the **IS/OOS Degradation** (the percentage drop in Profit Factor from the first 70% of historical bars to the last 30% holdout).
4. Assigns each combination to a predefined tier based on performance rules:
   - **Most Robust**: $PF \ge 1.5$, Degradation $\le 10\%$, $N \ge 30$ trades.
   - **High Quality**: $PF \ge 1.3$, $N \ge 20$ trades.
   - **Solid**: $PF \ge 1.1$, $N \ge 15$ trades.
   - **Marginal**: $PF \ge 1.0$.
5. Sorts the resulting grid by Profit Factor and Net PnL.

### HTML Visual Elements
- **5-Tier Quality Leaderboard**: A clean, detailed table categorized by robustness. Displays the ranking, pattern, body multiplier, timeframe, TP ticks, SL ticks, total trades, win rate, Profit Factor, net dollar return, and IS/OOS degradation.
- **Degradation Indicator Bars**: Visual representation of performance decay across holdout periods.

### Config Options & YAML Parameters
```yaml
sections:
  - id: candle_discovery_ranking
    options:
      top_n: 30                     # Limits the grid size
      min_trades_filter: 20         # Strict trade count floor
```

### Expert Interpretation & Actionable Advice
- **Selection Rule**: Choose only entries from the **Most Robust** or **High Quality** tiers for real-world trading.
- **Overfitting Warning**: A combination with a high Profit Factor ($> 2.0$) but high degradation ($> 40\%$) indicates curve-fitting on historical data. Reject it in favor of a lower PF ($1.4$) but stable degradation ($< 5\%$).

---

## 3. `ma_discovery_overview`

### Operational Purpose
Summarizes statistics and expectancies for moving average crossover and pullback signals. It maps trend-following rules across lookback periods and timeframes to determine trend continuation edge.

### Data Inputs & Pre-requisites
- **Moving Average Types**: Simple (SMA), Exponential (EMA), VWAP, Hull (HMA).
- **Timeframes & Lookbacks**: Multi-timeframe bar data, swept across standard moving average periods (e.g., 9, 20, 50, 200).

### Internal Logic & Calculation Steps
1. Identifies moving average crossover events (fast crosses slow) or pullbacks (price touches MA line but closes back in trend direction).
2. Simulates exits based on fixed ticks or trailing stops.
3. Groups the profit factors across:
   - Timeframes (average PF per TF).
   - Periods (average PF per lookback length).
   - Direction (long vs short expectancies).
4. Renders comparative matrices.

### HTML Visual Elements
- **Metrics Dashboard**: Visual boxes summarizing results analyzed, best Profit Factor, average Profit Factor, and edge threshold hits.
- **Signal × Timeframe Heatmap**: Compiles average profit factor for crossover/pullback signals on 1m, 5m, and 15m.
- **Signal × MA Period Heatmap**: Compiles performance across periods (e.g. Period 9, Period 20, Period 50).
- **Signal × Direction Matrix**: Side-by-side comparison of Long vs Short efficiency to reveal directional bias.

### Config Options & YAML Parameters
```yaml
ma_discovery:
  enabled: true
  timeframes: [1, 5, 15]
  signals:
    ema_crossover:
      enabled: true
      fast_periods: [9, 12]
      slow_periods: [20, 26, 50]
    ma_pullback:
      enabled: true
      periods: [20, 50, 100]
  outcome:
    ticks:
      take_profit: [20, 40]
      stop: [10, 20]
```

### Expert Interpretation & Actionable Advice
- **Crossover vs Pullback**: Pullback entries typically display tighter stop-loss metrics and higher overall Profit Factors compared to delayed crossover signals.
- **Asymmetric Edge**: Check the **Signal × Direction** heatmap. If EMA crossovers are highly profitable on the long side ($PF = 1.4$) but fail on the short side ($PF = 0.85$), configure your active strategy to trade long crossovers only.

---

## 4. `orb_discovery_overview`

### Operational Purpose
Evaluates opening range breakout (ORB) setups. It isolates the statistically profitable break horizons, evaluating if the first 15-minute or 30-minute range holds the highest edge for intraday trend continuation.

### Data Inputs & Pre-requisites
- **Opening Bell Reference**: Typically `07:30 America/Denver` (NY open).
- **Range Horizons**: 5-minute, 15-minute, and 30-minute opening ranges.

### Internal Logic & Calculation Steps
1. Tracks the high and low bounds of the opening period.
2. Identifies a range break (price crossing high or low bounds).
3. Simulates standard ORB trades (long on high break, short on low break) and ORB failure-reclaims (shorting a high break that fails and closes back inside the range).
4. Records performance metrics.

### HTML Visual Elements
- **Break Type × Horizon Matrix**: Displays average Profit Factors comparing standard breakout entry to failure-reclaim sweeps.
- **Time-of-Break Distribution Table**: Maps success rate based on the exact minute of the break (e.g. within 5 minutes of range boundary vs later in the session).
- **KPI Summary Grid**: Detailed list of average trades, net profit, and max drawdowns for each horizon configuration.

### Config Options & YAML Parameters
```yaml
orb_discovery:
  enabled: true
  session_start: "07:30"
  horizons: [15, 30]                # 15m and 30m opening ranges
  min_trades: 15
  outcome:
    ticks:
      take_profit: [30, 50]
      stop: [15, 25]
```

### Expert Interpretation & Actionable Advice
- **Magnet Effect**: If the **failure-reclaim** signal shows a high Profit Factor ($> 1.3$) on the 15m range, it indicates high-volume institutional trapping at the NY open. Trade the fade back to the opening range midpoint.
- **Clean Trend**: A high Profit Factor on 30m breakouts represents robust, high-momentum trend days.

---

## 5. `premarket_discovery_overview`

### Operational Purpose
Renders statistical predictive sweeps mapping pre-market high/low breaks to New York session directionality. It solves the question: "Does the pre-market action predict the open session?"

### Data Inputs & Pre-requisites
- **Premarket Definition**: Standard premarket hours (e.g. `02:00` to `07:30 America/Denver`).
- **Open Session Definition**: NY morning hours (`07:30` to `11:00`).

### Internal Logic & Calculation Steps
1. Compiles the high and low bounds established during premarket Globex hours.
2. Registers high/low breaks occurring within the first hour of the NY open.
3. Calculates conditional probabilities:
   - Probability that a premarket high break leads to a bullish close.
   - Profit expectancy of fades (fading the premarket extreme breach).
4. Aggregates results into direction-prediction mapping tables.

### HTML Visual Elements
- **Predictive Edge Summary Cards**: Highlights key metrics like "Premarket High Break Follow-through Rate".
- **Conditional Expectancy Grid**: Breaks down the average profit factor of breakout continuation vs mean-reversion fade setups at the premarket boundaries.
- **Premarket Range Width vs Session Trend Chart**: Displays correlation between the size of the premarket range and open session trendiness.

### Config Options & YAML Parameters
```yaml
premarket_discovery:
  enabled: true
  premarket_start: "02:00"
  premarket_end: "07:30"
  breakout_window_mins: 60          # Time to monitor after NY open
```

### Expert Interpretation & Actionable Advice
- **Volatility Squeezes**: If the pre-market range is extremely narrow ($< 0.5$ daily ATR), breakouts of the premarket boundaries have an exceptionally high follow-through rate.
- **Fades in Wide Ranges**: If the premarket range is wide ($> 1.5$ ATR), boundary breaks tend to fail. Look to trade reversals back into the premarket value area.

---

## 6. `bb_discovery_overview`

### Operational Purpose
Maps Bollinger Band squeeze, expansion, and mean-reversion expectancies across parameter settings, indicating whether volatility-band strategies have statistical edge.

### Data Inputs & Pre-requisites
- **Indicators**: Bollinger Bands (default period `20`, deviation `2.0`).
- **Market Data**: Intraday resampled price bars.

### Internal Logic & Calculation Steps
1. Computes Bollinger Band lines (Basis, Upper, Lower) and Bandwidth.
2. Identifies:
   - **Squeezes**: Bandwidth falls below the N-period rolling minimum bandwidth.
   - **Mean Reversions**: Price touches or closes outside the outer band, then closes back inside.
3. Simulates standard trades to fixed targets or trailing stops.
4. Aggregates profit factors by band deviation, period, and timeframe.

### HTML Visual Elements
- **Bandwidth Squeeze Performance Heatmap**: Profit factor of breakouts occurring after tight bandwidth contraction.
- **Mean-Reversion Expectancy Grid**: Displays performance of outer-band reversals grouped by deviation levels (e.g. 2.0 vs 2.5 standard deviations).
- **Parameter Sensitivity Table**: Renders profit factor changes across lookbacks (10, 20, 50) and deviations.

### Config Options & YAML Parameters
```yaml
bb_discovery:
  enabled: true
  periods: [20, 50]
  deviations: [2.0, 2.5]
  signals:
    squeeze_breakout: {enabled: true}
    band_reversal: {enabled: true}
```

### Expert Interpretation & Actionable Advice
- **Deviation Selection**: In high-volatility regimes, outer-band mean reversions are highly prone to "riding the band" (continuation). Use $2.5$ deviations to filter out early counter-trend entries, or restrict mean-reversion to low-volatility regimes.

---

## 7. `breakout_discovery_overview`

### Operational Purpose
Reviews the performance of basic volatility and structural breakouts (N-bar high/low channels). It determines the optimal timeframe and lookback length for momentum breakout entries.

### Data Inputs & Pre-requisites
- **Structure Lines**: N-bar rolling highs/lows (e.g. 5-bar, 10-bar, 20-bar channels).
- **Timeframes**: Multi-timeframe bar data.

### Internal Logic & Calculation Steps
1. Calculates rolling N-bar channels.
2. Registers price breaks crossing the outer channel lines.
3. Simulates momentum continuation trades with tight stops.
4. Computes performance KPIs grouped by lookback length and timeframe.

### HTML Visual Elements
- **Channel Length × Timeframe Matrix**: Heatmap indicating average Profit Factors for various channel lengths (5, 10, 20 bars) across timeframes.
- **KPI Leaderboard Table**: Renders the top breakout configurations sorted by average Profit Factor and trade count.

### Config Options & YAML Parameters
```yaml
breakout_discovery:
  enabled: true
  lookback_bars: [5, 10, 20]
  timeframes: [1, 5]
  signals:
    n_bar_breakout: {enabled: true}
    volatility_break: {enabled: true}
```

### Expert Interpretation & Actionable Advice
- **Optimal Channel**: 20-bar channels on the 5m timeframe generally yield cleaner breakouts than fast 5-bar channels, which are highly susceptible to noise and false breakouts.
- **Trend Confluence**: High breakout expectancies in this section indicate a strong momentum environment. Pair with moving average trend filters for enhanced win rates.

---

## 8. `pullback_discovery_overview`

### Operational Purpose
Evaluates pullback entries within established trends, analyzing how deep a pullback needs to be to offer high-expectancy continuation value.

### Data Inputs & Pre-requisites
- **Trend Metric**: Moving averages or swing structures defining the primary trend.
- **Retracement Metric**: Fib levels, ATR multiples, or swing pivots.

### Internal Logic & Calculation Steps
1. Establishes the direction of the dominant trend.
2. Identifies a pullback (counter-trend move) that touches a significant support/resistance reference without invalidating the structural trend.
3. Simulates entries on trend continuation signals.
4. Tallies profit factors grouped by pullback depth and timeframe.

### HTML Visual Elements
- **Pullback Depth Performance Grid**: Compiles Profit Factors across retracement depths (e.g. shallow vs mid vs deep retracements).
- **Timeframe Efficiency Matrix**: Compares 1m vs 5m execution performance for pullback setups.
- **Statistical Summary Strips**: Total setups, win rate, and profit expectations.

### Config Options & YAML Parameters
```yaml
pullback_discovery:
  enabled: true
  min_trades: 15
  timeframes: [1, 5]
  pullback_depth_atr: [0.5, 1.0, 1.5]
```

### Expert Interpretation & Actionable Advice
- **Optimal Depth**: Pullbacks measuring between $0.8$ and $1.2$ ATR units generally provide the optimal risk/reward profile. Shallower pullbacks ($< 0.5$ ATR) often fail to clean out weak hands, leading to sudden double-tops or failure patterns.

---

## 9. `level_discovery_overview`

### Operational Purpose
Aggregates performance sweeps for structural support and resistance levels, including swing highs/lows, consolidation zones, round number psychological levels, and prior session boundaries.

### Data Inputs & Pre-requisites
- **Levels Database**: Intraday pivots, historical high/low values, round price markers, VWAP boundaries.
- **Market Data**: Intraday bars.

### Internal Logic & Calculation Steps
1. Tracks multiple structural S&R level types concurrently.
2. Registers touches, bounces, or false-break sweeps of these levels.
3. Simulates mean-reversion fades or breakout continuation trades.
4. Compiles and aggregates comparative performance stats across all level types.

### HTML Visual Elements
- **Level Performance Matrix**: Shows average Profit Factors grouped by Level Type (Swing Test vs Consolidation vs Round Numbers vs VWAP vs Prior Session).
- **Proximity Touch Heatmap**: Evaluates success rates based on how close price gets to a level (e.g. 4 ticks vs 8 ticks vs 12 ticks) before reversing.
- **Top 10 Level Setups Table**: Renders individual configuration lines sorted by expectancy.

### Config Options & YAML Parameters
```yaml
level_discovery:
  enabled: true
  timeframes: [1, 5]
  signals:
    swing_level_test: {enabled: true, pivot_lookback: [5]}
    round_number_bounce: {enabled: true, level_step: [25.0, 50.0]}
    vwap_reclaim_reject: {enabled: true, max_dist_ticks: [16.0]}
```

### Expert Interpretation & Actionable Advice
- **Psychological Levels**: Psychological round numbers ending in `.00` (e.g. NQ 100-point levels) represent highly efficient liquidity pools. Bounces here typically have fast, high-momentum reactions.
- **Prior Session Value**: Prior session highs and lows are the single most consistent intraday support/resistance boundaries. Prior session closes are excellent magnets for mean-reversion strategies.

---

## 10. `lcr_discovery_overview`

### Operational Purpose
Evaluates Large Candle Regions (LCR)—essentially Fair Value Gaps (FVG) or imbalance zones—identifying their magnetic properties and breakout expectancies.

### Data Inputs & Pre-requisites
- **Onset Detection**: Outsized directional candles (body size $> 150\%$ of the rolling average body).
- **Zone Boundaries**: High/low of the body or range of the onset candle.

### Internal Logic & Calculation Steps
1. Monitors raw bars for onset candles meeting size criteria.
2. Establishes a virtual "LCR zone" covering the imbalance region.
3. Tracks:
   - **Retrace Behaviour**: Does price retrace back into the LCR zone?
   - **Region-to-Region (R2R) Continuation**: After breaking out of an LCR zone, does price reach the next LCR zone?
   - **Time of Day Impacts**: Outcomes analyzed by standard hour blocks.
4. Tallies metrics across Fresh, Touch, Break, and Retrace signal types.

### HTML Visual Elements
- **Region-to-Region Continuation Table**: Shows overall and directional reach rates, average bars to reach, and average distance in ticks.
- **Retrace Behaviour Summary**: Renders retrace rates and average region age at the break.
- **Break Outcome by Time of Day Grid**: Shows hourly breakdowns comparing retrace vs continuation vs failed extensions.
- **Top Signal Combinations Leaderboard**: Lists granular combinations sorted by Profit Factor, showing size multiplier, lookback, zone boundary type (body vs range), and IS/OOS degradation.

### Config Options & YAML Parameters
```yaml
lcr_discovery:
  enabled: true
  size_multipliers: [1.5, 2.0, 2.5]
  lookbacks: [10, 20]
  zone_types: ["body", "range"]
  signal_types: [fresh, touch, break, retrace]
  min_trades: 15
```

### Expert Interpretation & Actionable Advice
- **Magnet Expectancy**: A **retrace rate** exceeding $85\%$ indicates that the market consistently pulls back to fill the price imbalance. Use a **retrace limit entry** inside the zone for highly efficient fills.
- **Continuation Play**: If the R2R reach rate is high ($> 75\%$), look to trade momentum breakouts in the direction of the original large onset candle.

---

## 11. `filter_discovery`

### Operational Purpose
Evaluates the statistical impact of macro filters (e.g. higher timeframe EMA trends, ATR volatility states, VWAP location) on trade expectancy. It acts as a post-discovery optimization tool, weeding out unprofitable market states.

### Data Inputs & Pre-requisites
- **Trade Records**: Completed strategy backtest sweeps containing execution timestamps.
- **Market State Indicators**: HTF EMA slopes, local ATR, VWAP distance, entry hour.

### Internal Logic & Calculation Steps
1. Correlates completed trades with historical market conditions at the exact moment of entry.
2. Groups trades by:
   - **Volatility Quartile (ATR)**: Q1 (extremely low) to Q4 (extremely high).
   - **Higher Timeframe Trend Direction**: HTF EMA slope positive vs negative.
   - **VWAP Location**: Price above vs below the VWAP line.
   - **Time of Day Bucket**: Standard hourly windows.
3. Computes the baseline average profit and the **Edge vs Run** (how much a filter improves or degrades PnL).
4. Generates recommendations by flagging filters that increase Profit Factor or filter out downside volatility.

### HTML Visual Elements
- **Filter Standouts Table (Actionable Insights)**: Highlights the top-performing market filter conditions, displaying condition type, trades, win rate, and PnL change.
- **Granular Filter Cards**: Detailed cards per strategy run showing metric tables and charts for PnL by ATR quartile, HTF slope sign, VWAP side, and Time-of-Day.
- **ATR Distribution Bar Chart**: Inline visual chart plotting net PnL across the 4 ATR quartiles.

### Config Options & YAML Parameters
```yaml
filter_discovery:
  enabled: true
  top_n: 10
  bar_tf: "5m"                      # Core timeframe
  htf_tf: "15m"                     # Higher timeframe filter
  ema_period: 50                    # HTF trend filter length
  atr_period: 14                    # Volatility filter length
  sort_by: "net_pnl"
  min_trades: 50
```

### Expert Interpretation & Actionable Advice
- **Vol-State Filtering**: Check the **ATR Quartile** chart. If Q1 shows negative PnL, disable trading during low-volatility environments. If Q4 shows massive drawdown, implement a volatility cap.
- **Trend Confluence**: The **HTF Slope** filter is the single most effective tool for trend-following strategies. Filtering out counter-trend trades can boost a strategy's win rate by up to $8-12\%$.

---

## The 6-Stage Strategy Discovery Funnel

The entry discovery sweep sections are orchestrated via a highly optimized, progressive **funnel** designed to save compute time and systematically isolate real-world edges.

```
+-------------------------------------------------------+
|  01_quick_scan.yaml (Stage 1: Broad Scans)            | ---> Renders: strategy_discovery_unified
|  Locates which signal families have ANY edge.         |
+-------------------------------------------------------+
                           |  (Extract top 2-3 families)
                           v
+-------------------------------------------------------+
|  02_candle_patterns.yaml (Stage 2: Deep-Dive)         | ---> Renders: candle_discovery_ranking & overview
|  Sweeps all candle patterns and TP/SL configurations.  |
+-------------------------------------------------------+
                           |  (Extract top setups)
                           v
+-------------------------------------------------------+
|  03_levels_regions.yaml (Stage 3: Zones & Levels)     | ---> Renders: lcr_discovery_overview & S&R sweep
|  Analyzes LCR gaps, swing levels, round numbers.      |
+-------------------------------------------------------+
                           |  (Extract magnetic levels)
                           v
+-------------------------------------------------------+
|  04_ny_open.yaml (Stage 4: Morning Session Scalps)    | ---> Renders: Premarket & NY bell expectancy scans
|  Solves predictive edge around the opening bell.       |
+-------------------------------------------------------+
                           |  (Extract timing filters)
                           v
+-------------------------------------------------------+
|  05_orb_momentum.yaml (Stage 5: Range Breakouts)       | ---> Renders: ORB, BB, and MA trend continuations
|  Sweeps opening range, volatility bands, and averages. |
+-------------------------------------------------------+
                           |  (Take top 5-10 combos overall)
                           v
+-------------------------------------------------------+
|  06_validate.yaml (Stage 6: IS/OOS Robustness Check)   | ---> Renders: strategy_discovery_validation
|  Walk-forward validation to weed out overfit noise.   |
+-------------------------------------------------------+
```
