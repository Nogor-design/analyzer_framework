"""Issue a daily lineup to the outcome ledger (Phase-0 accountable prediction).

Two modes:
  * forward (default): pick a lineup for the NEXT day using ALL session history as
    train, and journal it as a recommendation. Run this daily before the session;
    record actuals after.
  * backfill: ``--for-day <date>`` inside the session window picks the lineup using
    only days strictly before it (leakage-free) and, with ``--with-actuals``,
    immediately journals what that lineup realised — seeds a track record from the
    proven session so the grader has data now.

Selectors: composite (Phase-1 v1) or any baseline name (top_pf / equal_weight /
most_recent_winner / regime_matched). The grader (`grade.py`) later scores the
issued lineups vs baselines on the same days.

Usage:
  python scripts/record_lineup.py --session opt_a09359e6b60b --bars "D:\\MarketData\\NQ 06-26.Export.txt"
  python scripts/record_lineup.py --session opt_a09359e6b60b --for-day 2026-05-20 --with-actuals
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from ta_foundation.analysis.selection.baselines import DEFAULT_BASELINES
from ta_foundation.analysis.selection.grade import track_record
from ta_foundation.analysis.selection.ledger import (
    DayActuals,
    LineupPick,
    LineupRecommendation,
    OutcomeLedger,
)
from ta_foundation.analysis.selection.loader import load_candidates_from_session
from ta_foundation.analysis.selection.model import Candidate, SelectionContext
from ta_foundation.analysis.selection.scoring import composite_selector

_SESSIONS = Path(".ta_artifacts/web_optimizer/sessions")


def _resolve_session(s: str) -> Path:
    p = Path(s)
    return p if p.exists() else _SESSIONS / s


def _pick_lineup(candidates, selector, for_day, regime_by_day, version):
    """Per-slice picks using only days strictly before ``for_day``."""
    by_slice: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_slice.setdefault(c.slice_key, []).append(c)
    calendar = sorted({d for c in candidates for d in c.daily_pnl})
    train_days = [d for d in calendar if d < for_day]
    picks: list[LineupPick] = []
    for slice_key, cands in by_slice.items():
        ctx = SelectionContext(
            train_days=train_days, test_day=for_day,
            regime_for_test_day=regime_by_day.get(for_day),
            regime_by_day=regime_by_day,
        )
        for c in selector(cands, ctx):
            picks.append(LineupPick(slice_key=slice_key, template_id=c.template_id))
    return picks, regime_by_day.get(for_day)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--bars", default=None, help="bars file for the regime baseline/labeling")
    ap.add_argument("--selector", default="composite",
                    help="composite | " + " | ".join(DEFAULT_BASELINES))
    ap.add_argument("--for-day", default=None, help="YYYY-MM-DD (default: day after last session day)")
    ap.add_argument("--with-actuals", action="store_true",
                    help="also journal realised P&L for the picks (backfill/seed)")
    ap.add_argument("--ledger", default=None, help="ledger jsonl path (default under .ta_artifacts)")
    ap.add_argument("--passed-only", action="store_true")
    args = ap.parse_args()

    sdir = _resolve_session(args.session)
    cands, regime_by_day = load_candidates_from_session(
        sdir, bars_file=args.bars, passed_only=args.passed_only)
    if not cands:
        print("no candidates loaded -- check session path / final_backtest output")
        return 1
    calendar = sorted({d for c in cands for d in c.daily_pnl})
    for_day = date.fromisoformat(args.for_day) if args.for_day else calendar[-1] + timedelta(days=1)

    selector = composite_selector if args.selector == "composite" else DEFAULT_BASELINES[args.selector]
    version = args.selector if args.selector != "composite" else "composite_v1"

    picks, regime = _pick_lineup(cands, selector, for_day, regime_by_day, version)
    rec = LineupRecommendation(
        for_day=for_day, selector_version=version, picks=picks,
        source_session=sdir.name, regime=regime,
        notes={"n_candidates": len(cands), "n_slices": len({p.slice_key for p in picks})},
    )
    led = OutcomeLedger(args.ledger) if args.ledger else OutcomeLedger()
    issued = led.record_recommendation(rec)
    print(f"{'recorded' if issued else 'already present'}: {rec.ledger_id}  "
          f"({len(picks)} picks across {len({p.slice_key for p in picks})} slices, regime={regime})")
    for p in picks:
        print(f"    [{p.slice_key}] {p.template_id}")

    if args.with_actuals:
        by_id = {c.template_id: c for c in cands}
        realized = {p.template_id: by_id[p.template_id].daily_pnl.get(for_day, 0.0)
                    for p in picks if p.template_id in by_id}
        led.record_actuals(DayActuals(rec.ledger_id, realized))
        print(f"  actuals journaled for {for_day}: lineup realised "
              f"${sum(realized.values()):,.0f} (per-template, pre equal-weight)")
        tr = track_record(led)
        print(f"  track record so far: {tr['n_graded']} graded days, net ${tr['net']:,.0f}, "
              f"expectancy ${tr['expectancy_daily']:,.0f}/day, maxDD ${tr['max_drawdown']:,.0f}")
    print(f"  ledger -> {led.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
