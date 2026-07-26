# Configuration Schema Reference

**Purpose:** Complete reference for all YAML configuration options.  
**File:** `report.yaml` (or any file passed via `--report-config`)  
**Format:** YAML 1.2  
**Audience:** Users configuring reports and analyses.

---

## Quick Start: Minimal Config

```yaml
report:
  title: "My Report"
  output_filename: "report.html"

sections:
  - id: run_kpi_cards
  - id: daily_scoreboard
```

This creates a basic report with KPI cards and daily summary.

---

## Top-Level Structure

```yaml
report:                              # Required: main report config
  title: string
  output_filename: string
  timezone: string

# Optional feature blocks (enable analyses)
anchor_interaction:
  enabled: boolean
  # ... feature-specific options

pattern_engine:
  enabled: boolean
  # ... feature-specific options

strategy_discovery:
  enabled: boolean
  # ... feature-specific options

entry_strategies:
  enabled: boolean
  # ... feature-specific options

regime_recommender:
  enabled: boolean
  # ... feature-specific options

# Required: sections to render
sections:
  - id: string
    options:
      key: value
  - id: string
```

---

## Report Configuration

### `report:`

Main report-level settings.

```yaml
report:
  title: "Strategy Analysis Report"           # Required. Displayed as report title.
  output_filename: "report.html"              # Required. Must end with .html.
  timezone: "America/Denver"                  # Optional. Default: "America/Denver".
```

**Fields:**

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `title` | string | ✓ Yes | — | Report title, displayed in HTML header |
| `output_filename` | string | ✓ Yes | — | HTML filename (e.g., "report.html", "2024_backtest.html") |
| `timezone` | string | ✗ No | "America/Denver" | IANA timezone for timestamp display |

---

## Feature Blocks

Feature blocks enable optional analyses. Each is independent; most can be left out (default disabled).

### `anchor_interaction:`

Moving average anchor analysis.

```yaml
anchor_interaction:
  enabled: true                                # Enable/disable this analysis
  strategy_family: "SMA"                       # Or "EMA", "TEMA", "VWMA"
  anchors:
    - family: "SMA"
      length: 20
      source: "close"                          # Or "high", "low", "hl2"
    - family: "EMA"
      length: 50
      source: "close"
```

**Options:**

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `enabled` | bool | false | Enable MA anchor analysis |
| `strategy_family` | string | "SMA" | Primary MA type (SMA, EMA, TEMA, VWMA) |
| `anchors` | list | — | List of anchor definitions |
| `anchors[].family` | string | — | MA type (SMA, EMA, TEMA, VWMA) |
| `anchors[].length` | int | — | MA period (e.g., 20, 50, 200) |
| `anchors[].source` | string | "close" | Price source (close, high, low, hl2) |

**Example:**
```yaml
anchor_interaction:
  enabled: true
  anchors:
    - family: "SMA"
      length: 20
      source: "close"
    - family: "SMA"
      length: 50
      source: "close"
    - family: "EMA"
      length: 13
      source: "hl2"
```

---

### `pattern_engine:`

Pattern discovery analysis.

```yaml
pattern_engine:
  enabled: true
  template_families:                           # Which templates to sweep
    - "ORB"
    - "breakout"
    - "candle"
  monte_carlo:
    enabled: true
    n_simulations: 1000                        # Robustness test iterations
    min_trades_per_cluster: 5                  # Ignore tiny clusters
  cross_validation:
    enabled: true
    folds: 5                                   # CV fold count
    min_degradation_pct: 20                    # Max acceptable IS/OOS decline
  clustering:
    algorithm: "kmeans"                        # Or "dbscan"
    n_clusters: 15                             # Target cluster count
  output:
    artifacts_dir: ".ta_artifacts/pattern_engine"
    export_parquet: true
    export_json: true
```

