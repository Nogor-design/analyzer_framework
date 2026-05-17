# Strategy Discovery Engine Design (ta_foundation + NinjaTrader 8)

## 1) System Architecture

The Strategy Discovery Engine extends `ta_foundation` using existing layers (analysis + reporting) without introducing new architectural tiers.

### High-level flow

1. **Ingest & Normalize (existing pipeline)**
   Load run-scoped strategy exports into `AnalysisPackage` and shared market bars/ticks into `MarketDataStore` (`run_id=None`).
2. **Phase 0 — Regime Labeling**
   Compute independent market regime labels from shared bars only. Must run before any strategy-level analysis to avoid circular regime definitions.
3. **Phase 1 — Feature Construction**
   Build temporally-gated feature matrix per instrument/contract/session. Every feature is validated against its lag policy before use.
4. **Phase 2 — Entry Discovery**
   Generate candidate entries from objective feature combinations (indicator, candle, structure, volatility/session context). Consumes existing Pattern Engine audit results as first-pass signal.
5. **Phase 3 — Entry Classification**
   Classify each candidate as `automated`, `semi_discretionary`, or `hybrid`.
6. **Phase 4 — MAE/MFE Analysis**
   Compute MAE/MFE/ETD distributions per candidate to drive exit parameter ranges. Feeds directly into Phase 5.
7. **Phase 5 — Exit Policy Discovery**
   Evaluate independent exit families per entry candidate using MAE/MFE-derived parameter bounds. Returns regime-conditional policies, not single-point optima.
8. **Phase 6 — Validation**
   Apply walk-forward validation, statistical significance tests, Monte Carlo trade-sequence analysis, and cost normalization. Hard-rejects candidates that fail minimum thresholds.
9. **Phase 7 — Strategy Evaluation & Ranking**
   Score validated entry+exit combinations on risk-adjusted quality, stability, robustness, and tradability. Includes ranking weight sensitivity check.
10. **Phase 8 — Output Templates & Insights**
    Emit durable strategy templates (rules only) and separate timestamped evaluation snapshots (performance metrics). Enables decay tracking over time.

### Placement in ta_foundation

- **Parsers:** unchanged unless new vendor format needed.
- **Pipeline:** minor enhancement only to wire analysis invocation and carry metadata/config.
- **Analysis modules:** primary location for discovery, optimization, clustering, ranking.
- **Report sections:** pure rendering of precomputed outputs from `pkg.metadata["derived"]`.

---

## 2) Module Responsibilities

Implement as analysis modules under `src/ta_foundation/analysis/strategy_discovery/` and optional renderer sections under `src/ta_foundation/reports/html/sections/`.

### A. `analysis/strategy_discovery/regime.py` *(new — runs first)*

Compute market regime labels **independently of any strategy output**. This is critical: regime labels must not be derived from strategy P&L, or all regime-conditional performance stats become circular.

- **Inputs:** shared market bars from `MarketDataStore` only. Never reads `AnalysisPackage`.
- **Label each day/session:** `trending_up`, `trending_down`, `ranging_tight`, `ranging_wide`, `high_vol_expansion`, `low_vol_compression`
- **Methods:**
  - Primary: ADX-based trend/range gate + ATR-regime (high/normal/low relative to rolling 20-day ATR percentile)
  - Optional: Hidden Markov Model for smoother state transitions
  - Simple fallback: EMA slope + ATR-regime combination
- **Output:** stored in `MarketDataStore` metadata keyed by `(instrument, contract, date)`, not in any `AnalysisPackage`. Sections and analysis modules look up regime by timestamp.
- Attach regime catalog and coverage stats in `MarketDataStore.metadata["regime"]`.

### B. `analysis/strategy_discovery/features.py`

Build temporally-gated feature matrix per instrument/contract/session.

- Ensure all timestamps are tz-aware America/Denver.
- **Look-ahead bias controls (mandatory):**
  - Every feature carries an explicit `lag_policy` specifying how many bars of delay before the value is available at trade entry.
  - Rolling windows must not extend past the entry bar timestamp.
  - VWAP, open-drive, and session-level features must declare their earliest available bar within the session (e.g., open-drive is not available until bar N of the session).
  - A validation pass runs at construction time and raises on any feature whose computation window overlaps the entry bar's future data.
