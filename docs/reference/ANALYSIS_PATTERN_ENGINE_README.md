# Pattern Engine Analysis Subsystem

**Location:** `src/ta_foundation/analysis/pattern_engine/`  
**Purpose:** Sweep parameterized price action templates against market data, discover patterns, validate robustness.  
**Triggers:** Full discovery pipeline (Phase 1-2).

---

## Overview

The pattern engine is the core discovery engine. It sweeps 20+ registered pattern templates (ORB patterns, breakout/retest, level bounces, etc.) across historical market bars/ticks with parameter variations, identifies clusters of similar successful patterns, runs Monte Carlo robustness tests, and ranks patterns by profitability and stability.

**Key outputs:**
- Pattern discovery report (JSON + parquet)
- Robustness scores (Monte Carlo, cross-validation)
- Trade-by-trade pattern audit
- Recommended pattern parameters for strategy codegen

---

## Core Concepts

### Pattern Template
A reusable definition of a price action sequence. Example: "Opening Range Breakout"
```
1. Identify opening range (first N bars)
2. Calculate high/low
3. Wait for breakout (close beyond range)
4. Optional: wait for retest to entry zone
5. Trade signal on next bar after breakout/retest
```

Each template is **parameterized**:
```
ORB::orb_break_retest
  └─ range_period: [30, 60, 120]      # First N bars define range
  └─ break_threshold: [0, 5, 10] pips # Required break distance
  └─ retest_bars: [1, 3, 5]           # Wait for retest (0 = no retest)
  └─ entry_offset: [0, 1, 5] bars     # Bars after break/retest to enter
```

### Pattern Sweep
Exhaustive run of a template across all historical bars with all parameter combinations.

```
For each parameter combo:
  For each bar in history:
    Check if pattern criteria match
    If match:
      Record pattern occurrence (bar, entry_price, params)
      Simulate trade (entry → exit)
      Record P&L, MAE, MFE
      
Results: N pattern occurrences, ranked by profitability
```

### Pattern Cluster
Group of similar patterns (same template, same parameters, same outcome).

Example: 15 ORB breakouts with range_period=60, break_threshold=5, all profitable.
- **Cluster center:** range_period=60, break_threshold=5
- **Cluster size:** 15 patterns
- **Win rate:** 14/15 = 93%
- **Avg profit:** $250

### Robustness Testing

**Monte Carlo:** Shuffle trade outcomes, recompute Sharpe/win rate. If metrics remain stable, pattern is robust.

**Cross-validation:** Split data into folds, train pattern on fold 1, test on fold 2, etc. If metrics degrade < 20%, pattern is robust.

---

## Modules

### `engine.py`
Core sweep orchestrator.

**Entry point:**
```python
from ta_foundation.analysis.pattern_engine.engine import PatternEngine

engine = PatternEngine()
results = engine.sweep(
    bars,  # pd.DataFrame with OHLCV
    template_families=["ORB", "breakout"],
    parameters={
        "ORB": {
            "range_period": [30, 60, 120],
            "break_threshold": [0, 5, 10],
        }
    }
)
# Returns: SweepResult with patterns, clusters, diagnostics
```

**Sweep workflow:**
```
1. Load template registry
2. For each family/template:
     3. For each parameter combination:
          4. For each bar:
               5. Check template criteria
               6. If match: simulate trade, record outcome
     7. Cluster patterns (same bar, similar params)
     8. Rank clusters by profitability
9. Return aggregate results
```

### `templates/`
Registered pattern templates.

**Builtin templates:**

| Family | Template | Pattern |
|--------|---|---|
| **ORB** | `orb_break_retest` | Open range breakout with optional retest |
| | `orb_breakfail` | ORB breakout fails, reverses to range |
| **Breakout** | `support_resistance_break` | Break of daily support/resistance |
| | `swing_high_low_break` | Break of recent swing high/low |
| **Candle** | `pin_bar_pattern` | Pin bar reversal from support/resistance |
| | `hammer_pattern` | Hammer candle at support |
| | `engulfing_pattern` | Engulfing reversal candle |
| **Level** | `gap_fill` | Gap gets filled (returns to open) |
| | `level_bounce` | Price bounces off key level |
| **Volatility** | `expansion_breakout` | Volatility expands, price breaks range |
| | `contraction_squeeze` | Volatility contracts, triggers breakout |
| **Session** | `high_of_day_retest` | Retest of high of day |
| | `low_of_day_retest` | Retest of low of day |

