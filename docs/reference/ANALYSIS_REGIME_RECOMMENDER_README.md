# Regime Recommender Analysis Subsystem

**Location:** `src/ta_foundation/analysis/regime_recommender/`  
**Purpose:** Classify market regimes and generate actionable trading recommendations per regime.  
**Triggers:** Daily + backtest report analysis.

---

## Overview

The regime recommender subsystem classifies market conditions (trending, choppy, volatile, calm) using technical indicators and generates regime-specific trading recommendations. It bridges gap between market analysis and strategy selection — answering "What should I trade in THIS market?"

**Key outputs:**
- Current market regime classification
- Regime confidence score
- Regime-specific recommendations (entry bias, position sizing, strategy selection)
- Historical regime timeline (backtest overlay)

---

## Core Concepts

### Market Regimes

Four primary regimes:

| Regime | Characteristics | Signal | Recommendation |
|--------|---|---|---|
| **Trending** | Strong directional bias, rising/falling, ADX > 25 | Trending candles, MA alignment | Trend-following strategies, larger position size |
| **Choppy** | Sideways, mean-reverting, ADX < 20 | Range-bound, bouncing off levels | Mean-reversion strategies, smaller position size, tighter stops |
| **Volatile** | High ATR, large candles, tight ranges break | Bollinger Band width expansion | Breakout strategies, wider stops, edge per regime |
| **Calm** | Low ATR, tight candles, low volume | Bollinger Band contraction | Grid trading, scalping, avoid large positions |

### Regime Transitions

Regimes transition gradually. The recommender tracks:
- **Regime strength** — How clearly does the current regime apply? (0-100%)
- **Transition probability** — Likelihood of regime change in next N bars
- **Holding period** — Average duration of current regime

---

## Core Modules

### `classifier.py`
Classifies current bar/day into a regime.

**Entry point:**
```python
from ta_foundation.analysis.regime_recommender.classifier import RegimeClassifier

classifier = RegimeClassifier()
regime = classifier.classify(
    bar,  # Current OHLCV bar
    lookback=20,  # Bars to look back
)
# Returns: RegimeClassification(regime="trending", strength=0.82, adx=28.5, atr=12.3)
```

**Features used:**
- **ADX (Average Directional Index)** — Trend strength (0-100, > 25 = trending)
- **ATR (Average True Range)** — Volatility magnitude
- **Bollinger Band width** — Volatility (band expansion = volatile)
- **MA alignment** — Are fast/medium/slow MAs aligned? (trending indicator)
- **Price vs levels** — Is price at support/resistance or mid-range? (choppy indicator)
- **Volume profile** — Is volume high or low?

**Classification logic:**
```python
if adx > 25 and ma_aligned:
    regime = "trending"
elif atr_percentile > 75:
    regime = "volatile"
elif bb_width_percentile < 25:
    regime = "calm"
else:
    regime = "choppy"
```

### `recommender.py`
Generates trading recommendations based on regime.

**Entry point:**
```python
from ta_foundation.analysis.regime_recommender.recommender import Recommender

recommender = Recommender()
rec = recommender.recommend(
    regime="trending",
    regime_strength=0.82,
    historical_trades_in_regime=[...],  # Optional: backtest trades in this regime
)
# Returns: Recommendation(entry_bias="long", position_size_pct=100, strategies=["momentum", "trend_follow"])
```

**Recommendation dimensions:**
- **Entry bias** — Long, short, neutral (based on regime direction)
- **Position size** — 50%, 75%, 100%, 125% of baseline (sized per regime confidence)
- **Recommended strategies** — Which entry families perform best in this regime?
- **Stop placement** — Tight (choppy), wide (trending), per-regime heuristics
- **Time-of-day bias** — E.g., "avoid entries after 2pm in volatile regime"

**Regime-specific heuristics:**
```
Trending:
  - Entry bias: direction of trend (long if price > avg, short if price < avg)
  - Position size: 100-125% (take advantage of strong signal)
  - Strategies: breakout, MA crossover, momentum
  - Stops: wide (let winners run)

Choppy:
  - Entry bias: mean-reversion (buy bounces off support, sell bounces off resistance)
  - Position size: 50-75% (reversal risk)
  - Strategies: mean-reversion, level-based, pullback
  - Stops: tight (reversals can be sharp)

Volatile:
  - Entry bias: breakout (requires confirmation)
  - Position size: 75-100% (larger moves, but also larger losses)
  - Strategies: breakout, volatility expansion
  - Stops: wide (accommodate volatility)

Calm:
  - Entry bias: sideways (scalp the range)
  - Position size: 50% (low reward environment)
  - Strategies: grid trading, level-based, micro-patterns
  - Stops: very tight (low absolute moves)
```