- **Memory management:** features are computed on-demand per candidate evaluation window, not pre-materialized as a full-dataset dense matrix. Add `max_bars` and `chunk_size` config to cap memory usage on large datasets (NQ 1m × 3 years ≈ 500k bars).
- Feature groups:
  - **Indicator:** SMA/EMA crosses, slope/separation, VWAP relation, RSI state, ATR regime, momentum/mean-reversion.
  - **Candle:** breakout/reversal/wick/engulfing/inside/outside/pressure/compression-expansion.
  - **Structure/context:** trend-range labels, MA distance, pullback depth, consolidation, break-retest.
  - **Environment:** TOD volatility, chop-directional state, compression→expansion transitions.
  - **Pattern Engine bridge:** reads `pkg.metadata["derived"]["trade_pattern_audit"]` and exposes per-bar `confirming_score`, `verdict`, and per-pattern fired flags as first-class features. This avoids duplicating logic already in the 22-pattern audit system.
- Attach feature catalog, lag policy map, and data quality flags in `pkg.metadata["derived"]["strategy_discovery"]["features"]`.

### C. `analysis/strategy_discovery/entry_discovery.py`

Generate candidate entries from composable rule grammar. Consumes Pattern Engine audit output as a first-pass filter to narrow the search space before combinatorial expansion.

- Rule grammar:
  - `required_conditions` (hard gate — must all be true)
  - `supporting_conditions` (soft score — weighted sum, sourced from pattern audit `confirming_score`)
  - `parameters` (searchable ranges with explicit step sizes)
  - `context_filters` (session, regime label from `regime.py`)
- The pattern audit's `confirming_score` and per-pattern correlation table map directly onto `supporting_conditions`. Do not recompute what the audit already provides.
- Produce long/short variants explicitly.
- Output candidate objects and first-pass event statistics.
- Enforce minimum event density before advancing a candidate: if fewer than 50 IS-period triggers exist, mark candidate as `insufficient_data` and skip exit discovery.

### D. `analysis/strategy_discovery/classification.py`

Classify candidates using explicit, objective criteria — not judgment calls:

- **automated:** all conditions are computable from OHLCV + indicators with no visual/contextual interpretation required. Executable as NT8 C# logic directly.
- **semi_discretionary:** objective setup + at least one condition requiring visual confirmation (e.g., "trend intact on 5m" or "no immediate overhead HVN"). Generates a structured review checklist.
- **hybrid:** machine-detected setup + single explicit human gate. Gate condition must be named and its automation path documented.

Store rationale fields per candidate:
- objective components (list)
- subjective/contextual components (list)
- automatable-later notes (list, with suggested proxy indicators)

### E. `analysis/strategy_discovery/mae_mfe.py` *(new — feeds exit discovery)*

Compute MAE/MFE/ETD distributions per candidate. This module drives exit parameter bounds rather than leaving them as arbitrary search ranges.

- **MAE distribution** → natural stop level: the 80th percentile of winning-trade MAE is the lower bound for stop placement. Stops tighter than this cut winners before they can develop.
- **MFE distribution** → natural target level: diminishing returns typically appear beyond the 60th percentile MFE. Targets beyond this have low capture probability.
- **ETD (end-trade drawdown)** → trailing stop calibration: the ETD distribution tells you how much profit is given back on average and at the 80th percentile, which sets the trailing activation and distance.
- Outputs a `exit_parameter_bounds` dict per candidate:
  ```python
  {
      "stop_min_atr_mult":   <float>,   # MAE p80 winners expressed in ATR units
      "stop_max_atr_mult":   <float>,
      "target_min_r":        <float>,   # MFE p60 expressed in R
      "target_max_r":        <float>,
      "trail_activation_r":  <float>,   # ETD p50 expressed in R
      "trail_distance_atr":  <float>,   # ETD p80 expressed in ATR
  }
  ```
- These bounds replace arbitrary hardcoded search ranges in `exit_discovery.py`.

