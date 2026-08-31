"""The per-day trade-budget rules shared by the deployment-matrix capability.

These used to live as three divergent inline fallbacks. Pin the semantics so a
future edit cannot quietly reintroduce the optimistic variant.
"""

from __future__ import annotations

from ta_foundation.web.optimizer_trade_budget import (
    compute_effective_trades,
    compute_true_max_loss,
)


def test_guardrails_can_cap_a_multi_trade_template_to_one():
    """MaxTrades is not the only ceiling: a one-bracket LossStop is single."""
    assert compute_effective_trades(
        max_trades=2, profit_stop=500, loss_stop=500,
        per_trade_profit=500, per_trade_loss=500,
    ) == 1


def test_roomy_stops_leave_max_trades_in_charge():
    assert compute_effective_trades(
        max_trades=5, profit_stop=9000, loss_stop=9000,
        per_trade_profit=500, per_trade_loss=500,
    ) == 5


def test_effective_trades_never_returns_zero():
    assert compute_effective_trades(
        max_trades=0, profit_stop=0, loss_stop=0,
        per_trade_profit=0, per_trade_loss=0,
    ) == 1


def test_worst_case_day_overshoots_the_loss_stop():
    """LossStop halts new entries; it does not cap the loss at LossStop.

    Sitting at -499 and taking one more full $500 stop ends the day at -999.
    Reporting a flat 500 here understates real prop-firm exposure by ~2x.
    """
    assert compute_true_max_loss(
        per_trade_max_loss=500.0, max_trades=3, loss_stop=500.0,
    ) == 999.0


def test_worst_case_is_still_bounded_by_max_trades():
    """Two trades cannot lose more than two full stops, whatever the guardrail."""
    assert compute_true_max_loss(
        per_trade_max_loss=100.0, max_trades=2, loss_stop=10_000.0,
    ) == 200.0


def test_no_loss_stop_means_max_trades_full_stops():
    assert compute_true_max_loss(
        per_trade_max_loss=250.0, max_trades=4, loss_stop=0.0,
    ) == 1000.0


def test_worst_case_never_understates_the_loss_stop():
    """Regression guard: the old fallback returned min(loss_stop, ...)."""
    for loss_stop in (100.0, 500.0, 1000.0, 2500.0):
        worst = compute_true_max_loss(
            per_trade_max_loss=500.0, max_trades=10, loss_stop=loss_stop,
        )
        assert worst >= loss_stop, (loss_stop, worst)