**Register custom templates:**
```python
from ta_foundation.analysis.pattern_engine.templates.builtins import default_template_registry

registry = default_template_registry()
registry.register(
    name="my_custom_pattern",
    family="custom",
    template=MyCustomTemplate(),
)
```

### `cluster.py`
Pattern clustering and aggregation.

**Entry point:**
```python
from ta_foundation.analysis.pattern_engine.cluster import cluster_patterns

clusters = cluster_patterns(
    patterns=sweep_results.patterns,
    similarity_metric="euclidean",
    n_clusters=10,  # Target cluster count
)

for cluster in clusters:
    print(f"Cluster {cluster.id}: {cluster.size} patterns, {cluster.win_rate:.1%} WR")
```

**Clustering logic:**
- Features: template type, parameters, outcome (win/loss), trade duration
- Algorithm: KMeans or DBSCAN
- Output: Clusters sorted by profitability

### `monte_carlo.py`
Robustness testing via Monte Carlo.

**Entry point:**
```python
from ta_foundation.analysis.pattern_engine.monte_carlo import monte_carlo_test

mc_result = monte_carlo_test(
    trades=cluster.trades,  # List of (entry_price, exit_price, duration)
    n_simulations=1000,
    permutation="outcome_shuffle",  # Shuffle win/loss order
)

print(f"Original Sharpe: {cluster.sharpe:.2f}")
print(f"MC avg Sharpe: {mc_result.mean_sharpe:.2f}")
print(f"MC std Sharpe: {mc_result.std_sharpe:.2f}")
print(f"Robustness: {mc_result.robustness_score:.1%}")
```

**Test variants:**
- **Outcome shuffle:** Randomly reorder wins/losses. If profitability remains, pattern is not order-dependent.
- **Trade window shuffle:** Randomly resample trades (with replacement). If metrics remain stable, pattern is not time-dependent.

### `robustness_cv.py`
Cross-validation robustness testing.

**Entry point:**
```python
from ta_foundation.analysis.pattern_engine.robustness_cv import cross_validate

cv_result = cross_validate(
    bars,
    pattern_cluster,
    folds=5,
    fold_method="time_series",  # No future leakage
)

print(f"IS Sharpe: {cv_result.is_sharpe:.2f}")
print(f"OOS Sharpe: {cv_result.oos_sharpe:.2f}")
print(f"Degradation: {cv_result.degradation_pct:.1%}")
print(f"Is robust: {cv_result.is_robust}")  # True if degradation < 20%
```

**Fold methods:**
- `time_series`: Sequential folds (train on [0:256], test on [256:512])
- `anchored`: Expanding window (train on [0:256], test on [0:512], then [0:768], etc.)
- `rolling`: Fixed window (like time_series but with overlap)

### `trade_pattern_audit.py`
Trade-by-trade pattern matching and audit.

**Entry point:**
```python
from ta_foundation.analysis.pattern_engine.trade_pattern_audit import audit_trades

audit = audit_trades(
    backtest_trades=pkg.trades,  # From AnalysisPackage
    discovered_patterns=sweep_results.clusters,
)

for trade in audit.matched_trades:
    print(f"Trade {trade.id}: Matched pattern '{trade.pattern_match.name}'")
    print(f"  Entry: {trade.pattern_match.entry_quality:.1%}")
    print(f"  Pattern frequency: {trade.pattern_match.cluster_size} occurrences")
```

**Audit output:**
- Which trades matched discovered patterns?
- Which trades had no pattern match? (Analyze for undiscovered patterns)
- Pattern quality vs actual trade outcome (did profitable pattern + bad execution = loss?)

---

## Data Flow