### F. `analysis/strategy_discovery/exit_discovery.py`

Apply exit families independently from entry discovery. Uses MAE/MFE-derived bounds from `mae_mfe.py` as the parameter search space.

Exit family search space (MAE/MFE bounds constrain min/max):
```yaml
exit_families:
  fixed_rr:
    stop_atr_mult:  {min: 0.8, max: 3.0, step: 0.2}   # overridden by mae_mfe bounds
    target_rr:      {min: 1.0, max: 3.0, step: 0.25}
  trailing:
    activation_r:   {min: 0.5, max: 1.5, step: 0.25}
    trail_atr_mult: {min: 1.5, max: 3.5, step: 0.25}
  time_stop:
    max_minutes:    {min: 15,  max: 120, step: 15}
  hybrid:
    target_1_pct_size: {min: 0.40, max: 0.60, step: 0.10}
    break_even_r:      {min: 0.6,  max: 1.2,  step: 0.2}
```

- Evaluate each family per candidate with session/regime slices.
- Return **regime-conditional policy**, not a single winner:
  - e.g., `{ranging: fixed_1.5R, trending: trail_2.0ATR, high_vol: fixed_2.5R}`
- Store the full family comparison matrix (all tested families + their IS metrics) for transparency.
- Return robust parameter bands, not single-point optima: prefer a plateau-center over a peak.

### G. `analysis/strategy_discovery/validation.py` *(new — hard gates)*

Centralized validation pass. Candidates that fail are hard-rejected and do not advance to ranking.

**Walk-forward specification:**
```yaml
walk_forward:
  wf_type: rolling        # rolling | anchored
  is_pct: 0.70            # 70% in-sample, 30% OOS
  min_oos_trades: 20      # hard reject below this
  min_is_trades: 50       # hard reject below this
  n_folds: 5
  degradation_threshold: 0.20   # OOS PF must be >= IS PF * (1 - 0.20)
```

**Statistical significance requirements:**
- Student's t-test on trade returns vs zero: p < 0.05 required to advance.
- Binomial confidence interval on win rate: lower bound of 95% CI must exceed 50% (for non-mean-reversion strategies) or the theoretical edge must be demonstrated via expectancy.
- Minimum 50 IS trades and 20 OOS trades enforced as hard rejects before any stat test is run.

**Monte Carlo trade-sequence analysis:**
- Randomize trade order N=1,000 times on the IS period trade list.
- Compute max drawdown distribution across shuffled runs.
- Hard reject if actual max drawdown exceeds the 95th percentile of the shuffled distribution — this catches strategies whose results depend on favorable trade sequencing rather than genuine edge.
- Report the Monte Carlo percentile rank of actual drawdown in the output.

**Transaction cost normalization:**
- All metric computation uses cost-normalized P&L. Raw NT8 P&L is adjusted before any metric is calculated.
- Config block:
  ```yaml
  cost_model:
    commission_per_side: 2.09    # NQ futures, per contract per side
    slippage_ticks: 1            # conservative 1-tick slippage on entry + exit
    tick_value: 5.00             # NQ = $5/tick
  ```
- Cost normalization applies to every candidate regardless of whether the originating NT8 backtest included commissions, preventing comparison bias.

### H. `analysis/strategy_discovery/evaluation.py`

Compute performance metrics on **cost-normalized, OOS-validated** trade sets only.

Required metrics:
- net profit, max drawdown, PF, win rate, avg trade, expectancy
- MAE/MFE/ETD (from `mae_mfe.py` — do not recompute)
- duration, long-short asymmetry
- session/regime segmented performance (using regime labels from `regime.py`)
- time stability via walk-forward folds (from `validation.py`)

Robustness diagnostics:
- sensitivity surfaces (parameter ± 1 step in each direction — how much does the metric change?)
- overfit risk indicators
- OOS degradation stats (IS PF vs OOS PF ratio)
- Monte Carlo drawdown percentile rank (from `validation.py`)

### I. `analysis/strategy_discovery/clustering.py`

Cluster near-duplicate entries using rule-signature + behavior similarity.

