# Prediction System

Two complementary forecasting layers ship in this package:

1. **Daily prediction** — one Claude-driven forecast per session covering
   direction, key levels, breakout probability, and chop/trend regime.
   Best for end-of-day judgement calls.
2. **Horizon prediction** — multi-timeframe, multi-horizon, calibrated
   probability forecasting with statistical / analogue / ensemble
   agents, walk-forward backtesting, and a tradable-zone filter that
   converts probabilities into trade decisions. Best for execution-side
   automation.

The two systems share no storage and never modify each other. Pick the
one that fits your task — or run both in parallel.

---

## Table of contents

- [Daily system](#daily-system)
  - [How it works](#daily-how-it-works)
  - [Setup](#daily-setup)
  - [Running predictions](#daily-running-predictions)
  - [Measuring outcomes](#daily-measuring-outcomes)
  - [What Claude predicts](#what-claude-predicts)
  - [Scoring](#daily-scoring)
  - [Adding your own agent](#daily-adding-your-own-agent)
- [Horizon system](#horizon-system)
  - [Architecture](#horizon-architecture)
  - [Quick start](#horizon-quick-start)
  - [The agents](#horizon-the-agents)
  - [Storage layout](#horizon-storage-layout)
  - [Reports](#horizon-reports)
  - [YAML config — `prediction.yaml`](#horizon-yaml-config--predictionyaml)
  - [HTML report integration](#horizon-html-report-integration)
  - [Building your own agent](#horizon-building-your-own-agent)
- [Module map](#module-map)

---

# Daily system

<a id="daily-how-it-works"></a>
## How it works

```
After NY close (every session)
        │
        ▼
┌─────────────────────┐
│  Build Context       │  Multi-TF features, regime, daily levels,
│  context_builder.py  │  economic events, historical analogues
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Call Claude         │  claude-opus-4-7 with adaptive thinking
│  claude_agent.py     │  and tool-use structured output
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Validate & Persist  │  Schema validation → PredictionStore
│  orchestrator.py     │  (.ta_artifacts/predictions/JSONL)
└────────┬────────────┘
         │
         ▼  (next session closes)
┌─────────────────────┐
│  Measure Outcome     │  Actual direction, efficiency ratio,
│  outcome_measurer.py │  level touches, breakout detection
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Score Prediction    │  Proper scoring rules — reward confident
│  scorer.py           │  correct calls, penalise confident wrong
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Feed Back           │  Scored outcomes become future analogues,
│  calibrator.py       │  ECE calibration, drift detection
└─────────────────────┘
```

<a id="daily-setup"></a>
## Setup

### 1. Install dependencies

```bash
pip install -e ".[prediction]"
```

Or if `anthropic` is already installed:

```bash
pip install anthropic>=0.90.0
```

### 2. Set your API key

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# macOS / Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Configure the daily YAML

The daily system uses a separate config (typically at the project
root, NOT this package's `prediction.yaml` which is for the horizon
system — see below):

```yaml
instrument: NQ
contract: "06-26"
market_data: C:/NinjaTrader/exports/market_data
calendar: C:/path/to/forexfactory_calendar.csv   # optional
output_dir: .ta_artifacts/predictions
model: claude-opus-4-7
n_similar: 5
dry_run: false
```

<a id="daily-running-predictions"></a>
## Running predictions

### End-of-day (after NY close)

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml
```

Output is a JSON prediction printed to stdout. The prediction is also
persisted to the store automatically.

### Test without an API call

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml --dry-run
```

Uses the `statistical_stub_agent` — a baseline that votes on direction
from historical analogues. No Claude call, no cost. Good for verifying
data connectivity.

### Override the date (back-fill)

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml --asof 2025-03-21
```

### CLI flags always override YAML

```bash
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --contract 06-26 \
  --model claude-sonnet-4-6
```

<a id="daily-measuring-outcomes"></a>
## Measuring outcomes (next session)

After the session you predicted closes, run `measure_and_learn` to
score all predictions for that date. This is the learning step —
scored outcomes become the historical analogues for future
predictions.

```python
from pathlib import Path
import pandas as pd
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.prediction.store import PredictionStore
from ta_foundation.prediction.orchestrator import measure_and_learn

market = MarketDataStore(Path("C:/NinjaTrader/exports/market_data"))
store = PredictionStore(Path(".ta_artifacts/predictions"), "NQ", "06-26")

bars = market.get_bars("NQ", "06-26", timeframe="1m")
prior_atr = 85.0  # prior day ATR in points

outcomes = measure_and_learn(
    bars_next_day=bars,
    store=store,
    instrument="NQ",
    contract="06-26",
    target_date="2025-03-22",
    prior_atr=prior_atr,
)

for o in outcomes:
    print(f"{o.prediction_id[:8]}  composite={o.composite_score:.3f}")
```

<a id="what-claude-predicts"></a>
## What Claude predicts

Every daily prediction contains:

| Field | Type | Description |
|---|---|---|
| `trend_direction` | `bullish` / `bearish` / `neutral` | Expected net direction |
| `trend_confidence` | float [0–1] | Confidence in the direction call |
| `is_trending` | bool | True = directional day, False = chop |
| `chop_confidence` | float [0–1] | Confidence in trend/chop classification |
| `predicted_high` | float | Estimated session high (instrument points) |
| `predicted_low` | float | Estimated session low |
| `breakout_probability` | float [0–1] | Prob. of opening-range breakout |
| `breakout_direction` | `up` / `down` / `either` / `none` | Expected breakout direction |
| `event_risk_score` | float [0–1] | Composite economic event risk |
| `key_levels` | list (≤8) | Price, label, type, touch probability |
| `reasoning` | string | 2–4 sentence narrative |

### Context Claude receives

1. **Market regime** — ADX, normalised ATR, trend strength label.
2. **Multi-timeframe features** — 1m / 5m / 15m / 60m indicators.
3. **Key daily levels** — Prior day H/L/C, prior week/month OHLC,
   classic pivots, Camarilla levels.
4. **Economic event proximity** — Hours to next event, impact weights.
5. **Historical analogues** — `n_similar` most similar past sessions
   (cosine similarity on a normalised 8-component feature vector),
   each showing what was predicted and what actually happened
   including the composite score.
6. **Agent performance stats** — Rolling accuracy, ECE calibration,
   drift warning if detected.

<a id="daily-scoring"></a>
## Scoring

Predictions are evaluated using **proper scoring rules** that reward
calibration, not just accuracy.

### Trend direction
```
score = 0.5 + (confidence - 0.5) × (2 × correct - 1)
```
- 0.5 confidence always scores exactly 0.5 (no information)
- 0.95 confidence + correct → ~0.95
- 0.95 confidence + wrong  → ~0.05

### Chop vs trend
Same formula applied to `is_trending == actual_is_trending`.

### Key levels
```
score = touch_probability          if level was touched
score = 1 - touch_probability      if level was not touched
```
Averaged across all levels. Penalty applied if fewer than 3 levels are
predicted.

### Breakout (Brier)
```
score = 1 - (predicted_probability - actual)²
```
Plus a +0.05 direction bonus if the breakout direction was also
correct.

### Composite
```
composite = 0.35 × trend + 0.25 × chop + 0.25 × levels + 0.15 × breakout
```

After each scored session, `compute_agent_stats` tracks rolling
accuracy (10 / 60-day windows), ECE, regime-conditional accuracy,
event-day vs normal-day delta, and drift detection. If the 10-day
mean drops more than 1 stdev below the 60-day mean, a drift warning
is injected into the next context so Claude can adjust.

<a id="daily-adding-your-own-agent"></a>
## Adding your own daily agent

Any callable with the signature `(context: dict) -> dict` is a valid
agent:

```python
def my_agent(context: dict) -> dict:
    return {
        "trend_direction": "bullish",
        "trend_confidence": 0.65,
        "is_trending": True,
        "chop_confidence": 0.60,
        "predicted_high": 21500.0,
        "predicted_low": 21200.0,
        "breakout_probability": 0.40,
        "breakout_direction": "up",
        "event_risk_score": 0.20,
        "key_levels": [
            {"price": 21350.0, "label": "PP",
             "level_type": "pivot", "touch_probability": 0.65},
        ],
        "reasoning": "...",
    }

prediction = predict_next_day(
    ...,
    agent_fn=my_agent,
    agent_id="my_agent_v1",
)
```

Multiple agents can run on the same session — each gets its own
`DailyPrediction` and `PredictionOutcome` keyed by `agent_id`.
Performance stats are tracked independently per agent, enabling
head-to-head comparison over time.

---

# Horizon system

A separate, parallel layer that produces calibrated probability
distributions for the next *N candles* on a specific timeframe, plus
the machinery to backtest, score, ensemble, and turn predictions into
trade decisions. See `HORIZON_DESIGN.md` for the original design
document and the per-phase shipping notes.

<a id="horizon-architecture"></a>
## Architecture

```
HorizonContext (asof, timeframe, horizon_n, session, regime, …)
            │
            ▼
HorizonAgent.predict(bars, asof_idx, horizon, …)   ◄── many agents
            │
            ▼
CandleHorizonPrediction
   • bullish/bearish/neutral probabilities (sum to 1)
   • return distribution (expected, p10/25/50/75/90)
   • path stats (MFE / MAE)
   • threshold first-hit probabilities
   • diagnostics (sample_size, fallback_level, calibration_bucket)
            │
            ▼
measure_horizon_outcome(bars, asof, N, thresholds)
            │
            ▼
CandleHorizonOutcome (actual direction / return / MFE / MAE / hits)
            │
            ▼
score_horizon_prediction(pred, outcome)
   • Brier (direction), Brier (thresholds), MAE (return)
   • path error, calibration error
   • composite ∈ [0, 1] under configurable weights
            │
            ▼
HorizonPredictionStore   (JSONL, partitioned by instrument + contract)
            │
            ├──► HorizonReportBundle  (leaderboard, matrices, edge, drift, calibration)
            │
            └──► HorizonConfig.apply()   ◄── deployment-time policy
                    • AbstentionPolicy   (operator vetoes)
                    • TradableZoneVerdict (cost-aware decision + sizing)
```

Walk-forward leakage is enforced everywhere:

- Agents only ever see `bars[:asof_idx + 1]` for prediction-side work.
- Outcome measurement reads `bars[asof_idx + 1 : asof_idx + 1 + N]`
  and never feeds those bars back into a re-prediction.
- Backtest scoring deliberately uses calibration_error=0 so future
  ECE never leaks into past scores; reports compute ECE separately
  from the persisted (prediction, outcome) pairs.

<a id="horizon-quick-start"></a>
## Quick start

### Run a backtest from the CLI

```bash
python -m ta_foundation.prediction.backtest_horizon_predictions \
    --minute-bars-file "C:/NinjaTrader/exports/NQ 06-26.Last.txt" \
    --store-dir .ta_artifacts/horizon \
    --timeframes 5m,15m \
    --horizons 3,5 \
    --asof-warmup 200 \
    --asof-stride 10 \
    --print-report
```

This loads the minute-bars file with the existing NinjaTrader parser,
populates a `MarketDataStore`, runs the four built-in agents
(statistical + analogue + regime / session specialists) over every
`(timeframe, horizon, asof)` triple in the schedule, scores each
outcome, and persists everything to
`.ta_artifacts/horizon/NQ_06-26/horizon_*.jsonl`.

`--print-report` then dumps the full report bundle (leaderboard,
matrices, edge, calibration, drift) to stdout in plaintext.

### Programmatic use

```python
from ta_foundation.prediction import (
    AnalogueProbabilityAgent,
    HorizonBatchRunner,
    HorizonBatchSpec,
    HorizonPredictionStore,
    StatisticalProbabilityAgent,
    asofs_from_bars,
    build_schedule,
    make_market_bar_loader,
    run_horizon_backtest,
)
from ta_foundation.marketdata.store import MarketDataStore

market = MarketDataStore()
# ... ingest data into `market` via your normal pipeline ...

bar_loader = make_market_bar_loader(market)
agents = [StatisticalProbabilityAgent(), AnalogueProbabilityAgent()]
store = HorizonPredictionStore(".ta_artifacts/horizon", "NQ", "06-26")

bars_5m = market.get_bars("NQ", "06-26", "5m")
specs = build_schedule(
    instrument="NQ", contract="06-26",
    timeframes=["5m"], horizons=[3, 5],
    asofs=asofs_from_bars(bars_5m, warmup=200, stride=10),
)

runner = HorizonBatchRunner(agents=agents, bar_loader=bar_loader, store=store)
summary = run_horizon_backtest(runner, specs)
print(summary.as_dict())
```

### Single live prediction

```python
from ta_foundation.prediction import (
    AnalogueProbabilityAgent,
    HorizonConfig,
    load_horizon_config_or_default,
)

agent = AnalogueProbabilityAgent()
pred = agent.predict(
    bars=bars_5m, asof_idx=len(bars_5m) - 1,
    horizon_candles=3,
    instrument="NQ", contract="06-26", timeframe="5m",
)

cfg: HorizonConfig = load_horizon_config_or_default(
    "src/ta_foundation/prediction/prediction.yaml"
)
result = cfg.apply(pred)
if result.verdict.is_tradable:
    print(
        f"{result.verdict.recommended_direction} "
        f"size={result.verdict.recommended_size_fraction:.2%} "
        f"target={result.verdict.recommended_target_points:.2f} pts "
        f"stop={result.verdict.recommended_stop_points:.2f} pts"
    )
else:
    print("Skip:", result.verdict.rejection_reasons)
```

<a id="horizon-the-agents"></a>
## The agents

All agents satisfy the `HorizonAgent` Protocol:

```python
class HorizonAgent(Protocol):
    agent_id: str
    def predict(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> CandleHorizonPrediction: ...
```

Built-in implementations (auto-registered in `DEFAULT_REGISTRY`):

| `agent_id` (registry key) | Class / factory | Method |
|---|---|---|
| `statistical` | `StatisticalProbabilityAgent` | Conditional-frequency baseline. Buckets historical bars by `(session, regime)`, computes empirical probabilities + return / MFE / MAE moments, smooths toward a parent baseline with empirical-Bayes alpha. Falls back full → session → unfiltered. |
| `analogue` | `AnalogueProbabilityAgent` | Distance-weighted KNN over a 4-dim continuous feature vector (atr_zscore, trend_slope, body_atr, momentum). Gaussian kernel weighting; soft fallback through filter relaxations. |
| `regime_specialist` | `make_regime_specialist_agent` | Analogue agent locked to regime match (ignores session). |
| `session_specialist` | `make_session_specialist_agent` | Analogue agent locked to session match (ignores regime). |
| `ensemble_v1` | `EnsembleHorizonAgent` | Combines members under per-bucket stacking weights learned from rolling composite scores; weighted-averages all distribution fields and renormalizes. Members that abstain are excluded; if every member abstains, the ensemble itself abstains. |

```python
from ta_foundation.prediction import DEFAULT_REGISTRY

stat   = DEFAULT_REGISTRY.create("statistical")
knn    = DEFAULT_REGISTRY.create("analogue")
regime = DEFAULT_REGISTRY.create("regime_specialist")
DEFAULT_REGISTRY.list_types()
# → ['analogue', 'regime_specialist', 'session_specialist', 'statistical']
```

### Ensemble + stacking weights

```python
from ta_foundation.prediction import (
    EnsembleHorizonAgent,
    StackingWeightTable,
    compute_stacking_weights,
)

# After a backtest has filled the store with (pred, outcome) pairs:
pairs = store.get_pairs(require_non_abstain=True)
table = compute_stacking_weights(
    pairs,
    min_samples_per_agent=20,
    floor_weight=0.05,        # quarter of mass reserved as min per agent
)
table.save_to_path(".ta_artifacts/horizon/stacking_weights.json")

# Load the table for live prediction:
loaded = StackingWeightTable.load_from_path(
    ".ta_artifacts/horizon/stacking_weights.json"
)
ensemble = EnsembleHorizonAgent(
    members=[stat, knn, regime],
    weight_table=loaded,
)
pred = ensemble.predict(bars_5m, asof_idx, horizon_candles=3,
                        instrument="NQ", contract="06-26", timeframe="5m")
```

<a id="horizon-storage-layout"></a>
## Storage layout

```
{store_dir}/
└── {instrument}_{contract}/
    ├── horizon_predictions.jsonl   # one CandleHorizonPrediction per line
    └── horizon_outcomes.jsonl      # one CandleHorizonOutcome per line
```

JSONL is append-only and idempotent on `prediction_id`. Outcomes are
keyed by `prediction_id` and reject duplicates with
`DuplicateHorizonOutcomeError` so the backtest can be re-run safely.

<a id="horizon-reports"></a>
## Reports

Six structured reports built read-only from the store:

```python
from ta_foundation.prediction import build_full_report, format_full_report

bundle = build_full_report(
    store,
    min_samples_cell=5,
    min_samples_edge=20,
    min_samples_calibration=20,
    drift_recent_n=50,
)
print(format_full_report(bundle))
```

| Report | Builder | Tells you |
|---|---|---|
| Agent leaderboard | `build_agent_leaderboard` | Composite, accuracy, ECE, drift flag, abstention rate per agent |
| Timeframe × horizon matrix | `build_timeframe_horizon_matrix` | Where each agent shines on (tf, horizon) cells |
| Session matrix | `build_session_matrix` | Strongest (agent, session, tf, horizon) combinations |
| Best-edge finder | `build_best_edge_cells` | Top cells by realized edge in ATR units |
| Calibration | `build_calibration_report` | Per-bucket ECE + reliability bands; worst first |
| Drift | `build_drift_report` | Recent-vs-long-window composite z-score per agent |

Each builder pairs with a `format_*` plaintext renderer, and
`HorizonReportBundle.as_dict()` round-trips to JSON-safe dicts so
you can persist or pipe them anywhere.

<a id="horizon-yaml-config--predictionyaml"></a>
## YAML config — `prediction.yaml`

The horizon system is driven by a single YAML at
`src/ta_foundation/prediction/prediction.yaml` (or your own copy).
Three sections, all optional:

```yaml
horizon:
  cost_model:        # round-trip trading friction in points
    fixed_points_per_side: 0.50
    slippage_atr_per_side: 0.05
    spread_points: 0.0
  abstention:        # operator vetoes applied AFTER the agent emits
    min_sample_size: 20
    min_effective_sample_size: 8.0
    max_fallback_level: 2
    min_confidence: 0.40
    regime_blacklist: []
    session_blacklist: []
    honor_agent_abstain: true
  tradable_zone:     # cost-aware "should we trade this" decision
    min_confidence: 0.55
    min_expected_edge_atr: 0.05
    min_effective_sample_size: 8.0
    allow_neutral_argmax: false
    kelly_cap: 0.25
    min_size_fraction: 0.0
```

The shipped `prediction.yaml` carries inline comments documenting
every parameter, typical value ranges per instrument style, and how
the three sections compose. Read that file first — it is the
authoritative reference.

```python
from ta_foundation.prediction import (
    HorizonConfig,
    load_horizon_config,
    load_horizon_config_or_default,
)

cfg = load_horizon_config("path/to/prediction.yaml")     # raises if missing
cfg = load_horizon_config_or_default("…")                # safe wrapper

# Apply the full deployment pipeline:
result = cfg.apply(my_prediction)
result.prediction       # post-abstention prediction (may be vetoed)
result.verdict          # TradableZoneVerdict
result.policy_changed   # True if abstention modified the prediction
```

The loader accepts both `horizon: { … }` and a flat top-level mapping.
Missing sections fall back to dataclass defaults; unknown keys are
ignored so adding fields here will never break older code.

<a id="horizon-html-report-integration"></a>
## HTML report integration

A `horizon_overview` section is registered in `SECTION_REGISTRY` for
the existing HTML report pipeline. Add it to your `report.yaml`:

```yaml
sections:
  - id: horizon_overview
    options:
      store_dir: .ta_artifacts/horizon
      instrument: NQ
      contract: "06-26"
      min_samples_cell: 5
      min_samples_edge: 20
      min_samples_calibration: 20
      drift_recent_n: 50
      top_n_edge: 20
```

Or pass an in-memory bundle directly via context:

```python
ctx = {"horizon_bundle": build_full_report(store), ...}
html = render_horizon_overview(ctx)
```

The section renders all six reports as self-contained HTML tables —
no external assets, no IO outside the optional store read.

<a id="horizon-building-your-own-agent"></a>
## Building your own horizon agent

```python
from ta_foundation.prediction import (
    CandleHorizonPrediction,
    DEFAULT_REGISTRY,
    HorizonAgent,
)

class MyAgent:
    agent_id: str = "my_agent_v1"

    def predict(self, bars, asof_idx, horizon_candles,
                instrument, contract, timeframe) -> CandleHorizonPrediction:
        # ... your model ...
        return CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument, contract=contract,
            timeframe=timeframe,
            asof_timestamp=str(bars.iloc[asof_idx]["dt"]),
            session_label="ny_open",
            horizon_candles=int(horizon_candles),
            bullish_probability=0.6, bearish_probability=0.2,
            neutral_probability=0.2,
            sample_size=42, method_used="my_method_v1",
            feature_snapshot={"regime": "trend_up", "prior_atr": 6.0},
            upside_threshold_points=6.0,
            downside_threshold_points=6.0,
            reasoning_summary="...",
        )

# Register so other code can build it by name
DEFAULT_REGISTRY.register("my_agent", MyAgent)

# Confirm it satisfies the Protocol at runtime
assert isinstance(MyAgent(), HorizonAgent)
```

Shared feature primitives live in `horizon_features.py`
(`compute_atr`, `compute_regime_labels`,
`compute_session_regime_atr`, `compute_lite_outcome`) — reuse them
when building new agents so your bucket semantics stay aligned with
the built-ins.

---

## Module map

### Daily system (unchanged across phases)
| File | Purpose |
|---|---|
| `models.py` | `DailyPrediction`, `PredictionOutcome`, `LevelOutcome`, `AgentStats`, `SimilarContext` |
| `store.py` | JSONL store at `.ta_artifacts/predictions/<instrument>/<contract>/` |
| `context_builder.py` | Assembles the context dict passed to the agent |
| `claude_agent.py` | `ClaudeMarketAgent` — Claude tool-use structured output |
| `ollama_agent.py` | Local LLM agent variant |
| `orchestrator.py` | `predict_next_day`, `measure_and_learn`, `statistical_stub_agent` |
| `outcome_measurer.py` | Measures actual outcomes from next-day bars |
| `scorer.py` | Proper scoring rules for daily components |
| `calibrator.py` | Feature vectors, cosine similarity, ECE, drift |
| `run_prediction.py` | Daily-system CLI entry point |
| `run_multi_agent.py` | Daily multi-agent orchestration |

### Horizon system
| File | Purpose | Phase |
|---|---|---|
| `horizon_models.py` | `CandleHorizonPrediction`, `CandleHorizonOutcome` | 1 |
| `session_classifier.py` | `SessionConfig`, `label_session` (NY-clock, DST-safe) | 1 |
| `horizon_features.py` | Shared feature primitives (ATR, regime, session, lite outcome) | 1 (deduped post-5) |
| `horizon_outcome_measurer.py` | `measure_horizon_outcome` — leakage-safe future-window measurement | 1 |
| `horizon_scorer.py` | Brier / MAE / composite scoring | 1 |
| `statistical_probability_agent.py` | Conditional-frequency baseline + EB smoothing | 1 |
| `analogue_probability_agent.py` | Distance-weighted KNN over 4-dim feature vector | 2 |
| `horizon_store.py` | JSONL store at `<base>/<instrument>_<contract>/` | 2 |
| `horizon_calibrator.py` | Per-bucket ECE + reliability buckets | 2 |
| `horizon_batch.py` | `HorizonBatchRunner`, `HorizonBatchSpec`, schedule helpers | 3 |
| `backtest_horizon_predictions.py` | Walk-forward replay; CLI entry point | 3 (CLI wired post-5) |
| `horizon_reports.py` | Leaderboard, matrices, edge, calibration, drift | 3 |
| `horizon_specialists.py` | Regime / session specialist factories | 4 |
| `horizon_ensemble.py` | `EnsembleHorizonAgent` + stacking weights (with JSON persistence) | 4 |
| `horizon_costs.py` | `CostModel` for round-trip trading friction | 5 |
| `horizon_tradable_zone.py` | Tradable-zone filter + Kelly-lite sizing | 5 |
| `horizon_abstention.py` | `AbstentionPolicy` (operator vetoes) | 5 |
| `horizon_config.py` | `HorizonConfig` + YAML loader | 5 |
| `horizon_agent.py` | `HorizonAgent` Protocol + `AgentRegistry` | post-5 |
| `prediction.yaml` | Default horizon config (fully documented inline) | 5 |
| `HORIZON_DESIGN.md` | Original design doc + per-phase shipping notes | — |
