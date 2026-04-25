# Prediction System

An end-of-day market prediction engine that uses Claude to analyse current market conditions and predict the next trading session, then learns from outcomes over time.

---

## How it works

### The daily loop

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

---

## Modules

| File | Purpose |
|---|---|
| `models.py` | Data classes: `DailyPrediction`, `PredictionOutcome`, `LevelOutcome`, `AgentStats`, `SimilarContext` |
| `store.py` | JSONL append-only persistence at `.ta_artifacts/predictions/<instrument>/<contract>/` |
| `context_builder.py` | Assembles the full context dict passed to the agent |
| `claude_agent.py` | `ClaudeMarketAgent` — calls Claude with tool-use structured output |
| `orchestrator.py` | `predict_next_day()`, `measure_and_learn()`, `statistical_stub_agent()` |
| `outcome_measurer.py` | Measures actual outcomes from next-day bars |
| `scorer.py` | Proper scoring rules for all prediction components |
| `calibrator.py` | Feature vectors, cosine similarity search, ECE, drift detection |
| `run_prediction.py` | CLI entry point |

---

## Setup

### 1. Install dependencies

```bash
pip install -e ".[prediction]"
```

Or if anthropic is already installed:

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

### 3. Configure prediction.yaml

Copy `prediction.yaml` from the project root and edit:

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

---

## Running predictions

### End-of-day (after NY close)

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml
```

Output is a JSON prediction printed to stdout. The prediction is also persisted to the store automatically.

### Test without an API call

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml --dry-run
```

Uses the `statistical_stub_agent` — a baseline that votes on direction from historical analogues. No Claude call, no cost. Good for verifying data connectivity.

### Override the date (back-fill)

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml --asof 2025-03-21
```

### CLI flags always override YAML

Any flag can be passed on the command line and will take precedence over the YAML value:

```bash
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --contract 06-26 \
  --model claude-sonnet-4-6
```

---

## Measuring outcomes (next session)

After the session you predicted closes, run `measure_and_learn` to score all predictions for that date. This is the learning step — scored outcomes become the historical analogues for future predictions.

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

---

## What Claude predicts

Every prediction contains:

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

---

## How the context is built

Claude receives a structured prompt containing:

1. **Market regime** — ADX, normalised ATR, trend strength label (from `regime_recommender`)
2. **Multi-timeframe features** — 1m / 5m / 15m / 60m indicators (ATR, MA relationship, volatility percentile, etc.)
3. **Key daily levels** — Prior day H/L/C, prior week/month OHLC, classic pivots (PP, R1–R3, S1–S3), Camarilla levels (R3/R4, S3/S4)
4. **Economic event proximity** — Hours to next event, impact weights, event_risk_score
5. **Historical analogues** — The `n_similar` most similar past sessions (cosine similarity on a normalised 8-component feature vector), each showing what was predicted and what actually happened including the composite score
6. **Agent performance stats** — Rolling accuracy, ECE calibration error, drift warning (if detected)

---

## Scoring rules

Predictions are evaluated using proper scoring rules that reward calibration:

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
Averaged across all levels. Penalty applied if fewer than 3 levels are predicted.

### Breakout
Brier score:
```
score = 1 - (predicted_probability - actual)²
```
Plus a +0.05 direction bonus if the breakout direction was also correct.

### Composite
```
composite = 0.35 × trend + 0.25 × chop + 0.25 × levels + 0.15 × breakout
```

---

## How the system learns

After each scored session:

- The outcome is persisted to the JSONL store alongside the original prediction
- `find_similar_contexts()` uses cosine similarity on a normalised 8-component feature vector to find the most similar past sessions for any future prediction
- `compute_agent_stats()` tracks:
  - Rolling accuracy (10-day and 60-day windows)
  - **Expected Calibration Error (ECE)** — are stated confidences actually correct that often?
  - **Regime-conditional accuracy** — does the agent perform differently in trending vs. choppy regimes?
  - **Event vs. normal day accuracy** — do economic events degrade predictions?
  - **Drift detection** — if 10-day mean score drops more than 1 stdev below 60-day mean, a drift warning is injected into the next context so Claude can adjust

The agent builds a self-improving feedback loop: every prediction it makes either reinforces or challenges a part of its world model, visible to it in every subsequent session.

---

## Adding your own agent

Any callable with the signature `(context: dict) -> dict` is a valid agent:

```python
def my_agent(context: dict) -> dict:
    # ... your logic ...
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
            {"price": 21350.0, "label": "PP", "level_type": "pivot", "touch_probability": 0.65},
        ],
        "reasoning": "...",
    }

prediction = predict_next_day(
    ...,
    agent_fn=my_agent,
    agent_id="my_agent_v1",
)
```

Multiple agents can run on the same session — each gets its own `DailyPrediction` and `PredictionOutcome` keyed by `agent_id`. Performance stats are tracked independently per agent, enabling head-to-head comparison over time.
