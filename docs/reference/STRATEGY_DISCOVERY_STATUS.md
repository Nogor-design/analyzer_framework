# Strategy Discovery — Current State & Architecture

**As of May 2026**

---

## Executive Summary

The ta_foundation project has a **6-stage funnel-based strategy discovery system** that sweeps parameterized entry signal families across market data to identify tradeable patterns. The system is designed to answer progressively more specific questions, filtering candidates at each stage before deeper analysis.

### Key Metrics
- **8 signal families** supported (candles, MA, ORB, Bollinger Bands, LCR, breakout, pullback, levels)
- **Stage 1** runs ~250 combos in 3-5 min (broad brush)
- **Stage 6** validates the top candidates in 2-3 min (confirmation)
- **Architecture**: 4-layer (Parser → Pipeline → Analysis → Report Sections)
- **Core abstraction**: Entry/exit discovery sweeps produce ranked candidate signals stored in `metadata["derived"]["strategy_discovery"]`

---

## The Discovery Funnel

Discovery is structured as a **progressive funnel**:

```
01_quick_scan.yaml       ~250 combos    Which signal families have ANY edge?
    ↓ (select top 2-3 families)
02_candle_patterns.yaml  ~600 combos    Which candle pattern + TP/SL combo is best?
    ↓ (note best TP/SL ranges)
03_levels_regions.yaml   ~400 combos    Do LCR/FVG zones produce edge?
    ↓ (select best zone signals)
04_ny_open.yaml          ~300 combos    Does premarket last candle predict NY open?
    ↓ (filter to RTH-only patterns)
05_orb_momentum.yaml     ~350 combos    ORB breakout vs. MA crossover: which wins?
    ↓ (pick top 5-10 combos across all stages)
06_validate.yaml         ~100 combos    Walk-forward IS/OOS validation
    (Output: tradeable signals only)
```

**Critical Rule:** Always run with `--no-tick-data` for discovery. Tick data adds 5-15 minutes and is never needed for entry signal evaluation.

---

## Stage Definitions

### Stage 1: Quick Scan (`01_quick_scan.yaml`)
**Question:** Does any signal family have edge on this instrument?

**Sweep Parameters:**
- **Candle Patterns:** 9 patterns × 1 TP (20 ticks) × 1 SL (10 ticks) × 1 TF (1m) = ~27 combos
- **MA Signals:** 2 families × 1 TP × 1 SL × 1 TF = ~24 combos
- **ORB:** 3 windows × 1 TP × 1 SL = ~24 combos
- **Bollinger Bands:** 4 signal types × 1 TP × 1 SL = ~16 combos
- **LCR:** 4 signal types × 1 TP × 1 SL = ~16 combos
- **Breakout:** 2 patterns × 1 TP × 1 SL = ~24 combos
- **Pullback:** 2 patterns × 1 TP × 1 SL = ~24 combos
- **Levels:** 3 patterns × 1 TP × 1 SL = ~24 combos

**Output:** "Unified Strategy Discovery" cross-family ranking table. Look for PF ≥ 1.2 to confirm edge exists.

**Success Criterion:** Any family with PF ≥ 1.2 is worth diving deeper.

---

### Stage 2: Candle Patterns Deep Dive (`02_candle_patterns.yaml`)
**Question:** Which specific candle pattern + TP/SL combination is best?

**What to Look At:** `candle_discovery_ranking` — 5-tier table showing robust vs. marginal signals.

**Tiers (from YAML):**
| Tier | Criteria | Action |
|---|---|---|
| Most Robust | PF ≥ 1.5, IS/OOS degradation ≤ 10%, n ≥ 30 | Trade it |
| High Quality | PF ≥ 1.3, n ≥ 20 | Paper trade, then live |
| Solid | PF ≥ 1.1, n ≥ 15 | Needs more data |
| Marginal | PF ≥ 1.0 | Skip (noise) |
| (below) | PF < 1.0 | Filtered out |