- Keep representative strategy per cluster (highest quality_score within cluster).
- Maintain diversity across clusters in the final ranked output.
- Prevent "parameter-neighbor spam": two candidates with the same rule set and parameters within one step of each other collapse to one.
- Report cluster membership counts so the user can see how well-represented each strategy archetype is.

### J. `analysis/strategy_discovery/importance.py`

Feature importance across families (global + per regime).

- Essential vs cosmetic condition tagging: a condition is "cosmetic" if removing it changes the quality_score by less than 5%.
- Failure pattern detection from losing-trade cohorts: what features were commonly present on losing trades that were absent on winners?
- Cross-reference with Pattern Engine per-pattern correlation data from `trade_pattern_audit` to avoid duplicate computation.

### K. `analysis/strategy_discovery/ranking.py`

Weighted composite score with penalties for fragility/complexity.

Produces ranked tables by:
- global quality
- session specialists
- regime specialists
- implementation simplicity

**Ranking weight sensitivity check (mandatory):**
After computing the final ranking, perturb each score component weight by ±20% and recompute rankings. Report which candidates change rank position. If the top-5 ranking changes significantly under weight perturbation, flag this in the output as `ranking_fragile: true`. This prevents the scoring weights from artificially driving results.

### L. Optional report sections (pure rendering)

- `strategy_discovery_overview`
- `strategy_discovery_ranked_table`
- `strategy_discovery_cluster_map`
- `strategy_discovery_template_cards`

All section logic reads `ctx["packages"]`, `ctx["market"]`, and `ctx["options"]`, never disk.

---

## 3) Data Flow Across Phases

### Step-by-step contract

1. **Pipeline ingest**
   - Parse run exports to `AnalysisPackage`.
   - Parse shared minute/tick market files to `MarketDataStore` (`run_id=None`).

2. **Regime labeling** *(Phase 0 — runs on MarketDataStore only)*
   - Compute regime labels per day/session from shared bars.
   - Store in `MarketDataStore.metadata["regime"]` — never in any `AnalysisPackage`.

3. **Feature build** *(Phase 1)*
   - Validate all features against lag policy before materialization.
   - Join run trades with shared bars where needed by timestamp/instrument.
   - Bridge Pattern Engine audit results into feature space.
   - Compute features in chunks; do not pre-materialize full-dataset matrix.

4. **Entry discovery** *(Phase 2)*
   - Use Pattern Engine `confirming_score` as first-pass filter.
   - Create entry candidates as formal rule templates.
   - Emit candidate IDs and trigger events.
   - Check minimum event density; skip `insufficient_data` candidates.

5. **Classification** *(Phase 3)*
   - Assign automation class + rationale using explicit objective criteria.

6. **MAE/MFE analysis** *(Phase 4)*
   - Compute stop/target/trail bounds from trade distribution.
   - Pass `exit_parameter_bounds` to exit discovery.

7. **Exit discovery** *(Phase 5)*
   - For each candidate ID, run family search within MAE/MFE-derived bounds.
   - Produce regime-conditional exit policies.

8. **Validation** *(Phase 6)*
   - Walk-forward on each candidate.
   - Statistical significance tests.
   - Monte Carlo trade-sequence test.
   - Cost-normalize all P&L.
   - Hard-reject failures; do not advance to ranking.

9. **Evaluation** *(Phase 7)*
   - Metrics on validated, cost-normalized trade sets.
   - Sensitivity surfaces and robustness diagnostics.

10. **Clustering & importance** *(Phase 7, parallel)*
    - Collapse near-duplicate candidates.
    - Tag essential vs cosmetic conditions.

11. **Ranking** *(Phase 7)*
    - Final composite score + confidence tier.
    - Weight sensitivity check; flag fragile rankings.

12. **Template output** *(Phase 8)*
    - Write durable strategy templates (rules only) to `pkg.assets`.
    - Write timestamped evaluation snapshots (performance metrics) separately.
    - Metadata summaries to `pkg.metadata["derived"]["strategy_discovery"]`.

### Storage locations

