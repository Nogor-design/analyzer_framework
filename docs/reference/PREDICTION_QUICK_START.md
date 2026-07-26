# Prediction Quick Start

**Status:** End-to-end guide to daily and horizon prediction systems  
**Last Updated:** May 24, 2026  
**Audience:** Traders, researchers, execution systems  

---

## Overview

ta_foundation provides **two complementary prediction systems**:

### 1. Daily Prediction
- **When:** End-of-day, before next session opens
- **What:** Direction, key levels, breakout probability, regime
- **How:** Claude Opus 4.7 analyzes market context
- **For:** Decision support, pre-session planning
- **Frequency:** Once per session (daily)

### 2. Horizon Prediction
- **When:** Continuous, before/during session
- **What:** Multi-timeframe probabilities for next N candles
- **How:** Ensemble of 5 statistical/ML agents with walk-forward backtesting
- **For:** Automated execution, intraday trading signals
- **Frequency:** Multiple times per session (as configured)

**Key difference:** Daily = judgement support; Horizon = automation ready.

---

## Daily Prediction System

### Setup (5 minutes)

#### 1. Install dependencies

```bash
pip install -e ".[prediction]"
# or just the anthropic library
pip install anthropic>=0.90.0
```

#### 2. Set Anthropic API key

```powershell
# Windows
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# PowerShell (alternate)
set ANTHROPIC_API_KEY=sk-ant-...

# Bash / macOS / Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

**Get your key:** https://console.anthropic.com/

#### 3. Create prediction.yaml

Save this file in your project root:

```yaml
# Daily prediction config (for Daily system)
instrument: NQ
contract: "06-26"
market_data: "C:/NinjaTrader/exports/market_data"
calendar: "C:/path/to/forexfactory_calendar.csv"    # Optional
output_dir: .ta_artifacts/predictions
model: claude-opus-4-7
n_similar: 5                                         # Historical analogues to show Claude
dry_run: false                                       # Set true for testing (no API calls)
```

**Fields:**

| Field | Required | Default | Meaning |
|---|---|---|---|
| `instrument` | yes | — | Ticker symbol (NQ, ES, YM, etc.) |
| `contract` | yes | — | Contract month (06-26, 09-26, etc.) |
| `market_data` | yes | — | Folder with minute bar data |
| `calendar` | no | — | ForexFactory CSV for economic events |
| `output_dir` | no | `.ta_artifacts/predictions` | Where to save predictions |
| `model` | no | `claude-opus-4-7` | LLM model name |
| `n_similar` | no | 5 | # of historical analogues to show Claude |
| `dry_run` | no | false | Test mode (no API call) |

#### 4. Verify market data

```bash
# Check that minute bars exist
ls "C:/NinjaTrater/exports/market_data/"
# Should list: NQ.06-26.Last.txt (or similar)

# Verify format (OHLCV with timestamps)
head -5 "NQ.06-26.Last.txt"
```

### Running Daily Predictions

#### End-of-day (after NY close)

```bash
python -m ta_foundation.prediction.run_prediction --config prediction.yaml
```

**Output:** JSON prediction printed to stdout + saved to store.

```json
{
  "prediction_id": "pred_20260524_173920_claude_opus_4_7",
  "asof": "2026-05-24T20:00:00Z",
  "trend_direction": "bullish",
  "trend_confidence": 0.72,
  "is_trending": true,
  "chop_confidence": 0.65,
  "predicted_high": 21500.0,
  "predicted_low": 21200.0,
  "breakout_probability": 0.55,
  "breakout_direction": "up",
  "event_risk_score": 0.30,
  "key_levels": [
    {
      "price": 21350.0,
      "label": "Pivot Point",
      "level_type": "pivot",
      "touch_probability": 0.72
    },
    {
      "price": 21500.0,
      "label": "Resistance 1",
      "level_type": "resistance",
      "touch_probability": 0.45
    }
  ],
  "reasoning": "Strong bullish bias on daily close + positive economic context. Expecting range expansion."
}
```

#### Test mode (no API cost)

```bash
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --dry-run
```

Uses a statistical baseline instead of Claude. Good for:
- Testing data pipeline
- Verifying market data connectivity
- CI/CD pipeline validation

#### Override date (back-fill predictions)

```bash
# Predict for a past session (e.g., to measure outcomes)
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --asof 2026-05-23
```

#### CLI overrides YAML

```bash
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --contract "09-26" \
  --model claude-sonnet-4-6 \
  --dry-run
