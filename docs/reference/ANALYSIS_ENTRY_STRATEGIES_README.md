# Entry Strategies Analysis Subsystem

**Location:** `src/ta_foundation/analysis/entry_strategies/`  
**Purpose:** Discover and validate entry signal patterns across 8 strategy families.  
**Triggers Phase 1-2** of the strategy discovery pipeline.

---

## Overview

The entry strategies subsystem sweeps parameterized entry signal templates across market data, clusters results, and validates signals via walk-forward backtesting. It produces a ranked catalog of entry patterns (entry bars, confidence scores, optimal parameters) that feed into downstream strategy discovery and NinjaTrader code generation.

**Key outputs:**
- Entry signal clusters (N clusters per family, K best per cluster)
- Validation metrics (IS/OOS Sharpe, degradation %)
- Executable strategy specs (JSON format for NinjaTrader codegen)

---

## 8 Entry Strategy Families

### 1. Candle Pattern Recognition (`candle/`)
Detects 20+ named candle patterns (hammer, engulfing, pin bar, etc.) on daily and intraday bars.

**Key modules:**
- `candle_patterns.py` — 20+ pattern definitions (shape criteria)
- `mtf.py` — Multi-timeframe aggregation (e.g., daily signal confirmed on 5m bar)
- `sweep.py` — Master candle pattern sweep orchestrator
- `confidence.py` — Signal confidence from pattern geometry

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies import sweep
result = sweep.run_candle_sweep(market_data, settings={
    "patterns": ["hammer", "engulfing"],
    "timeframes": ["1d", "5m"],
    "mtf_confirm": True,
})
```

**Output:** Clusters of (entry_bar, pattern_type, confidence) tuples.

---

### 2. Moving Average Crossovers (`ma/`)
Detects MA fast/slow crossover signals with optional trend confirmation.

**Key modules:**
- `indicators.py` — Fast/slow MA calculation (SMA, EMA)
- `signals.py` — Crossover signal generation
- `optimization.py` — Parameter sweep (fast_period, slow_period)
- `trend_context.py` — Optional ADX/trend confirmation

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.ma import sweep_ma_crossovers
result = sweep_ma_crossovers(bars, fast_range=(5, 20), slow_range=(20, 50))
```

**Output:** MA crossover signal clusters with parameter optimization.

---

### 3. Bollinger Band Touches (`bb/`)
Detects touches/breaks of Bollinger Band levels for mean-reversion and breakout entries.

**Key modules:**
- `bb_levels.py` — Band calculation (SMA, std dev)
- `touch_signals.py` — Touch detection (price touches band)
- `break_signals.py` — Break detection (price breaks band + closes beyond)
- `regime_context.py` — Volatility-adjusted confidence

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.bb import sweep_band_signals
result = sweep_band_signals(bars, band_period=20, num_std=(1.5, 2.0, 2.5))
```

**Output:** BB touch/break signals with regime context.

---

### 4. Opening Range Breakout (`orb/`)
Detects breakouts of the opening range (first N bars of session).

**Key modules:**
- `range_detection.py` — ORB high/low calculation
- `break_signals.py` — Breakout detection (price breaks ORB + volume confirmation)
- `time_filters.py` — Session time guards (e.g., no breakout after 2pm)
- `retest_logic.py` — Retest detection (pullback after break)

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.orb import sweep_orb_breakouts
result = sweep_orb_breakouts(bars, orb_period=60, min_break_distance=10)
```

**Output:** ORB breakout signals with retest pattern variants.

---

### 5. General Breakout (`breakout/`)
Detects breaks of recent highs/lows (not specific to opening range).

**Key modules:**
- `level_detection.py` — Recent support/resistance detection
- `breakout_signals.py` — Break confirmation (close beyond level)
- `volume_filters.py` — Volume normalization filters
- `sweep_base.py` — Shared sweep orchestrator

**Entry points:**
```bash
python -m ta_foundation.cli.main --strategy-discovery --entry-family breakout
```

**Output:** Support/resistance breakout signals.

---

### 6. Pullback Confirmation (`pullback/`)
Detects pullbacks toward support after rallies (or toward resistance after declines).

**Key modules:**
- `trend_detection.py` — Trend phase identification (impulse vs pullback)
- `pullback_confirmation.py` — Pullback bar detection
- `entry_zones.py` — Optimal entry zone computation
- `sweep_base.py` — Shared sweep orchestrator

