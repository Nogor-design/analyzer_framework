# Discovery Registry Extension Guide

**Purpose:** Extend the agentic discovery system with custom hypothesis types, validators, and scoring rules.  
**Audience:** Advanced users, strategy researchers, developers extending discovery.  
**Complexity:** High (requires understanding of discovery pipeline).

---

## Overview

The discovery registry is the extensibility layer for the agentic research system. It controls:
- Which hypothesis types are discoverable (candle patterns, entry signals, regime transitions, etc.)
- How hypotheses are validated (walk-forward, cross-validation, robustness tests)
- How validated hypotheses are scored and ranked
- Which hypotheses get promoted to strategy candidates

This guide explains how to register custom components.

---

## Core Discovery Architecture

```
SQLite Research Ledger
└─ Families (strategy types)
└─ Hypotheses (candidate ideas per family)
   └─ Runs (test executions)
   └─ Candidates (validated hypotheses)
   └─ Decisions (human-in-the-loop approvals)

Discovery Agents (Phases A-D)
└─ Phase A: Initialize ledger
└─ Phase B: Triage + post-mortem agents
└─ Phase C: Hypothesis author + sweep operator
└─ Phase D: Shadow health + narrative scribe

Registry
└─ Hypothesis types (available ideas)
└─ Validators (test logic)
└─ Scorers (ranking logic)
└─ Promoters (candidate selection rules)
```

---

## Hypothesis Type Registration

A **hypothesis type** is a discoverable idea category (e.g., "Candle Pattern Entry", "Support Level Bounce").

### Define a Custom Hypothesis Type

```python
# File: src/ta_foundation/analysis/strategy_discovery/hypothesis_types.py

from dataclasses import dataclass
from typing import Any, dict

@dataclass
class MyCustomHypothesis:
    """Custom hypothesis: volatility breakout on volume surge."""
    
    name: str = "volatility_breakout_on_volume"
    description: str = "Entry when ATR expands 50% + volume > 1.5x avg"
    family: str = "breakout"
    
    # Parameters to sweep
    atr_expansion_pct: float = 0.50         # 50% ATR expansion
    volume_multiple: float = 1.5             # 1.5x volume
    lookback_atr: int = 20                   # ATR lookback period
    lookback_volume: int = 20                # Volume lookback period
    
    # Optional metadata
    tags: list[str] = None
    notes: str = "Volatility surge typically precedes breakouts"
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = ["volatility", "breakout", "volume"]
```

### Register the Hypothesis Type

```python
# File: src/ta_foundation/analysis/strategy_discovery/registry.py

from ta_foundation.analysis.strategy_discovery.hypothesis_types import MyCustomHypothesis
from ta_foundation.analysis.strategy_discovery.registry import HypothesisRegistry

def register_custom_hypothesis_types():
    """Register all custom hypothesis types."""
    registry = HypothesisRegistry()
    
    # Register the custom type
    registry.register(
        hypothesis_type=MyCustomHypothesis,
        category="entry_signals",
        enabled=True,
        priority=0.8,  # 0-1 priority (higher = more likely to be discovered)
    )
    
    return registry
```

### Reference in Discovery

In the discovery phase orchestrator:
```python
# File: src/ta_foundation/analysis/strategy_discovery/orchestrator.py

from ta_foundation.analysis.strategy_discovery.registry import register_custom_hypothesis_types

registry = register_custom_hypothesis_types()

# Discovery will now include MyCustomHypothesis
for hyp_type in registry.hypothesis_types:
    print(f"Discoverable: {hyp_type.name}")
```

---

## Validator Registration

A **validator** tests whether a hypothesis actually works. Validators:
- Load historical data
- Test the hypothesis (simulate trades)
- Compute metrics (Sharpe, win rate, etc.)
- Return pass/fail + confidence score

### Define a Custom Validator

