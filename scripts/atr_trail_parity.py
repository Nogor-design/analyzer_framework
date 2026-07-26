"""Trail-parity check (Parity A): does the Python NT-AtrTrail replica reproduce
PantheonMaster's ACTUAL NinjaTrader backtest stop exits?

Pools every stop-exit trade across a completed PantheonMaster deployment session
and grades the replica's modeled exit price against NT's actual exit price, under
BOTH ATR smoothings (Wilder vs SMA) — the documented "NT ATR" parity question.
A high match rate under one mode says the Python exit model is faithful to NT
(prerequisite for trusting exit pre-selection AND for interpreting Parity B,
NT-backtest vs NT-live). A low match rate localizes the divergence.

Usage: python scripts/atr_trail_parity.py [session_id] [bars_file]
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    compare_nt_atr_trail,
)
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser

DEFAULT_SESSION = "opt_a09359e6b60b"
DEFAULT_BARS = r"D:\MarketData\NQ 06-26.Export.txt"


def _num(x: str) -> float | None:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_trades(session_dir: Path) -> pd.DataFrame:
    """Pool entry/exit from every F_xxx Trades.csv (AtrTrail exit params are pinned
    identically across all templates, so they share one exit model)."""
    results = session_dir / "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results"
    rows: list[dict] = []
    for trades_csv in sorted(results.glob("*/Trades.csv")):
        with trades_csv.open("r", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                et = (r.get("Entry time") or "").strip()
                pos = (r.get("Market pos.") or "").strip().lower()
                if not et or pos not in {"long", "short"}:
                    continue
                dt = pd.to_datetime(et, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
                if pd.isna(dt):
                    continue
                rows.append({
                    "entry_dt": dt.to_pydatetime(),
                    "entry_price": _num(r.get("Entry price")),
                    "direction": 1 if pos == "long" else -1,
                    "nt_exit_price": _num(r.get("Exit price")),
                    "nt_exit_name": (r.get("Exit name") or "").strip(),
                })
    return pd.DataFrame(rows)


def load_bars(bars_file: str) -> pd.DataFrame:
    art = MinuteBarsLastTxtParser().parse(Path(bars_file), run_id=None)
    bars = art.df.copy()
    bars["dt"] = pd.to_datetime(bars["dt"]).dt.tz_localize(None)  # naive Denver, matches NT-local trades
    return bars.sort_values("dt").reset_index(drop=True)


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SESSION
    bars_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BARS
    sdir = Path(".ta_artifacts/web_optimizer/sessions") / session_id

    trades = load_trades(sdir)
    bars = load_bars(bars_file)
    print(f"=== AtrTrail Parity A: {session_id} ===")
    print(f"  {len(trades)} trades pooled; bars {bars['dt'].min()}..{bars['dt'].max()} ({len(bars)})")
    exit_names = trades["nt_exit_name"].str.lower().value_counts()
    print("  NT exit-name mix:", dict(exit_names.head(8)))

    for mode in ("wilder", "sma"):
        cfg = NtAtrTrailConfig(atr_mode=mode)  # AtrPeriod14/mult2/stop60/tick0.25 defaults = NT
        res = compare_nt_atr_trail(trades, bars, cfg)
        mr = res["match_rate"]
        print(f"\n  ATR={mode}: stop-exits={res['n_stop_exits']} graded={res['n_graded']} "
              f"match={res['n_match']} match_rate={'%.1f%%' % (mr*100) if mr is not None else 'n/a'} "
              f"median_diff={res['median_diff_ticks']} ticks")

    print("\nReads: high match under one ATR mode => Python AtrTrail model is faithful to NT's "
          "backtest (and identifies the correct ATR smoothing). Low match => divergence to localize "
          "before trusting exit pre-selection or running Parity B (backtest vs live).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
