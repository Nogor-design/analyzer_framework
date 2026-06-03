# Data Model Schema Reference

**Purpose:** Formal specification of all core data structures and their contracts.  
**Audience:** Developers extending the codebase, contributors adding new analysis modules.  
**Status:** Reference document (changes are documented and announced).

---

## Table of Contents

1. [Core Data Models](#core-data-models)
2. [Derived Data Conventions](#derived-data-conventions)
3. [Configuration Models](#configuration-models)
4. [API Response Models](#api-response-models)
5. [Constraints & Invariants](#constraints--invariants)

---

## Core Data Models

### AnalysisPackage

**File:** `src/ta_foundation/core/model.py`  
**Purpose:** Container for all per-run backtest data and analysis results.

```python
class AnalysisPackage:
    run_id: str                      # Unique identifier (e.g., "Strategy_2024-01-15")
    
    # Primary data (ingest)
    trades: pd.DataFrame             # Trade-level P&L
                                     # Columns: entry_time, exit_time, entry_price, exit_price, 
                                     #          pnl, pnl_pct, duration_bars, instrument, ...
    
    daily: pd.DataFrame              # Daily summary
                                     # Columns: date, trade_count, pnl, high, low, 
                                     #          winning_trades, losing_trades, ...
    
    summary: SummaryBlock            # KPI summary (see below)
    
    settings: pd.DataFrame           # Strategy parameters
                                     # Columns: parameter_name, parameter_value, parameter_type
    
    # Assets (embedded images)
    assets: dict[str, str]           # {filename: base64_png_data}
                                     # Example: {"run_card.png": "iVBORw0KGgo..."}
    
    # Derived analysis results (must be JSON-safe)
    metadata: dict[str, Any]         # {
                                     #   "derived": {
                                     #     "anchor_interaction": {...},
                                     #     "pattern_engine": {...},
                                     #     "entry_strategies": {...},
                                     #     ...
                                     #   },
                                     #   "custom_field": {...}  # allowed but not recommended
                                     # }
    
    # Warnings during ingest
    warnings: list[str]              # ["Missing tick data", "No winning trades", ...]
```

**Constraints:**
- `run_id` must be unique and deterministic (derived from backtest filename)
- All timestamps in `trades` and `daily` must be tz-aware (America/Denver)
- `metadata` must be JSON-serializable (no DataFrames, callables, registry objects)
- Never add dynamic attributes at module level (use `metadata["derived"][...]`)
- `assets` keys are filenames (no path separators); values are data URIs

**Methods:**
```python
def json_safe_metadata(self) -> dict:
    """Return metadata ready for JSON serialization (used when saving manifest)."""
    return json.loads(json.dumps(self.metadata))  # Validate JSON-safe

def to_dict(self) -> dict:
    """Convert to dict for report context."""
    return {
        "run_id": self.run_id,
        "trades": self.trades,
        "daily": self.daily,
        "summary": self.summary.to_dict() if self.summary else {},
        # ... rest
    }
```

---

### SummaryBlock

**File:** `src/ta_foundation/core/model.py`  
**Purpose:** KPI summary (total net profit, Sharpe, win rate, etc.).

```python
class SummaryBlock:
    start_dt: pd.Timestamp           # Strategy start time (tz-aware)
    end_dt: pd.Timestamp             # Strategy end time (tz-aware)
    
    # All metrics are stored in three groups (normalized keys: lowercase, no punctuation)
    kpis_all: dict[str, float]       # All KPIs {
                                     #   "total net profit": 5000.00,
                                     #   "profit factor": 1.50,
                                     #   "sharpe ratio": 1.25,
                                     #   "win rate": 0.55,
                                     #   ...
                                     # }
    
    kpis_long: dict[str, float]      # Long-only KPIs
    kpis_short: dict[str, float]     # Short-only KPIs
```

**KPI Normalization:**
All keys are normalized:
```python
def normalize_key(key: str) -> str:
    """'Total Net Profit' → 'total net profit'"""
    return key.lower().replace("_", " ").strip()

# Access: pkg.summary.kpis_all.get("total net profit")
```

**Common KPIs:**
| Key | Type | Example |
|-----|------|---------|
| `total net profit` | float | 5000.00 |
| `total trades` | int | 42 |
| `winning trades` | int | 23 |
| `losing trades` | int | 19 |
| `breakeven trades` | int | 0 |
| `win rate` | float | 0.548 |
| `avg winning trade` | float | 250.00 |
| `avg losing trade` | float | -125.00 |
| `largest winning trade` | float | 1500.00 |
| `largest losing trade` | float | -800.00 |
| `profit factor` | float | 1.50 |
| `sharpe ratio` | float | 1.25 |
| `sortino ratio` | float | 1.80 |
| `max drawdown pct` | float | 0.15 |
| `max consecutive losses` | int | 5 |
| `recovery factor` | float | 3.33 |
| `expectancy` | float | 119.05 |

---

### MarketDataStore

**File:** `src/ta_foundation/marketdata/store.py`  
**Purpose:** Shared (non-run-scoped) market data accessed by all runs.

```python
class MarketDataStore:
    data: dict[tuple[str, str], pd.DataFrame]  # {(instrument, contract): bars_df}
                                               # Example: ("NQ", "H25") → 5000 minute bars
    
    # Minute bars DataFrame
    # Columns: timestamp (tz-aware America/Denver), open, high, low, close, volume, ...
    
    tick_cache: TickCache              # Optional: intrabar tick storage
```

**Key contract:**
- `run_id` is always `None` for artifacts stored here
- Minute bars are stored once, never duplicated per run
- Accessed by `(instrument_root, contract)` tuple
- All timestamps are tz-aware (America/Denver)

**Access pattern:**
```python
from ta_foundation.marketdata.store import MarketDataStore

store = MarketDataStore()
bars = store.data[("NQ", "H25")]  # Get minute bars for NQ H25 contract
```

---

### OptimizationStore

**File:** `src/ta_foundation/optimization/model.py`  
**Purpose:** Parameter optimization results from NinjaTrader optimization exports.

```python
class OptimizationBatch:
    batch_id: str                      # Derived from filename (e.g., "PantheonMasterV01")
    source_path: Path                  # Original CSV file path
    strategy_name: str                 # Strategy being optimized
    instrument: str                    # Instrument (e.g., "NQ")
    
    results: pd.DataFrame              # Optimization parameter sweep results
                                       # Columns: param_<Name> (one per parameter),
                                       #          Total Net Profit, Sharpe Ratio, Win Rate, ...
    
    parameter_names: list[str]         # Ordered parameter names
    metric_columns: list[str]          # KPI column names
    
    row_count: int                     # Total rows parsed
    successfully_parsed_rows: int      # Rows with valid data
    warnings: list[str]                # Parse warnings
```

**Contract:**
- One batch per `*_Optimization.csv` file
- Never merged into `AnalysisPackage`
- Parameters extracted from "Parameters" column using regex parsing
- Metrics are numeric KPI columns (Profit, Sharpe, etc.)

**Access pattern:**
```python
from ta_foundation.optimization.model import OptimizationStore

opt_store = OptimizationStore()
for batch in opt_store.batches:
    print(f"{batch.batch_id}: {batch.row_count} parameter combinations")
    print(f"  Best: {batch.results.nlargest(1, 'Total Net Profit')}")
```

---

### ParsedArtifact

**File:** `src/ta_foundation/parsers/base.py`  
**Purpose:** Intermediate representation from parser before pipeline assembly.

```python
class ParsedArtifact:
    kind: str                          # Artifact type (e.g., "trades", "daily", "minute_bars")
    source_path: Path                  # Original file path
    run_id: str | None                 # Run ID if run-scoped; None if shared
    
    data: dict[str, Any]               # Raw parsed data {
                                       #   "trades": trades_df,
                                       #   "settings": settings_df,
                                       # }
    
    warnings: list[str]                # Parse warnings (e.g., "6 malformed rows skipped")
```

**Contract:**
- `run_id != None` → artifact is run-scoped (attaches to AnalysisPackage)
- `run_id = None` → artifact is shared (attaches to MarketDataStore)
- All datetimes must be tz-aware (America/Denver) before returning from parser
- Data must be JSON-safe (if stored in metadata)

---

## Derived Data Conventions

All computed metrics attach under `pkg.metadata["derived"][...]`. This section documents the key derived data structures.

### Anchor Interaction Results

```python
pkg.metadata["derived"]["anchor_interaction"] = {
    "analysis_type": "moving_average_structure",
    "anchors": [
        {
            "family": "SMA",
            "length": 20,
            "source": "close",
            "interaction_strength": 0.75,  # How often trades interact with this MA
            "tp_sl_scores": {
                "take_profit_pct": [0.01, 0.02, 0.05],  # TP distances
                "scores": [0.82, 0.79, 0.71],           # Score per distance
            },
            "recommended_tp": 0.02,
            "recommended_sl": 0.005,
        }
    ],
    "regime_context": {
        "trending_win_rate": 0.62,
        "choppy_win_rate": 0.48,
    }
}
```

### Pattern Engine Results

```python
pkg.metadata["derived"]["pattern_engine"] = {
    "artifacts": [".ta_artifacts/pattern_engine/run_001/clusters.parquet"],
    "diagnostics": {
        "sweep_complete": True,
        "total_patterns": 1250,
        "total_clusters": 18,
        "robust_clusters": 12,
    },
    "top_clusters": [
        {
            "cluster_id": "ORB_0",
            "family": "ORB",
            "parameters": {"range_period": 60, "break_threshold": 5},
            "size": 15,
            "win_rate": 0.93,
            "sharpe": 1.45,
        }
    ]
}
```

### Entry Strategies Results

```python
pkg.metadata["derived"]["entry_strategies"] = {
    "candle": [
        {
            "pattern": "hammer",
            "entry_count": 12,
            "win_rate": 0.58,
            "sharpe": 0.95,
            "is_robust": True,
        }
    ],
    "ma_crossover": [
        {
            "fast_period": 12,
            "slow_period": 40,
            "entry_count": 18,
            "win_rate": 0.61,
            "sharpe": 1.12,
            "is_robust": True,
        }
    ]
}
```

### Regime Recommender Results

```python
pkg.metadata["derived"]["regime_recommender"] = {
    "current_regime": "trending",
    "current_strength": 0.82,
    "current_features": {
        "adx": 28.5,
        "atr": 12.3,
        "atr_percentile": 0.78,
    },
    "recommendation": {
        "entry_bias": "long",
        "position_size_pct": 1.0,
        "strategies": ["momentum", "trend_follow"],
    },
    "regime_timeline": [
        {"start_bar": 0, "end_bar": 45, "regime": "choppy"},
        {"start_bar": 46, "end_bar": 120, "regime": "trending"},
    ]
}
```

### Daily Outcomes

```python
pkg.metadata["derived"]["daily_outcomes"] = {
    "win_loss_by_date": {
        "2024-01-15": {"trades": 5, "wins": 3, "losses": 2, "pnl": 750},
        "2024-01-16": {"trades": 2, "wins": 1, "losses": 1, "pnl": 100},
    },
    "daily_pnl_mean": 425.00,
    "daily_pnl_std": 325.50,
    "consecutive_loss_days": [
        {"start_date": "2024-01-20", "end_date": "2024-01-22", "days": 3}
    ]
}
```

### Trade Time Profile

```python
pkg.metadata["derived"]["trade_time_profile"] = {
    "hourly_distribution": {
        "09": {"trade_count": 12, "avg_pnl": 85.50, "win_rate": 0.58},
        "10": {"trade_count": 18, "avg_pnl": 125.00, "win_rate": 0.61},
        # ...
    },
    "best_hours": ["10", "11", "13"],
    "worst_hours": ["15", "16"],
}
```

---

## Configuration Models

### Report Configuration

**File:** `report.yaml` (top-level structure)

```yaml
# Main report definition
report:
  title: "Strategy Comparison"
  output_filename: "comparison_report.html"
  timezone: "America/Denver"

# Feature blocks (used to enable analysis)
anchor_interaction:
  enabled: true
  strategy_family: "SMA"
  anchors:
    - family: "SMA"
      length: 20

pattern_engine:
  enabled: true
  template_families: [ORB, breakout]
  monte_carlo:
    enabled: true
    n_simulations: 1000

entry_strategies:
  enabled: true
  families: [candle, ma, bb]
  validation:
    train_period: 252

strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"

regime_recommender:
  enabled: true
  lookback: 20

# Sections to render
sections:
  - id: run_kpi_cards
  - id: daily_scoreboard
    options:
      top_n: 12
```

**Type definitions:**

```python
from dataclasses import dataclass

@dataclass
class ReportConfig:
    title: str
    output_filename: str
    timezone: str
    sections: list[SectionConfig]
    anchor_interaction: Optional[AnalysisConfig]
    pattern_engine: Optional[AnalysisConfig]
    # ... other feature blocks

@dataclass
class SectionConfig:
    id: str
    options: dict[str, Any] = field(default_factory=dict)

@dataclass
class AnalysisConfig:
    enabled: bool
    # ... feature-specific fields
```

---

## API Response Models

### Discovery Session Response

```python
{
    "session_id": "sess_abc123",
    "created_at": "2024-01-15T10:30:00-07:00",
    "instrument": "NQ",
    "contract": "H25",
    "phase": "A",  # Current phase (A, B, C, D)
    "runs": [
        {
            "run_id": "run_001",
            "type": "triage_run",
            "started_at": "2024-01-15T10:31:00-07:00",
            "completed_at": "2024-01-15T10:35:00-07:00",
            "status": "completed",
            "strategies_discovered": 5,
            "avg_sharpe": 1.12,
        }
    ],
    "ledger": {
        "hypotheses_count": 12,
        "runs_count": 3,
        "candidates_count": 8,
    }
}
```

### Job Status Response

```python
{
    "job_id": "job_12345",
    "status": "running",  # or "completed", "failed"
    "progress": {
        "phase": "pattern_engine_sweep",
        "percent": 45,
        "message": "Sweeping ORB family (3/5 families complete)",
    },
    "result": None,  # Populated when status == "completed"
    "error": None,   # Populated when status == "failed"
}
```

---

## Constraints & Invariants

### Non-Negotiable Rules

1. **Timestamps**: All datetimes must be tz-aware, localized to `America/Denver`.
   ```python
   # ✅ Correct
   dt = pd.Timestamp("2024-01-15 10:30:00", tz="America/Denver")
   
   # ❌ Wrong
   dt = pd.Timestamp("2024-01-15 10:30:00")  # Naive!
   ```

2. **Metadata JSON-safety**: `pkg.metadata["derived"]` must be JSON-serializable.
   ```python
   # ✅ Good
   pkg.metadata["derived"]["results"] = {"count": 42, "names": ["a", "b"]}
   
   # ❌ Bad
   pkg.metadata["derived"]["results"] = pd.DataFrame(...)  # Not JSON-safe
   ```

3. **AnalysisPackage attributes**: Never add dynamic top-level attributes.
   ```python
   # ✅ Good
   pkg.metadata["derived"]["custom_field"] = {...}
   
   # ❌ Bad
   pkg.custom_field = {...}  # Will break serialization
   ```

4. **Run-scoped vs shared data**: Shared data must have `run_id = None` and live in `MarketDataStore`.
   ```python
   # ✅ Good (shared market data)
   market_artifact = ParsedArtifact(kind="minute_bars", run_id=None, data={...})
   
   # ❌ Bad (duplicates per run)
   pkg.minute_bars = {...}
   ```

5. **Derived metrics**: All computed metrics attach under `metadata["derived"]`, never as top-level attributes.
   ```python
   # ✅ Good
   pkg.metadata["derived"]["my_metric"] = {...}
   
   # ❌ Bad
   pkg.my_metric = {...}
   ```

### Data Type Contracts

| Field | Type | Nullable | Notes |
|-------|------|----------|-------|
| `AnalysisPackage.run_id` | `str` | ✓ No | Unique, deterministic |
| `AnalysisPackage.trades` | `pd.DataFrame` | ✓ No | Columns: entry_time, exit_time, pnl, ... |
| `AnalysisPackage.daily` | `pd.DataFrame` | ✓ No | Columns: date, trade_count, pnl, ... |
| `AnalysisPackage.summary` | `SummaryBlock` | ✓ Yes | Can be None if no trades |
| `AnalysisPackage.metadata` | `dict` | ✓ No | Must be JSON-safe |
| `Timestamp` (all) | `pd.Timestamp` | ✓ No | Must be tz-aware |
| `KPI value` (all) | `float` | ✓ Yes | Can be NaN if metric not applicable |

---

## Versioning & Stability

**Schema version:** 1.0  
**Last updated:** May 24, 2026  
**Breaking changes policy:** Breaking changes to core models (AnalysisPackage, SummaryBlock) will be announced with >= 2 weeks notice and include migration scripts.

**Stable:** ✓ AnalysisPackage, SummaryBlock, MarketDataStore  
**Stable:** ✓ `metadata["derived"]` key conventions (approved schema)  
**In development:** Report section rendering (section registry may change)  
**In development:** API response shapes (may change in 2026 Q3)

---

## Glossary

- **Run-scoped**: Artifact belongs to one specific backtest run (attaches to AnalysisPackage)
- **Shared**: Artifact is shared across all runs (attaches to MarketDataStore)
- **JSON-safe**: Data can be serialized via `json.dumps()` (primitives, dicts, lists only)
- **KPI**: Key Performance Indicator (total profit, Sharpe, win rate, etc.)
- **Derived**: Computed metric (not from original ingest file)
- **Artifact**: General term for parsed or computed data (could be DataFrame, JSON, parquet, PNG)

---

## References

- [CLAUDE.md](CLAUDE.md) — Architecture overview
- [Core Model Source](src/ta_foundation/core/model.py)
- [Test Suite](src/ta_foundation/tests/) — Examples of data construction

---

Last updated: May 24, 2026