```python
# File: src/ta_foundation/analysis/strategy_discovery/validators/volatility_validator.py

from dataclasses import dataclass
from typing import Any, dict
import pandas as pd
import numpy as np

@dataclass
class VolatilityBreakoutValidator:
    """Validates volatility breakout hypothesis."""
    
    name: str = "volatility_breakout_validator"
    
    def validate(
        self,
        hypothesis: dict[str, Any],
        bars: pd.DataFrame,
        trades: pd.DataFrame,
    ) -> dict[str, Any]:
        """
        Test hypothesis against historical data.
        
        Args:
            hypothesis: Hypothesis parameters (atr_expansion_pct, volume_multiple, etc.)
            bars: OHLCV bars with columns: open, high, low, close, volume
            trades: Backtest trades (for comparison)
        
        Returns:
            {
                "is_valid": True/False,
                "confidence": 0-1,
                "metrics": {"sharpe": 1.2, "win_rate": 0.55, ...},
                "issues": ["List of validation failures"],
            }
        """
        
        # Extract parameters
        atr_exp_pct = hypothesis.get("atr_expansion_pct", 0.50)
        vol_mult = hypothesis.get("volume_multiple", 1.5)
        lookback_atr = hypothesis.get("lookback_atr", 20)
        lookback_vol = hypothesis.get("lookback_volume", 20)
        
        issues = []
        
        # Step 1: Compute features
        bars["atr"] = self._compute_atr(bars, lookback_atr)
        bars["atr_prev"] = bars["atr"].shift(1)
        bars["volume_avg"] = bars["volume"].rolling(lookback_vol).mean()
        
        # Step 2: Detect signal (ATR expansion + volume surge)
        bars["signal"] = (
            (bars["atr"] > bars["atr_prev"] * (1 + atr_exp_pct)) &
            (bars["volume"] > bars["volume_avg"] * vol_mult)
        )
        
        signal_count = bars["signal"].sum()
        if signal_count < 5:
            issues.append(f"Only {signal_count} signals found (need >= 5)")
        
        # Step 3: Simulate trades from signals
        simulated_trades = self._simulate_trades(bars, hypothesis)
        
        if len(simulated_trades) == 0:
            return {
                "is_valid": False,
                "confidence": 0.0,
                "metrics": {},
                "issues": issues + ["No trades generated"],
            }
        
        # Step 4: Compute metrics
        metrics = self._compute_metrics(simulated_trades)
        
        # Step 5: Validate metrics
        if metrics.get("sharpe", 0) < 0.5:
            issues.append(f"Sharpe too low: {metrics['sharpe']:.2f}")
        
        if metrics.get("win_rate", 0) < 0.45:
            issues.append(f"Win rate too low: {metrics['win_rate']:.1%}")
        
        is_valid = len(issues) == 0
        
        return {
            "is_valid": is_valid,
            "confidence": metrics.get("sharpe", 0) / 2.0,  # Normalize to 0-1
            "metrics": metrics,
            "issues": issues,
        }
    
    def _compute_atr(self, bars: pd.DataFrame, period: int) -> pd.Series:
        """Compute Average True Range."""
        high_low = bars["high"] - bars["low"]
        high_close = (bars["high"] - bars["close"].shift()).abs()
        low_close = (bars["low"] - bars["close"].shift()).abs()
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()
    
    def _simulate_trades(
        self,
        bars: pd.DataFrame,
        hypothesis: dict[str, Any],
    ) -> list[dict[str, float]]:
        """Simulate trades from signals."""
        trades = []
        
        for idx in range(1, len(bars)):
            if bars.iloc[idx]["signal"]:
                # Entry on signal bar
                entry_price = bars.iloc[idx]["open"]
                
                # Exit: next bar close
                exit_idx = idx + 1
                if exit_idx < len(bars):
                    exit_price = bars.iloc[exit_idx]["close"]
                    pnl = exit_price - entry_price
                    
                    trades.append({
                        "entry_bar": idx,
                        "exit_bar": exit_idx,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                    })
        
        return trades
    
    def _compute_metrics(self, trades: list[dict[str, float]]) -> dict[str, float]:
        """Compute trade metrics."""
        if not trades:
            return {}
        
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) if pnls else 0
        
        # Simple Sharpe approximation
        pnl_series = pd.Series(pnls)
        sharpe = pnl_series.mean() / pnl_series.std() if pnl_series.std() > 0 else 0
        
        return {
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "total_pnl": float(total_pnl),
            "win_rate": float(win_rate),
            "sharpe": float(sharpe),
            "avg_win": float(np.mean(wins)) if wins else 0,
            "avg_loss": float(np.mean(losses)) if losses else 0,
        }
```

