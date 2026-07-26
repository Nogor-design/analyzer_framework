# Edge Discovery — Practical Guide

This document covers the full research workflow: from hypothesis formation through
sweep execution, statistical validation, and final holdout promotion.

---

## The Core Problem This Solves

Running many strategy sweeps and keeping the ones that look good is how you get
a backtest curve that means nothing. Every time you look at results and decide
to test another variation, you are implicitly testing more hypotheses, and your
p-values are correspondingly more meaningless.

This system enforces discipline at three chokepoints:

1. **Pre-registration** — write down *why* you think an edge exists before you
   see any results, and lock the holdout date range immediately.
2. **Multi-hypothesis correction** — the Bonferroni denominator grows with every
   experiment you register. Running 50 tests and accepting p < 0.05 is the same
   as random noise. The system tracks this automatically.
3. **One-shot holdout** — the final 20% of your date range can only be evaluated
   once, and only after the IS/VAL gates pass. There is no second chance.

---

## Installation

```bash
pip install -e ".[analysis,persistence]"
```

`analysis` adds numpy, scipy, scikit-learn, pyarrow.
`persistence` adds duckdb for the experiment registry.

---

## The Workflow

```
1. Form a hypothesis (written sentence)
2. Pre-register it  →  holdout dates locked immediately
3. Run your sweep on IS+VAL data only
4. run_validation() with all gates
5. If all gates pass → promote_strategy (one-shot holdout)
6. Repeat from step 1 (Bonferroni denominator grows)
```

The key discipline: **steps 1–2 happen before you look at any results.**
If you have already run the sweep and are deciding what to register based on
what you saw, you have already contaminated it.

---

## Step 1 — Pre-Register a Hypothesis

### Interactive (recommended for new experiments)

```bash
python -m ta_foundation.cli.register_hypothesis --db-path experiments.duckdb
```

You will be prompted for:

| Field | Example | Required |
|---|---|---|
| Family | `candle` | yes |
| Signal ID | `bull_engulf` | no |
| Instrument | `NQ` | yes |
| Contract | `H25` | no |
| Timeframe | `5m` | no |
| Date range start | `2023-01-01` | no |
| Date range end | `2024-12-31` | no |
| Hypothesis | one sentence | yes |

The hypothesis statement is what keeps you honest. Write it as a falsifiable claim:

> "Bullish engulfing candles at the VWAP level on NQ 5m bars produce a positive
> expected value entry when the 20-period ATR is above its 20-day median."

Not:
> "Candle patterns look good."

### Non-interactive (for scripts or CI)

```bash
python -m ta_foundation.cli.register_hypothesis \
    --db-path experiments.duckdb \
    --non-interactive \
    --family candle \
    --signal-id bull_engulf \
    --instrument NQ \
    --contract H25 \
    --timeframe 5m \
    --date-range-start 2023-01-01 \
    --date-range-end 2024-12-31 \
    --hypothesis "Bullish engulfing at VWAP on NQ 5m produces +EV entries above median ATR"
```

Prints: `Registered experiment id=3`

### List all registered experiments

```bash
python -m ta_foundation.cli.register_hypothesis --db-path experiments.duckdb --list
```

---

## Step 2 — Lock the Holdout (optional but recommended)

If you have your full trade history available at registration time, lock the
holdout boundary explicitly so it cannot shift later:

```python
import pandas as pd
from ta_foundation.analysis.strategy_discovery.holdout import lock_holdout

trades = pd.read_parquet("full_history.parquet")
part = lock_holdout(db_path="experiments.duckdb", experiment_id=3, trades=trades)

print(f"IS:      {part.n_is} trades  ({part.is_start} → {part.is_end})")
print(f"VAL:     {part.n_val} trades  ({part.val_start} → {part.val_end})")
print(f"HOLDOUT: {part.n_holdout} trades  ({part.holdout_start} → {part.holdout_end})")
```

Default split: IS=60%, VAL=20%, Holdout=20% of the date span.

After this call, `holdout_start` and `holdout_end` are written into the
experiments table and will not change even if you call `lock_holdout` again.

---

## Step 3 — Run Your Sweep on IS+VAL Only

Before running a sweep, filter your trades to exclude the holdout:

```python
from ta_foundation.analysis.strategy_discovery.holdout import filter_for_validation

# Returns only trades before the locked holdout boundary
is_val_trades = filter_for_validation(
    trades,
    db_path="experiments.duckdb",
    experiment_id=3,
)
```

If no holdout is locked yet, this returns all trades (safe fallback).

You can also enforce that a hypothesis is registered before running:

```python
from ta_foundation.cli.register_hypothesis import assert_registered

exp_id = assert_registered(
    "experiments.duckdb",
    family="candle",
    signal_id="bull_engulf",
    instrument="NQ",
)
# raises RuntimeError if no matching registered experiment found
```