**Options:**

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `enabled` | bool | false | Enable pattern engine |
| `template_families` | list | ["ORB", "breakout"] | Which families to sweep |
| `monte_carlo.enabled` | bool | true | Run MC robustness tests |
| `monte_carlo.n_simulations` | int | 1000 | Shuffle iterations |
| `monte_carlo.min_trades_per_cluster` | int | 5 | Min pattern count to test |
| `cross_validation.enabled` | bool | true | Run CV validation |
| `cross_validation.folds` | int | 5 | Fold count (3-10 typical) |
| `cross_validation.min_degradation_pct` | int | 20 | Max OOS degradation (%) |
| `clustering.algorithm` | string | "kmeans" | "kmeans" or "dbscan" |
| `clustering.n_clusters` | int | 15 | Target cluster count |
| `output.artifacts_dir` | string | ".ta_artifacts/..." | Where to save artifacts |
| `output.export_parquet` | bool | true | Export cluster details as parquet |
| `output.export_json` | bool | true | Export summary JSON |

**Template families:**
```
ORB               — Opening Range Breakout
breakout          — General breakout patterns
pullback          — Pullback entries
candle            — Candle patterns (hammer, engulfing, pin bar)
level             — Level-based entries (pivots, prev high/low)
lcr               — Left-Center-Right level discovery
```

**Example:**
```yaml
pattern_engine:
  enabled: true
  template_families: [ORB, breakout, level]
  monte_carlo:
    enabled: true
    n_simulations: 500
  cross_validation:
    enabled: true
    folds: 3
```

---

### `entry_strategies:`

Entry signal discovery.

```yaml
entry_strategies:
  enabled: true
  families:                                    # Entry families to discover
    - "candle"
    - "ma"
    - "bb"
    - "orb"
  validation:
    train_period: 252                          # Training period (days)
    test_period: 63                            # Testing period (days)
    step: 21                                   # Roll step (days)
    max_degradation: 0.5                       # Max 50% IS/OOS decline
  min_trades_per_signal: 5                     # Filter signals with < 5 trades
```

**Options:**

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `enabled` | bool | false | Enable entry discovery |
| `families` | list | [] | Entry families to discover |
| `validation.train_period` | int | 252 | Days of training data |
| `validation.test_period` | int | 63 | Days of test data per fold |
| `validation.step` | int | 21 | Days to roll forward |
| `validation.max_degradation` | float | 0.5 | Max IS/OOS Sharpe decline |
| `min_trades_per_signal` | int | 5 | Filter signals by trade count |

**Entry families:**
```
candle          — Candle pattern recognition (hammer, engulfing, etc.)
ma              — Moving average crossovers
bb              — Bollinger Band touches/breaks
orb             — Opening Range Breakout
breakout        — General breakout patterns
pullback        — Pullback confirmation entries
level           — Support/resistance level entries
lcr             — Left-Center-Right level discovery
```

**Example:**
```yaml
entry_strategies:
  enabled: true
  families: [candle, ma, bb]
  validation:
    train_period: 252
    test_period: 63
    max_degradation: 0.30  # Max 30% decline
  min_trades_per_signal: 10
```

---

### `regime_recommender:`

Market regime classification and recommendations.

```yaml
regime_recommender:
  enabled: true
  lookback: 20                                 # Bars to compute features
  adx_threshold: 25                            # ADX cutoff for trending
  atr_volatility_pct: 75                       # ATR percentile for volatile
  bb_calm_pct: 25                              # BB width percentile for calm
  position_size_baseline: 1.0                  # Base position size (1 contract / 1%)
```

**Options:**

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `enabled` | bool | false | Enable regime analysis |
| `lookback` | int | 20 | Bars for feature computation |
| `adx_threshold` | int | 25 | ADX > threshold = trending |
| `atr_volatility_pct` | int | 75 | ATR percentile for volatile |
| `bb_calm_pct` | int | 25 | BB width percentile for calm |
| `position_size_baseline` | float | 1.0 | Base position size |