```
1. Market bars (OHLCV)
        ↓
2. Load template registry (engine.py)
        ↓
3. Sweep each template family (engine.py)
        ├─ For each parameter combination
        ├─ For each bar: check template criteria
        └─ Record pattern + simulated trade P&L
        ↓
4. Cluster patterns (cluster.py)
        ↓
5. Rank clusters by profitability
        ↓
6. Robustness testing (monte_carlo.py + robustness_cv.py)
        ├─ Monte Carlo shuffle
        └─ Cross-validation
        ↓
7. Filter for robustness (Sharpe stable, degradation < 20%)
        ↓
8. Trade-by-trade audit (trade_pattern_audit.py)
        ├─ Match backtest trades to discovered patterns
        └─ Identify pattern misses
        ↓
9. Store artifacts (parquet + JSON)
        ↓
10. Attach metadata references under metadata["derived"]["pattern_engine"]
```

---

## Configuration

Pattern engine is configured in `report.yaml`:

```yaml
pattern_engine:
  enabled: true
  template_families:          # Which templates to sweep
    - ORB
    - breakout
    - candle
  monte_carlo:
    enabled: true
    n_simulations: 1000
    min_trades_per_cluster: 5  # Ignore tiny clusters
  cross_validation:
    enabled: true
    folds: 5
    min_degradation_pct: 20     # Robust if degradation < 20%
  clustering:
    algorithm: "kmeans"         # or "dbscan"
    n_clusters: 15
  output:
    artifacts_dir: ".ta_artifacts/pattern_engine"
    export_parquet: true        # Save cluster details as parquet
    export_json: true           # Save summary JSON
```

---

## Usage Examples

### Example 1: Full Pattern Sweep
```bash
python -m ta_foundation.cli.main \
  --input ./backtest_exports \
  --output ./reports \
  --report-config report.yaml
```

With `report.yaml`:
```yaml
pattern_engine:
  enabled: true
  template_families: [ORB, breakout, candle]
```

Output: HTML report with pattern discovery results, robustness scores, trade audit.

### Example 2: Sweep Specific Family
```python
from ta_foundation.analysis.pattern_engine.engine import PatternEngine

engine = PatternEngine()
orb_results = engine.sweep(
    bars,
    template_families=["ORB"],
    parameters={
        "ORB": {
            "range_period": [30, 60, 120],
            "break_threshold": [0, 5, 10],
            "retest_bars": [0, 1, 3, 5],
        }
    }
)

print(f"Found {len(orb_results.patterns)} ORB patterns")
print(f"Clustered into {len(orb_results.clusters)} groups")

for cluster in orb_results.clusters[:10]:
    print(f"  {cluster.params}: {cluster.size} trades, {cluster.win_rate:.1%} WR, Sharpe {cluster.sharpe:.2f}")
```

### Example 3: Robustness Analysis
```python
from ta_foundation.analysis.pattern_engine.robustness_cv import cross_validate
from ta_foundation.analysis.pattern_engine.monte_carlo import monte_carlo_test

# Cross-validation
cv = cross_validate(bars, cluster, folds=5)
print(f"CV Degradation: {cv.degradation_pct:.1%}")

# Monte Carlo
mc = monte_carlo_test(cluster.trades, n_simulations=1000)
print(f"MC Robustness: {mc.robustness_score:.1%}")

if cv.is_robust and mc.robustness_score > 0.70:
    print("Pattern is ROBUST ✓")
else:
    print("Pattern is fragile ✗")
```

### Example 4: Trade Audit
```python
from ta_foundation.analysis.pattern_engine.trade_pattern_audit import audit_trades

audit = audit_trades(
    backtest_trades=pkg.trades,
    discovered_patterns=sweep_results.clusters,
)

matched = len([t for t in audit.matched_trades if t.pattern_match])
unmatched = len([t for t in audit.matched_trades if not t.pattern_match])

print(f"Matched: {matched}/{len(pkg.trades)} ({matched/len(pkg.trades):.1%})")
print(f"Unmatched: {unmatched} (potential new patterns)")

# Analyze unmatched trades
for trade in audit.unmatched_trades[:5]:
    print(f"  Trade {trade.id} ({trade.p_and_l:+.0f}): No pattern match")
```

---

## Output Structure