---

## Step 4 — Validate

### Basic call

```python
from ta_foundation.analysis.strategy_discovery.validation import run_validation

result = run_validation(trades)
print(result.summary)
```

### With the registry wired in (recommended)

```python
result = run_validation(
    trades,
    db_path="experiments.duckdb",
    experiment_id=3,
)
```

This does two things automatically:
- Writes the result to `validation_results` table with the correct
  `n_hypotheses_at_time` denominator for retroactive Bonferroni correction.
- Enables the DSR gate using the total experiment count from the DB.

### All gates

```python
result = run_validation(
    trades,
    # Cost model (applied before all gates)
    cost_model={
        "commission_per_side": 2.09,
        "slippage_ticks": 1,
        "tick_value": 5.00,
    },
    # Walk-forward config
    wf_config={
        "is_pct": 0.70,
        "min_is_trades": 50,
        "min_oos_trades": 20,
        "degradation_threshold": 0.20,
        "n_folds": 5,
    },
    # Bonferroni correction (or auto-read from DB)
    n_hypotheses_tested=12,
    min_t_stat_abs=3.0,          # require t > 3 regardless of alpha
    # DSR gate (auto-enabled when db_path is set)
    dsr_n_trials=12,
    dsr_threshold=0.95,
    # Regime stratification
    regime_breakdown=by_regime_dict,     # from compute_regime_breakdown()
    regime_dispersion_count=3,
    regime_dispersion_min=2,
    min_per_regime_expectancy=0.0,       # all regimes must be profitable
    # Session concentration
    session_concentration_cap=0.55,
    session_concentration_max=0.70,
    # Persistence
    db_path="experiments.duckdb",
    experiment_id=3,
)
```

### Gate reference

| Gate | Always applied | What it checks | Default threshold |
|---|---|---|---|
| `min_counts` | yes | IS ≥ 50 trades, OOS ≥ 20 | configurable |
| `degradation` | yes | OOS PF ≥ 80% of IS PF | 20% max degradation |
| `t_test` | yes | t-stat > Bonferroni-corrected critical value | p < 0.05 / N |
| `monte_carlo` | yes | actual max-DD < 95th pct of shuffled DDs | p95 |
| `fold_sign_consistency` | auto (rolling WF) | ≥ 60% of folds have positive OOS | 0.6 |
| `dsr` | when N is known | DSR ≥ 0.95 | 0.95 |
| `regime_dispersion` | when count provided | ≥ 2 distinct regimes | 2 |
| `min_per_regime_expectancy` | when breakdown provided | all regimes profitable | 0.0 |
| `session_concentration` | when cap provided | no session > 70% of trades | 0.70 |
| `sensitivity_class` | when class provided | fragile / moderate / robust rank | configurable |

### Reading the result

```python
# Did it pass all gates?
print(result.passed)           # True / False

# Human-readable gate-by-gate breakdown
print(result.summary)

# Individual gate results
for gate in result.gates:
    status = "PASS" if gate.passed else "FAIL"
    print(f"{gate.name:30} {status}  {gate.reason}")

# Raw numbers
print(result.t_test["t_stat"])
print(result.t_test["required_t_stat"])
print(result.wf_results["oos_pf"])
print(result.wf_results["rolling"]["fold_sign_consistency"])
print(result.monte_carlo["actual_max_dd"])

# JSON-safe dict for storage
d = result.to_dict()
```

---

## Step 5 — Promote to Holdout (one-shot)

Once IS+VAL validation passes, you get exactly one chance to evaluate the
holdout slice. This is the only number that matters for the research record.

### CLI

```bash
python -m ta_foundation.cli.promote_strategy \
    --db-path experiments.duckdb \
    --experiment-id 3 \
    --trades-parquet full_history.parquet

# Exit code 0 = holdout passed, 1 = holdout failed
```

Accepts `--trades-parquet` or `--trades-csv`. The CLI automatically partitions
the trades using the same IS/VAL/Holdout split (60/20/20 by default).

### Python

```python
from ta_foundation.analysis.strategy_discovery.holdout import run_holdout_evaluation

result = run_holdout_evaluation(
    trades=full_history_df,
    db_path="experiments.duckdb",
    experiment_id=3,
)

print(result.summary)
# Experiment status is now "promoted" or "holdout_failed"
```

Calling this twice on the same experiment raises `RuntimeError`. This is
intentional — there is no second chance at the holdout.

---

## Step 6 — Check the Registry

### In Python

