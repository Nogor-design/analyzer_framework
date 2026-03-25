# Strategy Discovery Engine Design (ta_foundation + NinjaTrader 8)

## 1) System Architecture

The Strategy Discovery Engine extends `ta_foundation` using existing layers (analysis + reporting) without introducing new architectural tiers.

### High-level flow

1. **Ingest & Normalize (existing pipeline)**  
   Load run-scoped strategy exports into `AnalysisPackage` and shared market bars/ticks into `MarketDataStore` (`run_id=None`).
2. **Phase 1 — Entry Discovery**  
   Generate candidate entries from objective feature combinations (indicator, candle, structure, volatility/session context).
3. **Phase 2 — Entry Classification**  
   Classify each candidate as `automated`, `semi_discretionary`, or `hybrid`.
4. **Phase 3 — Exit Policy Discovery**  
   Evaluate independent exit families per entry candidate.
5. **Phase 4 — Strategy Evaluation & Ranking**  
   Score entry+exit combinations on risk-adjusted quality, stability, robustness, and tradability.
6. **Phase 5 — Output Templates & Insights**  
   Emit standardized templates for automated, semi-discretionary, and hybrid workflows.

### Placement in ta_foundation

- **Parsers:** unchanged unless new vendor format needed.
- **Pipeline:** minor enhancement only to wire analysis invocation and carry metadata/config.
- **Analysis modules:** primary location for discovery, optimization, clustering, ranking.
- **Report sections:** pure rendering of precomputed outputs from `pkg.metadata["derived"]`.

## 2) Module Responsibilities

Implement as analysis modules under `src/ta_foundation/analysis/` and optional renderer sections under `src/ta_foundation/reports/html/sections/`.

### A. `analysis/strategy_discovery/features.py`
- Build canonical feature matrix per instrument/contract/session.
- Ensure all timestamps are tz-aware America/Denver.
- Feature groups:
  - Indicator: SMA/EMA crosses, slope/separation, VWAP relation, RSI state, ATR regime, momentum/mean-reversion.
  - Candle: breakout/reversal/wick/engulfing/inside/outside/pressure/compression-expansion.
  - Structure/context: trend-range labels, MA distance, pullback depth, consolidation, break-retest.
  - Environment: TOD volatility, chop-directional state, compression→expansion transitions.
- Attach feature catalog and data quality flags in `pkg.metadata["derived"]["strategy_discovery"]["features"]`.

### B. `analysis/strategy_discovery/entry_discovery.py`
- Generate candidate entries from composable rule grammar:
  - `required_conditions` (hard gate)
  - `supporting_conditions` (soft score)
  - `parameters` (searchable ranges)
  - `context_filters` (session, regime)
- Produce long/short variants explicitly.
- Output candidate objects and first-pass event statistics.

### C. `analysis/strategy_discovery/classification.py`
- Classify candidates:
  - **automated:** fully objective + executable in NT8 C# logic.
  - **semi_discretionary:** objective setup + contextual confirmation checklist.
  - **hybrid:** machine setup + human veto/confirm step.
- Store rationale fields:
  - objective components
  - subjective/contextual components
  - automatable-later notes

### D. `analysis/strategy_discovery/exit_discovery.py`
- Apply exit families independently from entry discovery:
  - fixed, volatility-based, trailing, time-based, structural, hybrid exits.
- Evaluate per candidate with session/regime slices.
- Return best policy family plus robust parameter bands (not single-point optimum).

### E. `analysis/strategy_discovery/evaluation.py`
- Compute required metrics:
  - net profit, max drawdown, PF, win rate, avg trade, expectancy
  - MAE/MFE/ETD, duration, long-short asymmetry
  - session/regime segmented performance
  - time stability (walk-forward segments)
- Add robustness diagnostics:
  - sensitivity surfaces
  - overfit risk indicators
  - OOS degradation stats

### F. `analysis/strategy_discovery/clustering.py`
- Cluster near-duplicate entries using rule-signature + behavior similarity.
- Keep representative strategy per cluster and diversity across clusters.
- Prevent “parameter-neighbor spam” in final output.

### G. `analysis/strategy_discovery/importance.py`
- Feature importance across families (global + per regime).
- Essential vs cosmetic condition tagging.
- Failure pattern detection from losing-trade cohorts.

