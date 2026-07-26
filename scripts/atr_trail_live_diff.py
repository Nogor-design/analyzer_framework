"""Parity B, Step 2 — diff NT backtest Trades.csv vs NT live/replay Trades.csv.

Source-of-truth measurement of the AtrTrail backtest->live gap. Step 1
(`atr_trail_live_estimate.py`) PREDICTED ~-24% from a tick model; this script
MEASURES it from a real NT Market Replay run, matched per trade on entry time.

Inputs:
  --bt    a session id (pools its final_backtest Trades.csv) OR a path to a
          Trades.csv / folder of *Trades.csv.
  --live  path to the replay Trades.csv (or a folder of them) the operator
          exported from the Market Replay run (see docs/runbooks/atr_trail_parity.md
          Step 2 capture sheet).

The ChangeOrder Output prints are NOT needed here (no timestamp); the realized
Trades.csv P&L is the truth. Pure analysis, no NT.

Usage:
  python scripts/atr_trail_live_diff.py --bt opt_a09359e6b60b --live C:\\temp\\replay
  python scripts/atr_trail_live_diff.py --bt C:\\path\\bt --live C:\\path\\live --trail-only
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import diff_backtest_vs_live_trades

PREDICTED_HAIRCUT_PCT = -23.9  # Parity B Step 1 tick-model estimate (the floor)


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _iter_trades_csv(src: str):
    """Yield Trades.csv paths from a session id, a file, or a folder."""
    p = Path(src)
    if p.is_file():
        yield p
        return
    if p.is_dir():
        yield from sorted(p.glob("**/*Trades.csv"))
        yield from sorted(p.glob("**/Trades.csv"))
        return
    # treat as a session id
    root = Path(".ta_artifacts/web_optimizer/sessions") / src / \
        "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results"
    yield from sorted(root.glob("*/Trades.csv"))


def load_trades(src: str) -> pd.DataFrame:
    rows = []
    seen = set()
    for tc in _iter_trades_csv(src):
        if tc in seen:
            continue
        seen.add(tc)
        with tc.open("r", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                pos = (r.get("Market pos.") or "").strip().lower()
                if pos not in {"long", "short"}:
                    continue
                edt = pd.to_datetime((r.get("Entry time") or "").strip(),
                                     format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
                xdt = pd.to_datetime((r.get("Exit time") or "").strip(),
                                     format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
                if pd.isna(edt):
                    continue
                rows.append({
                    "entry_dt": edt, "exit_dt": xdt,
                    "entry_price": _num(r.get("Entry price")),
                    "exit_price": _num(r.get("Exit price")),
                    "direction": 1 if pos == "long" else -1,
                    "profit": _num(r.get("Profit")),
                    "exit_name": (r.get("Exit name") or "").strip(),
                })
    df = pd.DataFrame(rows)
    return df.dropna(subset=["entry_price", "exit_price", "profit"]).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default="opt_a09359e6b60b", help="session id | Trades.csv | folder")
    ap.add_argument("--live", required=True, help="replay Trades.csv | folder")
    ap.add_argument("--trail-only", action="store_true", help="grade only trailed-stop exits")
    ap.add_argument("--tolerance", type=float, default=2.0, help="entry-match tolerance (s)")
    args = ap.parse_args()

    bt = load_trades(args.bt)
    live = load_trades(args.live)
    print(f"=== AtrTrail backtest<->live diff (Parity B Step 2) ===")
    print(f"  backtest trades: {len(bt)}   live/replay trades: {len(live)}")
    if bt.empty or live.empty:
        print("  ERROR: one side is empty -- check --bt / --live paths.")
        return 1

    out = diff_backtest_vs_live_trades(
        bt, live, match_tolerance_s=args.tolerance, trail_only=args.trail_only)
    s = out["summary"]
    pop = "trail-exit" if args.trail_only else "all"
    print(f"\n  matched {s['n_matched']} {pop} trades "
          f"(bt unmatched {s['n_bt_unmatched']}, live unmatched {s['n_live_unmatched']})")
    print(f"  NT backtest total P&L:  ${s['bt_pnl']:>12,.0f}")
    print(f"  NT live/replay  P&L:    ${s['live_pnl']:>12,.0f}")
    print(f"  MEASURED HAIRCUT:       ${s['haircut']:>12,.0f}  ({s['haircut_pct']:+.1f}% of backtest)")
    print(f"  worse-live trades: {s['n_worse_live']}/{s['n_matched']}   "
          f"median per-trade P&L delta ${s['median_pnl_delta']:+.0f}   "
          f"median exit-time delta {s['median_exit_time_delta_s']:+.0f}s")
    print(f"\n  Step 1 tick-model PREDICTED {PREDICTED_HAIRCUT_PCT:+.1f}% (a floor).")
    gap = s["haircut_pct"] - PREDICTED_HAIRCUT_PCT
    print(f"  measured vs predicted: {gap:+.1f} pts "
          f"({'live worse than predicted -- real fills/latency, as warned' if gap < 0 else 'better than the floor'}).")
    if abs(s["haircut_pct"]) <= 10:
        print("  DECISION: within ~10% -- backtested pool is trustworthy for sizing.")
    else:
        print(f"  DECISION: material gap -- haircut backtested AtrTrail PF/net by "
              f"{abs(s['haircut_pct']):.0f}% before promotion/sizing.")

    out_csv = Path("atr_trail_live_diff_detail.csv")
    out["detail"].to_csv(out_csv, index=False)
    print(f"\n  per-trade detail -> {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