```

### What Claude Sees (Context)

Before making a prediction, Claude receives:

1. **Market Regime** (2 features)
   - ADX (trend strength: 0–100)
   - ATR (volatility: normalized)

2. **Multi-Timeframe Indicators** (8 features)
   - 1-minute: RSI, slope
   - 5-minute: RSI, slope
   - 15-minute: RSI, slope
   - 60-minute: RSI, slope

3. **Key Daily Levels** (8 levels max)
   - Prior day high/low/close
   - Prior week OHLC
   - Prior month OHLC
   - Classic Pivot, Camarilla, support/resistance

4. **Economic Events** (next 24 hours)
   - Event name, hours away, impact weight (1–3)

5. **Historical Analogues** (N=5 most similar past sessions)
   - Past session: date, what was predicted, what actually happened, score
   - Enables Claude to learn from past mistakes

6. **Agent Performance** (rolling stats)
   - 10-day accuracy, 60-day accuracy
   - Expected Calibration Error (ECE) — how well-calibrated are the confidence scores?
   - Drift warning if recent performance < prior mean

### What Claude Predicts

| Field | Range | Meaning |
|---|---|---|
| `trend_direction` | bullish / bearish / neutral | Net expected direction |
| `trend_confidence` | 0.0–1.0 | Confidence in the call |
| `is_trending` | true / false | True = directional day; false = choppy |
| `chop_confidence` | 0.0–1.0 | Confidence in trend/chop classification |
| `predicted_high` | float | Estimated session high (in instrument points) |
| `predicted_low` | float | Estimated session low |
| `breakout_probability` | 0.0–1.0 | Probability of opening-range breakout |
| `breakout_direction` | up / down / either / none | Expected breakout direction |
| `event_risk_score` | 0.0–1.0 | Risk from economic events (0=safe, 1=high risk) |
| `key_levels` | list ≤8 | Price, label, type, touch probability |
| `reasoning` | string | 2–4 sentence narrative |

### Measuring Outcomes (Next Session)

After the session you predicted closes, run the outcome measurement to:
- See if your prediction was right
- Score how well-calibrated it was
- Learn from the result

```python
from pathlib import Path
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.prediction.store import PredictionStore
from ta_foundation.prediction.orchestrator import measure_and_learn

# Load data
market = MarketDataStore(Path("C:/NinjaTrader/exports/market_data"))
store = PredictionStore(Path(".ta_artifacts/predictions"), "NQ", "06-26")

# Get the next day's bars
bars = market.get_bars("NQ", "06-26", timeframe="1m")

# Measure outcomes for the session we predicted (May 24)
outcomes = measure_and_learn(
    bars_next_day=bars,
    store=store,
    instrument="NQ",
    contract="06-26",
    target_date="2026-05-24",
    prior_atr=85.0  # From previous day
)

# View results
for outcome in outcomes:
    print(f"Prediction: {outcome.prediction_id[:8]}")
    print(f"Composite score: {outcome.composite_score:.3f}")
    print(f"Trend: predicted={outcome.predicted_direction}, actual={outcome.actual_direction}")
    print(f"---")
```

### Scoring Explained

Predictions use **proper scoring rules** that reward calibration:

```
Trend direction score:
  = 0.5 + (confidence - 0.5) × (2 × correct - 1)
  
  Example:
    confidence=0.9, correct=True   → 0.5 + 0.4 × 1   = 0.9  (great)
    confidence=0.9, correct=False  → 0.5 + 0.4 × -1  = 0.1  (bad)
    confidence=0.5, correct=True   → 0.5 + 0.0 × 1   = 0.5  (no signal)
```

**Composite score** (weighted average):
```
composite = 0.35 × trend_score
          + 0.25 × chop_score
          + 0.25 × levels_score
          + 0.15 × breakout_score
```

### Viewing Prediction Store

```bash
# List all stored predictions
ls .ta_artifacts/predictions/

# View recent predictions (JSONL format)
tail -5 .ta_artifacts/predictions/predictions.jsonl

# Count predictions
wc -l .ta_artifacts/predictions/predictions.jsonl

# Filter by date
grep "2026-05-24" .ta_artifacts/predictions/predictions.jsonl
```

### Integrating with Live Trading

Once you have a prediction, use it in your trading logic:

```python
from ta_foundation.prediction.store import PredictionStore

store = PredictionStore(Path(".ta_artifacts/predictions"), "NQ", "06-26")
latest = store.get_latest()

if latest.trend_direction == "bullish" and latest.trend_confidence > 0.70:
    # Take long trade
    enter_long(stop=latest.predicted_low - 50)
