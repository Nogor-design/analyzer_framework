# Entry Pattern Discovery — Design Specification

**Status:** Design Contract
**Scope:** Integrating Pattern Engine signals as first-class entry candidates in Strategy Discovery
**Goal:** Find new entry patterns from raw market data and validate them through the full discovery pipeline

---

## 1. The Problem

The system currently has two independent subsystems that do not talk to each other:

### Pattern Engine (what it does)
- Sweeps parametric pattern templates (ORB break/retest, MA alignment, VWAP bounce, etc.) across **all** market bars
- Computes **forward outcomes** for every signal firing: `avg_ticks`, `win_rate`, `mae_p50`, `mfe_p50` over 10/20/40-bar horizons
- Statistical corpus = **every bar in the dataset**, not just executed trades
- Result: knows that "ORB::orb_break_retest fired 312 times with 58% win rate"

### Entry Discovery (what it does)
- Analyzes **executed trades only** to find rules that distinguish winners from losers
- Can reference patterns via `pat_ma_alignment == True` (a bool column from trade_pattern_audit)
- Never sees the **120 signals the pattern engine found** that weren't executed trades
- Never uses the pattern engine's outcome statistics

### The Gap
```
Pattern engine: 312 ORB signals found → avg_ticks=12.5, win_rate=0.58
                              ↓
Trade pattern audit: 38 of your 45 executed trades had ORB fire
                              ↓
Entry discovery: "pat_orb_direction was True in 38 trades" → rule score based on 38 trades only

LOST: 274 signals that were never traded. The unexecuted corpus is larger and less biased.
```

---

## 2. Design Goals

1. **Pattern engine signals become entry candidates** — not just post-hoc labels on executed trades
2. **Unexecuted pattern corpus drives discovery** — leverage all 312 signals, not just 38 executed ones
3. **Feedback loop is bidirectional** — pattern stats inform rule scoring; rule filtering sharpens pattern selection
4. **Pure market entry discovery** — find entries from bars alone (no executed trades required)
5. **All existing contracts preserved** — sections remain pure renderers; metadata schema unchanged

---

## 3. System Architecture

### 3.1 Current Data Flow (Broken)

```
Market Bars ──→ [Pattern Engine] ──→ signals_df (312 rows)
                                         ↓ (ignored by discovery)
                       [Trade Pattern Audit] ──→ pat_* bool columns on 45 trades
                                         ↓
Executed Trades ──→ [Feature Matrix] ──→ feature_df (45 rows)
                                         ↓
                       [Entry Discovery] ──→ rules ("pat_orb == True AND adx >= 25")
```

### 3.2 Proposed Unified Data Flow

```
Market Bars ──→ [Pattern Engine] ──→ signals_df (312 rows)
                    ↓                     ↓
              pattern_stats_df      [Signal Feature Join]
              (win_rate, avg_ticks)       ↓
                    ↓             signal_feature_df (312 rows × market features)
                    ↓                     ↓
Executed Trades ──→ [Unified Feature Matrix Builder] ←── Trade-Signal Match
                    (45 executed + 267 unexecuted signals)
                              ↓
              [Entry Pattern Discovery]
              Mode A: rule discovery on executed trades (existing)
              Mode B: signal corpus discovery (new — uses all 312 rows)
              Mode C: hybrid scoring (blends both)
                              ↓
              [Unified Rule Ranking] — weighted by corpus + execution agreement
                              ↓
              Discovered rules → strategy_discovery["entry_patterns"]
```

---

## 4. New Module: `analysis/strategy_discovery/entry_pattern_bridge.py`

This is the central integration point. It has one job: produce a **unified entry candidate dataframe** from the pattern engine's signal corpus and the executed trade set.

### 4.1 Function Signatures

