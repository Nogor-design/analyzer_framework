"""Baseline daily-lineup selectors — the bar every smarter method must clear.

A *selector* maps (candidates competing in one slice, SelectionContext) -> the
chosen template(s) for that slice on ``ctx.test_day``. Returning several
candidates means "trade them all, equal-weight" (the replay averages their
realised P&L). Every selector ranks ONLY on ``ctx.train_days`` so the replay
stays leakage-free.

These are deliberately dumb. Per ``daily_lineup_selector.md`` the transparent
composite scorer (``scoring.py``, Phase-1 v1) ships only if it beats these
out-of-sample on expectancy AND survival; otherwise the winning baseline is the
production selector.
"""
from __future__ import annotations

from typing import Sequence

from .model import Candidate, SelectionContext, daily_metrics, window_pnls


def _top_pf_key(cand: Candidate, train_days: Sequence) -> tuple[float, float]:
    """Sort key: daily profit factor desc, then mean daily P&L desc — both on the
    train window only. A no-loss template (daily_pf == inf / None) ranks by net."""
    m = daily_metrics(window_pnls(cand, train_days))
    pf = m["daily_pf"]
    if pf is None:
        pf_key = 0.0
    elif pf == float("inf"):
        pf_key = float("inf") if m["net"] > 0 else 0.0
    else:
        pf_key = pf
    return (pf_key, m["mean_daily"])


def top_pf(candidates: Sequence[Candidate], ctx: SelectionContext) -> list[Candidate]:
    """Pick the single highest train-window daily-PF template in the slice.

    Prefers templates that actually traded in the train window; falls back to the
    whole slice if none did."""
    if not candidates:
        return []
    active = [
        c for c in candidates
        if daily_metrics(window_pnls(c, ctx.train_days))["n_active_days"] > 0
    ]
    pool = active or list(candidates)
    return [max(pool, key=lambda c: _top_pf_key(c, ctx.train_days))]


def equal_weight(candidates: Sequence[Candidate], ctx: SelectionContext) -> list[Candidate]:
    """Trade the whole slice, equal-weight (the dumbest honest baseline)."""
    return list(candidates)


def most_recent_winner(
    candidates: Sequence[Candidate], ctx: SelectionContext, *, tail: int = 5
) -> list[Candidate]:
    """Momentum baseline: pick the best net P&L over the last ``tail`` train days."""
    if not candidates:
        return []
    tail_days = ctx.train_days[-tail:] if len(ctx.train_days) >= tail else list(ctx.train_days)
    return [max(candidates, key=lambda c: daily_metrics(window_pnls(c, tail_days))["net"])]


def regime_matched(candidates: Sequence[Candidate], ctx: SelectionContext) -> list[Candidate]:
    """Pick the template with the best train-window P&L in the *same regime* as the
    upcoming session. Falls back to ``top_pf`` when regime is unknown or no train
    day shared the test-day regime."""
    if not candidates or ctx.regime_for_test_day is None:
        return top_pf(candidates, ctx)
    regime = ctx.regime_for_test_day
    same_regime_days = [d for d in ctx.train_days if ctx.regime_by_day.get(d) == regime]
    if not same_regime_days:
        return top_pf(candidates, ctx)
    best = max(
        candidates,
        key=lambda c: daily_metrics(window_pnls(c, same_regime_days))["mean_daily"],
    )
    return [best]


# Name -> selector. ``regime_matched`` needs ``regime_by_day`` populated on the
# context (the replay fills it); the others ignore it.
DEFAULT_BASELINES = {
    "top_pf": top_pf,
    "equal_weight": equal_weight,
    "most_recent_winner": most_recent_winner,
    "regime_matched": regime_matched,
}
