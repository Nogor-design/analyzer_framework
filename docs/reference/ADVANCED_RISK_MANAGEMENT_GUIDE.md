# Advanced Risk Management (Gap 4) — Implementation Guide

**Status:** ✅ IMPLEMENTED  
**Date:** May 27, 2026  
**Lines of Code:** 700+ (implementation + tests)  
**Tests Passed:** ✅ 22/22

---

## Overview

Gap 4 implements sophisticated position sizing and risk management techniques for strategy discovery:

1. **Volatility-Based Sizing (VBS)** — Size contracts based on ATR and account risk
2. **Kelly Criterion** — Optimal fractional f based on win rate and payoff ratio
3. **Volatility-Adjusted Outcomes** — Dynamically adjust TP/SL based on regime ATR
4. **Conservative Scaling** — Apply safety factor to Kelly (e.g., use 25% of Kelly)

This enables discovery to not just optimize entries/exits but also risk management parameters.

---

## Key Components

### 1. VolatilitySizing Class

Calculates position size using ATR-based risk management.

**Usage:**
```python
from ta_foundation.analysis.entry_strategies.advanced_risk_management import VolatilitySizing

sizing = VolatilitySizing(
    account_size=100000,        # $100k account
    target_risk_pct=1.0,        # Risk 1% per trade
)

result = sizing.calculate_size(
    entry_price=4500.0,
    stop_price=4450.0,
    atr_value=30.0,
)
# → {contracts: 4, risk_dollars: 1000, ...}
```

---

### 2. KellyCriterion Class

Calculates Kelly Criterion for optimal position sizing.

**Formula:** `f = (win_rate × avg_win - loss_rate × avg_loss) / avg_win`

**Usage:**
```python
kelly = KellyCriterion()
result = kelly.calculate(
    win_rate=0.55,              # 55% win rate
    avg_win_ticks=100.0,
    avg_loss_ticks=50.0,        # 2:1 payoff
    max_kelly_fraction=0.25     # Use 25% of Kelly (conservative)
)
# → {kelly_f: 0.325, kelly_f_conservative: 0.081, ...}
```

---

### 3. compute_kelly_contracts()

Converts Kelly fraction to contract size.

**Usage:**
```python
result = compute_kelly_contracts(
    kelly_fraction=0.08,
    account_size=100000,
    entry_price=4500.0,
    stop_price=4450.0,
)
# → {contracts: 2, risk_amount_dollars: 8000, ...}
```

---

### 4. volatility_adjusted_outcome_config()

Dynamically adjust TP/SL based on ATR regime.

**Usage:**
```python
adjusted = volatility_adjusted_outcome_config(
    base_config,
    atr_value=45.0,
    current_regime_volatility=30.0,  # Scales by 1.5x
)
```

---

## YAML Configuration

```yaml
outcome:
  ticks:
    enabled: true
    take_profit: [30, 60, 100]
    stop: [20, 30, 40]

  volatility_sizing:
    enabled: true
    target_risk_pct: 1.0
    account_size: 100000
    max_contracts: 20

  kelly_criterion:
    enabled: true
    max_kelly_fraction: 0.25
    min_kelly_fraction: 0.01

  volatility_regime:
    bull_atr: 25.0
    bear_atr: 35.0
    flat_atr: 15.0
```

---

## Testing

**Status:** ✅ All 22 tests pass

```bash
pytest src/ta_foundation/tests/analysis/entry_strategies/test_arm_gap4.py -v
```

**Coverage:**
- VolatilitySizing: 5 tests (sizing, scaling, invalid inputs, ATR-based, caps)
- KellyCriterion: 8 tests (edge cases, scaling, equity curves)
- Contracts: 3 tests (basic, zero, cap)
- Config Adjustment: 4 tests (high vol, low vol, no regime, VIX)
- Integration: 2 tests (full workflow, comparison)

---

## Examples

### Example 1: Fixed Volatility Sizing

Risk 1% of $100k account per trade:

```python
sizing = VolatilitySizing(account_size=100000, target_risk_pct=1.0)
size = sizing.calculate_size(4500, 4450, 30.0)
# → 4 contracts at 1% risk
```

### Example 2: Kelly-Based Sizing

For strategy with 55% WR, 2:1 payoff ratio:

```python
kelly = KellyCriterion()
result = kelly.calculate(0.55, 100, 50, max_kelly_fraction=0.25)
contracts = compute_kelly_contracts(
    result["kelly_f_conservative"],
    100000, 4500, 4450
)
# → Use 8.1% of Kelly = 2 contracts
```

### Example 3: Regime-Aware Discovery

```python
regime_atr = config["volatility_regime"]["bull_atr"]  # 25.0
current_atr = bars["atr"].iloc[-1]  # 35.0

adjusted = volatility_adjusted_outcome_config(
    config["outcome"],
    atr_value=current_atr,
    current_regime_volatility=regime_atr
)
# Widens TP/SL by 1.4x in high vol environment
```

---

## Impact

| Metric | Improvement |
|--------|-------------|
| Account risk management | Dynamically adapts to volatility |
| Kelly utilization | Fully automated (no guessing) |
| Overfitting | Reduced (conservative sizing) |
| Robustness | Regime-aware adjustments |

---

## Files Created

- `src/ta_foundation/analysis/entry_strategies/advanced_risk_management.py` (700+ lines)
- `src/ta_foundation/tests/analysis/entry_strategies/test_arm_gap4.py` (22 tests)
- `ADVANCED_RISK_MANAGEMENT_GUIDE.md` (this file)

---

## Next Steps

- Integrate into unified discovery runner
- Wire into outcome simulator
- Test with real discovery runs
- Gap 5: Multi-symbol discovery
- Gap 6: Complete regime integration