```python
def build_signal_feature_matrix(
    signals_df: pd.DataFrame,          # from pattern engine artifacts
    outcomes_df: pd.DataFrame,         # from pattern engine artifacts
    pattern_stats_df: pd.DataFrame,    # per-pattern aggregated stats
    bars_with_regime: pd.DataFrame,    # ADX, ATR, regime from strategy_discovery/regime.py
    options: dict,                     # bridge config block
) -> pd.DataFrame:
    """
    Produce a feature matrix where EACH ROW IS A PATTERN SIGNAL FIRING.

    Unlike the existing feature_df (which has one row per executed trade),
    this has one row per signal — including signals that had no corresponding
    executed trade.

    Columns produced:
      signal_id          : from signals_df
      pattern_id         : from signals_df
      family             : e.g. "ORB", "MA_TREND"
      structure          : e.g. "orb_break_retest"
      direction          : +1 / -1
      dt                 : signal datetime
      session_label      : "us_open", "us_morning", etc.
      day_id             : date
      regime             : trending_up / ranging_tight / high_vol_expansion / ...
      adx                : ADX value at signal bar
      atr                : ATR value at signal bar
      vol_regime         : low_vol / normal_vol / high_vol
      tod_bucket         : open_0_30 / mid / close

      # Pattern outcomes (from outcomes_df, collapsed to primary horizon)
      horizon            : primary horizon used (e.g. 20)
      ret_ticks          : realized return over horizon
      mfe_ticks          : max favorable excursion
      mae_ticks          : max adverse excursion
      is_winner          : ret_ticks > 0

      # Pattern corpus stats (from pattern_stats_df — stable at pattern level)
      corpus_n           : total signals from this pattern_id
      corpus_win_rate    : overall win_rate across all signal firings
      corpus_avg_ticks   : overall avg_ticks across all signal firings
      corpus_mfe_p50     : overall median MFE
      corpus_mae_p50     : overall median MAE

      # Execution match (populated if a trade was executed near this signal)
      has_executed_trade : bool
      trade_profit       : actual trade profit (NaN if no match)
      trade_mae          : actual trade MAE (NaN if no match)
    """
```

```python
def match_trades_to_signals(
    trades: pd.DataFrame,              # pkg.trades
    signals_df: pd.DataFrame,          # from pattern engine
    options: dict,
    bars_with_regime: pd.DataFrame,
) -> pd.DataFrame:
    """
    Match executed trades to pattern signals by time proximity.

    A trade at time T matches a signal at time S if:
      |T - S| <= match_window_bars * bar_duration
      direction matches market_pos

    Returns trades with new columns:
      matched_signal_id     : closest matching signal_id or NaN
      matched_pattern_id    : pattern_id of matched signal
      matched_family        : family of matched pattern
      matched_structure     : structure of matched pattern
      signal_corpus_win_rate: pattern's overall win_rate (from corpus)
      signal_corpus_avg_ticks: pattern's overall avg_ticks
      match_quality         : "exact" / "close" / "none"
    """
```

```python
def discover_entry_patterns(
    signal_feature_df: pd.DataFrame,  # from build_signal_feature_matrix()
    trades_feature_df: pd.DataFrame,  # existing feature matrix (executed trades)
    options: dict,
) -> dict:
    """
    Three-mode entry pattern discovery.

    Mode A — trade-anchored (existing behavior, enhanced):
      Uses trades_feature_df. Same as current entry_discovery.py but with
      richer features (corpus_win_rate, corpus_avg_ticks available).

    Mode B — signal-corpus discovery (NEW):
      Uses signal_feature_df directly. Finds conditions on signals where
      ret_ticks is above baseline (e.g. adx >= 25 AND regime == trending_up
      selects 89 signals with win_rate=0.65 vs baseline 0.52).
      This mode does NOT require any executed trades.

    Mode C — hybrid (NEW, default):
      Runs both A and B, merges rules, scores by:
        hybrid_score = α * corpus_win_rate_lift
                     + β * execution_agreement    (does corpus agree with trades?)
                     + γ * selectivity
      α=0.45, β=0.35, γ=0.20 (configurable)

    Returns:
      {
        "top_rules": [...],           # ranked rules (unified)
        "corpus_rules": [...],        # mode B rules (signal-corpus only)
        "execution_rules": [...],     # mode A rules (trade-anchored)
        "hybrid_rules": [...],        # mode C merged rules
        "baseline": {...},
        "diagnostics": {...},
      }
    """
```

---

## 5. New Module: `analysis/strategy_discovery/signal_entry_discovery.py`

This module handles **pure market discovery mode** — finding entry patterns without any executed trades. This is the "scan bars and find what works" mode.

