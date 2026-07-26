"""Daily-lineup selection — core data model + daily-window metrics.

A ``Candidate`` is one deployable template (a deployment-matrix cell) with its
realised per-calendar-day P&L. Everything in the selector spine operates on
``Candidate`` objects, NOT on NinjaTrader files, so the harness is pure and
unit-testable; ``loader.py`` is the only piece that touches real session output.

All ranking metrics are computed over an explicit *window of days* so the replay
harness can enforce a strict train/test split (no future data leaks into a pick).
See ``docs/designs/daily_lineup_selector.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class Candidate:
    """One deployable template and its realised daily P&L.

    ``daily_pnl`` maps a calendar day -> realised $ for that template on that day
    (absent day == the template took no trades that day == flat 0.0). ``slice_key``
    is the lineup bucket the template competes in (e.g. the session time-slice);
    the daily lineup picks per slice. ``metrics``/``features`` are static
    full-window descriptors (PF, net, trades, regime fit) carried for reference —
    the replay must NOT rank on full-window metrics (that leaks test days).
    """

    template_id: str
    slice_key: str
    daily_pnl: dict[date, float]
    metrics: dict[str, Any] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectionContext:
    """What a selector may look at to make a pick for ``test_day``.

    ``train_days`` are strictly before ``test_day`` (the harness guarantees it).
    ``regime_for_test_day`` is the classified market regime for the upcoming
    session; ``regime_by_day`` is the full history for regime-conditioned ranking.
    """

    train_days: list[date]
    test_day: date
    regime_for_test_day: Optional[str] = None
    regime_by_day: dict[date, str] = field(default_factory=dict)


def window_pnls(cand: Candidate, days: Sequence[date]) -> list[float]:
    """Realised daily P&L over ``days``, 0.0 where the template didn't trade."""
    return [float(cand.daily_pnl.get(d, 0.0)) for d in days]


def daily_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    """Summary stats for a daily-P&L series.

    ``daily_pf`` is the *daily* profit factor (sum of up-days / |sum of down-days|),
    a leakage-safe proxy for trade PF when only daily granularity is available.
    ``max_drawdown`` is the deepest peak-to-trough of cumulative P&L (<= 0) — the
    survival signal the design weights heavily for prop accounts.
    """
    n = len(pnls)
    if n == 0:
        return {
            "n_days": 0, "net": 0.0, "mean_daily": 0.0, "daily_pf": None,
            "worst_day": 0.0, "max_drawdown": 0.0, "n_active_days": 0,
        }
    net = float(sum(pnls))
    pos = float(sum(p for p in pnls if p > 0))
    neg = float(sum(p for p in pnls if p < 0))
    if neg == 0:
        daily_pf = None if pos == 0 else float("inf")
    else:
        daily_pf = pos / abs(neg)
    cum = peak = mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {
        "n_days": n,
        "net": net,
        "mean_daily": net / n,
        "daily_pf": daily_pf,
        "worst_day": float(min(pnls)),
        "max_drawdown": mdd,
        "n_active_days": sum(1 for p in pnls if p != 0.0),
    }