### `storage.py`
Persistence layer for regime analysis (SQLite storage, query interface).

**Entry point:**
```python
from ta_foundation.analysis.regime_recommender.storage import RegimeStore

store = RegimeStore(".ta_artifacts/regime_store.db")

# Record a regime observation
store.record_observation(
    timestamp="2024-01-15 10:30:00",
    regime="trending",
    adx=28.5,
    atr=12.3,
)

# Query historical regimes
history = store.query_regimes(start_date="2024-01-01", end_date="2024-01-31")
# Returns: list of (timestamp, regime, strength) tuples

# Analyze regime dwell time
dwell_stats = store.dwell_time_stats()
# Returns: {"trending": {"avg_bars": 45, "min_bars": 3, "max_bars": 120}}
```

### `context_builder.py`
Builds regime context (features) for higher-level analysis.

Used by prediction system and agentic discovery to understand current market regime and adjust strategies accordingly.

---

## Data Flow

```
1. Market bar (OHLCV)
        ↓
2. Compute features (ADX, ATR, BB width, MA alignment)
        ↓
3. Classify regime (classifier.py)
        ↓
4. Score confidence (strength %)
        ↓
5. Generate recommendations (recommender.py)
        ↓
6. Store observation (storage.py)
        ↓
7. Attach to pkg.metadata["derived"]["regime_recommender"]
```

---

## Configuration

Regime recommender is configured in `report.yaml`:

```yaml
regime_recommender:
  enabled: true
  lookback: 20                    # bars for feature computation
  adx_threshold: 25               # ADX cutoff for trending vs choppy
  atr_volatility_pct: 75          # ATR percentile for volatile classification
  bb_calm_pct: 25                 # BB width percentile for calm classification
  position_size_baseline: 1.0     # Base position size (1 contract / 1% account equity)
```

---

## Usage Examples

### Example 1: Backtest Regime Analysis
```bash
python -m ta_foundation.cli.main \
  --input ./backtest_exports \
  --output ./reports \
  --report-config report.yaml
```

With `report.yaml`:
```yaml
regime_recommender:
  enabled: true
```

Output: Backtest is overlaid with regime timeline + per-regime statistics (win rate, avg profit, Sharpe).

### Example 2: Current Regime Classification
```python
from ta_foundation.analysis.regime_recommender.classifier import RegimeClassifier
import pandas as pd

# Load current market data
bars = pd.read_csv("market_data.csv")
latest_bar = bars.iloc[-1]

classifier = RegimeClassifier()
regime = classifier.classify(latest_bar, lookback=20)

print(f"Current regime: {regime.regime}")
print(f"Strength: {regime.strength:.1%}")
print(f"ADX: {regime.adx:.1f}")
print(f"ATR: {regime.atr:.2f}")
```

### Example 3: Generate Recommendations
```python
from ta_foundation.analysis.regime_recommender.recommender import Recommender

recommender = Recommender()
rec = recommender.recommend(
    regime=regime.regime,
    regime_strength=regime.strength,
    backtest_trades_in_regime=[...],  # Optional
)

print(f"Entry bias: {rec.entry_bias}")
print(f"Position size: {rec.position_size_pct * 100:.0f}%")
print(f"Recommended strategies: {rec.strategies}")
```

### Example 4: Regime Timeline Query
```python
from ta_foundation.analysis.regime_recommender.storage import RegimeStore

store = RegimeStore(".ta_artifacts/regime_store.db")

# Get regime transitions in January 2024
transitions = store.regime_transitions(
    start_date="2024-01-01",
    end_date="2024-01-31",
)

for trans in transitions:
    print(f"{trans.timestamp}: {trans.from_regime} → {trans.to_regime}")

# Regime statistics
stats = store.dwell_time_stats()
for regime, dwell in stats.items():
    print(f"{regime}: avg {dwell['avg_bars']} bars, {dwell['avg_pct']:.1%} win rate")
```

---

## Output Structure

