"""Report the outcome ledger's track record + baseline head-to-head (Phase 0/1).

Prints the accountable "what our issued lineups actually did" summary, and — if a
session universe is given — replays the baselines on the same graded days so the
issued selector can be judged against top_pf / equal_weight / regime_matched on
expectancy AND survival (the Phase-1 trust gate).

Usage:
  python scripts/grade_ledger.py --ledger .ta_artifacts/selection/lineup_ledger.jsonl \
      --session opt_a09359e6b60b --bars "D:\\MarketData\\NQ 06-26.Export.txt"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ta_foundation.analysis.selection.grade import grade_against_baselines, track_record
from ta_foundation.analysis.selection.ledger import OutcomeLedger
from ta_foundation.analysis.selection.loader import load_candidates_from_session

_SESSIONS = Path(".ta_artifacts/web_optimizer/sessions")


def _fmt(s: dict) -> str:
    dd = s.get("daily_sharpe")
    return (f"days={s['n_test_days']:>3}  net=${s['net']:>10,.0f}  "
            f"exp=${s['expectancy_daily']:>7,.0f}/d  hit={s['hit_rate']*100:>4.0f}%  "
            f"maxDD=${s['max_drawdown']:>10,.0f}  sharpe={'n/a' if dd is None else f'{dd:>5.2f}'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--session", default=None, help="universe for baseline head-to-head")
    ap.add_argument("--bars", default=None)
    args = ap.parse_args()

    led = OutcomeLedger(args.ledger) if args.ledger else OutcomeLedger()
    tr = track_record(led)
    print(f"=== Outcome ledger: {led.path} ===")
    print(f"  graded days: {tr['n_graded']}   pending (no actuals yet): {tr['n_pending']}")
    if tr["n_graded"] == 0:
        print("  nothing to grade yet -- issue lineups + record actuals first.")
        return 0
    print(f"\n  AS ISSUED   {_fmt(tr)}")

    if args.session:
        sdir = Path(args.session) if Path(args.session).exists() else _SESSIONS / args.session
        cands, regime_by_day = load_candidates_from_session(sdir, bars_file=args.bars)
        out = grade_against_baselines(led, cands, regime_by_day=regime_by_day)
        print("\n  --- baseline head-to-head on the same graded days ---")
        for name, s in out.items():
            tag = "AS ISSUED" if name == "as_issued" else name
            print(f"  {tag:<16} {_fmt(s)}")
        print("\n  Phase-1 gate: the issued selector must beat the best baseline on BOTH "
              "expectancy and survival (maxDD) OOS to ship; else the baseline is production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