- **Regime labels:** `MarketDataStore.metadata["regime"]` — shared, never per-package
- **Primary analytic outputs:** `pkg.metadata["derived"]["strategy_discovery"][...]`
- **Durable strategy templates:** `pkg.assets["strategy_discovery"]["templates"][...]` (rules, classification, NT8 notes)
- **Evaluation snapshots:** `pkg.assets["strategy_discovery"]["evaluations"][...]` (timestamped performance, regenerated each run)
- **Shared market data:** only in `MarketDataStore`, never copied into package tables

---

## 4) Output Differences by Strategy Type

### A) Fully automated
- Exact boolean entry rules, parameter bounds, exact exit rules.
- NT8-ready order semantics (entry signal name, stop/target model, time/session filters, risk cap rules).
- No subjective fields required.
- Regime-conditional exit policy embedded directly.

### B) Semi-discretionary
- Machine-detected setup + structured review checklist.
- Confidence score and grade (A/B/C).
- Required checklist items vs optional confirmations.
- Explicit "skip conditions" and failure signatures.
- Each subjective checklist item must name its candidate automation proxy.

### C) Hybrid
- Deterministic setup detection + single explicit human gate.
- Gate condition must be named, not described vaguely.
- Protocol: detect → alert → confirm/veto window → execute.
- Trace which human decision dimensions are candidates for future automation.

---

## 5) Suggested Scoring & Ranking Framework

Use a two-level score: **Quality Score** + **Deployment Score**.

### 5.1 Quality Score (0–100)

`quality = 0.30*RiskAdj + 0.25*Stability + 0.20*Robustness + 0.15*RegimeFit + 0.10*ExecutionFit`

Components:
- **RiskAdj:** normalized expectancy, PF, avg trade / drawdown — all on cost-normalized, OOS trade sets.
- **Stability:** rolling-period WF consistency, OOS retention, low equity volatility spikes.
- **Robustness:** parameter plateau width, low sensitivity curvature, low cluster over-concentration, Monte Carlo drawdown percentile rank.
- **RegimeFit:** whether it performs where enabled and degrades gracefully where disabled (using independent regime labels from `regime.py`).
- **ExecutionFit:** slippage tolerance proxy, holding-time practicality, trade frequency suitability.

### 5.2 Deployment Score (0–100)

`deploy = 0.45*Automatability + 0.25*Simplicity + 0.20*OperationalRisk + 0.10*MonitoringEase`

- **Automatability:** objective completeness of rule set (automated > hybrid > semi_discretionary).
- **Simplicity:** number of conditions/parameters and branching depth. More conditions = lower score.
- **OperationalRisk:** session transition sensitivity, news/vol spikes fragility.
- **MonitoringEase:** clarity of failure conditions and live diagnostics.

### 5.3 Final rank

`final_score = 0.75*quality + 0.25*deploy - overfit_penalty - complexity_penalty`

Guardrails (all enforced before scoring — not just recommended):
- Minimum 50 IS trades and 20 OOS trades (hard reject).
- Statistical significance: p < 0.05 on t-test of trade returns (hard reject).
- Minimum OOS PF and expectancy thresholds.
- Monte Carlo drawdown must be below 95th percentile of shuffled runs (hard reject).
- Hard reject if severe instability or regime collapse.

### 5.4 Ranking weight sensitivity check

After final ranking is produced, perturb each weight in the quality and deployment formulas by ±20% independently and recompute all scores. Record which candidates change rank position in the top 10.

- If the top-5 ranking is stable across all perturbations: `ranking_stable: true`
- If any top-5 candidate moves out of the top 10 under perturbation: `ranking_fragile: true` with details

This check is cheap (matrix multiply) and prevents scoring weights from artificially driving the output.

---

## 6) Output Schema

**Critical distinction:** durable template (rules — version-controlled, changes only when rules change) is stored separately from the evaluation snapshot (performance metrics — timestamped, regenerated on each run). This enables decay tracking by diffing evaluation snapshots over time.

### 6.1 Durable strategy template (`strategy_template.json`)

