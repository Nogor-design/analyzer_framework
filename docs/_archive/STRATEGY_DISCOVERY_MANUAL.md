# Strategy Discovery Engine — User Manual

## What It Is

The Strategy Discovery engine is an automated analysis pipeline that runs against your NinjaTrader export data and produces a single HTML report. It answers two questions:

1. **Pattern finding** — What market conditions, time-of-day, regime, and filter rules actually explain when your strategy works and when it fails?
2. **Strategy modification** — Which exits, entry conditions, and position sizes should you change to improve out-of-sample performance?

It does not tell you to buy or sell. It analyses what has already happened in your trade history and gives you evidence to act on.

---

## Quick Start

```powershell
python -m ta_foundation.cli.main `
  --input  "C:/Users/Owner/Downloads/B99" `
  --output ./outputs `
  --report-config ./strategy_discovery_report.yaml `
  --market-data "D:/MarketData" `
  --recursive
```

The output is a single self-contained HTML file. Open it in any browser.

---

## Before You Run: Configure the YAML

Open `strategy_discovery_report.yaml` and set these fields first. Everything else can stay at its default.

### Instrument and Contract

```yaml
strategy_discovery:
  instrument: "NQ"        # NQ, MNQ, ES, MES, CL, GC, etc.
  contract: "03-26"       # pin to specific contract, or null for auto
  timeframe: "5m"         # used for regime + ATR calculations
  tick_size: 0.25         # NQ/ES/MNQ = 0.25, CL = 0.01
```

**Why this matters:** Regime labels and ATR-based exit sweep values are derived from market bars, not from your trades. If the instrument or contract does not match a file in your `--market-data` folder, regime data will be missing and most of the analysis will be degraded.

### Cost Model

```yaml
  cost_model:
    commission_per_side: 2.09    # your actual broker commission per side
    slippage_ticks: 1            # realistic slippage for your instrument
    tick_value: 5.00             # NQ = $5.00 per tick, MNQ = $0.50
```

**Why this matters:** Every trade in the report is run through this cost model before validation. If your costs are wrong, the walk-forward validation pass/fail verdict and all P&L figures will be wrong. Use your real numbers.

### Walk-Forward Validation

```yaml
  walk_forward:
    wf_type: rolling     # rolling (standard) or anchored (expanding window)
    is_pct: 0.70         # 70% of each fold is in-sample, 30% out-of-sample
    n_folds: 5           # 5 folds across your trade history
    degradation_threshold: 0.20  # OOS profit factor may drop up to 20% vs IS