```python
def run_signal_entry_discovery(
    signal_feature_df: pd.DataFrame,  # from entry_pattern_bridge
    options: dict,
    profit_col: str = "ret_ticks",    # forward return column
) -> dict:
    """
    Pure market-based entry discovery.

    Equivalent to entry_discovery.py but operating on:
      - signal_feature_df (one row per pattern firing, not per executed trade)
      - target = ret_ticks (forward return), not trade profit

    This discovers conditions like:
      "When ORB::orb_break_retest fires AND regime == trending_up AND adx >= 25,
       forward 20-bar return = +18 ticks (win_rate=0.71, n=47)"

    Atoms generated:
      CATEGORICAL:
        regime == "trending_up" | "ranging_tight" | ...
        session_label == "us_open" | "us_morning" | ...
        family == "ORB" | "MA_TREND" | "VWAP" | ...
        structure == "orb_break_retest" | "ma_alignment" | ...
        direction == 1 | -1
        vol_regime == "high_vol" | "normal_vol" | "low_vol"
        tod_bucket == "open_0_30" | "mid" | "close"
      NUMERIC:
        adx >= 20 | 25 | 30
        atr >= p25 | p50 | p75
        corpus_win_rate >= 0.50 | 0.55 | 0.60

    Returns JSON-safe dict with:
      top_signal_rules   : [{rule_str, conditions, stats}]
      baseline           : overall signal corpus stats
      diagnostics        : {n_signals, n_patterns, n_atoms, ...}
    """
```

---

## 6. Extensions to Existing Modules

### 6.1 `features.py` — extend `build_feature_matrix()`

Add optional `signal_feature_df` parameter:

```python
def build_feature_matrix(
    trades: pd.DataFrame,
    bars_with_regime: Optional[pd.DataFrame] = None,
    audit_df: Optional[pd.DataFrame] = None,
    signal_feature_df: Optional[pd.DataFrame] = None,  # NEW
) -> pd.DataFrame:
    """
    Existing behavior unchanged when signal_feature_df is None.

    When signal_feature_df is provided, adds to each trade row:
      corpus_win_rate          : matched pattern's overall win_rate
      corpus_avg_ticks         : matched pattern's overall avg_ticks
      corpus_mfe_p50           : matched pattern's MFE p50
      corpus_mae_p50           : matched pattern's MAE p50
      signal_family            : pattern family at entry
      signal_structure         : pattern structure at entry
      signal_match_quality     : "exact" / "close" / "none"
    """
```

### 6.2 `entry_discovery.py` — extend atom generation

Add new atom types when corpus columns are present:

```python
# NEW atom types (added when corpus_win_rate is in feature_df):
_CORPUS_COLS = ["corpus_win_rate", "corpus_avg_ticks"]
_CORPUS_THRESHOLDS = {
    "corpus_win_rate": [0.50, 0.55, 0.60, 0.65],
    "corpus_avg_ticks": [0.0, 5.0, 10.0, 15.0],
}

# NEW categorical atoms (added when signal_family / signal_structure present):
_PATTERN_CAT_COLS = ["signal_family", "signal_structure", "signal_match_quality"]
```

Rule descriptions updated to be human-readable:
- Old: `"pat_orb_direction == True AND adx >= 25"`
- New: `"Pattern ORB::orb_break_retest fired AND adx >= 25 (corpus win_rate=0.58, n=47)"`

### 6.3 `orchestrator.py` — new pipeline phase

Insert between feature matrix build and entry discovery:

```python
# NEW: Phase 2b — Pattern Signal Bridge
# Runs only when pattern engine artifacts are available
try:
    from .entry_pattern_bridge import build_signal_feature_matrix, match_trades_to_signals

    pe_meta = (pkg.assets or {}).get("pattern_engine") or {}
    signals_df = _load_pe_artifact(pe_meta, "signals")
    outcomes_df = _load_pe_artifact(pe_meta, "outcomes")
    pattern_stats_df = _load_pe_artifact(pe_meta, "pattern_stats")

    if signals_df is not None and len(signals_df) > 0:
        signal_feature_df = build_signal_feature_matrix(
            signals_df=signals_df,
            outcomes_df=outcomes_df,
            pattern_stats_df=pattern_stats_df,
            bars_with_regime=bars_with_regime,
            options=bridge_options,
        )
        discovery_block["signal_entry_discovery"] = run_signal_entry_discovery(
            signal_feature_df=signal_feature_df,
            options=signal_entry_options,
        )
        # Enrich feature_df with corpus stats for trade-anchored discovery
        feature_df = build_feature_matrix(
            trades, bars_with_regime, audit_df,
            signal_feature_df=signal_feature_df,  # NEW parameter
        )
        discovery_block["n_signal_corpus"] = len(signals_df)
    else:
        signal_feature_df = None

except Exception as exc:
    discovery_block["entry_pattern_bridge"] = {"error": str(exc)}
    signal_feature_df = None
```

