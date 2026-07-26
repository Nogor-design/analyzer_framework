"""Cross-instrument LOWER-FREQUENCY (daily/swing) edge scout (Lever 1 payoff).

The intraday/tick scouts proved a real but tiny edge that fixed costs swamp. The
only structural way to beat fixed cost is a bigger move PER TRADE -> lower
frequency, where the captured move dwarfs cost. That needs years of data; NT's
intraday horizon is ~6 months, so we use free multi-year DAILY futures from
Yahoo (scripts/fetch_daily_history.py).

Reuses the proven machinery unchanged -- the same mechanism-grounded setups,
the same cost-aware outcome simulator, and the same pooled-t-stat cross-instrument
gate -- just fed DAILY bars (lookbacks/holds in days, no RTH filter). Pools
per-trade NET R-multiples across NQ/ES/YM/RTY (26/26/24/9 yrs), ranks by pooled
t-stat. A reversion/momentum edge that survives realistic cost across 4
instruments and ~25 years of regimes (dotcom, GFC, COVID, ...) would be the real
thing.

    python scripts/cross_instrument_daily_scout.py
    python scripts/cross_instrument_daily_scout.py --common-start 2017-07-09
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cross_instrument_scout as cis  # noqa: E402

DAILY_DIR = Path(r"D:\MarketData\daily")
# Diversified cross-asset TSMOM panel. tick specs are nominal (1.0/1.0): the
# daily scout runs ZERO-cost (swing cost ~0.01R, negligible), which makes the
# per-trade R purely price-based (P&L/ATR-risk) and independent of tick_value,
# so exact contract specs don't matter for the momentum signal.
_EQUITY = ["NQ", "ES", "YM", "RTY"]
_COMMOD = ["GC", "SI", "HG", "CL", "NG", "ZC", "ZS", "ZW"]
_RATES = ["ZB", "ZN", "ZF"]
_FX = ["6E", "6J", "6B", "6A"]
ALL_INSTR = _EQUITY + _COMMOD + _RATES + _FX
DAILY_INSTR = {i: {"tick_size": 1.0, "tick_value": 1.0} for i in ALL_INSTR}


def load_daily(inst: str, common_start: str | None,
               common_end: str | None = None) -> pd.DataFrame:
    path = DAILY_DIR / f"{inst}_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["dt"])
    df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_convert("America/Denver")
    if common_start:
        df = df[df["dt"] >= pd.Timestamp(common_start, tz="America/Denver")]
    if common_end:
        df = df[df["dt"] < pd.Timestamp(common_end, tz="America/Denver")]
    return df.sort_values("dt").reset_index(drop=True)


def build_combos():
    combos = []
    atr_len = 14
    # Mean reversion (fade >= k*ATR daily stretch from SMA), swing holds.
    for sma in (10, 20, 50):
        for k in (1.0, 1.5, 2.0):
            for tm, sm, mb in ((1.0, 1.0, 5), (1.5, 1.0, 10), (2.0, 1.5, 20)):
                combos.append(("MR", f"MR sma{sma} k{k} tp{tm} sl{sm} hold{mb}",
                               lambda bars, s=sma, kk=k: cis.setup_mean_reversion(
                                   bars, sma_len=s, k=kk, atr_len=atr_len, rth_only=False),
                               tm, sm, mb))
    # Momentum breakout (N-day high/low break), trend holds.
    for lb in (20, 50):
        for tm, sm, mb in ((2.0, 1.0, 20), (3.0, 2.0, 30)):
            combos.append(("MO", f"MO lookback{lb} tp{tm} sl{sm} hold{mb}",
                           lambda bars, l=lb: cis.setup_momentum_breakout(
                               bars, lookback=l, atr_len=atr_len, rth_only=False),
                           tm, sm, mb))
    # Trend-CAPTURE momentum: wide target (let winners run) + long hold, so the
    # exit rides the trend to the timeout-close instead of capping at a fixed RR.
    for lb in (20, 50):
        for tm, sm, mb in ((8.0, 2.0, 40), (10.0, 3.0, 60)):
            combos.append(("MOt", f"MOt lookback{lb} tp{tm} sl{sm} hold{mb}",
                           lambda bars, l=lb: cis.setup_momentum_breakout(
                               bars, lookback=l, atr_len=atr_len, rth_only=False),
                           tm, sm, mb))
    return combos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--common-start", default=None,
                    help="restrict all instruments to dates >= this (same-period test)")
    ap.add_argument("--common-end", default=None,
                    help="restrict all instruments to dates < this (period split / holdout)")
    ap.add_argument("--min-pooled-trades", type=int, default=200)
    ap.add_argument("--min-pooled-t", type=float, default=2.0)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)

    print("Loading daily bars ...", flush=True)
    bars_by_inst = {}
    for inst in DAILY_INSTR:
        b = load_daily(inst, args.common_start, args.common_end)
        bars_by_inst[inst] = b
        if b.empty:
            print(f"  {inst}: NO DATA")
        else:
            print(f"  {inst}: {len(b):,} daily bars  {b['dt'].min().date()} -> {b['dt'].max().date()}")
    panel = [i for i, b in bars_by_inst.items() if not b.empty]
    print(f"Panel: {panel}\n", flush=True)

    rows = []
    for fam, label, builder, tm, sm, mb in build_combos():
        per_inst = {}
        for inst in panel:
            pend = builder(bars_by_inst[inst])
            R = cis.simulate_R(pend, bars_by_inst[inst], DAILY_INSTR[inst],
                               target_mult=tm, stop_mult=sm, max_bars=mb,
                               commission=0.0, slippage_ticks=0.0)
            per_inst[inst] = R
        verdict = cis.evaluate_cross_instrument(
            {k: v.tolist() for k, v in per_inst.items()},
            min_pooled_t=args.min_pooled_t, min_pooled_trades=args.min_pooled_trades)
        rows.append((fam, label, verdict, per_inst))

    rows.sort(key=lambda r: (r[2].pooled_t_stat if np.isfinite(r[2].pooled_t_stat) else -1e9),
              reverse=True)
    classes = {"eq": _EQUITY, "cmd": _COMMOD, "rate": _RATES, "fx": _FX}

    def class_means(per):
        out = []
        for cname, members in classes.items():
            arrs = [per[i] for i in members if i in per and len(per[i])]
            if arrs:
                m = np.mean(np.concatenate(arrs))
                out.append(f"{cname}:{m:+.3f}")
        return " ".join(out)

    print("=" * 110)
    print(f"{'fam':<4}{'label':<34}{'pooledN':>8}{'t_stat':>8}{'PF':>6}{'meanR':>8}{'inst+':>7}"
          f"   mean R by asset class")
    print("-" * 110)
    for fam, label, v, per in rows[:args.top]:
        flag = "  <== ROBUST EDGE" if v.is_robust_edge else ""
        print(f"{fam:<4}{label:<34}{v.pooled_trades:>8}{v.pooled_t_stat:>8.2f}"
              f"{v.pooled_profit_factor:>6.2f}{v.pooled_mean_ticks:>8.3f}"
              f"{v.instruments_profitable:>4}/{v.instruments_total:<2}   {class_means(per)}{flag}")
    print("=" * 110)
    robust = [r for r in rows if r[2].is_robust_edge]
    print(f"\n{len(robust)} of {len(rows)} passed ALL cross-instrument gates "
          f"(pooled t>={args.min_pooled_t}, PF>=1.2, >=75% inst profitable, "
          f"N>={args.min_pooled_trades}).  meanR is NET of cost (negligible at daily scale).")
    if robust:
        print("\nTop robust candidate gate detail:")
        for line in robust[0][2].reasons:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