elif latest.trend_direction == "bearish" and latest.trend_confidence > 0.70:
    # Take short trade
    enter_short(stop=latest.predicted_high + 50)
```

---

## Horizon Prediction System

### Overview

The **Horizon system** predicts probabilities for the **next N candles** (e.g., 3 candles, 5 candles, 1 hour). It's designed for automation:

- Multi-timeframe (5m, 15m, 1h, etc.)
- Multi-horizon (3c, 5c, 1h, 4h, etc.)
- Ensemble of 5 agents (statistical, ML, analogue, regime-specialist, ensemble)
- Walk-forward backtested
- Calibrated (Expected Calibration Error measured)
- Tradable zones (converts probabilities → trade signals)

### Setup

#### 1. Install dependencies (already done if you did Daily setup)

```bash
pip install -e ".[prediction]"
```

#### 2. Create prediction.yaml for Horizon

Save as `horizon_prediction.yaml` (separate from daily config):

```yaml
# Horizon prediction config
horizon_system:
  enabled: true
  
  # Timeframes to predict on
  timeframes:
    - "5m"
    - "15m"
    - "1h"
  
  # Horizons: number of candles to predict into the future
  horizons:
    - 3      # 3 candles
    - 5      # 5 candles
  
  # Agent configuration
  agents:
    - name: statistical_baseline
      enabled: true
      params:
        lookback: 252      # 252-bar lookback for frequency
    
    - name: analogue_probability
      enabled: true
      params:
        k: 5               # KNN neighbors
        metric: cosine
    
    - name: regime_specialist
      enabled: true
      params:
        regime_column: "market_regime"  # from features
    
    - name: session_specialist
      enabled: true
      params:
        session_type: "ny_session"
    
    - name: ensemble
      enabled: true
      params:
        weights: "learned"  # or static weights
  
  # Market data
  market_data_root: "C:/NinjaTrader/exports/market_data"
  instrument: "NQ"
  contract: "06-26"
  
  # Output
  store_dir: ".ta_artifacts/horizon_predictions"
  
  # Walk-forward backtesting
  backtest:
    enabled: true
    train_size: 252      # Use 252 bars to train
    test_size: 20        # Hold out 20 bars for test
    step: 5              # Roll forward by 5 bars each iteration
  
  # Calibration
  calibration:
    enabled: true
    n_buckets: 5         # Probability buckets: [0–0.2], [0.2–0.4], etc.
    drift_detection: true
    drift_threshold: 0.15  # Warn if ECE > threshold
```

#### 3. Verify market data

Same as daily — need minute bars in `C:/NinjaTrader/exports/market_data/`.

### Running Horizon Predictions

#### Backtest the system (offline)

```bash
python -m ta_foundation.prediction.backtest_horizon_predictions \
  --config horizon_prediction.yaml \
  --minute-bars-file "C:/NinjaTrader/exports/NQ.06-26.Last.txt" \
  --store-dir .ta_artifacts/horizon
```

**Output:** Walk-forward results + calibration plots in `.ta_artifacts/horizon/`.

#### Get a live prediction (during session)

```python
from pathlib import Path
from ta_foundation.prediction.horizon import HorizonPredictor

predictor = HorizonPredictor(
    config_path=Path("horizon_prediction.yaml")
)

# Get prediction for 5m / 3-candle horizon
prediction = predictor.predict(
    timeframe="5m",
    horizon_n=3,
    asof_idx=-1  # Current bar
)

print(f"Direction: {prediction.direction}")  # bullish / bearish / neutral
print(f"Prob bullish: {prediction.prob_bullish:.2f}")
print(f"Prob bearish: {prediction.prob_bearish:.2f}")
print(f"Expected return: {prediction.expected_return:.2f}")
```

### Understanding Horizon Output

#### CandleHorizonPrediction

```python
{
  "timeframe": "5m",
  "horizon_n": 3,
  "direction": "bullish",
  "prob_bullish": 0.65,
  "prob_bearish": 0.25,
  "prob_neutral": 0.10,
  "expected_return": 8.5,        # points
  "return_p10": -5.0,
  "return_p25": 2.0,
  "return_p50": 8.5,
  "return_p75": 15.0,
  "return_p90": 22.0,
  "path_stats": {
    "max_favorable_excursion": 25.0,
    "max_adverse_excursion": -8.0
  },
  "threshold_hits": {
    "25_point_up": 0.48,      # Prob of hitting +25 in next 3 candles
    "25_point_down": 0.12
  },
  "diagnostics": {
    "sample_size": 127,
    "agents_contributing": 5,
    "calibration_bucket": 3,   # [0.6–0.8]
    "ece": 0.08                # Expected Calibration Error
  }
}
```

#### Interpreting Results

| Field | Meaning | Action |
|---|---|---|
| `direction` | bullish/bearish/neutral | Net expected direction |
| `prob_bullish` | 0.0–1.0 | Confidence in up move |
| `expected_return` | float | Expected move in points |
| `return_p90` | float | 90th percentile expected return (upside) |
| `return_p10` | float | 10th percentile (downside) |
| `threshold_hits` | dict | Prob of hitting specific price targets |
| `ece` | float | Calibration error (0.0 = perfect, 1.0 = terrible) |

### Tradable Zones

Convert horizon probabilities into trade signals:

```python
from ta_foundation.prediction.horizon import compute_tradable_zones