**Key Metric:** IS/OOS degradation — how much profit factor drops from first 70% of bars (in-sample) to last 30% (out-of-sample). Degradation ≤ 10% = excellent; ≥ 50% = likely overfit.

---

### Stage 3: Levels & Regions (`03_levels_regions.yaml`)
**Question:** Do large candle regions (LCR/FVG) and swing levels produce edge?

**What to Look At:** `lcr_discovery_overview` — retrace rate and R2R (range-to-range) reach rate.
- 88%+ retrace rate = strong magnet signal
- `break_retrace` signal type typically best

---

### Stage 4: NY Open Scalp (`04_ny_open.yaml`)
**Question:** Does the last premarket candle predict the first NY open move?

**What to Look At:** `candle_discovery_overview` filtered to the 07:29 bar (last bar before 07:30 bell).
Look for patterns with PF > 1.3 on `next_open` entry timing only.

---

### Stage 5: ORB + Momentum (`05_orb_momentum.yaml`)
**Question:** Is ORB breakout or MA crossover the better opening range play?

**What to Look At:** `orb_discovery_overview` and `ma_discovery_overview` side-by-side.

---

### Stage 6: Validation (`06_validate.yaml`)
**Question:** Does the edge survive walk-forward IS/OOS validation?

**Output:** `strategy_discovery_validation` section. Only trade signals with IS/OOS degradation < 0.15 (15% drop from IS to OOS).

---

## Architecture: How Signals Are Discovered

### Entry Signal Families (8 Total)

Each family lives in `src/ta_foundation/analysis/entry_strategies/<family>/`:

1. **Candle Patterns** (`candle/`)
   - Pattern detection: `patterns.py`
   - Feature computation: `features.py` (ATR, candle size relative to lookback)
   - Entry timing: `signals.py` (next_open, break_extreme, body_midpoint)
   - Multi-timeframe confluence: `mtf.py`

2. **Moving Averages** (`ma/`)
   - Signals: crossover, pullback
   - Types: SMA, EMA
   - Parameters: fast_period, slow_period

3. **Bollinger Bands** (`bb/`)
   - Signal types: mean reversion, continuation, squeeze breakout, band walk
   - Parameters: band width (N standard deviations), period

4. **Opening Range Breakout** (`orb/`)
   - Window sizes: 5, 15, 30 minutes
   - Breakout trigger: high/low of window + buffer

5. **Breakout Patterns** (`breakout/`)
   - N-bar breakout: break of N-bar high/low
   - Volatility breakout: break by ±N ATR

6. **Pullback Entries** (`pullback/`)
   - Trend pullback: pullback within an uptrend/downtrend
   - Retracement entry: Fibonacci retracement levels

7. **Support/Resistance Levels** (`level/`)
   - Swing level: previous swing high/low
   - Consolidation: range breakout
   - Round number: big figure (1900, 1920, etc.)

8. **Large Candle Region (LCR)** (`lcr/`)
   - Region detection: candles > 2× average size
   - Signal types: fresh (inside region), touch (approaches edge), break (punches through), retrace (bounces off edge)

### The Sweep Engine (`_sweep_base.py`)

Core function: `_run_single_combo(bars, trades, params, session_filter, regime_filter)`

For each combo:
1. **Filter market bars** by session (RTH 07:30-16:00, ONH, London, etc.)
2. **Generate entry signals** (apply pattern/MA/ORB logic)
3. **Simulate exits** (fixed ticks TP/SL, ATR-based exits, max bars timeout)
4. **Compute trade outcomes** (P&L, MAE/MFE, duration)
5. **Rank by metrics** (PF, Win%, Sharpe, IS/OOS degradation)

### Output: metadata["derived"]["strategy_discovery"]

All discovery results attach as JSON under `pkg.metadata["derived"]["strategy_discovery"]`:

```python
metadata["derived"]["strategy_discovery"] = {
    "candle_discovery": {
        "results": [...],           # List of combos (ranked by PF)
        "is_oos_validation": {...}  # Walk-forward metrics
    },
    "ma_discovery": {...},
    "orb_discovery": {...},
    "bb_discovery": {...},
    "lcr_discovery": {...},
    "breakout_discovery": {...},
    "pullback_discovery": {...},
    "level_discovery": {...},
    "unified_ranking": {...}        # Cross-family comparison
}
```

**Critical:** All values are JSON-safe (no DataFrames, callables, or registries).

---

## Current Implementation Status

### ✅ Fully Implemented
- **Candle pattern discovery** (9 patterns, entry timing, multi-timeframe)
- **MA discovery** (crossover, pullback)
- **ORB discovery** (3 window sizes)
- **Bollinger Bands discovery** (4 signal types)
- **LCR discovery** (4 signal types, zone tracking)
- **Breakout discovery** (N-bar, volatility)
- **Pullback discovery** (trend, retracement)
- **Level discovery** (swing, consolidation, round numbers)
- **Exit simulation** (fixed ticks, ATR-based, max bars timeout)
- **IS/OOS validation** (walk-forward rolling/anchored)
- **Report sections** (~100+ HTML renderers)
- **CLI pipeline** (all 6 stages runnable via YAML config)
- **Web UI discovery builder** (interactive stage configuration)

### ⚠️ Partially Implemented
- **Phase 1-2 strategy optimization** (entry/exit discovery stubs exist but incomplete)
- **Pantheon bot template generation** (works for v2 and master, but limited parameter coverage)
- **Regime-specific filtering** (can be applied to sweeps but not fully integrated everywhere)

### 🔴 Not Yet Implemented / Gaps
1. **Dynamic parameter discovery** — Currently all parameter ranges are hard-coded per stage. No auto-expand based on edge strength.
2. **Cross-family optimization** — No top-level search across families and timeframes to find global best combo.
3. **Conditional entry rules** — Can't express "enter candles in RTH, ORB in premarket" in a single run.
4. **Stop migration** — No dynamic trailing stops or breakeven moves.
5. **Risk-based sizing** — All exits are fixed ticks; no Kelly criterion or volatility-adjusted position sizing.
6. **Multi-symbol discovery** — Can run one instrument at a time; no portfolio-level signal ranking.
7. **Regime-aware entry filters** — Can filter by regime but not "only enter candles in low-volatility regimes."

---

## Workflow: From Data to Deployable Signal

### Input
- NinjaTrader backtest exports (trades, daily, summary, settings CSVs)
- Minute bars (Last.txt files from TaFoundationMinuteBarExporter indicator)
- Optional tick data

### Pipeline

1. **Ingest** (`core/pipeline.py::ingest_folder`)
   - Register parsers
   - Detect run_id from filename
   - Separate run-scoped artifacts (trades, daily) from shared artifacts (market bars, ticks)
   - Merge into `AnalysisPackage` and `MarketDataStore`

2. **Strategy Discovery** (CLI calls each sweep in order)
   ```
   for pkg in packages.values():
       run_candle_discovery(pkg, market, cfg["candle_discovery"])
       run_ma_discovery(pkg, market, cfg["ma_discovery"])
       run_orb_discovery(pkg, market, cfg["orb_discovery"])
       ... (etc.)
       pkg.metadata["derived"]["strategy_discovery"] = merged_results
   ```

3. **Ranking & Validation**
   - Compute IS/OOS degradation
   - Rank by Sharpe, profit factor, trade count
   - Filter marginal signals (PF < 1.0)

4. **Report Rendering** (section renderers read from metadata)
   - `strategy_discovery_unified` — cross-family ranking
   - `candle_discovery_ranking` — 5-tier candle breakdown
   - `strategy_discovery_validation` — IS/OOS metrics
   - Individual family overviews