Pattern engine results are stored under:
```python
pkg.metadata["derived"]["pattern_engine"] = {
    "artifacts": [
        ".ta_artifacts/pattern_engine/<run_id>/clusters.parquet",
        ".ta_artifacts/pattern_engine/<run_id>/audit.parquet",
    ],
    "diagnostics": {
        "sweep_complete": True,
        "total_patterns": 1250,
        "total_clusters": 18,
        "robust_clusters": 12,  # Passed MC + CV tests
        "families_swept": ["ORB", "breakout", "candle"],
        "monte_carlo_enabled": True,
        "cross_validation_enabled": True,
    },
    "top_clusters": [
        {
            "cluster_id": "ORB_0",
            "family": "ORB",
            "parameters": {"range_period": 60, "break_threshold": 5, "retest_bars": 1},
            "size": 15,
            "win_rate": 0.93,
            "sharpe": 1.45,
            "avg_profit": 250.00,
            "mc_sharpe_mean": 1.42,
            "mc_sharpe_std": 0.08,
            "cv_degradation": 0.08,
            "is_robust": True,
        },
        # ... more clusters
    ],
}
```

Files in `.ta_artifacts/pattern_engine/<run_id>/`:
- `clusters.parquet` — Detailed cluster stats (parquet for efficiency)
- `audit.parquet` — Trade-by-trade pattern matching results
- `metadata.json` — Sweep metadata (families, parameters, timings)

---

## Integration with Strategy Discovery

After pattern discovery, **Phase 2** (strategy_discovery) uses patterns as:
- **Entry basis** — All generated strategies use one of the discovered patterns
- **Parameter seed** — Optimal pattern parameters become strategy parameters
- **Validation check** — If trade doesn't match any discovered pattern, flag as potential overfitting

---

## Common Issues & Fixes

### Issue: "Pattern sweep takes > 30 minutes for 5 years of 1m bars"
**Cause:** Parameter sweep is exponential; too many combinations or template families.
**Fix:** Reduce parameter ranges (fewer values per parameter). Disable slow template families. Use `--no-tick-data` to work with daily/5m only.

### Issue: "All discovered patterns are fragile (CV degradation > 50%)"
**Cause:** Patterns overfit to in-sample period. Parameters too specific.
**Fix:** Reduce parameter resolution (coarser grids). Increase fold count (more robust CV). Consider simpler templates.

### Issue: "Pattern audit shows most trades don't match discovered patterns"
**Cause:** Patterns are too specific (tight parameter ranges). Real trades deviate slightly from template criteria.
**Fix:** Loosen pattern matching criteria. Add fuzzy matching (e.g., "close to range_period, not exactly").

### Issue: "Pattern engine not appearing in report"
**Cause:** `pattern_engine.enabled: false` in YAML.
**Fix:** Set `enabled: true`.

---

## Performance Tuning

**Large parameter spaces:**
- Use **parameter importance analysis** (scikit-learn permutation feature importance) to identify which parameters matter most
- Drop low-importance parameters → dramatically reduce search space

**Time-consuming templates:**
- Parallelize family sweeps using `multiprocessing.Pool`
- Cache intermediate results (features like ADX, Bollinger Bands) across templates

**Memory usage:**
- Don't store all pattern occurrences in memory; stream to disk (parquet)
- Use sparse representation for trade data

---

## Extending Pattern Engine

To add a custom template:

1. **Create template file** `src/ta_foundation/analysis/pattern_engine/templates/my_template.py`
   ```python
   class MyPattern:
       def __init__(self, param1, param2):
           self.param1 = param1
           self.param2 = param2
       
       def match(self, bars, idx):
           # Return True if pattern criteria match at bar idx
           return (bars.iloc[idx]["close"] > bars.iloc[idx-1]["close"] and
                   bars.iloc[idx]["volume"] > bars.iloc[idx-1]["volume"])
       
       def entry_bar(self):
           # Bar at which to enter
           return idx + 1
   ```

2. **Register in `builtins.py`**
   ```python
   def default_template_registry():
       registry = TemplateRegistry()
       registry.register("my_pattern::bullish", MyPattern(param1=20, param2=50))
       return registry
   ```

3. **Add to `report.yaml`**
   ```yaml
   pattern_engine:
     template_families: [ORB, breakout, my_pattern]
   ```

---

## Sign-Off

**Subsystem:** Pattern Engine  
**Phase:** Phase 1-2 (core discovery + validation)  
**Status:** Fully implemented, partially documented  
**Dependencies:** Market bars, pandas, matplotlib (for diagnostics)  
**Next:** Phase 3 includes pattern-to-strategy bridging (entry_pattern_bridge.py) for automatic strategy template generation.

---

Last updated: May 24, 2026