---

## 7. Metadata Schema Extension

### `pkg.metadata["derived"]["strategy_discovery"]["signal_entry_discovery"]`

```python
{
    "signal_corpus": {
        "n_signals": 312,
        "n_patterns": 8,
        "n_matched_to_trades": 38,
        "corpus_baseline": {
            "win_rate": 0.52,
            "avg_ticks": 6.2,
        }
    },
    "top_signal_rules": [
        {
            "rank": 1,
            "rule_str": "ORB::orb_break_retest AND regime=trending_up AND adx >= 25",
            "conditions": [
                {"column": "family", "op": "eq", "value": "ORB"},
                {"column": "structure", "op": "eq", "value": "orb_break_retest"},
                {"column": "regime", "op": "eq", "value": "trending_up"},
                {"column": "adx", "op": "gte", "value": 25},
            ],
            "n_signals": 47,
            "win_rate": 0.71,
            "avg_ticks": 18.2,
            "win_rate_lift": 0.19,
            "avg_ticks_lift": 12.0,
            "mfe_p50": 28.0,
            "mae_p50": 8.5,
            "execution_agreement": 0.82,  # fraction where trade also won
            "score": 84.2,
        },
        ...
    ],
    "diagnostics": {
        "mode": "hybrid",
        "n_atoms": 28,
        "n_candidates": 142,
        "primary_horizon": 20,
        "issues": [],
    }
}
```

---

## 8. Report Section: `strategy_discovery_signal_entries`

New read-only renderer that shows signal-corpus entry discoveries.

### What it reads

```python
sd = pkg.metadata["derived"]["strategy_discovery"]
signal_disc = sd.get("signal_entry_discovery") or {}
top_rules = signal_disc.get("top_signal_rules") or []
```

### What it renders

1. **Corpus Health Card** — n_signals, n_patterns, n_matched_to_trades, corpus baseline win_rate
2. **Signal Rule Table** — ranked rules with: rule_str, n_signals, win_rate, avg_ticks, corpus stats, execution agreement
3. **Per-Rule Detail** (accordion) — conditions breakdown, win_rate by regime, MFE/MAE profile from corpus
4. **Execution Agreement Chart** — for each rule, compares corpus win_rate vs actual trade win_rate when pattern fired

### Template generator feed

Rules from `signal_entry_discovery.top_signal_rules` can populate the NinjaTrader template generator's entry conditions. A rule like:

```
"ORB::orb_break_retest AND regime=trending_up AND adx >= 25"
```

maps to template parameters:
- `EntryMode = PatternBased`
- `RequirePatternFamily = ORB`
- `RequirePatternStructure = orb_break_retest`
- `RequireRegimeMode = TrendingOnly`
- `AdxThreshold = 25`
- `StopTicks = mae_p50 * 1.2` (from corpus)
- `TargetTicks = mfe_p50 * 0.75` (from corpus)

---

## 9. Configuration YAML Block

```yaml
strategy_discovery:
  enabled: true
  # ... existing config ...

  # NEW: Entry pattern bridge config
  entry_pattern_bridge:
    enabled: true              # requires pattern_engine to also be enabled
    primary_horizon: 20        # which outcome horizon to use as target
    match_window_bars: 3       # trade matched to signal if within N bars of entry
    mode: hybrid               # trade_anchored | corpus | hybrid
    hybrid_weights:
      corpus_win_rate: 0.45    # weight given to pattern corpus performance
      execution_agreement: 0.35 # weight given to corpus vs trade agreement
      selectivity: 0.20        # weight given to fraction of signals captured
    min_signals: 20            # min signal firings for a rule to qualify

  signal_entry_discovery:
    enabled: true
    profit_col: ret_ticks      # column in signal_feature_df to optimize
    max_depth: 2               # max conditions per rule (same as entry_discovery)
    min_signals: 15            # minimum signal firings per candidate rule
    top_n: 20
    adx_thresholds: [20, 25, 30]
    corpus_win_rate_thresholds: [0.50, 0.55, 0.60, 0.65]
```