Regime results are stored under:
```python
pkg.metadata["derived"]["regime_recommender"] = {
    "current_regime": "trending",
    "current_strength": 0.82,
    "current_features": {
        "adx": 28.5,
        "atr": 12.3,
        "atr_percentile": 0.78,
        "bb_width": 45.2,
        "bb_width_percentile": 0.42,
        "ma_alignment": True,
    },
    "recommendation": {
        "entry_bias": "long",
        "position_size_pct": 1.0,
        "strategies": ["momentum", "trend_follow", "breakout"],
        "stops": "wide",
        "time_filter": None,
    },
    "regime_timeline": [
        {"start_bar": 0, "end_bar": 45, "regime": "choppy", "avg_strength": 0.65},
        {"start_bar": 46, "end_bar": 120, "regime": "trending", "avg_strength": 0.80},
        {"start_bar": 121, "end_bar": 200, "regime": "volatile", "avg_strength": 0.72},
    ],
    "regime_statistics": {
        "choppy": {
            "occurrences": 3,
            "avg_bars": 35,
            "avg_strength": 0.68,
            "avg_win_rate": 0.52,
            "best_strategy": "mean_reversion",
        },
        "trending": {
            "occurrences": 5,
            "avg_bars": 62,
            "avg_strength": 0.79,
            "avg_win_rate": 0.61,
            "best_strategy": "momentum",
        },
    },
}
```

---

## Integration with Other Subsystems

### Entry Strategies
Entry discovery uses regime context to understand signal quality:
- Candle patterns in trending → higher confidence
- Pullbacks in choppy → higher confidence
- ORB in calm → lower confidence (less follow-through)

### Prediction System
Daily/horizon prediction system queries current regime to adjust confidence:
- Trending regime → trend direction prediction weighted higher
- Choppy regime → level-bounce prediction weighted higher

### Strategy Discovery
Strategy validation accounts for regime:
- Trend-following strategy overfit to choppy periods? → Degradation flagged
- Mean-reversion strategy overfit to trending periods? → Degradation flagged

---

## Common Issues & Fixes

### Issue: "Regime keeps switching every bar"
**Cause:** Lookback period too short, or ADX threshold miscalibrated.
**Fix:** Increase lookback (default 20 is good). Smooth regime transitions using a 3-bar moving median.

### Issue: "Recommender suggests small position sizes in strong trending market"
**Cause:** Regime strength confidence is low (0.4-0.5 range).
**Fix:** Check ADX calculation; ensure MAs are properly aligned. Increase position size baseline.

### Issue: "Regime timeline not appearing in report"
**Cause:** `regime_recommender.enabled: false` in YAML.
**Fix:** Set `enabled: true`.

---

## Extending Regime Classification

To add custom regimes (e.g., "expansion" or "compression"):

1. **Extend `RegimeType` enum** in `classifier.py`
2. **Add feature detection** in `classifier.py:classify()`
3. **Add regime-specific rules** in `recommender.py`
4. **Update storage schema** (SQLite regime table)
5. **Test on historical data** (ensure transitions make sense)

Example: Add "expansion" regime
```python
class RegimeType(Enum):
    TRENDING = "trending"
    CHOPPY = "choppy"
    VOLATILE = "volatile"
    CALM = "calm"
    EXPANSION = "expansion"  # NEW

def classify(self, bar, lookback=20):
    # ... existing logic ...
    
    # NEW: Detect expansion (ATR rising, volume rising)
    recent_atr = compute_atr(bars[-lookback:])
    volume_trend = bars[-5:]["volume"].mean() > bars[-20:-5]["volume"].mean()
    if recent_atr[-1] > recent_atr[-10] and volume_trend:
        return RegimeClassification(regime="expansion", ...)
```

---

## Performance Tuning

**Real-time classification:**
- Classifiers are O(lookback) — precompute features once per bar
- Cache ADX/ATR/BB for 1-minute intervals if processing tick data

**Backtest analysis:**
- Regime timeline can be pre-computed once during ingest
- Store in `.ta_artifacts/regime_timeline.parquet` (disk-referenced, not embedded in metadata)

---

## Sign-Off

**Subsystem:** Regime Recommender  
**Phase:** Phase 1 (core) + Phase 3 (calibration)  
**Status:** Fully implemented, partially documented  
**Dependencies:** ADX/ATR calculation, Bollinger Bands, MA alignment  
**Next:** Phase 3 includes deeper regime-specific strategy tuning + multi-regime backtesting.

---

Last updated: May 24, 2026
