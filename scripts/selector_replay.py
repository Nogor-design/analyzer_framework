"""Replay the daily-lineup baselines over a completed deployment session and print
the head-to-head. The Phase-1 accountability gate: any smarter selector
(scoring.py) must beat the best baseline here OOS on expectancy AND survival.

Bootstraps on backtest OOS results we already have (no live ledger needed).

Usage: python scripts/selector_replay.py [session_id] [bars_file]
"""
from __future__ import annotations

import sys

from ta_foundation.analysis.selection import DEFAULT_BASELINES, compare_selectors
from ta_foundation.analysis.selection.loader import load_candidates_from_session

DEFAULT_SESSION = "opt_a09359e6b60b"
DEFAULT_BARS = r"D:\MarketData\NQ 06-26.Export.txt"


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SESSION
    bars_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BARS
    sdir = f".ta_artifacts/web_optimizer/sessions/{session_id}"

    cands, regime_by_day = load_candidates_from_session(sdir, bars_file=bars_file)
    slices = sorted({c.slice_key for c in cands})
    days = sorted({d for c in cands for d in c.daily_pnl})
    print(f"=== selector replay: {session_id} ===")
    print(f"  {len(cands)} templates across {len(slices)} slices; "
          f"{len(days)} days {days[0]}..{days[-1]}; {len(regime_by_day)} regime-days")

    table = compare_selectors(cands, DEFAULT_BASELINES, regime_by_day=regime_by_day,
                              train_min_days=10)
    hdr = f"{'selector':>20} {'net':>9} {'exp/day':>8} {'hit':>5} {'maxDD':>8} {'sharpe':>7}"
    print("\n" + hdr)
    for name, s in sorted(table.items(), key=lambda kv: -kv[1]["net"]):
        sh = s["daily_sharpe"]
        print(f"{name:>20} {s['net']:>9.0f} {s['expectancy_daily']:>8.1f} "
              f"{s['hit_rate']:>5.2f} {s['max_drawdown']:>8.0f} "
              f"{(('%.2f' % sh) if sh is not None else 'n/a'):>7}  (n={s['n_test_days']})")

    print("\nNote: survival (maxDD) is weighted heavily for prop accounts; a lower-net,"
          " lower-drawdown lineup can be the right production choice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