**Example:**
```yaml
regime_recommender:
  enabled: true
  lookback: 20
  adx_threshold: 25
```

---

### `strategy_discovery:`

Full strategy synthesis (requires entry strategies).

```yaml
strategy_discovery:
  enabled: true
  instrument: "NQ"                             # Instrument (e.g., NQ, ES, CL)
  contract: "H25"                              # Contract (e.g., H25, Z24)
  timeframe: "5m"                              # Entry timeframe (1m, 5m, 15m, 1d)
  entry_families:                              # Which entries to use
    - "candle"
    - "ma"
  max_strategies: 50                           # Max strategies to generate
  validation:
    train_period: 252
    test_period: 63
    walk_forward: true
```

**Options:**

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `enabled` | bool | false | Enable strategy discovery |
| `instrument` | string | — | Instrument symbol (NQ, ES, CL, YM, GC, etc.) |
| `contract` | string | — | Futures contract (H25, Z24, etc.) |
| `timeframe` | string | "5m" | Entry signal timeframe (1m, 5m, 15m, 1d) |
| `entry_families` | list | [] | Entry families to use for strategy building |
| `max_strategies` | int | 50 | Max strategies to generate |
| `validation.train_period` | int | 252 | Training days |
| `validation.test_period` | int | 63 | Test days |
| `validation.walk_forward` | bool | true | Use walk-forward validation |

**Example:**
```yaml
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
  timeframe: "5m"
  entry_families: [candle, ma, bb]
  max_strategies: 20
```

---

## Sections Configuration

The `sections:` list controls which report sections to render.

```yaml
sections:
  - id: run_kpi_cards
  - id: daily_scoreboard
    options:
      top_n: 12
  - id: my_custom_section
    options:
      style: "compact"
      colors: ["#FF5733", "#33FF57"]
```

### Section Options

Each section accepts optional `options:` to customize rendering.

**Global patterns:**
- `title` (string) — Override section title
- `enabled` (bool) — Conditionally include section (default: true)
- `description` (string) — Custom description

**Common section options:**

| Section | Option | Type | Example |
|---------|--------|------|---------|
| `daily_scoreboard` | `top_n` | int | 12 (show top 12 days) |
| `trade_list` | `sort_by` | string | "pnl", "duration", "entry_time" |
| `trade_list` | `limit` | int | 100 (show first 100 trades) |
| `correlation_heatmap` | `instruments` | list | ["NQ", "ES", "YM"] |
| `equity_curve` | `log_scale` | bool | true |
| `hourly_profile` | `group_by` | string | "hour" or "day_of_week" |
| `pattern_discovery` | `top_clusters` | int | 10 |
| `entry_signals` | `family_filter` | string | "candle" or "ma" |

**Example with multiple options:**
```yaml
sections:
  - id: daily_scoreboard
    options:
      top_n: 20
      sort_by: "pnl_pct"
  - id: trade_list
    options:
      limit: 50
      sort_by: "entry_time"
  - id: equity_curve
    options:
      log_scale: false
      smooth: true
```

---

## Complete Example Config

```yaml
# Report metadata
report:
  title: "NQ H25 Strategy Analysis - Q1 2024"
  output_filename: "nq_h25_analysis.html"
  timezone: "America/Denver"

# MA anchor analysis
anchor_interaction:
  enabled: true
  strategy_family: "SMA"
  anchors:
    - family: "SMA"
      length: 20
      source: "close"
    - family: "SMA"
      length: 50
      source: "close"
    - family: "EMA"
      length: 9
      source: "close"

# Pattern discovery
pattern_engine:
  enabled: true
  template_families: [ORB, breakout, candle]
  monte_carlo:
    enabled: true
    n_simulations: 500
  cross_validation:
    enabled: true
    folds: 5

# Entry strategy discovery
entry_strategies:
  enabled: true
  families: [candle, ma, bb, orb]
  validation:
    train_period: 252
    test_period: 63
    max_degradation: 0.30
  min_trades_per_signal: 5

# Regime analysis
regime_recommender:
  enabled: true
  lookback: 20
  adx_threshold: 25

# Strategy discovery (uses entry strategies)
strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
  timeframe: "5m"
  entry_families: [candle, ma, bb]
  max_strategies: 30

# Report sections
sections:
  - id: run_kpi_cards
  - id: daily_scoreboard
    options:
      top_n: 15
  - id: equity_curve
    options:
      log_scale: true
  - id: trade_list
    options:
      limit: 100
      sort_by: "entry_time"
  - id: trade_time_profile
  - id: pattern_discovery
    options:
      top_clusters: 10
  - id: entry_signals_summary
  - id: regime_timeline
  - id: regime_statistics
```

