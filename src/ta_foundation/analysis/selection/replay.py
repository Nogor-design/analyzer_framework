"""Walk-forward replay harness — the deliverable that gates trust.

For each test day (after a minimum train window), each selector picks a lineup
per slice using ONLY prior days, and we realise the picks on the test day. The
result is a metrics table per selector: expectancy, hit rate, and — weighted
heavily for prop accounts — survival (max drawdown / worst day).

Leakage is the whole risk here, so the contract is explicit: the train window for
``test_day`` is every day strictly before it (expanding window). A selector
physically cannot see ``test_day``'s outcome when it picks. The unit tests assert
this with a candidate that's great only on test days and must NOT get picked.

See ``docs/designs/daily_lineup_selector.md``.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Optional, Sequence

from .model import Candidate, SelectionContext, daily_metrics

Selector = Callable[[Sequence[Candidate], SelectionContext], list[Candidate]]


def _trading_calendar(candidates: Sequence[Candidate]) -> list[date]:
    return sorted({d for c in candidates for d in c.daily_pnl})


def replay_selector(
    candidates: Sequence[Candidate],
    selector: Selector,
    *,
    days: Optional[Sequence[date]] = None,
    regime_by_day: Optional[dict[date, str]] = None,
    train_min_days: int = 10,
) -> dict[str, Any]:
    """Replay one selector walk-forward and summarise the realised daily lineup.

    The lineup's realised P&L on a day is the sum over slices of the (equal-weight)
    realised P&L of that slice's picks — i.e. one unit of risk per slice, split
    across that slice's picks. ``train_min_days`` is the warm-up before the first
    test day.
    """
    if not candidates:
        return _summarize({}, [])
    calendar = list(days) if days is not None else _trading_calendar(candidates)
    regime_by_day = dict(regime_by_day or {})

    by_slice: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_slice[c.slice_key].append(c)

    if len(calendar) <= train_min_days:
        return _summarize({}, [])

    daily_lineup_pnl: dict[date, float] = {}
    picks_log: list[dict[str, Any]] = []
    for i, test_day in enumerate(calendar[train_min_days:]):
        train_days = calendar[: train_min_days + i]  # strictly before test_day
        ctx = SelectionContext(
            train_days=train_days,
            test_day=test_day,
            regime_for_test_day=regime_by_day.get(test_day),
            regime_by_day=regime_by_day,
        )
        day_total = 0.0
        for slice_key, cands in by_slice.items():
            picks = selector(cands, ctx)
            if not picks:
                continue
            realized = sum(c.daily_pnl.get(test_day, 0.0) for c in picks) / len(picks)
            day_total += realized
            picks_log.append({
                "day": test_day.isoformat(),
                "slice": slice_key,
                "picks": [c.template_id for c in picks],
                "realized": realized,
            })
        daily_lineup_pnl[test_day] = day_total
    return _summarize(daily_lineup_pnl, picks_log)


def compare_selectors(
    candidates: Sequence[Candidate],
    selectors: dict[str, Selector],
    *,
    days: Optional[Sequence[date]] = None,
    regime_by_day: Optional[dict[date, str]] = None,
    train_min_days: int = 10,
) -> dict[str, dict[str, Any]]:
    """Replay several selectors over the same calendar -> {name: summary}.

    This is the head-to-head the Phase-1 gate reads: a candidate selector must beat
    the best baseline on expectancy AND survival out-of-sample to ship."""
    return {
        name: replay_selector(
            candidates, sel, days=days, regime_by_day=regime_by_day,
            train_min_days=train_min_days,
        )
        for name, sel in selectors.items()
    }


def _summarize(daily_pnl_map: dict[date, float], picks_log: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted(daily_pnl_map)
    pnls = [daily_pnl_map[d] for d in days]
    m = daily_metrics(pnls)
    n = len(pnls)
    hit_rate = (sum(1 for p in pnls if p > 0) / n) if n else 0.0
    std = statistics.pstdev(pnls) if n > 1 else 0.0
    sharpe = (m["mean_daily"] / std) if std > 0 else None
    return {
        "n_test_days": n,
        "net": m["net"],
        "expectancy_daily": m["mean_daily"],
        "hit_rate": hit_rate,
        "max_drawdown": m["max_drawdown"],   # survival (<= 0; closer to 0 is safer)
        "worst_day": m["worst_day"],
        "daily_sharpe": sharpe,
        "daily_pnl": {d.isoformat(): daily_pnl_map[d] for d in days},
        "picks": picks_log,
    }