### Register the Validator

```python
# File: src/ta_foundation/analysis/strategy_discovery/registry.py

from ta_foundation.analysis.strategy_discovery.validators.volatility_validator import VolatilityBreakoutValidator

def register_custom_validators():
    """Register all custom validators."""
    registry = ValidatorRegistry()
    
    registry.register(
        validator=VolatilityBreakoutValidator(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
    
    return registry
```

---

## Scorer Registration

A **scorer** ranks validated hypotheses. Scorers:
- Take hypothesis + validation results
- Return a score (0-1)
- Influence which hypotheses become strategy candidates

### Define a Custom Scorer

```python
# File: src/ta_foundation/analysis/strategy_discovery/scorers/custom_scorer.py

from typing import Any, dict

class VolatilityScorer:
    """Score volatility breakout hypotheses."""
    
    def score(
        self,
        hypothesis: dict[str, Any],
        validation_result: dict[str, Any],
    ) -> float:
        """
        Score hypothesis (0-1).
        
        Higher scores = better candidates for strategy conversion.
        """
        
        if not validation_result.get("is_valid"):
            return 0.0
        
        metrics = validation_result.get("metrics", {})
        
        # Weight factors
        sharpe_weight = 0.40
        win_rate_weight = 0.35
        trade_count_weight = 0.25
        
        # Normalize metrics to 0-1
        sharpe_score = min(metrics.get("sharpe", 0) / 2.0, 1.0)  # 2.0 = perfect
        win_rate_score = metrics.get("win_rate", 0)  # Already 0-1
        trade_count_score = min(metrics.get("total_trades", 0) / 50, 1.0)  # 50+ = perfect
        
        # Composite score
        composite = (
            sharpe_score * sharpe_weight +
            win_rate_score * win_rate_weight +
            trade_count_score * trade_count_weight
        )
        
        return float(composite)
```

### Register the Scorer

```python
# File: src/ta_foundation/analysis/strategy_discovery/registry.py

from ta_foundation.analysis.strategy_discovery.scorers.custom_scorer import VolatilityScorer

def register_custom_scorers():
    """Register all custom scorers."""
    registry = ScorerRegistry()
    
    registry.register(
        scorer=VolatilityScorer(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
    
    return registry
```

---

## Promoter Registration

A **promoter** decides which validated hypotheses become strategy candidates. Promoters:
- Take hypothesis + validation + score
- Return True/False (promote or not)
- Can implement complex selection logic (e.g., "only promote if Sharpe > 1.0 AND win rate > 55%")

### Define a Custom Promoter

```python
# File: src/ta_foundation/analysis/strategy_discovery/promoters/volatility_promoter.py

from typing import Any, dict

class VolatilityPromoter:
    """Decide whether to promote volatility breakout hypotheses."""
    
    def promote(
        self,
        hypothesis: dict[str, Any],
        validation_result: dict[str, Any],
        score: float,
    ) -> tuple[bool, str]:
        """
        Decide whether to promote hypothesis.
        
        Returns:
            (should_promote, reason)
        """
        
        if not validation_result.get("is_valid"):
            return False, "Validation failed"
        
        metrics = validation_result.get("metrics", {})
        
        # Promotion criteria
        min_sharpe = 0.8
        min_win_rate = 0.50
        min_trades = 10
        
        if metrics.get("sharpe", 0) < min_sharpe:
            return False, f"Sharpe {metrics['sharpe']:.2f} < {min_sharpe}"
        
        if metrics.get("win_rate", 0) < min_win_rate:
            return False, f"Win rate {metrics['win_rate']:.1%} < {min_win_rate:.1%}"
        
        if metrics.get("total_trades", 0) < min_trades:
            return False, f"Only {metrics['total_trades']} trades < {min_trades}"
        
        # All criteria met
        return True, "All criteria met"
```