---

## Validation Rules

### Required Fields
- `report.title` — string, non-empty
- `report.output_filename` — string, must end with `.html`
- `sections` — list, at least one section

### Type Validation
- `enabled` fields must be boolean (true/false)
- Numeric fields must be integers or floats
- List fields must be YAML lists (- item syntax)

### Conditional Requirements
- If `strategy_discovery.enabled: true`, then `entry_strategies.enabled: true` (strategy discovery depends on entry discovery)
- If `strategy_discovery` is enabled, then `instrument`, `contract`, `timeframe` are required

### Example Invalid Configs

```yaml
# ❌ Missing required field
report:
  output_filename: "report.html"
  # Missing: title

# ❌ Invalid section ID
sections:
  - id: nonexistent_section

# ❌ Type mismatch
pattern_engine:
  enabled: "yes"  # Should be boolean (true/false)
  n_simulations: "1000"  # Should be integer

# ❌ Conditional requirement missing
strategy_discovery:
  enabled: true
  entry_families: [candle, ma]
  # Missing: instrument, contract, timeframe
```

---

## Performance Tips

1. **Large datasets (> 5 years, 5m bars):**
   - Disable `monte_carlo` if too slow (set `enabled: false`)
   - Reduce `pattern_engine.clustering.n_clusters` (e.g., 8 instead of 15)
   - Reduce `cross_validation.folds` (e.g., 3 instead of 5)

2. **Faster reports:**
   - Disable expensive analyses (pattern_engine, strategy_discovery)
   - Reduce section count (render only core sections)
   - Set `--output` to fast disk (SSD, not network drive)

3. **Memory efficiency:**
   - Disable `regime_recommender` if memory-constrained
   - Set `pattern_engine.monte_carlo.enabled: false`

---

## Common Configurations

### Quick Backtest Report
```yaml
report:
  title: "Backtest Report"
  output_filename: "backtest.html"

sections:
  - id: run_kpi_cards
  - id: equity_curve
  - id: daily_scoreboard
```

### Full Discovery Report
```yaml
report:
  title: "Strategy Discovery Report"
  output_filename: "discovery.html"

pattern_engine:
  enabled: true
  template_families: [ORB, breakout, candle]

entry_strategies:
  enabled: true
  families: [candle, ma, bb, orb]

regime_recommender:
  enabled: true

strategy_discovery:
  enabled: true
  instrument: "NQ"
  contract: "H25"
  entry_families: [candle, ma, bb]

sections:
  - id: run_kpi_cards
  - id: pattern_discovery
  - id: entry_signals_summary
  - id: strategy_discovery_results
  - id: regime_statistics
```

### Minimal Report (Fast)
```yaml
report:
  title: "Quick Report"
  output_filename: "quick.html"

sections:
  - id: run_kpi_cards
  - id: trade_list
    options:
      limit: 50
```

---

## Sign-Off

**Version:** 1.0  
**Status:** Stable (no breaking changes expected)  
**Last updated:** May 24, 2026  
**Feedback:** Report configuration issues to documentation team.

---

Last updated: May 24, 2026