5. **Template Generation** (optional)
   ```python
   for candidate in top_candidates:
       nt_code = generate_nt_template(candidate)
       write_to_file(nt_code)  # Generate C# NinjaScript
   ```

---

## What's Working Well

1. **Fast broad discovery** — 250 combos in 3-5 min lets you know if an instrument has edge
2. **Staged filtering** — Funnel approach avoids parameter explosion (72 vs. 3,584 combos)
3. **IS/OOS validation** — Built-in degradation checks catch overfit signals
4. **Signal transportability** — Discovered signals can be auto-generated as NinjaTrader templates
5. **Real-time entry timing** — `next_open`, `break_extreme`, `body_midpoint` entry timing is combat-tested
6. **Session awareness** — RTH/premarket/overnight filtering is robust
7. **Multi-family ranking** — Can compare candles vs. ORB vs. MA in one report

---

## What Needs Work / Next Steps

### High Priority
1. **Auto-expand parameter space** — If stage 1 candles win with PF > 1.5, auto-include more TP/SL values in stage 2
2. **Cross-family optimization** — Find the single best combo across all 8 families (currently you pick one family, then drill)
3. **Conditional entry rules** — YAML syntax for "if regime=bull, use candles; if regime=flat, use ORB"
4. **Risk management layer** — Integrate position sizing, stop migration, trailing stops

### Medium Priority
5. **Regime integration** — Regime classifier exists (`regime_recommender/`); not yet fully wired into sweeps
6. **Multi-symbol discovery** — Discover signals on NQ, then validate on ES, RTY
7. **Parameter sensitivities** — Heatmaps showing how PF changes with each parameter

### Lower Priority
8. **ML classification** — Use gradient boosting to predict P&L from market features (prototype exists, not yet production)
9. **Ensemble signal combination** — Run multiple families together and score ensemble performance
10. **Backtester integration** — Direct bridge to NinjaTrader for continuous validation

---

## Example: Run the Quick Scan (You Can Do This Now)

```powershell
# Assuming you have:
# - C:/exports containing NinjaTrader backtest CSVs
# - D:/MarketData containing NQ_ES_*.Last.txt minute bar files
# - A Python environment with ta_foundation installed

pip install -e .

python -m ta_foundation.cli.main \
  --input "C:/exports" \
  --output ./outputs/discovery_scan \
  --report-config ./discovery/01_quick_scan.yaml \
  --market-data "D:/MarketData" \
  --recursive \
  --no-tick-data

# Open ./outputs/discovery_scan/01_quick_scan.html
# Look at the "Unified Strategy Discovery" section
# Note which families have PF >= 1.2 and on which timeframe
```

Then pick the winning family and run the matching stage 2+ YAML.

---

## Key Files & Modules

| What | Where | Purpose |
|---|---|---|
| Discovery workflow | `discovery/README.md` | Human-readable funnel guide |
| Quick scan config | `discovery/01_quick_scan.yaml` | Stage 1 entry point |
| Candle family | `analysis/entry_strategies/candle/` | Pattern detection + MTF support |
| LCR family | `analysis/entry_strategies/lcr/` | Large candle region signals |
| Sweep engine | `analysis/entry_strategies/_sweep_base.py` | Core combo runner |
| Orchestrator | `analysis/strategy_discovery/orchestrator.py` | High-level pipeline |
| Report sections | `reports/html/sections/` | 100+ renderers (strategy_discovery_* files) |
| CLI | `cli/main.py` | Entry point (calls all sweeps) |
| Web UI | `web/discovery_builder.py` | Interactive stage config builder |

---

## Links to Deep Dives

- **How to narrow between stages:** See `discovery/README.md` "How to Narrow Between Stages"
- **Report tiers explained:** See `discovery/README.md` "What the Tiers Mean"
- **Entry strategy families:** `src/ta_foundation/analysis/entry_strategies/` (8 subdirectories)
- **Validation logic:** `analysis/entry_strategies/validation.py` (IS/OOS degradation computation)
