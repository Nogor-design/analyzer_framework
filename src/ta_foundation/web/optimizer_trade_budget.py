from __future__ import annotations

"""Per-day trade-budget maths shared by the deployment-matrix capability.

The external ``template_naming`` package owns these rules. It is an optional
dependency -- it lives outside this repository and is absent on most checkouts
-- so every consumer here needs a local answer when the import fails.

Before this module there were three different local answers:

* ``optimizer_deployment_matrix.classify_effective`` gave up entirely and used
  a raw-cap rule (``MaxTrades == 1``), which labels almost everything "multi";
* ``optimizer_template_quality_features`` used ``min(loss_stop, ...)``, which
  **understates** the worst-case daily loss -- the dangerous direction for a
  prop-firm guardrail;
* ``optimizer_template_naming_fallback`` implemented the documented rule
  correctly, but nothing else used it.

They now share one implementation, which mirrors the documented naming-guide
rules exactly as the naming fallback already did.
"""

import math


def compute_effective_trades(
    *,
    max_trades: int,
    profit_stop: float,
    loss_stop: float,
    per_trade_profit: float,
    per_trade_loss: float,
) -> int:
    """How many trades a day can actually reach, given the daily guardrails.

    ``MaxTrades`` is only one of three ceilings: hitting ``ProfitStop`` or
    ``LossStop`` ends the day too. A template allowed five trades whose stops
    are one bracket wide is a single-trade template in practice, and must be
    classified as one.
    """

    counts = [max(1, int(max_trades))]
    if profit_stop > 0 and per_trade_profit > 0:
        counts.append(max(1, math.ceil(profit_stop / per_trade_profit)))
    if loss_stop > 0 and per_trade_loss > 0:
        counts.append(max(1, math.ceil(loss_stop / per_trade_loss)))
    return min(counts)


def compute_true_max_loss(
    *,
    per_trade_max_loss: float,
    max_trades: int,
    loss_stop: float,
) -> float:
    """Worst-case daily loss, including the overshoot past ``LossStop``.

    ``LossStop`` does not cap the day's loss at ``LossStop``: it stops *new
    entries* once the day is already down that far. So the worst realistic day
    sits just under the stop and then takes one more full-stop trade, ending at
    ``loss_stop - 1 + per_trade_max_loss``. Reporting a flat ``loss_stop``
    understates the real drawdown -- for a $500 stop with a $500 bracket the
    true exposure is $999, nearly double.

    Still bounded by ``max_trades`` full stops, which is the most that can be
    lost regardless of where the guardrail sits.
    """

    cap = per_trade_max_loss * max(1, int(max_trades))
    if loss_stop <= 0:
        return cap
    return min(cap, max(0.0, loss_stop - 1.0) + per_trade_max_loss)