**Entry points:**
```bash
python -m ta_foundation.cli.main --strategy-discovery --entry-family pullback
```

**Output:** Pullback entry signals with zone-based confidence.

---

### 7. Level-Based Entries (`level/`)
Detects entries from support/resistance levels (daily pivots, previous day high/low).

**Key modules:**
- `level_sources.py` — Level definitions (daily pivot, previous high/low, etc.)
- `level_touch.py` — Level touch detection
- `level_bounce.py` — Bounce pattern from level
- `lcr_detection.py` — Left-Center-Right level cluster detection

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.level import sweep_level_entries
result = sweep_level_entries(bars, level_sources=["daily_pivot", "prev_high_low"])
```

**Output:** Level-based entry signals.

---

### 8. Left-Center-Right (LCR) Level Discovery (`lcr/`)
Advanced: Discovers dynamic support/resistance zones via clustering.

**Key modules:**
- `clustering.py` — Price level clustering (sklearn KMeans or DBSCAN)
- `zone_geometry.py` — Zone center/width/touches computation
- `bounce_stats.py` — Bounce frequency and profitability per zone
- `ranking.py` — Zone ranking by bounce consistency

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.lcr import discover_lcr_zones
zones = discover_lcr_zones(bars, n_clusters=5, min_touches=3)
```

**Output:** Dynamic support/resistance zones.

---

## Shared Modules & Utilities

### `_sweep_base.py`
Shared sweep logic for breakout, pullback, level entries.

```python
class SweepBase:
    def run(self, bars, parameters) -> SweepResult:
        # 1. Generate signals
        # 2. Cluster signals (same bar, similar parameters)
        # 3. Rank clusters by profitability/validation
        # 4. Attach entry specs to AnalysisPackage
```

### `outcome/`
Trade outcome simulation for entry signals.

**Key modules:**
- `entry_outcome.py` — Simulate trade P&L from entry signal
  - Takes: entry_bar, entry_price, trade_parameters
  - Outputs: exit_price, P&L, max_adverse_excursion (MAE), max_favorable_excursion (MFE)
- `metrics.py` — Win rate, avg profit, Sharpe ratio per signal
- `validation.py` — Walk-forward validation logic

### `ranking.py`
Rank entry signals by:
- In-sample Sharpe ratio
- Out-of-sample degradation (IS Sharpe vs OOS Sharpe)
- Win rate
- Profit factor
- Signal frequency (too frequent = overfitting)

### `validation.py`
Walk-forward validation framework.

**Entry points:**
```python
from ta_foundation.analysis.entry_strategies.validation import validate_entry_signals
val_result = validate_entry_signals(
    bars=market_data,
    entry_signals=discovered_signals,
    train_period=252,  # 1 year
    test_period=63,    # 13 weeks
    step=21,           # 1 month roll
)
```

**Outputs:**
- `is_sharpe`: in-sample Sharpe
- `oos_sharpe`: out-of-sample Sharpe
- `degradation_pct`: (is_sharpe - oos_sharpe) / is_sharpe
- `is_overfitting`: degradation_pct > 50%

---

## Data Flow

```
1. Market bars + trade data from AnalysisPackage
        ↓
2. Entry strategy family sweep (candle, MA, BB, ORB, etc.)
   → Generate N candidate signals per family
        ↓
3. Cluster candidate signals (same bar, similar params)
        ↓
4. Rank clusters by profitability
        ↓
5. Walk-forward validation (IS vs OOS)
        ↓
6. Filter for robustness (degradation < 50%)
        ↓
7. Attach under metadata["derived"]["entry_strategies"]
        ↓
8. Feed to strategy_discovery (Phase 2) for full strategy synthesis
```

---

## Configuration

Entry strategy discovery is configured in `report.yaml` under a top-level `entry_strategies:` block:

```yaml
entry_strategies:
  enabled: true
  families:
    - candle
    - ma
    - bb
    - orb
  validation:
    train_period: 252    # days
    test_period: 63
    step: 21
    max_degradation: 0.5  # 50%
  min_trades_per_signal: 5  # require at least 5 trades per entry signal
```

---

## Usage Examples

### Example 1: Sweep All Families
```bash
python -m ta_foundation.cli.main \
  --input ./backtest_exports \
  --output ./reports \
  --report-config report.yaml
```

