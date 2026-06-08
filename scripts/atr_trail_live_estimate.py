"""Parity B, Step 1 — offline estimate of the AtrTrail backtest->live HAIRCUT.

The bar-close replica (validated ~ NT backtest, Parity A) and the tick-trail
replica (~ NT live: trails off tick highs via ChangeOrder) bracket NT's two stop
engines. Running the tick model on the real tick cache and comparing its P&L to
NT's actual backtest P&L estimates how much the backtest OVERSTATES AtrTrail
results before any live run — the number to haircut the pool by for sizing.

Pure analysis; no NT. Tick coverage gates the sample (NQ ticks end ~May 27).

Usage: python scripts/atr_trail_live_estimate.py [session_id] [bars] [ticks]
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NT_TRAIL_EXIT_NAMES,
    NtAtrTrailConfig,
    compute_atr,
    replicate_nt_atr_trail_tick,
)
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser

SESSION = sys.argv[1] if len(sys.argv) > 1 else "opt_a09359e6b60b"
BARS = sys.argv[2] if len(sys.argv) > 2 else r"D:\MarketData\NQ 06-26.Export.txt"
TICKS = sys.argv[3] if len(sys.argv) > 3 else r"D:\MarketData\NQ 06-26 Tick.Last.txt.parquet"
NQ_POINT_VALUE = 20.0  # $ per index point (E-mini NQ)
CFG = NtAtrTrailConfig()  # AtrPeriod 14 / mult 2 / stop 60 / tick 0.25 / Wilder (Parity A)


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_trail_trades(session: str) -> pd.DataFrame:
    root = Path(".ta_artifacts/web_optimizer/sessions") / session / \
        "deployment_package" / "final_backtest_handoff" / "nt8_backtest_results"
    rows = []
    for tc in sorted(root.glob("*/Trades.csv")):
        with tc.open("r", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                pos = (r.get("Market pos.") or "").strip().lower()
                name = (r.get("Exit name") or "").strip().lower()
                if pos not in {"long", "short"} or name not in NT_TRAIL_EXIT_NAMES:
                    continue
                dt = pd.to_datetime((r.get("Entry time") or "").strip(),
                                    format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
                if pd.isna(dt):
                    continue
                rows.append({
                    "entry_dt": dt, "entry_price": _num(r.get("Entry price")),
                    "direction": 1 if pos == "long" else -1,
                    "nt_exit_price": _num(r.get("Exit price")),
                })
    return pd.DataFrame(rows).dropna(subset=["entry_price", "nt_exit_price"])


def main() -> int:
    print(f"=== AtrTrail backtest->live haircut estimate: {SESSION} ===")
    trades = load_trail_trades(SESSION)

    art = MinuteBarsLastTxtParser().parse(Path(BARS), run_id=None)
    bars = art.df.copy()
    bars["dt"] = pd.to_datetime(bars["dt"]).dt.tz_localize(None)
    bars = bars.sort_values("dt").reset_index(drop=True)
    bar_atr = compute_atr(bars, CFG.atr_period, CFG.atr_mode).to_numpy()
    bar_ns = bars["dt"].values.astype("datetime64[ns]").astype("int64")

    t = time.time()
    tdf = pd.read_parquet(TICKS)
    tick_dt = pd.to_datetime(tdf["dt"]).dt.tz_localize(None)
    tick_ns = tick_dt.values.astype("datetime64[ns]").astype("int64")
    tick_px = tdf["last"].to_numpy(dtype=float)
    tmax = tick_dt.max()
    print(f"  {len(tdf):,} ticks ({tick_dt.min()}..{tmax}) loaded {time.time()-t:.1f}s")

    in_cov = trades[trades["entry_dt"] <= tmax].reset_index(drop=True)
    print(f"  {len(trades)} trail trades; {len(in_cov)} in tick coverage (<= {tmax.date()})")

    horizon_ns = pd.Timedelta(minutes=CFG.max_hold_minutes).value
    nt_pnl = tick_pnl = 0.0
    deltas, graded = [], 0
    for tr in in_cov.itertuples(index=False):
        e_ns = np.datetime64(tr.entry_dt).astype("datetime64[ns]").astype("int64")
        lo = np.searchsorted(tick_ns, e_ns, "right")
        hi = np.searchsorted(tick_ns, e_ns + horizon_ns, "right")
        if hi <= lo:
            continue
        sl = slice(lo, hi)
        bidx = np.clip(np.searchsorted(bar_ns, tick_ns[sl], "right") - 1, 0, len(bar_atr) - 1)
        res = replicate_nt_atr_trail_tick(
            entry_price=tr.entry_price, direction=tr.direction,
            tick_prices=tick_px[sl], tick_dts=tick_ns[sl], tick_atr=bar_atr[bidx], cfg=CFG)
        if res["exit_price"] is None:
            continue
        nt_p = (tr.nt_exit_price - tr.entry_price) * tr.direction * NQ_POINT_VALUE
        tk_p = (res["exit_price"] - tr.entry_price) * tr.direction * NQ_POINT_VALUE
        nt_pnl += nt_p
        tick_pnl += tk_p
        deltas.append(tk_p - nt_p)
        graded += 1

    d = np.array(deltas)
    print(f"\n  graded {graded} trail trades")
    print(f"  NT backtest  total P&L:  ${nt_pnl:>12,.0f}")
    print(f"  tick (live)  total P&L:  ${tick_pnl:>12,.0f}")
    hair = tick_pnl - nt_pnl
    pct = (hair / nt_pnl * 100) if nt_pnl else float("nan")
    print(f"  HAIRCUT (live - backtest): ${hair:>11,.0f}  ({pct:+.1f}% of backtest)")
    if graded:
        print(f"  per-trade delta: mean ${d.mean():+.1f}  median ${np.median(d):+.1f}  "
              f"worse(live<bt) {int((d < 0).sum())}/{graded} ({(d<0).mean()*100:.0f}%)")
    print("\nReads: a negative haircut = live trails tighter (exits earlier) than backtest, "
          "so the pool's backtested AtrTrail PF/net is optimistic -- apply this haircut before "
          "sizing. Confirm with the NT replay run (Parity B Step 2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