### H. `analysis/strategy_discovery/ranking.py`
- Weighted composite score with penalties for fragility/complexity.
- Produces ranked tables by:
  - global quality
  - session specialists
  - regime specialists
  - implementation simplicity

### I. Optional report sections (pure rendering)
- `strategy_discovery_overview`
- `strategy_discovery_ranked_table`
- `strategy_discovery_cluster_map`
- `strategy_discovery_template_cards`

All section logic reads `ctx["packages"]`, `ctx["market"]`, and `ctx["options"]`, never disk.

## 3) Data Flow Across Phases

## Step-by-step contract

1. **Pipeline ingest**
   - Parse run exports to `AnalysisPackage`.
   - Parse shared minute/tick market files to `MarketDataStore` (`run_id=None`).
2. **Feature build**
   - Join run trades with shared bars where needed by timestamp/instrument.
   - Derive objective features per bar/event.
3. **Entry discovery**
   - Create entry candidates as formal templates.
   - Emit candidate IDs and trigger events.
4. **Classification**
   - Assign automation class + rationale.
5. **Exit discovery**
   - For each candidate ID, run independent exit policy search by family.
6. **Evaluation**
   - Produce metrics global + by session/regime/segment.
7. **Clustering & importance**
   - Collapse similar candidates; compute condition value and failure motifs.
8. **Ranking**
   - Final composite score + confidence tier.
9. **Template output**
   - Write machine-usable templates to `pkg.assets` and metadata summaries to `pkg.metadata["derived"]`.

### Storage locations

- **Primary analytic outputs:** `pkg.metadata["derived"]["strategy_discovery"][...]`
- **Exportable artifacts:** `pkg.assets["strategy_discovery"][...]` (JSON/CSV/HTML snippets)
- **Shared market data:** only in `MarketDataStore`, never copied into package tables.

## 4) Output Differences by Strategy Type

### A) Fully automated
- Exact boolean entry rules, parameter bounds, exact exit rules.
- NT8-ready order semantics (entry signal name, stop/target model, time/session filters, risk cap rules).
- No subjective fields required.

### B) Semi-discretionary
- Machine-detected setup + structured review checklist.
- Confidence score and grade (A/B/C).
- Required checklist items vs optional confirmations.
- Explicit “skip conditions” and failure signatures.

### C) Hybrid
- Deterministic setup detection + single human gate.
- Protocol: detect → alert → confirm/veto window → execute.
- Trace which human decision dimensions are candidates for future automation.

## 5) Suggested Scoring & Ranking Framework

Use a two-level score: **Quality Score** + **Deployment Score**.

### 5.1 Quality Score (0–100)

`quality = 0.30*RiskAdj + 0.25*Stability + 0.20*Robustness + 0.15*RegimeFit + 0.10*ExecutionFit`

Components:
- **RiskAdj:** normalized expectancy, PF, avg trade / drawdown.
- **Stability:** rolling-period consistency, OOS retention, low equity volatility spikes.
- **Robustness:** parameter plateau width, low sensitivity curvature, low cluster over-concentration.
- **RegimeFit:** whether it performs where enabled and degrades gracefully where disabled.
- **ExecutionFit:** slippage tolerance proxy, holding-time practicality, trade frequency suitability.

### 5.2 Deployment Score (0–100)

`deploy = 0.45*Automatability + 0.25*Simplicity + 0.20*OperationalRisk + 0.10*MonitoringEase`

- **Automatability:** objective completeness of rule set.
- **Simplicity:** number of conditions/parameters and branching depth.
- **OperationalRisk:** session transition sensitivity, news/vol spikes fragility.
- **MonitoringEase:** clarity of failure conditions and live diagnostics.

### 5.3 Final rank

`final_score = 0.75*quality + 0.25*deploy - overfit_penalty - complexity_penalty`

Use guardrails:
- Minimum trade count threshold per segment.
- Minimum OOS PF and expectancy thresholds.
- Hard reject if severe instability or regime collapse.

## 6) Recommended Output Schema

Use one canonical schema with type-specific blocks.