```

For fewer than 150 trades, reduce `n_folds` to 3 or raise `min_is_trades` to avoid folds with too few trades. The engine will warn you when folds are skipped.

---

## The Analysis Phases

The engine runs these phases in order. Each feeds the next.

| Phase | What It Does |
|---|---|
| 0 | Labels every 5-minute bar with a market regime (trending / ranging / high-vol) using ADX + ATR |
| 1 | Computes MAE/MFE percentile bounds (how far trades go against and in favour before closing) |
| 1 | Walk-forward validation — tests whether your strategy works out-of-sample |
| 1 | Evaluation — core P&L KPIs, direction breakdown, session breakdown, equity curve |
| 1b | Feature importance — which conditions (regime, session, ADX, pattern score) correlate with winners |
| 2 | Entry rule discovery — finds condition conjunctions that select subsets with better PF/WR |
| 2b | Filter discovery — finds conditions whose removal improves the overall trade set |
| 3 | Exit policy sweep — tests Fixed R:R, ATR trail, Chandelier, and giveback policies |
| 4 | Strategy classification — rates the strategy as Automated / Hybrid / Semi-Discretionary |
| 5 | Ranking — weighted composite score across all metrics; sensitivity check |
| 6 | Clustering — groups near-identical parameter variants together |
| 7 | Position sizing — Kelly criterion + Monte Carlo ruin-risk |
| 8 | Risk metrics — Sharpe, Sortino, Calmar, Omega ratios |
| 9 | Drawdown analysis — max DD, streaks, Ulcer Index, rolling worst-case |
| 10 | Cohort analysis — detects whether performance is improving, stable, or decaying over time |
| 11 | Parameter sensitivity — tests whether your entry rule thresholds are robust or peaked |

---

## Reading the Report: Section by Section

The report is divided into 17 sections. The most useful reading order for **finding patterns** is different from the order for **modifying a strategy**.

---

### Reading Order for Pattern Finding

Use this order when you want to understand what is actually driving performance.

#### 1. Feature Importance

**What to look for:**

- **Numeric correlations table:** The top-ranked features with the highest absolute correlation to win/loss. A feature with correlation ≥ 0.15 (shown in green) has a real relationship with trade outcomes. Features below 0.08 are noise.
- **Categorical effects (Cramér's V):** Which categorical values predict wins. Look at the group means column — it shows average profit per group. A session like `RTH` showing a large positive mean profit while `ONH` shows a negative mean is an actionable filter.
- **Random Forest importance:** If present, this cross-validates the correlation findings. Features that appear in both the correlation table and the RF ranking are the most reliable.

**What to do with it:**

If `session_label` is your top feature, go to the Entry Rules section and look for session-based rules. If `adx` is top-ranked, your strategy has a trend-dependency — look at the regime section to understand when ADX is high.

#### 2. Market Regime Distribution

**What to look for:**

- The **dominant regime** for your trade dates. If your history is 70% `ranging_wide` but your strategy is a breakout strategy, that is a structural misfit.
- The **% Trending vs % Ranging** bars. A strategy that works only in trending regimes needs an ADX filter.
- The **recent days table**: check whether the last 20 sessions are representative of the historical regime mix, or whether market character has shifted.

**What to do with it:**

Cross-reference with the Entry Rules section. If `regime == trending_up` appears in your top entry rules, and the recent days show mostly ranging, you should expect weaker performance ahead.

#### 3. Entry Rule Discovery

**What to look for:**

- **Top rules ranked by PF lift** — the percentage improvement in profit factor over the baseline. A rule with PF lift of +0.30 or higher and at least 30 matching trades is worth considering.
- **The conditions in each rule** — these are conjunctions like `adx >= 25 AND session_label == RTH`. Single-condition rules (depth=1) are cleaner than two-condition rules (depth=2) for implementation.
- **Win rate vs profit factor tradeoff** — a rule might improve PF by reducing the trade count (removing bad trades) without improving WR. That is a filter, not an entry signal. A true entry improvement raises both WR and PF.

**What to do with it:**

A rule that appears in the top-3 across multiple runs in the Cross-Run comparison is the most trustworthy. Single-run top rules may be overfitted to that run's specific dates.

#### 4. Filter Rule Discovery

**What to look for:**

- Rules that when **removed** (excluded) improve the trade set. These are the conditions under which your strategy reliably loses.
- **PF lift and WR lift** after applying the filter. Aim for filters with PF lift ≥ 0.20 and at least 15 remaining trades.
- The number of trades excluded. A filter that removes 5% of trades and improves PF by 0.30 is very valuable. One that removes 40% of trades for the same gain may be over-fitting.

**What to do with it:**

If `regime == high_vol_expansion` is your top filter (removing those trades improves results), add an ATR-based volatility filter to your strategy to avoid entering during high-vol conditions.

#### 5. Parameter Sensitivity

**What to look for:**

- Rules classified as **ROBUST** (green) — the edge is flat across a range of thresholds. This means the rule is not curve-fitted to one specific value.
- Rules classified as **FRAGILE** (red) — the edge spikes at exactly the discovered threshold and collapses on either side. This is a curve-fit artifact and should not be trusted.
- The **sensitivity score (0-100)**: below 30 is robust, above 60 is fragile.
- The **PF bar chart** per threshold: you want a flat or gradually-sloped chart, not a single tall bar surrounded by short bars.

**What to do with it:**

Only implement entry rules that score as ROBUST or MODERATE. A FRAGILE rule will likely not hold in live trading. If all your top rules are fragile, the strategy does not have a systematic edge.

---

### Reading Order for Strategy Modification

Use this order when you want specific, actionable changes to an existing strategy.

#### 1. Walk-Forward Validation (start here)

**The gate.** If a run FAILS validation, everything else in that run is suspect. A PASS does not guarantee future profitability, but a FAIL means the strategy's performance does not hold across time periods.

**What to look for:**

- **IS PF vs OOS PF per fold:** In-sample profit factor should be higher than out-of-sample but not by more than the `degradation_threshold` (default 20%). A fold where IS PF is 2.0 and OOS PF is 0.8 shows the strategy is overfitting that period.
- **T-test p-value:** Below 0.05 means the mean trade profit is statistically distinguishable from zero. Above 0.10 means the edge may be noise.
- **Monte Carlo drawdown percentile:** If your equity curve ranks in the worst 5th percentile of 500 random simulations, the historical path was unusually lucky — expect worse in live trading.
- **OOS degradation:** How much does OOS PF decline relative to IS PF on average across folds? Values above 30% suggest over-optimization.

**What to do with it:**

If a run fails validation: look at the fold breakdown. If 4 of 5 folds pass but one fails badly, identify what was different about that period (regime? unusual volatility?). If most folds fail, the strategy parameters need to change or the strategy needs more robust entry conditions.

#### 2. MAE/MFE Profile

**The most direct guide to exit modification.**

**What to look for:**

- **MAE percentiles (p25, p50, p75, p90):** MAE is how far a trade goes against you before resolution. Your stop loss should be wider than the p75 MAE to avoid stopping out on noise. If your current stop is tighter than the p50 MAE, you are getting stopped out on most trades that would have eventually recovered.
- **MFE percentiles:** MFE is how far a trade moves in your favour before closing. The p50 MFE is approximately the "natural target" — where the average winner peaks. If your current target is past the p90 MFE, you are rarely capturing full moves.
- **MAE/MFE by direction (Long vs Short):** These are often asymmetric. Long trades may have a tighter MAE tolerance than short trades. Set stops separately if the difference is large (>20%).

**Practical rules:**

| Situation | What to change |
|---|---|
| Stop < p50 MAE | Widen stop; you are getting stopped on noise |
| Target > p90 MFE | Reduce target; you are leaving trades open too long |
| p50 MAE ≈ p50 MFE | Tight channel; the strategy is marginal — exits are the least of your problems |
| Large MAE/MFE long vs short gap | Split stop/target by direction |

#### 3. Exit Policy Sweep

**Specific exit configurations tested against your actual trades.**

**What to look for:**

- The **top 10 ranked policies** per run. The ranking is by net P&L after costs, not gross profit factor, so it reflects real-world performance.
- **Policy families tested:**
  - `FixedRR` — fixed risk:reward ratio (1:1.5, 1:2, 1:2.5, etc.)
  - `ATR_Trail` — ATR-multiple trailing stop
  - `Chandelier` — highest-high/lowest-low trailing stop
  - `Giveback` — exit when a percentage of open profit is given back
  - `BreakEven` — move stop to breakeven after X ticks of profit
- **Compare your current exit to the top-ranked policy.** If your current stop is 20 ticks fixed and the top-ranked policy is `ATR_Trail(1.5x)`, that is a concrete experiment to run.
- **By-regime breakdown:** Some exit policies work in trending regimes but fail in ranging ones. Look for policies where performance is consistent across regimes rather than only strong in one.

**What to do with it:**

Take the top 2-3 policies and paper-trade them alongside your current exit for 2-4 weeks before committing to a change. The sweep uses historical data — the MAE/MFE profile confirms whether the exit is physically achievable (i.e., the trade actually reached that target).

#### 4. Trade Evaluation (direction and session breakdown)

**What to look for:**

- **Long vs Short P&L breakdown:** A strategy with PF 1.6 Long but PF 0.8 Short is a long-bias strategy masquerading as a two-directional strategy. The fix is to disable short entries or add a directional filter.
- **Session breakdown (RTH / ONH / ETH):** Overnight session (ONH) typically has higher volatility and lower fill quality. If ONH trades are dragging down performance, add a session-time filter.
- **Equity sparkline:** Look for the shape. A straight upward slope is healthy. A flat or upward curve with a sharp drop at the end suggests recent regime change or drawdown. A very jagged curve with many inflection points suggests high variance — position sizing becomes critical.

#### 5. Position Sizing (Kelly / Monte Carlo)

**What to look for:**

- **Kelly full fraction (f\*)**: The theoretically optimal fraction of equity to risk per trade. In practice you never use full Kelly — it maximises long-run growth but produces enormous drawdowns.
- **Recommended fraction:** The system recommends between 0.25× and 0.5× Kelly for most strategies. This is the fraction you should target.
- **Monte Carlo ruin probability:** The probability that a 200-trade simulated run hits a 20% equity drawdown. Below 5% is acceptable for funded accounts. Above 15% means your current position size is dangerous.
- **Fractional Kelly comparison table:** Shows net P&L, max drawdown, and ruin probability for each fraction (0.25×, 0.50×, 0.75×, 1.00×). Find the fraction where ruin probability is below your threshold.

**What to do with it:**

If you are currently trading 1 contract per $10,000, use the initial equity and Kelly results to decide whether to scale up or down. If 0.25× Kelly implies risking 1.5% of equity per trade and you are risking 3%, halve your size or widen your stop.

#### 6. Drawdown Analysis

**What to look for:**

- **Max drawdown and recovery factor:** Recovery factor = total net profit / max drawdown. Below 1.0 means the strategy never fully recovered from its worst drawdown period. Above 3.0 is strong.
- **Ulcer Index:** A measure of the depth and duration of drawdowns combined. Lower is better. High Ulcer Index (above 15) means the equity curve spends a lot of time in drawdown — this is psychologically difficult to trade live.
- **Max consecutive losses:** Knowing your realistic worst streak (from actual data, not simulation) helps you set a daily/weekly stop-loss rule. If max consecutive losses is 8, a daily limit of 3 losses is reasonable.
- **Rolling max drawdown chart:** Each bar shows the worst 20-trade window in your history. If recent windows are worse than historical average, the strategy is deteriorating.
- **Regime breakdown:** Which regime produces the worst drawdowns? If `high_vol_expansion` dominates your worst windows, use the ATR filter to avoid those periods.

#### 7. Cohort Analysis (Drift and Decay)

**What to look for:**

- **Trend classification:** `Improving`, `Stable`, `Degrading`, or `Volatile`. A `Degrading` classification means the most recent cohorts have lower PF/WR than early cohorts — a direct signal to review or retrain.
- **Decay score (0-100):** Below 30 is healthy. Above 60 means the edge is eroding. Above 80 means the strategy should not be traded live without modification.
- **Early vs late cohort comparison:** Compares the first half of trades to the second half. A large drop in PF from early to late cohorts (>0.3) warrants investigation.
- **Cohort table:** Look for individual cohorts with sharply negative PF — these may correspond to specific market events (Fed days, earnings, regime shifts) that your strategy cannot handle.

**What to do with it:**

If cohort analysis shows degradation, return to the Feature Importance section. Check whether the features that predicted wins in early cohorts still hold in recent cohorts. If the regime distribution has shifted (trending earlier, ranging recently), regime-based filters may need updating.

#### 8. Risk-Adjusted Metrics

**What to look for:**

- **Sharpe ratio:** Above 1.0 is acceptable for a trading strategy. Above 2.0 is strong. Below 0.5 means the return does not justify the volatility.
- **Sortino ratio:** Like Sharpe but only penalises downside volatility. Should be higher than Sharpe for a strategy with controlled losses. If Sortino < Sharpe, your losses are more volatile than your wins.
- **Calmar ratio:** Annualised return divided by max drawdown. Above 1.0 is acceptable. This is the number that funded trading firms care most about.
- **Omega ratio:** Compares all positive outcomes to all negative outcomes weighted by magnitude. Above 1.5 is good. Below 1.0 means net negative expected value after costs.

**What to do with it:**

Use the cross-run comparison table to identify which run has the best Sharpe or Calmar. That run's configuration is your benchmark. Its entry rules and exit policy are starting points for optimization.

#### 9. Strategy Ranking (the summary verdict)

**What to look for:**

- **Final score and grade (A/B/C/D/F):** The composite score across all dimensions. The grade is relative to all runs you submitted — a grade of A on a bad set of runs is still a bad strategy.
- **Score components:** `RiskAdj × 0.30`, `Stability × 0.25`, `Robustness × 0.20`, `RegimeFit × 0.15`, `ExecutionFit × 0.10`. Clicking on a run in the ranked table expands the component breakdown. The lowest-scoring component is your biggest opportunity.
- **Cluster badges:** Runs in the same cluster are behaviorally similar (near-identical parameter variants). The cluster representative is the best run from each group. Do not treat highly-clustered runs as independent evidence — they are parameter variants of the same strategy.
- **Weight sensitivity flag:** If the run is marked `ranking_fragile`, its position changes significantly when scoring weights are perturbed. This means its ranking is borderline — treat it with lower confidence.

---

## Cross-Run Comparison: When Running Multiple Strategies

When you feed multiple NinjaTrader export runs, the report compares them side-by-side throughout. This is the most powerful use case.

**Reading the comparison table:**

- Bold values in each column indicate the best run for that metric.
- A run that is best in one column but worst in another is not necessarily better overall — use the ranked table for the summary verdict.
- **Clustering matters.** If four of your five top runs share a cluster badge, you effectively only have two distinct strategies, not five.

**Finding patterns across runs:**

- Go to the Feature Importance cross-run frequency table. Features that appear in the top-5 across 3 or more runs are the most reliable predictors.
- Go to Entry Rules. If the same rule (e.g., `adx >= 25 AND session_label == RTH`) appears in the top-3 across multiple runs, it is a structural edge, not a coincidence.
- If all your runs fail validation but entry rules look promising, the exit is the problem — go to Exit Policy Sweep first.

---

## Common Workflows

### Workflow 1: Evaluating a New Strategy (First Run)

1. Set `instrument`, `contract`, `cost_model` in the YAML
2. Run the report
3. Check **Walk-Forward Validation** — PASS is a prerequisite for proceeding
4. Read **Feature Importance** — identify the 1-3 features most correlated with winning trades
5. Read **Entry Rule Discovery** — find top rules involving those features; check if they are ROBUST in the Parameter Sensitivity section
6. Read **MAE/MFE Profile** — verify your stop and target are in sensible percentile ranges
7. Check **Cohort Analysis** — confirm performance is stable or improving, not decaying
8. Check **Position Sizing** — verify your current size is below the Kelly-implied safe fraction

### Workflow 2: Fixing a Losing Streak

1. Check **Cohort Analysis** first — is the recent decay structural (all cohorts degrading) or episodic (one bad window)?
2. Check **Market Regime Distribution** — has the regime mix shifted recently? (e.g., more high-vol sessions than your training period)
3. Check **Filter Discovery** — is there a condition that now characterises recent losing trades?
4. Check **Drawdown Analysis** rolling chart — when did the drawdown start? Correlate with regime data
5. If regime-specific: add or tighten an ADX / ATR filter using the thresholds from Entry Rule Discovery
6. Re-run the report with the filter applied to see if walk-forward validation now passes

### Workflow 3: Optimizing an Exit

1. Run the report on your current strategy
2. Note your current stop/target in ticks
3. Read **MAE/MFE Profile** p25/p50/p75 values
4. If stop < p50 MAE: widen stop to p75 MAE
5. If target > p90 MFE: reduce target to p75 MFE
6. Go to **Exit Policy Sweep** — find the top-ranked policy. Note its family and parameters
7. Check whether the top policy is consistent across the by-regime breakdown
8. Implement the top policy in NinjaTrader and re-export; re-run the report to compare

### Workflow 4: Comparing Parameter Variants

1. Export 5-10 variants from NinjaTrader with different parameter settings into the same input folder
2. Run with `--recursive` so all variants are loaded
3. Read **Strategy Ranking** — identify the top 2-3 by final score
4. Check **Clustering** — confirm they are in different clusters (if they cluster together, they are the same strategy with minor variations)
5. Read **Validation** for those top runs — both must PASS
6. Read the **Cross-Run Comparison** — find the variant that is best across the most metric groups
7. Use **Parameter Sensitivity** to confirm that the top variant's entry thresholds are ROBUST, not FRAGILE

---

## Config Tuning Guide

### Too few trades (< 100 total)

```yaml
walk_forward:
  n_folds: 3           # reduce from 5
  min_is_trades: 20    # reduce from 50
  min_oos_trades: 10   # reduce from 20
