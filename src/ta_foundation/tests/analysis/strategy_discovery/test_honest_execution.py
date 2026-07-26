from __future__ import annotations

import pandas as pd
import pytest

from ta_foundation.analysis.strategy_discovery.honest_execution import (
    apply_honest_execution,
)

COST_MODEL = {"commission_per_side": 2.09, "tick_value": 5.0, "slippage_ticks": 1}


def _trades(wins: int, win_gross: float, losses: int, loss_gross: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "profit": [win_gross] * wins + [loss_gross] * losses,
            "result": ["win"] * wins + ["loss"] * losses,
        }
    )


def test_solid_edge_survives_the_absolute_gate() -> None:
    trades = _trades(wins=24, win_gross=200.0, losses=16, loss_gross=-60.0)
    out = apply_honest_execution(trades, cost_model=COST_MODEL)
    assert out["passed"] is True
    assert out["honest"]["profit_factor"] > 1.10
    assert out["honest"]["expectancy"] > 0
    # The honest re-price is strictly worse than the optimistic baseline.
    assert out["honest"]["expectancy"] < out["baseline"]["expectancy"]
    assert 0 < out["edge_retained_pct"] < 100


def test_marginal_edge_positive_at_baseline_fails_honest_gate() -> None:
    # Optimistic pricing shows a (tiny) positive edge; honest fills flip it.
    trades = _trades(wins=20, win_gross=85.0, losses=20, loss_gross=-55.0)
    out = apply_honest_execution(trades, cost_model=COST_MODEL)
    assert out["baseline"]["expectancy"] > 0       # looks profitable optimistically
    assert out["honest"]["expectancy"] < 0         # ...but is not, honestly
    assert out["passed"] is False
    assert any("expectancy" in m for m in out["issues"])


def test_haircut_math_is_exact() -> None:
    # win: gross 100 - (entry 1 + win_exit 2) ticks * $5 - 2*$2.09 = 80.82
    # loss: gross -40 - (1 + stop 2)*$5 - 4.18 = -59.18
    # timeout: gross -30 - (1 + timeout 3)*$5 - 4.18 = -54.18
    trades = pd.DataFrame(
        {"profit": [100.0, -40.0, -30.0], "result": ["win", "loss", "timeout"]}
    )
    out = apply_honest_execution(
        trades, cost_model=COST_MODEL, options={"min_trades": 3}
    )
    assert out["honest"]["net_profit"] == pytest.approx(80.82 - 59.18 - 54.18)
    # baseline cost is a flat 2*2.09 + 2*1*5 = 14.18 per trade
    assert out["baseline"]["net_profit"] == pytest.approx(85.82 - 54.18 - 44.18)


def test_min_trades_floor_blocks_thin_samples() -> None:
    trades = _trades(wins=10, win_gross=300.0, losses=2, loss_gross=-50.0)
    out = apply_honest_execution(trades, cost_model=COST_MODEL)  # 12 < 30
    assert out["passed"] is False
    assert any("min_trades" in m for m in out["issues"])


def test_disabled_returns_unpassed() -> None:
    trades = _trades(wins=30, win_gross=200.0, losses=10, loss_gross=-40.0)
    out = apply_honest_execution(
        trades, cost_model=COST_MODEL, options={"enabled": False}
    )
    assert out["passed"] is False
    assert any("disabled" in m for m in out["issues"])


def test_missing_data_is_graceful() -> None:
    assert apply_honest_execution(None, cost_model=COST_MODEL)["passed"] is False
    no_profit = pd.DataFrame({"result": ["win", "loss"]})
    out = apply_honest_execution(no_profit, cost_model=COST_MODEL)
    assert out["passed"] is False
    assert any("profit" in m for m in out["issues"])


def test_result_column_inferred_from_pnl_sign_when_absent() -> None:
    trades = pd.DataFrame({"profit": [200.0] * 24 + [-60.0] * 16})
    out = apply_honest_execution(trades, cost_model=COST_MODEL)
    assert any("inferred" in m for m in out["issues"])
    assert out["n_trades"] == 40
    assert out["honest"]["expectancy"] is not None
