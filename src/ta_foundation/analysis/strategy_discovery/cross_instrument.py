from __future__ import annotations

"""Cross-instrument robustness check for discovered strategies.

Same-instrument in-sample/out-of-sample validation is too weak. Two adjacent
slices of one instrument in the same market regime correlate even for an
overfit strategy: a 567-combo ORB parameter sweep showed Spearman +0.80
IS->OOS on NQ, yet the "best" candidate (PF 1.3-1.4 on NQ) was breakeven or
losing on ES/RTY/YM and not statistically significant on NQ itself
(t ~ 1.0). The IS->OOS persistence measured NQ's own character, not an edge.

A genuine structural edge must hold across *correlated but distinct*
instruments — that is far harder to overfit. This module pools a strategy's
per-trade P&L (in cost-adjusted ticks, so instruments with different dollar
values are comparable) across an instrument panel and tests whether the
pooled result is statistically distinguishable from zero.

The caller supplies per-instrument arrays of cost-adjusted tick P&L (one
value per trade); this module owns the aggregation-and-significance
methodology, which is the part the discovery pipeline was missing.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Sequence

import numpy as np


@dataclass(frozen=True)
class CrossInstrumentVerdict:
    """Result of a cross-instrument robustness evaluation."""

    is_robust_edge: bool
    pooled_trades: int
    pooled_profit_factor: float
    pooled_t_stat: float
    pooled_mean_ticks: float
    instruments_total: int
    instruments_profitable: int
    per_instrument: Dict[str, Dict[str, float]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_robust_edge": self.is_robust_edge,
            "pooled_trades": self.pooled_trades,
            "pooled_profit_factor": self.pooled_profit_factor,
            "pooled_t_stat": self.pooled_t_stat,
            "pooled_mean_ticks": self.pooled_mean_ticks,
            "instruments_total": self.instruments_total,
            "instruments_profitable": self.instruments_profitable,
            "per_instrument": self.per_instrument,
            "reasons": list(self.reasons),
        }


def _instrument_stats(pnl_ticks: Sequence[float]) -> Dict[str, float]:
    """PF, mean, t-stat and trade count for one instrument's trades."""
    arr = np.asarray([float(x) for x in pnl_ticks], dtype=float)
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "net_ticks": 0.0, "profit_factor": float("nan"),
                "mean_ticks": 0.0, "t_stat": 0.0}
    gross_profit = float(arr[arr > 0].sum())
    gross_loss = abs(float(arr[arr < 0].sum()))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    t = float(arr.mean() / (std / np.sqrt(n))) if std > 0 else 0.0
    return {"n": n, "net_ticks": float(arr.sum()), "profit_factor": pf,
            "mean_ticks": float(arr.mean()), "t_stat": t}


def evaluate_cross_instrument(
    per_instrument_pnl_ticks: Dict[str, Sequence[float]],
    *,
    min_pooled_t: float = 2.0,
    min_pooled_pf: float = 1.2,
    min_fraction_profitable: float = 0.75,
    min_pooled_trades: int = 150,
) -> CrossInstrumentVerdict:
    """Decide whether a strategy is a cross-instrument-robust genuine edge.

    Parameters
    ----------
    per_instrument_pnl_ticks
        ``{instrument_name: [per-trade cost-adjusted tick P&L, ...]}``. Tick
        units (not dollars) so instruments with different tick values pool
        cleanly; the caller must already have subtracted slippage/commission.
    min_pooled_t
        Minimum pooled t-statistic. ``2.0`` ~ 95% confidence the pooled edge
        is not zero — the gate the old same-instrument validation lacked.
    min_pooled_pf, min_fraction_profitable, min_pooled_trades
        Supporting gates: a meaningful profit factor, profitable on most of
        the panel (not carried by one instrument), and enough total trades.

    Returns
    -------
    CrossInstrumentVerdict — ``is_robust_edge`` is True only if every gate
    passes; ``reasons`` records each gate's pass/fail.
    """
    per: Dict[str, Dict[str, float]] = {
        name: _instrument_stats(pnl) for name, pnl in per_instrument_pnl_ticks.items()
    }
    n_total = len(per)
    n_profitable = sum(1 for s in per.values() if s["n"] > 0 and s["net_ticks"] > 0)

    pooled = np.concatenate(
        [np.asarray([float(x) for x in pnl], dtype=float)
         for pnl in per_instrument_pnl_ticks.values()]
    ) if per_instrument_pnl_ticks else np.array([], dtype=float)
    pooled = pooled[~np.isnan(pooled)]
    pooled_stats = _instrument_stats(pooled)

    frac_profitable = (n_profitable / n_total) if n_total else 0.0
    reasons: list[str] = []

    def gate(label: str, ok: bool) -> bool:
        reasons.append(f"{'PASS' if ok else 'FAIL'} {label}")
        return ok

    g_trades = gate(
        f"pooled_trades {pooled_stats['n']} >= {min_pooled_trades}",
        pooled_stats["n"] >= min_pooled_trades,
    )
    g_t = gate(
        f"pooled_t {pooled_stats['t_stat']:.2f} >= {min_pooled_t}",
        pooled_stats["t_stat"] >= min_pooled_t,
    )
    g_pf = gate(
        f"pooled_pf {pooled_stats['profit_factor']:.2f} >= {min_pooled_pf}",
        pooled_stats["profit_factor"] >= min_pooled_pf,
    )
    g_frac = gate(
        f"instruments_profitable {n_profitable}/{n_total} "
        f"(>= {min_fraction_profitable:.0%})",
        frac_profitable >= min_fraction_profitable,
    )

    return CrossInstrumentVerdict(
        is_robust_edge=bool(g_trades and g_t and g_pf and g_frac),
        pooled_trades=pooled_stats["n"],
        pooled_profit_factor=pooled_stats["profit_factor"],
        pooled_t_stat=pooled_stats["t_stat"],
        pooled_mean_ticks=pooled_stats["mean_ticks"],
        instruments_total=n_total,
        instruments_profitable=n_profitable,
        per_instrument=per,
        reasons=reasons,
    )
