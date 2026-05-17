from __future__ import annotations

"""Tests for the bootstrap robustness check."""

import math
from pathlib import Path

import pytest

from ta_foundation.optimization.robustness import (
    RobustnessError,
    bootstrap_trades_csv,
    parameter_neighborhood_check,
    walk_forward_validation,
)


def _write_trades_csv(path: Path, profits: list[float]) -> None:
    header = (
        "Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,"
        "Entry time,Exit time,Entry name,Exit name,Profit,Cum. net profit,Commission,"
        "Clearing Fee,Exchange Fee,IP Fee,NFA Fee,MAE,MFE,ETD,Bars,\n"
    )
    body_lines: list[str] = []
    cum = 0.0
    for i, p in enumerate(profits):
        cum += p
        profit_str = f"(${abs(p):.2f})" if p < 0 else f"${p:.2f}"
        cum_str = f"(${abs(cum):.2f})" if cum < 0 else f"${cum:.2f}"
        body_lines.append(
            f"{i},NQ 06-26,Backtest,Strat,Long,1,100,101,"
            f"4/15/2026 9:00:00 AM,4/15/2026 10:00:00 AM,Buy,Profit target,"
            f"{profit_str},{cum_str},$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,60,"
        )
    path.write_text(header + "\n".join(body_lines) + "\n", encoding="utf-8")


def test_bootstrap_reports_observed_stats(tmp_path: Path):
    profits = [100, 100, 100, -50, 100, -50, 100, -50, 100, -50]  # net 400, PF 5, DD 50
    p = tmp_path / "Trades.csv"
    _write_trades_csv(p, profits)

    result = bootstrap_trades_csv(p, run_id="T", samples=200, seed=1)
    assert result.run_id == "T"
    assert result.trade_count == 10
    assert result.bootstrap_samples == 200
    # Profits: 6 wins of $100 + 4 losses of $50 -> net 400, PF = 600/200 = 3.0
    assert result.net_profit.observed == pytest.approx(400.0)
    assert result.profit_factor.observed == pytest.approx(3.0)
    # Observed max drawdown: from the equity curve [100,200,300,250,350,300,400,350,450,400]
    # the largest drawdown from any peak is 50.
    assert result.max_drawdown.observed == pytest.approx(50.0)


def test_bootstrap_distribution_brackets_observed(tmp_path: Path):
    """The bootstrap p05..p95 of net profit should bracket the observed
    value for a symmetric/balanced trade set."""
    profits = [50, -30, 50, -30, 50, -30, 50, -30, 50, -30, 50, -30]
    p = tmp_path / "Trades.csv"
    _write_trades_csv(p, profits)

    result = bootstrap_trades_csv(p, samples=500, seed=7)
    obs = result.net_profit.observed
    lo = result.net_profit.bootstrap_p05
    hi = result.net_profit.bootstrap_p95
    assert lo is not None and hi is not None
    assert lo <= obs <= hi, f"observed {obs} not in [{lo}, {hi}]"


def test_bootstrap_pf_p_value_near_half_for_random_orderings(tmp_path: Path):
    """A trade set where the observed PF is near the median of all
    permutations should have p ~ 0.5."""
    profits = [100, -50, 100, -50, 100, -50, 100, -50, 100, -50, 100, -50]
    p = tmp_path / "Trades.csv"
    _write_trades_csv(p, profits)

    result = bootstrap_trades_csv(p, samples=1000, seed=11)
    p_val = result.profit_factor.p_at_or_above_observed
    assert p_val is not None
    assert 0.05 < p_val < 0.95, f"PF p-value {p_val} suspicious for a balanced symmetric set"


def test_bootstrap_handles_empty_trades(tmp_path: Path):
    p = tmp_path / "Trades.csv"
    _write_trades_csv(p, [])
    result = bootstrap_trades_csv(p, samples=100, seed=1)
    assert result.trade_count == 0
    assert result.profit_factor.observed is None
    assert any("no parseable profit" in n.lower() for n in result.notes)


def test_bootstrap_raises_when_file_missing(tmp_path: Path):
    with pytest.raises(RobustnessError):
        bootstrap_trades_csv(tmp_path / "does_not_exist.csv")


def test_bootstrap_small_trade_count_adds_caveat_note(tmp_path: Path):
    profits = [100, -50, 100]  # 3 trades
    p = tmp_path / "Trades.csv"
    _write_trades_csv(p, profits)
    result = bootstrap_trades_csv(p, samples=100, seed=1)
    assert result.trade_count == 3
    assert any("Trade count is 3" in n for n in result.notes)


def test_walk_forward_and_neighborhood_stubs_raise():
    with pytest.raises(NotImplementedError):
        walk_forward_validation()
    with pytest.raises(NotImplementedError):
        parameter_neighborhood_check()