zones = compute_tradable_zones(
    prediction=prediction,
    current_price=21350.0,
    kelly_fraction=0.25,      # Conservative Kelly sizing
    min_probability=0.60      # Only trade if confidence >= 60%
)

print(f"Entry zone: {zones.entry_price}")
print(f"Target: {zones.target}")
print(f"Stop: {zones.stop}")
print(f"Position size: {zones.position_size}")
```

### Walk-Forward Backtesting

Horizon system includes built-in backtesting with rolling train/test windows:

```bash
python -m ta_foundation.prediction.backtest_horizon_predictions \
  --minute-bars-file "C:/NinjaTrader/exports/NQ.06-26.Last.txt" \
  --store-dir .ta_artifacts/horizon \
  --timeframes 5m,15m,1h \
  --horizons 3,5 \
  --train-size 252 \
  --test-size 20 \
  --step 5
```

**Output:**

```
.ta_artifacts/horizon/
  results/
    backtest_results.csv      # All test bar predictions + outcomes
    agent_performance.csv     # Per-agent accuracy by horizon
    calibration_plots/        # ECE + reliability diagrams
    manifesto.json
  stores/
    predictions.parquet       # Predictions used
    outcomes.parquet          # Actual market moves
```

### Inspecting Results

```python
import pandas as pd

results = pd.read_csv(".ta_artifacts/horizon/results/backtest_results.csv")

# Accuracy by horizon
print(results.groupby('horizon_n')['accuracy'].mean())

# Accuracy by timeframe
print(results.groupby('timeframe')['accuracy'].mean())

# Accuracy by agent
print(results.groupby('agent_id')['accuracy'].mean())

# ECE by probability bucket
print(results.groupby('calibration_bucket')['ece'].mean())
```

### Combining Daily + Horizon

Use them together for multi-level decision making:

```python
from ta_foundation.prediction.store import PredictionStore
from ta_foundation.prediction.horizon import HorizonPredictor

# Get daily context
daily_store = PredictionStore(...)
daily = daily_store.get_latest()

# Get horizon signal
horizon = predictor.predict(timeframe="5m", horizon_n=3)

# Trade only if both agree
if daily.trend_direction == "bullish" and horizon.prob_bullish > 0.65:
    enter_long(
        entry=horizon.tradable_zones.entry_price,
        target=horizon.tradable_zones.target,
        stop=horizon.tradable_zones.stop
    )
```

---

## Troubleshooting

### "API call failed: AuthenticationError"

**Cause:** API key not set or invalid.

**Fix:**
1. Check key is set: `echo $ANTHROPIC_API_KEY`
2. Verify it starts with `sk-ant-`
3. Generate a new one at https://console.anthropic.com/
4. Verify account has quota remaining

---

### "No market data found for NQ 06-26"

**Cause:** Minute bar file not found.

**Fix:**
1. Check path in config: `C:/NinjaTrader/exports/market_data/`
2. Verify file exists: `ls "C:/NinjaTrader/exports/market_data/*NQ*"`
3. Check file format (first line should be timestamp + OHLCV)

---

### "Prediction store corrupted / can't read JSON"

**Cause:** Partial write or file truncation.

**Fix:**
```bash
# Backup original
cp .ta_artifacts/predictions/predictions.jsonl predictions_backup.jsonl

