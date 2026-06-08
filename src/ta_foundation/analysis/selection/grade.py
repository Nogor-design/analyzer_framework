"""Grade the outcome ledger — the accountable track record + baseline head-to-head.

Two questions, both answered from *real issued recommendations* (not a backtest
replay):

1. **What did we actually do?** ``track_record(ledger)`` realises each issued
   lineup against its recorded actuals and summarises the series (net,
   expectancy, hit-rate, drawdown/survival) with the same vocabulary the replay
   harness uses. This is the honest "accountable prediction" output.

2. **Was it better than dumb?** ``grade_against_baselines(...)`` replays the
   baseline selectors over the *same recommendation days*, leakage-free (each
   pick uses only days strictly before it), so the issued lineup can be compared
   to top_pf / equal_weight / regime_matched on identical days. A selector only
   earns trust if it beats the best baseline on expectancy AND survival.

The realised P&L of a lineup follows the replay convention: per slice, the
equal-weight mean of that slice's picks; summed across slices (one unit of risk
per slice). See ``docs/designs/daily_lineup_selector.md``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional, Sequence

from .baselines import DEFAULT_BASELINES
from .ledger import LineupRecommendation, OutcomeLedger
from .model import Candidate, SelectionContext
from .replay import Selector, _summarize


def _lineup_realized(rec: LineupRecommendation, realized_by_template: dict[str, float]) -> float:
    """Realised $ for an issued lineup: per slice, equal-weight mean of its
    picks' realised P&L; summed across slices (missing template == flat 0.0)."""
    total = 0.0
    for _slice, template_ids in rec.slices().items():
        if not template_ids:
            continue
        total += sum(realized_by_template.get(t, 0.0) for t in template_ids) / len(template_ids)
    return total


def track_record(ledger: OutcomeLedger) -> dict[str, Any]:
    """Summarise the realised P&L of every issued lineup that has actuals.

    The primary accountability artifact — needs only the ledger. ``picks`` lists
    each graded day's issued templates and realised total for inspection.
    """
    daily: dict[date, float] = {}
    picks_log: list[dict[str, Any]] = []
    for rec, act in ledger.joined():
        if act is None:
            continue
        realized = _lineup_realized(rec, act.realized_by_template)
        daily[rec.for_day] = daily.get(rec.for_day, 0.0) + realized
        picks_log.append({
            "day": rec.for_day.isoformat(),
            "selector": rec.selector_version,
            "regime": rec.regime,
            "templates": [p.template_id for p in rec.picks],
            "realized": realized,
        })
    summary = _summarize(daily, picks_log)
    summary["n_graded"] = len(picks_log)
    summary["n_pending"] = sum(1 for _r, a in ledger.joined() if a is None)
    return summary


def _replay_on_days(
    candidates: Sequence[Candidate],
    selector: Selector,
    test_days: Sequence[date],
    regime_by_day: dict[date, str],
) -> dict[str, Any]:
    """Realise ``selector`` on exactly ``test_days`` (each pick trained only on
    days strictly before it — the leakage contract), and summarise. Mirrors
    ``replay_selector`` but on a caller-chosen day set so baselines align to the
    ledger's recommendation days rather than a warm-up cutoff."""
    calendar = sorted({d for c in candidates for d in c.daily_pnl})
    by_slice: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_slice[c.slice_key].append(c)

    daily: dict[date, float] = {}
    for test_day in sorted(test_days):
        train_days = [d for d in calendar if d < test_day]
        if not train_days:
            continue
        ctx = SelectionContext(
            train_days=train_days,
            test_day=test_day,
            regime_for_test_day=regime_by_day.get(test_day),
            regime_by_day=regime_by_day,
        )
        day_total = 0.0
        for cands in by_slice.values():
            picks = selector(cands, ctx)
            if not picks:
                continue
            day_total += sum(c.daily_pnl.get(test_day, 0.0) for c in picks) / len(picks)
        daily[test_day] = day_total
    return _summarize(daily, [])


def grade_against_baselines(
    ledger: OutcomeLedger,
    candidates: Sequence[Candidate],
    *,
    regime_by_day: Optional[dict[date, str]] = None,
    baselines: Optional[dict[str, Selector]] = None,
) -> dict[str, dict[str, Any]]:
    """Head-to-head on the ledger's *graded recommendation days*: the issued
    lineup (``"as_issued"``) vs each baseline, all realised on the same days from
    the same candidate universe. Returns ``{name: summary}``.

    ``candidates`` must carry the full per-template daily P&L for those days
    (load via ``loader.load_candidates_from_session``). A baseline is only
    meaningful where its picks exist in that universe.
    """
    regime_by_day = dict(regime_by_day or {})
    baselines = baselines or DEFAULT_BASELINES

    graded_days = [rec.for_day for rec, act in ledger.joined() if act is not None]
    out: dict[str, dict[str, Any]] = {"as_issued": track_record(ledger)}
    if not graded_days or not candidates:
        return out
    for name, sel in baselines.items():
        out[name] = _replay_on_days(candidates, sel, graded_days, regime_by_day)
    return out