```yaml
strategy_id: "SD-NQ-EMA_VWAP_PULLBACK-LONG-v1"
strategy_type: "automated | semi_discretionary | hybrid"
instrument: "NQ"
timeframe: "1m"
template_version: 1
template_created: "2026-03-25"

entry:
  pattern_name: "EMA Pullback to VWAP"
  side: "long"
  required_conditions:
    - "ema_fast > ema_slow"
    - "close crosses above vwap"
    - "pullback_depth_ticks <= 14"
  supporting_conditions:
    - "rsi_14 between 48 and 62"
    - "atr_regime in [normal, high]"
  parameters:
    ema_fast: {min: 8,  max: 21, robust_band: [10, 16]}
    ema_slow: {min: 34, max: 55, robust_band: [34, 48]}
  context:
    sessions: ["NY_OPEN", "POWER_HOUR"]
    regimes:  ["trending_up", "high_vol_expansion"]   # from regime.py labels
  confidence_features:
    - "ma_separation_zscore"
    - "vwap_reclaim_strength"
    - "pattern_audit_confirming_score"   # bridges Pattern Engine audit

classification:
  category: "hybrid"
  rationale: "Objective setup detection with discretionary tape confirmation"
  objective_parts:
    - "trend filter"
    - "vwap reclaim"
    - "pullback depth"
  subjective_parts:
    - "micro-structure aggression"
  automatable_later:
    - "replace tape read with bid/ask delta threshold"

exit_policy:
  best_family: "hybrid"
  regime_conditional:
    ranging:    {family: "fixed_rr",  stop_atr_mult: 1.1, target_rr: 1.5}
    trending:   {family: "trailing",  activation_r: 0.8,  trail_atr_mult: 2.2}
    high_vol:   {family: "fixed_rr",  stop_atr_mult: 1.6, target_rr: 2.0}
  tested_families: ["fixed", "atr", "trailing", "time", "structural", "hybrid"]
  mae_mfe_basis:
    stop_derived_from: "mae_p80_winners"
    target_derived_from: "mfe_p60"
    trail_derived_from: "etd_p80"

nt8_notes:
  entry_signal_name: "SD_EMA_VWAP_PULLBACK_L"
  managed_mode: true
  calculate: "OnBarClose or OnEachTick per variant"
  session_template: "CME US Index Futures RTH+ETH"
  risk_controls:
    daily_loss_limit: 1500
    max_consecutive_losses: 3

semi_discretionary:
  checklist_required:
    - "trend intact on 5m"
    - "no immediate overhead HVN"
  checklist_optional:
    - "delta confirms"
  skip_if:
    - "inside first 2 minutes after macro release"
    - "ATR regime extreme with wick>2x body"
```

### 6.2 Timestamped evaluation snapshot (`strategy_eval_{date}.json`)

```yaml
strategy_id: "SD-NQ-EMA_VWAP_PULLBACK-LONG-v1"
eval_date: "2026-03-25"
eval_contract: "NQ 03-26"
cost_model:
  commission_per_side: 2.09
  slippage_ticks: 1
  tick_value: 5.00

performance:
  net_profit: 125430
  max_drawdown: -18400
  profit_factor: 1.48
  win_rate: 0.44
  expectancy: 82.4
  avg_trade: 67.1
  mae_avg: -112
  mfe_avg: 196
  etd_avg: 59
  avg_duration_min: 23
  asymmetry:
    long:  {pf: 1.56}
    short: {pf: 1.12}
  by_session:
    NY_OPEN: {pf: 1.62, expectancy: 104}
    MIDDAY:  {pf: 0.91, expectancy: -8}
  by_regime:
    trending_up:        {pf: 1.72, n_trades: 84}
    ranging_tight:      {pf: 0.88, n_trades: 31}
    high_vol_expansion: {pf: 1.54, n_trades: 47}

validation:
  is_trades: 118
  oos_trades: 42
  is_pf: 1.61
  oos_pf: 1.44
  oos_degradation: 0.11
  t_test_p_value: 0.018
  win_rate_ci_lower: 0.38
  monte_carlo_dd_percentile: 72   # actual DD was at 72nd pct of shuffled runs — acceptable
  wf_type: rolling
  n_folds: 5

robustness:
  parameter_plateau_score: 0.81
  walk_forward_stability: 0.74
  overfit_risk: "medium"
  sensitivity:
    ema_fast_plus1:  {pf_delta: -0.04}
    ema_fast_minus1: {pf_delta: +0.02}
    ema_slow_plus2:  {pf_delta: -0.07}

ranking:
  quality_score: 78.2
  deployment_score: 69.5
  final_score: 73.8
  rank_global: 4
  rank_cluster: 1
  ranking_stable: true   # top-5 position held under ±20% weight perturbation
```