```python
from ta_foundation.persistence.db import ExperimentRegistry

reg = ExperimentRegistry("experiments.duckdb")

# All experiments
for exp in reg.list_experiments():
    print(exp["id"], exp["status"], exp["hypothesis_text"][:60])

# Validation history for one experiment
for vr in reg.list_validation_results(experiment_id=3):
    print(vr["run_at"], vr["passed"], vr["n_hypotheses_at_time"])

# How many experiments have been run (Bonferroni denominator)
print(reg.count_experiments())
```

### Experiment statuses

| Status | Meaning |
|---|---|
| `registered` | Hypothesis locked, sweep not yet run |
| `promoted` | Holdout passed |
| `holdout_failed` | Holdout evaluated, did not pass |

---

## Using the DSR Correctly

The Deflated Sharpe Ratio answers: "given that I tested N strategies and picked
the best one, what is the probability the observed Sharpe is genuine?"

With N=1 (single hypothesis, no fishing), DSR ≈ standard Sharpe test.
With N=50 (50 variations tested), the bar is much higher — most strategies with
a Sharpe of 0.5 will fail DSR when N=50.

The registry makes this tractable. Every time you call `run_validation()` with
`db_path` set, the DB is queried for the current experiment count and that count
is used as N. If you ran 30 experiments last month and have since registered 10
more, old results can be re-evaluated with the correct N using the stored
`n_hypotheses_at_time` column.

```python
# Standalone DSR check (without validation pipeline)
from ta_foundation.analysis.statistics.dsr import compute_dsr
import pandas as pd

returns = pd.Series([...])  # per-trade P&L

result = compute_dsr(returns, n_trials=25)
print(f"DSR: {result.dsr:.4f}")
print(f"Observed SR: {result.sr_hat:.3f}")
print(f"Expected max SR across {result.n_trials} trials: {result.sr_star:.3f}")
print(f"Passed (DSR ≥ 0.95): {result.passed}")
```

---

## Partition Logic

All date partitions use **date span**, not trade count:

```
[  IS 60%  |  VAL 20%  |  HOLDOUT 20%  ]
 ──────────────────────────────────────
 t_min                              t_max
```

A strategy with 500 trades bunched in one quarter of a year is treated the same
as one with 500 trades spread over two years. This prevents you from choosing
a partition boundary that happens to place all the good trades in IS.

```python
from ta_foundation.analysis.strategy_discovery.holdout import partition_trades

part = partition_trades(trades, is_frac=0.60, val_frac=0.20)
print(part.to_dict())
```

---

## Why Bonferroni and Not FDR / BH?

Bonferroni is the conservative choice and that is appropriate here. False
Discovery Rate methods (Benjamini-Hochberg) are designed for high-volume
settings (genomics, fMRI) where you expect many true positives among thousands
of tests. In trading, you are running tens to hundreds of tests, you want very
high specificity (almost no false positives), and a missed true edge is less
costly than a false one that you deploy capital against. Bonferroni with
`min_t_stat_abs=3.0` enforces both the alpha-corrected significance level and a
minimum effect size simultaneously.

---

## Common Mistakes

**Running the sweep before registering.**
The holdout boundary is set on registration. If you register after seeing
results, you may unconsciously choose a date range that excludes a bad period.
Register first, always.

**Registering "candle patterns" as one experiment.**
Each distinct signal should be a separate experiment. Testing bull_engulf,
bear_engulf, hammer, and shooting_star as one registration means N=1 in your
Bonferroni denominator when the true N is 4.

**Calling promote_strategy before IS/VAL gates all pass.**
Nothing prevents you from doing this technically, but the holdout result will
mean nothing if the IS/VAL foundation was not solid. Use IS/VAL as a filter,
not a formality.

**Reusing the holdout to tune parameters.**
If the holdout fails and you adjust parameters then run the holdout again, it is
no longer a holdout. It is just more IS data with an extra step. The one-shot
enforcement exists to prevent this from happening accidentally.

---

## File Reference

| File | Purpose |
|---|---|
| `src/ta_foundation/persistence/db.py` | `ExperimentRegistry` — DuckDB wrapper |
| `src/ta_foundation/cli/register_hypothesis.py` | Pre-registration CLI + `assert_registered()` |
| `src/ta_foundation/cli/promote_strategy.py` | One-shot holdout evaluation CLI |
| `src/ta_foundation/analysis/strategy_discovery/holdout.py` | `partition_trades`, `lock_holdout`, `filter_for_validation`, `run_holdout_evaluation` |
| `src/ta_foundation/analysis/strategy_discovery/validation.py` | `run_validation()` — all statistical gates |
| `src/ta_foundation/analysis/statistics/dsr.py` | `compute_dsr()`, `compute_dsr_gate()` |

## Web page
pip install flask
The web UI is ready to use: python -m ta_foundation.web.app --market-data <dir> --db-path experiments.duckdb --port 7734

python -m ta_foundation.web.app --market-data D:/MarketData --db-path experiments.duckdb --port 7734