### Register the Promoter

```python
# File: src/ta_foundation/analysis/strategy_discovery/registry.py

from ta_foundation.analysis.strategy_discovery.promoters.volatility_promoter import VolatilityPromoter

def register_custom_promoters():
    """Register all custom promoters."""
    registry = PromoterRegistry()
    
    registry.register(
        promoter=VolatilityPromoter(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
    
    return registry
```

---

## End-to-End: Register Everything

```python
# File: src/ta_foundation/analysis/strategy_discovery/registry.py

from ta_foundation.analysis.strategy_discovery.hypothesis_types import MyCustomHypothesis
from ta_foundation.analysis.strategy_discovery.validators.volatility_validator import VolatilityBreakoutValidator
from ta_foundation.analysis.strategy_discovery.scorers.custom_scorer import VolatilityScorer
from ta_foundation.analysis.strategy_discovery.promoters.volatility_promoter import VolatilityPromoter

def register_all_custom_components():
    """Register all custom hypothesis components."""
    
    # 1. Register hypothesis type
    HypothesisRegistry().register(
        hypothesis_type=MyCustomHypothesis,
        category="entry_signals",
        enabled=True,
        priority=0.8,
    )
    
    # 2. Register validator
    ValidatorRegistry().register(
        validator=VolatilityBreakoutValidator(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
    
    # 3. Register scorer
    ScorerRegistry().register(
        scorer=VolatilityScorer(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
    
    # 4. Register promoter
    PromoterRegistry().register(
        promoter=VolatilityPromoter(),
        hypothesis_type="volatility_breakout_on_volume",
        enabled=True,
    )
```

---

## Testing Custom Components

```python
# File: src/ta_foundation/tests/strategy_discovery/test_volatility_components.py

import pytest
import pandas as pd
from ta_foundation.analysis.strategy_discovery.validators.volatility_validator import VolatilityBreakoutValidator
from ta_foundation.analysis.strategy_discovery.scorers.custom_scorer import VolatilityScorer
from ta_foundation.analysis.strategy_discovery.promoters.volatility_promoter import VolatilityPromoter

@pytest.fixture
def sample_hypothesis():
    """Sample volatility breakout hypothesis."""
    return {
        "atr_expansion_pct": 0.50,
        "volume_multiple": 1.5,
        "lookback_atr": 20,
        "lookback_volume": 20,
    }

@pytest.fixture
def sample_bars():
    """Sample OHLCV bars."""
    dates = pd.date_range("2024-01-01", periods=100, freq="h", tz="America/Denver")
    return pd.DataFrame({
        "timestamp": dates,
        "open": range(5000, 5100),
        "high": range(5002, 5102),
        "low": range(4998, 5098),
        "close": range(5001, 5101),
        "volume": [10000 + i*100 for i in range(100)],
    })

def test_volatility_validator(sample_hypothesis, sample_bars):
    """Test validator produces valid results."""
    validator = VolatilityBreakoutValidator()
    result = validator.validate(sample_hypothesis, sample_bars, None)
    
    assert "is_valid" in result
    assert "metrics" in result
    assert "issues" in result

def test_volatility_scorer(sample_hypothesis):
    """Test scorer produces 0-1 score."""
    scorer = VolatilityScorer()
    
    validation_result = {
        "is_valid": True,
        "metrics": {
            "sharpe": 1.2,
            "win_rate": 0.55,
            "total_trades": 20,
        }
    }
    
    score = scorer.score(sample_hypothesis, validation_result)
    
    assert 0 <= score <= 1

def test_volatility_promoter(sample_hypothesis):
    """Test promoter makes binary promote/reject decision."""
    promoter = VolatilityPromoter()
    
    validation_result = {
        "is_valid": True,
        "metrics": {
            "sharpe": 1.2,
            "win_rate": 0.60,
            "total_trades": 20,
        }
    }
    
    should_promote, reason = promoter.promote(sample_hypothesis, validation_result, score=0.75)
    
    assert isinstance(should_promote, bool)
    assert isinstance(reason, str)
```