---

## 7) Practical NinjaTrader 8 Implementation Guidance

### 7.1 Research mode vs production mode

- **Research mode**
  - Wider parameter ranges, richer diagnostics, heavier segmentation.
  - Output includes full sensitivity maps, cluster diagnostics, and Monte Carlo distributions.
  - Walk-forward uses rolling 5-fold with wide IS windows.
- **Production mode**
  - Freeze approved robust parameter bands.
  - Enforce strict risk constraints and enable/disable rules by session/regime.
  - Apply cost model before any promotion decision.
  - Emit concise NT8 implementation packet only.

### 7.2 NT8 integration checklist

1. Convert selected template into NT8 C# strategy skeleton.
2. Keep entry and exit modules separate classes/method groups.
3. Implement session filter as explicit schedule map using regime-conditional exit blocks.
4. Implement regime gate as pre-trade predicate, using the same regime definition as `regime.py`.
5. Add daily risk governor (loss cap, max trades, cooldown).
6. Log live feature values for drift monitoring — these feed back into the Live-monitored promotion stage.
7. Validate on replay + walk-forward segments before live.
8. Apply identical cost model (commission + slippage) in NT8 backtest settings and in ta_foundation `cost_model` config to ensure metric comparability.

### 7.3 Anti-curve-fit controls (mandatory)

- Prefer robust plateaus over peak settings.
- Cap rule complexity (max required conditions + max parameters).
- Enforce minimum sample sizes by session/regime — these are hard rejects in `validation.py`, not advisory.
- Require OOS confirmation before promotion: OOS PF ≥ IS PF × (1 − degradation_threshold).
- Reject strategies with unstable MAE/MFE profile drift.
- Monte Carlo trade-sequence test is mandatory: actual drawdown must be below the 95th percentile of 1,000 shuffled runs.
- Statistical significance: p < 0.05 on t-test of trade returns required. Low win-rate strategies must also pass a binomial test.

### 7.4 Promotion lifecycle

1. **Discovered** — candidate generated, event density check passed.
2. **Validated** — passes `validation.py` (WF, stat significance, Monte Carlo, cost-normalization). This is a hard gate.
3. **Paper-ready** — operational checklist complete, regime-conditional exits assigned, NT8 packet generated.
4. **Live-small** — reduced size with drift monitoring active.
5. **Live-standard** — full deployment with continuous scoring.
6. **Live-monitored** — NT8 exports live trades which are ingested by the existing parser pipeline. The quality_score is recomputed on rolling live data. An alert fires when:
   - `quality_score` drops > 15 points from the paper-ready baseline, **or**
   - OOS degradation exceeds threshold on accumulated live trades, **or**
   - Regime distribution shifts significantly from the IS regime mix.
   This stage feeds back into the discovery engine for strategy retirement or re-optimization decisions.

---

## 8) Template Artifacts to Generate

For each promoted strategy/setup, generate two artifact classes:

**Durable (version-controlled, rules only):**
- `strategy_template.json` — machine-readable full rule schema
- `nt8_mapping.yaml` — field mapping to NT8 parameters/signals
- `regime_enablement.yaml` — on/off rules by session/volatility/trend, keyed to `regime.py` labels

**Timestamped (regenerated each evaluation run):**
- `strategy_eval_{date}.json` — performance snapshot with cost-normalized metrics, WF results, Monte Carlo stats
- `review_card_{date}.html` — human summary card
- `failure_patterns_{date}.csv` — common loss signatures from current evaluation period
- `parameter_plateau_{date}.csv` — robust ranges from current sensitivity surface

Separating durable from timestamped artifacts allows iterative refinement and explicit decay tracking: diffing two evaluation snapshots shows whether a strategy is improving, stable, or degrading as new data accumulates.