```yaml
strategy_id: "SD-NQ-EMA_VWAP_PULLBACK-LONG-v1"
strategy_type: "automated | semi_discretionary | hybrid"
instrument: "NQ"
contract: "NQ 06-26"
timeframe: "1m"

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
    ema_fast: {min: 8, max: 21, robust_band: [10, 16]}
    ema_slow: {min: 34, max: 55, robust_band: [34, 48]}
  context:
    sessions: ["NY_OPEN", "POWER_HOUR"]
    regimes: ["trend", "expansion_after_compression"]
  confidence_features:
    - "ma_separation_zscore"
    - "vwap_reclaim_strength"

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
  tested_families: ["fixed", "atr", "trailing", "time", "structural", "hybrid"]
  recommended_logic:
    stop: "1.3 * ATR(14)"
    target_1: "1.2R at 50% size"
    runner: "ATR trail 2.2"
    break_even_rule: "activate at +0.8R"
    time_stop: "exit after 45 minutes"
  regime_overrides:
    high_vol: {stop_mult: 1.6, trail_mult: 2.6}
    low_vol: {stop_mult: 1.1, target_r: 1.0}

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
    long: {pf: 1.56}
    short: {pf: 1.12}
  by_session:
    NY_OPEN: {pf: 1.62, expectancy: 104}
    MIDDAY: {pf: 0.91, expectancy: -8}

robustness:
  parameter_plateau_score: 0.81
  walk_forward_stability: 0.74
  oos_degradation: 0.12
  overfit_risk: "medium"

ranking:
  quality_score: 78.2
  deployment_score: 69.5
  final_score: 73.8
  rank_global: 4
  rank_cluster: 1

semi_discretionary:
  checklist_required:
    - "trend intact on 5m"
    - "no immediate overhead HVN"
  checklist_optional:
    - "delta confirms"
  setup_grade:
    score: 82
    tier: "A"
  skip_if:
    - "inside first 2 minutes after macro release"
    - "ATR regime extreme with wick>2x body"

nt8_notes:
  entry_signal_name: "SD_EMA_VWAP_PULLBACK_L"
  managed_mode: true
  calculate: "OnBarClose or OnEachTick per variant"
  session_template: "CME US Index Futures RTH+ETH"
  risk_controls:
    daily_loss_limit: 1500
    max_consecutive_losses: 3
```

## 7) Practical NinjaTrader 8 Implementation Guidance

### 7.1 Research mode vs production mode

- **Research mode**
  - Wider parameter ranges, richer diagnostics, heavier segmentation.
  - Output includes full sensitivity maps and cluster diagnostics.
- **Production mode**
  - Freeze approved robust parameter bands.
  - Enforce strict risk constraints and enable/disable rules by session/regime.
  - Emit concise NT8 implementation packet only.

### 7.2 NT8 integration checklist

1. Convert selected template into NT8 C# strategy skeleton.
2. Keep entry and exit modules separate classes/method groups.
3. Implement session filter as explicit schedule map.
4. Implement regime gate as pre-trade predicate.
5. Add daily risk governor (loss cap, max trades, cooldown).
6. Log live feature values for drift monitoring.
7. Validate on replay + walk-forward segments before live.

### 7.3 Anti-curve-fit controls (mandatory)

- Prefer robust plateaus over peak settings.
- Cap rule complexity (max required conditions + max parameters).
- Enforce minimum sample sizes by session/regime.
- Require OOS confirmation before promotion.
- Reject strategies with unstable MAE/MFE profile drift.

### 7.4 Suggested promotion lifecycle

1. **Discovered** → candidate generated.
2. **Validated** → passes segmentation and OOS thresholds.
3. **Paper-ready** → operational checklist complete.
4. **Live-small** → reduced size with drift monitoring.
5. **Live-standard** → full deployment with continuous scoring.

## 8) Template Artifacts to Generate

For each promoted strategy/setup, generate:

- `strategy_template.json` (machine-readable full schema)
- `nt8_mapping.yaml` (field mapping to NT8 parameters/signals)
- `review_card.html` (human summary)
- `failure_patterns.csv` (common loss signatures)
- `parameter_plateau.csv` (robust ranges)
- `regime_enablement.yaml` (on/off rules by session/volatility/trend)

These artifacts allow iterative refinement without redesigning discovery logic.