---

## 10. Pure Market Discovery Mode (No Executed Trades)

When running in market_discovery scope (from PATTERN_ENGINE_REDESIGN.md), the entire Strategy Discovery pipeline runs on a synthetic package. The signal_entry_discovery module is the **primary** output instead of entry_discovery.

```python
# orchestrator: synthetic package for pure market scan
run_id = f"__market__::NQ::1m::2025Q1::RTH::{config_hash}"
pkg = AnalysisPackage(run_id=run_id)
# no trades — signal_entry_discovery uses corpus only
```

In this mode:
1. Pattern engine sweeps all bars → produces signals_df (all firings)
2. `signal_entry_discovery` discovers conditions where corpus win_rate >> 50%
3. Walk-forward CV validates rules on held-out periods
4. Monte Carlo evaluates prop survival for top rules
5. Output: `candidate_specs.parquet` — ready for NinjaTrader template generation

This is the **"find new strategies from scratch"** capability the user requested.

---

## 11. Implementation Order

### Phase 1 — Bridge & Schema (Days 1–2)
- [ ] `entry_pattern_bridge.py` — `build_signal_feature_matrix()` + `match_trades_to_signals()`
- [ ] Extend `features.py` — add `signal_feature_df` parameter
- [ ] Wire into `orchestrator.py` — Phase 2b insertion

### Phase 2 — Signal Entry Discovery (Days 3–4)
- [ ] `signal_entry_discovery.py` — `run_signal_entry_discovery()` (reuses atom generation from `entry_discovery.py`)
- [ ] Extend `entry_discovery.py` — corpus columns as new atom types
- [ ] Update metadata schema — `signal_entry_discovery` block in orchestrator

### Phase 3 — Report Section (Day 5)
- [ ] `strategy_discovery_signal_entries.py` — section renderer
- [ ] Register in `registry.py`
- [ ] Add to `strategy_discovery_report.yaml`

### Phase 4 — Template Feed (Day 6)
- [ ] Extend `nt_template_generator.py` — read `signal_entry_discovery.top_signal_rules`
- [ ] Map pattern family/structure → C# template entry conditions

### Phase 5 — Pure Market Discovery (Days 7–8)
- [ ] Orchestrator: synthetic package creation
- [ ] Full pipeline run on signal corpus only (no trades)
- [ ] `candidate_specs.parquet` generation
- [ ] Walk-forward validation on signals

---

## 12. Key Contracts (Non-Negotiable)

| Contract | Rule |
|---|---|
| Sections | Pure renderers — no analysis inside sections |
| Metadata | All results in `pkg.metadata["derived"]["strategy_discovery"]` |
| Timestamps | All datetimes tz-aware America/Denver |
| Pattern artifacts | Read from `pkg.assets["pattern_engine"]["signals"]` etc. (not re-loaded from disk) |
| Signal feature df | One row per pattern firing; never contains executed-trade-only rows |
| Trade feature df | One row per executed trade; enriched with corpus stats but not signal rows |
| These two never mixed | `signal_feature_df` and `trades_feature_df` remain separate until `discover_entry_patterns()` merges rules |

---

## 13. What Changes vs What Stays

### No changes needed
- `entry_discovery.py` — unchanged core logic, only atom types extended
- `features.py` — unchanged unless signal_feature_df provided
- `trade_pattern_audit.py` — unchanged (still used for pat_* columns)
- Pattern engine orchestrator — unchanged
- All existing section renderers — unchanged
- YAML schema — additive only (new blocks, no renames)

### New files
- `analysis/strategy_discovery/entry_pattern_bridge.py`
- `analysis/strategy_discovery/signal_entry_discovery.py`
- `reports/html/sections/strategy_discovery_signal_entries.py`

### Minimal edits
- `features.py` — add optional `signal_feature_df` parameter (backward compatible)
- `entry_discovery.py` — add corpus columns to atom generators (behind `if col in df.columns` guards)
- `orchestrator.py` — Phase 2b insertion + `signal_entry_discovery` call
- `nt_template_generator.py` — read signal_entry_discovery rules for entry conditions
- `registry.py` — register new section
- `strategy_discovery_report.yaml` — add new section and config blocks