---

## Integration with Discovery Pipeline

Once registered, custom components are automatically used:

```bash
# Run discovery with custom components
python -m ta_foundation.cli.main \
  --input ./backtest_exports \
  --output ./reports \
  --discovery-phases A B C D
```

The discovery system will:
1. **Phase A:** Initialize ledger with custom hypothesis types
2. **Phase B:** Triage agents test hypotheses using custom validators
3. **Phase C:** Author agents sweep parameters; operators validate using custom validators
4. **Phase D:** Promoters use custom scorers/promoters to select strategy candidates

---

## Registry Interface Reference

### HypothesisRegistry

```python
class HypothesisRegistry:
    def register(
        self,
        hypothesis_type: Type,
        category: str,
        enabled: bool = True,
        priority: float = 0.5,
    ) -> None:
        """Register a new hypothesis type."""

    def get_by_name(self, name: str) -> Optional[Type]:
        """Get hypothesis type by name."""

    def list_enabled(self, category: str = None) -> list[Type]:
        """List enabled hypothesis types."""
```

### ValidatorRegistry

```python
class ValidatorRegistry:
    def register(
        self,
        validator: Any,
        hypothesis_type: str,
        enabled: bool = True,
    ) -> None:
        """Register a validator for a hypothesis type."""

    def get(self, hypothesis_type: str) -> Optional[Any]:
        """Get validator for hypothesis type."""
```

### ScorerRegistry

```python
class ScorerRegistry:
    def register(
        self,
        scorer: Any,
        hypothesis_type: str,
        enabled: bool = True,
    ) -> None:
        """Register a scorer."""

    def score(
        self,
        hypothesis_type: str,
        hypothesis: dict,
        validation_result: dict,
    ) -> float:
        """Score a hypothesis."""
```

### PromoterRegistry

```python
class PromoterRegistry:
    def register(
        self,
        promoter: Any,
        hypothesis_type: str,
        enabled: bool = True,
    ) -> None:
        """Register a promoter."""

    def promote(
        self,
        hypothesis_type: str,
        hypothesis: dict,
        validation_result: dict,
        score: float,
    ) -> tuple[bool, str]:
        """Decide whether to promote hypothesis."""
```

---

## Common Mistakes

**❌ Don't:** Hardcode logic in validators  
**✅ Do:** Make validators configurable (parameters in hypothesis)

**❌ Don't:** Return scores < 0 or > 1  
**✅ Do:** Normalize scores to [0, 1]

**❌ Don't:** Ignore edge cases (empty data, single trade, etc.)  
**✅ Do:** Return clear error messages for invalid cases

**❌ Don't:** Block discovery with complex validators  
**✅ Do:** Make validators fast (< 1s for typical hypothesis)

---

## Performance Considerations

- **Validator speed:** < 1 second per hypothesis (1000+ hypotheses tested daily)
- **Scorer speed:** < 10ms (called for every validated hypothesis)
- **Promoter speed:** < 10ms (called for every scored hypothesis)

If your validator is slow, parallelize hypothesis batches:
```python
from multiprocessing import Pool

def parallel_validate(hypotheses, bars):
    with Pool(processes=4) as pool:
        results = pool.starmap(
            validator.validate,
            [(h, bars, None) for h in hypotheses],
        )
    return results
```

---

## Sign-Off

**Feature:** Discovery Registry Extension  
**Status:** Stable (discovery pipeline is production)  
**Last updated:** May 24, 2026  
**Next:** Add hypothesis type versioning (track hypothesis evolution over time)

---

Last updated: May 24, 2026