# Remove corrupted lines
grep -v '{"incomplete_json' predictions_backup.jsonl > predictions.jsonl
```

---

### "Horizon backtest runs forever"

**Cause:** Too much data or too many parameter combinations.

**Fix:**
1. Reduce backtest size: `--test-size 10`
2. Reduce step size: `--step 10` (instead of 5)
3. Disable some agents in config: `enabled: false`
4. Reduce timeframes: `timeframes: ["5m"]` (instead of 3)

---

### "LLM confidence scores seem calibrated"

**Cause:** Claude's estimates are consistently off.

**Fix:**
1. Run `measure_and_learn()` to populate historical analogues
2. Add economic calendar (helps Claude understand event risk)
3. Increase `n_similar` (show Claude more historical examples)
4. Try `claude-sonnet-4-6` (faster, sometimes better calibrated)

---

## Performance Tips

### Speed up daily predictions

```bash
# Use Sonnet instead of Opus (faster, ~80% accuracy of Opus)
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --model claude-sonnet-4-6

# Reduce historical analogues (fewer comparisons)
python -m ta_foundation.prediction.run_prediction \
  --config prediction.yaml \
  --n-similar 3
```

### Reduce horizon backtest time

```bash
# Run on a subset of data
python -m ta_foundation.prediction.backtest_horizon_predictions \
  --minute-bars-file "C:/data/NQ_last_500_bars.txt" \
  --timeframes "5m" \
  --horizons 3 \
  --train-size 100 \
  --test-size 10 \
  --step 10
```

### Parallelize

Horizon backtest can be parallelized:

```python
from ta_foundation.prediction.horizon import backtest_horizon_parallel

results = backtest_horizon_parallel(
    config_path=Path("horizon_prediction.yaml"),
    n_workers=4
)
```

---

## Integration Examples

### Example 1: Daily Signal + Paper Trading

```python
import time
from ta_foundation.prediction.store import PredictionStore

store = PredictionStore(Path(".ta_artifacts/predictions"), "NQ", "06-26")

while True:
    # Check for new prediction every minute
    latest = store.get_latest()
    if latest and latest.asof > time.time() - 60:
        # Fresh prediction
        if latest.trend_direction == "bullish":
            print(f"BUY signal: confidence={latest.trend_confidence:.2f}")
            paper_trade_long(stop=latest.predicted_low - 50)
        elif latest.trend_direction == "bearish":
            print(f"SELL signal: confidence={latest.trend_confidence:.2f}")
            paper_trade_short(stop=latest.predicted_high + 50)
    
    time.sleep(60)
```

### Example 2: Scheduled Daily + Outcome Measurement

```bash
#!/bin/bash

# 4:15 PM ET: Run prediction
python -m ta_foundation.prediction.run_prediction --config prediction.yaml

# 4:30 PM ET: Check previous day's outcome
python -c "
from ta_foundation.prediction.orchestrator import measure_and_learn
from pathlib import Path
yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
measure_and_learn(..., target_date=yesterday)
"
```

### Example 3: Horizon Ensemble Signal

```python
from ta_foundation.prediction.horizon import HorizonPredictor

pred = HorizonPredictor()

# Get 5-minute / 3-candle prediction
sig_5m_3c = pred.predict("5m", 3)
# Get 15-minute / 5-candle prediction
sig_15m_5c = pred.predict("15m", 5)

# Trade only if both agree on direction
if sig_5m_3c.direction == sig_15m_5c.direction == "bullish":
    entry_price = (sig_5m_3c.expected_return + sig_15m_5c.expected_return) / 2
    target = current_price + entry_price
    enter_long(stop=current_price - 20, target=target)
```

---

## See Also

- **src/ta_foundation/prediction/README.md** — Detailed module documentation
- **src/ta_foundation/prediction/horizon_design.md** — Horizon system design
- **COMPLETE_CAPABILITIES_MATRIX.md** — Capability 3 & 4
- **AGENTIC_WORKFLOW_GUIDE.md** — Using predictions with research ledger

---

## Glossary

| Term | Meaning |
|---|---|
| **Daily Prediction** | End-of-day forecast of next session (direction, levels, regime) |
| **Horizon Prediction** | Intraday forecast of next N candles with probabilities |
| **Agent** | Statistical/ML model that makes a prediction; several run in ensemble |
| **ECE** | Expected Calibration Error (0=perfect, 1=terrible); measure of confidence calibration |
| **Walk-forward** | Backtest with rolling train/test windows; prevents overfitting |
| **Proper scoring** | Scoring rule that rewards calibration, not just accuracy |
| **Historical Analogue** | Past session similar to current; used to inform Claude |
| **Tradable Zone** | Price range + position size computed from probability prediction |
| **Drift Detection** | Alert when recent performance drops below historical mean |