With `report.yaml`:
```yaml
entry_strategies:
  enabled: true
  families: [candle, ma, bb, orb, breakout, pullback, level, lcr]
```

### Example 2: Validate Specific Signals
```python
from ta_foundation.analysis.entry_strategies.validation import validate_entry_signals

val = validate_entry_signals(
    bars=market_data,
    entry_signals=[
        {"type": "hammer", "bar": 100, "confidence": 0.85},
        {"type": "ma_cross", "bar": 105, "confidence": 0.72},
    ],
    train_period=252,
    test_period=63,
)

for sig in val.robust_signals:
    print(f"Signal: {sig['type']}, OOS Sharpe: {sig['oos_sharpe']:.2f}")
```

### Example 3: Discover Candle Patterns
```python
from ta_foundation.analysis.entry_strategies.candle.sweep import run_candle_sweep

result = run_candle_sweep(
    market_data,
    settings={
        "patterns": ["hammer", "engulfing"],
        "confirm_on_mtf": "5m",
        "min_confidence": 0.70,
    }
)

print(f"Found {len(result.clusters)} candle pattern clusters")
for cluster in result.clusters[:5]:
    print(f"  - {cluster.pattern}: {cluster.entry_count} entries, {cluster.win_rate:.1%} WR")
```

---

## Output Structure

Entry strategy results are stored under:
```python
pkg.metadata["derived"]["entry_strategies"] = {
    "candle": [
        {
            "pattern": "hammer",
            "timeframe": "1d",
            "entry_count": 12,
            "win_rate": 0.58,
            "avg_profit": 125.50,
            "sharpe": 0.95,
            "is_robust": True,
            "oos_degradation": 0.15,
        },
        # ... more patterns
    ],
    "ma_crossover": [
        {
            "fast_period": 12,
            "slow_period": 40,
            "entry_count": 18,
            "win_rate": 0.61,
            "sharpe": 1.12,
            "is_robust": True,
        },
        # ... more MA params
    ],
    # ... other families
}
```

---

## Common Issues & Fixes

### Issue: "No entry signals found for [family]"
**Cause:** Market conditions don't match signal criteria, or bar count too low.
**Fix:** Lower minimum requirements (confidence threshold, min_trades_per_signal). Add more historical data.

### Issue: "All signals failed walk-forward validation"
**Cause:** Signals overfit to in-sample period; OOS performance is poor.
**Fix:** Increase train_period, reduce parameter count (simplify signals), require higher IS Sharpe before testing OOS.

### Issue: "Entry strategies not appearing in report"
**Cause:** `entry_strategies.enabled: false` in `report.yaml`.
**Fix:** Set `enabled: true` and run CLI again.

---

## Integration with Strategy Discovery

After entry strategies are discovered, **Phase 2** (strategy_discovery) uses these signals as:
- **Entry basis** — All generated strategies use one of the discovered entry signals
- **Parameter seed** — Optimal entry parameters become strategy starting points
- **Validation baseline** — IS/OOS degradation from entry discovery informs strategy validation thresholds

---

## Extending Entry Strategies

To add a new entry family:

1. **Create a new directory** `src/ta_foundation/analysis/entry_strategies/<family>/`
2. **Implement required modules:**
   - `__init__.py` — exports sweep function
   - `signals.py` — signal generation
   - `confidence.py` — confidence scoring
3. **Register in sweep orchestrator** (`sweep.py`)
4. **Add config section** to `report.yaml` schema
5. **Write tests** in `src/ta_foundation/tests/analysis/entry_strategies/<family>/`

See existing families (candle/, ma/, bb/) for implementation patterns.

---

## Performance Tuning

**Large datasets (> 5 years, 5m bars):**
- Reduce sweep parameter ranges (fewer combinations)
- Increase clustering threshold (fewer clusters to rank)
- Use `--no-tick-data` flag to skip intraday tick detail

**Many concurrent sweeps:**
- Parallelize family sweeps using `multiprocessing`
- Implement in `sweep_orchestrator.py` (currently sequential)

---

## Sign-Off

**Subsystem:** Entry Strategies Discovery  
**Phase:** Phase 1-2 (discovery + validation)  
**Status:** Fully implemented, partially documented  
**Next:** Phase 3 includes `HYPOTHESIS_AUTHORING.md` for custom entry template creation.

---

Last updated: May 24, 2026