entry_discovery:
  min_trades: 10       # reduce from 20
filter_discovery:
  min_trades: 10
  min_remaining_trades: 8
cohort_analysis:
  min_trades: 20       # reduce from 30
  min_cohorts: 2       # reduce from 3
```

### Too many rules being discovered (noise)

```yaml
entry_discovery:
  max_depth: 1         # single conditions only; no AND pairs
  min_trades: 40       # require more trades per rule
  top_n: 10            # show fewer rules
parameter_sensitivity:
  n_steps: 6           # wider sweep to expose fragile rules
```

### Analysis runs too slowly

```yaml
entry_discovery:
  max_candidates: 100  # reduce from 300
exit_discovery:
  max_combos: 40       # reduce from 80
position_sizing:
  n_sim: 200           # reduce from 500
parameter_sensitivity:
  top_n_rules: 3       # reduce from 5
  n_steps: 3           # reduce from 4
```

### Different instrument (ES, CL, etc.)

```yaml
strategy_discovery:
  instrument: "ES"
  tick_size: 0.25
  cost_model:
    commission_per_side: 2.09
    slippage_ticks: 1
    tick_value: 12.50      # ES = $12.50 per tick
  position_sizing:
    initial_equity: 25000  # ES requires more margin
```

---

## Interpreting Confidence Levels

Not all findings are equally reliable. Use this as a guide:

| Signal | Confidence | Reason |
|---|---|---|
| Feature with correlation > 0.15 across 3+ runs | High | Cross-run validation |
| Entry rule appearing in top-3 for 3+ runs | High | Consistent, not run-specific |
| Walk-forward PASS, Sortino > 1.5, decay score < 30 | High | All three independent gates pass |
| Entry rule ROBUST in parameter sensitivity | High | Threshold is not curve-fitted |
| Top feature in one run only | Medium | May be data-specific |
| Exit policy improvement of > 15% PF | Medium | Confirm with MAE/MFE check |
| Entry rule with < 25 matching trades | Low | Insufficient sample |
| Entry rule FRAGILE in parameter sensitivity | Low | Likely overfitted |
| Walk-forward FAIL but high historical PF | Low | Historical PF is curve-fitted |
| Cohort trend = Degrading + decay score > 60 | Disqualifying | Strategy not suitable for live trading |

---

## Glossary

**ADX** — Average Directional Index. Measures trend strength. ADX ≥ 25 is trending; below 20 is ranging.

**ATR** — Average True Range. Measures price volatility over a lookback period.

**Calmar ratio** — Annualised return divided by maximum drawdown. Used by proprietary trading firms.

**Cohort** — A sequential group of trades (e.g., trades 1-20, 21-40). Used to track how performance changes over time.

**Decay score** — 0-100 score measuring how much performance has deteriorated from early to recent cohorts.

**Exit policy family** — A class of exit mechanism: FixedRR, ATR_Trail, Chandelier, Giveback, or BreakEven.

**Feature matrix** — A table where each row is a trade and each column is a condition measured at entry time (regime, session, ADX value, pattern score, etc.).

**Full Kelly (f\*)** — The mathematically optimal fraction of equity to risk per trade to maximise long-run growth. Too aggressive for live trading; use 0.25–0.5× Kelly.

**IS / OOS** — In-Sample and Out-of-Sample. In WF validation, IS is the training window; OOS is the unseen test window.

**MAE** — Maximum Adverse Excursion. How far a trade goes against you before it closes.

**MFE** — Maximum Favourable Excursion. How far a trade moves in your favour before it closes.

**Omega ratio** — Ratio of all positive trade outcomes to all negative outcomes, probability-weighted. Above 1.5 is positive expectancy.

**Profit Factor (PF)** — Gross winners / gross losers. 1.0 = breakeven. 1.5+ is generally considered tradeable.

**Regime** — Market condition label: `trending_up`, `trending_down`, `ranging_wide`, `ranging_tight`, `high_vol_expansion`, `low_vol_compression`.

**Run** — One NinjaTrader export file (one set of strategy settings).

**Sharpe ratio** — Mean return / standard deviation of returns. Annualised. Risk-free rate subtracted.

**Sortino ratio** — Like Sharpe but only penalises downside (losing) volatility.

**Ulcer Index** — Combined measure of drawdown depth and duration. Lower is better.

**Walk-forward validation** — Testing a strategy on time periods it was not optimised on. The standard for detecting overfitting.

**Win rate (WR)** — Percentage of trades that are profitable after costs.
