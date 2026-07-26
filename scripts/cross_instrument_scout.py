"""Cross-instrument, large-N scout for genuinely mechanizable intraday edges.

Why this exists (2026-06-15, see memory project-edge-sample-power)
------------------------------------------------------------------
The edge hunt was sample-starved: ~95 trading days of data and ~1-trade/day
setups produce ~10-50 trades, where a search has no statistical power and BOTH
"0 survivors" and "PF-5 mirages" are the same symptom. The fix is to manufacture
sample, not search cleverer:

  * FREQUENCY  -- target setups that fire many times/day (hundreds-thousands of
                  trades on the data we already own), so a small real edge
                  (mean R > 0) becomes statistically distinguishable.
  * POOLING    -- require a SHARED-parameter edge to hold across NQ/ES/YM/RTY
                  simultaneously; a fitted artifact dies on 4 independent
                  instruments. This is the brutal anti-overfit test.

This reuses the proven pieces rather than rebuilding:
  * parsers.ninjatrader.minute_bars_last_txt -> tz-aware Denver bars
  * analysis.entry_strategies.outcome.simulator.simulate_atr_outcomes
        -> cost-aware per-trade P&L (commission + slippage, conservative ties)
  * analysis.strategy_discovery.cross_instrument.evaluate_cross_instrument
        -> pooled t-stat / PF / fraction-profitable gates (the methodology the
           old same-instrument validation lacked)

P&L is pooled in R-multiples (net P&L / initial dollar risk) so instruments with
different tick value/volatility pool on equal footing. Candidates are ranked by
pooled t-stat, NOT profit factor.

Run:
    python scripts/cross_instrument_scout.py
    python scripts/cross_instrument_scout.py --min-pooled-trades 300 --rth-only
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_atr_outcomes
from ta_foundation.analysis.strategy_discovery.cross_instrument import evaluate_cross_instrument

MARKET_DATA = Path(r"D:\MarketData")

# Index-futures panel: correlated but distinct -> the cross-instrument test.
# tick_size converts price->ticks; tick_value gives dollar risk for R-multiples.
INSTRUMENTS: Dict[str, Dict] = {
    "NQ":  {"tick_size": 0.25, "tick_value": 5.00,  "files": ["NQ 03-26.Last.txt", "NQ 06-26.Last.txt"]},
    "ES":  {"tick_size": 0.25, "tick_value": 12.50, "files": ["ES 03-26.Last.txt"]},
    "YM":  {"tick_size": 1.00, "tick_value": 5.00,  "files": ["YM 03-26.Last.txt", "YM 06-26.Last.txt"]},
    "RTY": {"tick_size": 0.10, "tick_value": 5.00,  "files": ["RTY 03-26.Last.txt"]},
}

# Regular-session window in America/Denver (09:30-16:00 ET = 07:30-14:00 MT).
RTH_START = (7, 30)
RTH_END = (13, 55)


# --------------------------------------------------------------------------- #
# Data loading                                                                #
# --------------------------------------------------------------------------- #
def load_bars(inst: str) -> pd.DataFrame:
    parser = MinuteBarsLastTxtParser()
    parts = []
    for fname in INSTRUMENTS[inst]["files"]:
        path = MARKET_DATA / fname
        if not path.exists():
            continue
        df = parser.parse(path, None).df
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame()
    bars = pd.concat(parts, ignore_index=True)
    bars = bars.sort_values("dt").drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)
    return bars


def _add_indicators(bars: pd.DataFrame, atr_len: int, er_len: int = 20) -> pd.DataFrame:
    b = bars.copy()
    prev_close = b["close"].shift(1)
    tr = np.maximum(
        b["high"] - b["low"],
        np.maximum((b["high"] - prev_close).abs(), (b["low"] - prev_close).abs()),
    )
    # Simple-mean ATR: matches the discovery candle-feature convention
    # (NOT Wilder; Wilder is for exit pre-selection only -- see memory).
    b["atr"] = tr.rolling(atr_len, min_periods=atr_len).mean()
    b["next_open"] = b["open"].shift(-1)
    # Kaufman efficiency ratio over er_len: |net move| / sum(|bar moves|).
    # ~1 => strong directional trend; ~0 => choppy/rangebound. Mean reversion
    # is a chop mechanism, so we gate it to LOW ER (don't fade a trend).
    net = (b["close"] - b["close"].shift(er_len)).abs()
    vol = b["close"].diff().abs().rolling(er_len, min_periods=er_len).sum()
    b["er"] = net / vol
    return b


# --------------------------------------------------------------------------- #
# Setups -- each returns a `pending` entries frame for the simulator           #
# --------------------------------------------------------------------------- #
def _finalize_pending(b: pd.DataFrame, sig: pd.Series, direction: pd.Series,
                      rth_only: bool, entry: str = "market") -> pd.DataFrame:
    """Common assembly: fresh-crossing only, session filter, entry order.

    entry="market" -> next_open (crosses the spread; full slippage).
    entry="limit"  -> passive limit at the signal-bar close (body_midpoint):
                      fills only if price trades back to it within a few bars,
                      otherwise the signal is skipped (honest selection effect).
                      Liquidity-providing, so entry slippage ~0.
    """
    fresh = sig & ~sig.shift(1, fill_value=False)
    ok = fresh & b["atr"].notna() & (b["atr"] > 0) & b["next_open"].notna()
    if rth_only:
        mins = b["dt"].dt.hour * 60 + b["dt"].dt.minute
        ok &= (mins >= RTH_START[0] * 60 + RTH_START[1]) & (mins <= RTH_END[0] * 60 + RTH_END[1])
    cols = {
        "signal_dt": b.loc[ok, "dt"],
        "dt": b.loc[ok, "dt"],
        "direction": direction.loc[ok].astype(int),
        "signal_atr": b.loc[ok, "atr"],
    }
    if entry == "limit":
        cols["timing_mode"] = "body_midpoint"
        cols["limit_price"] = b.loc[ok, "close"]   # post a passive bid/offer at last
        cols["entry_price"] = b.loc[ok, "close"]
        cols["fill_timeout_bars"] = 3
    else:
        cols["timing_mode"] = "next_open"
        cols["entry_price"] = b.loc[ok, "next_open"]
    return pd.DataFrame(cols).reset_index(drop=True)


def setup_mean_reversion(bars: pd.DataFrame, *, sma_len: int, k: float,
                         atr_len: int, rth_only: bool,
                         er_max: float = 1.0, entry: str = "market") -> pd.DataFrame:
    """Fade an over-extension: enter TOWARD a short SMA when price is >= k*ATR away.

    Mechanism: late momentum chasers get trapped at a short-horizon extreme;
    forced-flow reversion. Fires often -> large N.

    er_max gates the signal to a low-efficiency-ratio (choppy) regime: only
    fade when the market is NOT trending (er <= er_max). 1.0 = filter off.
    """
    b = _add_indicators(bars, atr_len, er_len=sma_len)
    sma = b["close"].rolling(sma_len, min_periods=sma_len).mean()
    dev = (b["close"] - sma) / b["atr"]
    regime_ok = b["er"] <= er_max
    long_sig = (dev <= -k) & regime_ok
    short_sig = (dev >= k) & regime_ok
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _finalize_pending(b, sig, direction, rth_only, entry=entry)


def setup_momentum_breakout(bars: pd.DataFrame, *, lookback: int,
                            atr_len: int, rth_only: bool) -> pd.DataFrame:
    """Trade the break of an N-bar range in the breakout direction.

    Mechanism: trapped counter-side participants forced to cover on the break.
    """
    b = _add_indicators(bars, atr_len)
    prior_high = b["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    prior_low = b["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    long_sig = b["close"] > prior_high
    short_sig = b["close"] < prior_low
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _finalize_pending(b, sig, direction, rth_only)


# --------------------------------------------------------------------------- #
# Simulation -> per-trade net R-multiples                                      #
# --------------------------------------------------------------------------- #
def simulate_trades(pending: pd.DataFrame, bars: pd.DataFrame, inst_cfg: Dict,
                    *, target_mult: float, stop_mult: float, max_bars: int,
                    commission: float = 2.09, slippage_ticks: float = 1) -> pd.DataFrame:
    """Return trades with per-trade NET R-multiple, entry hour, and ER tag."""
    if pending.empty:
        return pd.DataFrame()
    cfg = {
        "atr": {"enabled": True, "target_mult": target_mult, "stop_mult": stop_mult},
        "ticks": {"enabled": False},
        "max_bars_timeout": max_bars,
        "timeout_result": "at_close",   # honest: mark out at the timeout bar close
        "tick_size": inst_cfg["tick_size"],
        "tick_value": inst_cfg["tick_value"],
        "commission_per_side": commission,
        "slippage_ticks": slippage_ticks,
    }
    trades = simulate_atr_outcomes(pending, bars, cfg)
    if trades.empty:
        return pd.DataFrame()
    atr = trades["signal_atr"].astype(float).values
    risk_dollars = stop_mult * atr / inst_cfg["tick_size"] * inst_cfg["tick_value"]
    with np.errstate(divide="ignore", invalid="ignore"):
        trades["R"] = trades["profit_net"].astype(float).values / risk_dollars
    trades = trades[np.isfinite(trades["R"])].copy()
    trades["hour"] = pd.to_datetime(trades["entry_time"]).dt.hour
    return trades


def simulate_R(pending: pd.DataFrame, bars: pd.DataFrame, inst_cfg: Dict,
               *, target_mult: float, stop_mult: float,
               max_bars: int, commission: float = 2.09,
               slippage_ticks: float = 1) -> np.ndarray:
    """Return per-trade NET R-multiples (net P&L / initial dollar risk)."""
    t = simulate_trades(pending, bars, inst_cfg, target_mult=target_mult,
                        stop_mult=stop_mult, max_bars=max_bars,
                        commission=commission, slippage_ticks=slippage_ticks)
    return t["R"].values if not t.empty else np.array([], dtype=float)


# --------------------------------------------------------------------------- #
# Sweep driver                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class Combo:
    family: str
    label: str
    builder: Callable[[pd.DataFrame], pd.DataFrame]
    target_mult: float
    stop_mult: float
    max_bars: int


def build_combos(rth_only: bool) -> List[Combo]:
    combos: List[Combo] = []
    atr_len = 14
    # Mean-reversion sweep -- including EXTREME stretches with WIDE ATR stops.
    # Rationale: zero-cost edge is monotonic in k (more extreme -> bigger edge),
    # and wider ATR stops shrink fixed-cost-as-a-fraction-of-R. Both push the
    # edge/cost ratio toward 1. Longer max_bars lets bigger reversion play out.
    for sma_len in (20,):
        for k in (2.0, 2.5, 3.0, 4.0):
            for tm, sm, mb in (
                (1.5, 1.0, 30), (2.0, 2.0, 60), (3.0, 3.0, 90), (4.0, 3.0, 120),
            ):
                combos.append(Combo(
                    family="MR",
                    label=f"MR k{k} tp{tm} sl{sm} mb{mb}",
                    builder=lambda bars, s=sma_len, kk=k: setup_mean_reversion(
                        bars, sma_len=s, k=kk, atr_len=atr_len, rth_only=rth_only),
                    target_mult=tm, stop_mult=sm, max_bars=mb,
                ))
    # Momentum-breakout sweep
    for lb in (20, 40):
        for tm, sm in ((1.5, 1.0), (2.0, 1.0)):
            combos.append(Combo(
                family="MO",
                label=f"MO lb{lb} tp{tm} sl{sm}",
                builder=lambda bars, l=lb: setup_momentum_breakout(
                    bars, lookback=l, atr_len=atr_len, rth_only=rth_only),
                target_mult=tm, stop_mult=sm, max_bars=30,
            ))
    return combos


def _pooled(arr: np.ndarray):
    """(n, mean, t_stat) for a pooled R array."""
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n == 0:
        return 0, 0.0, 0.0
    sd = arr.std(ddof=1) if n > 1 else 0.0
    t = arr.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return n, float(arr.mean()), float(t)


def run_compare_entry(bars_by_inst, panel, *, k, target_mult, stop_mult, max_bars,
                      rth_only, commission):
    """Market vs passive-limit entry, RTH only, pooled across instruments.

    Tests the proven binding constraint (cost): a limit entry provides liquidity
    (entry slippage ~0; only the stop exit crosses), so slippage_ticks=0.5.
    Reports overall and the best RTH hours.
    """
    configs = [
        ("market (next_open, slip=1.0t)", "market", 1.0),
        ("limit  (passive, slip=0.5t)",   "limit",  0.5),
        ("limit  (passive, slip=0.0t)",   "limit",  0.0),
    ]
    print(f"\nENTRY COMPARISON  MR k{k} tp{target_mult} sl{stop_mult} mb{max_bars} "
          f"RTH-only={rth_only}  (fill selection effect honest for limit)\n")
    print(f"  {'config':<34}{'pooled_N':>10}{'meanR':>9}{'t_stat':>9}{'inst+':>8}")
    best = None
    for label, entry, slip in configs:
        pooled = []
        n_pos = 0
        per_hour: Dict[int, List[float]] = {}
        for inst in panel:
            pend = setup_mean_reversion(bars_by_inst[inst], sma_len=20, k=k,
                                        atr_len=14, rth_only=rth_only, entry=entry)
            tr = simulate_trades(pend, bars_by_inst[inst], INSTRUMENTS[inst],
                                 target_mult=target_mult, stop_mult=stop_mult,
                                 max_bars=max_bars, commission=commission,
                                 slippage_ticks=slip)
            R = tr["R"].values if not tr.empty else np.array([])
            pooled.append(R)
            if R.size and R.mean() > 0:
                n_pos += 1
            if not tr.empty:
                for h, g in tr.groupby("hour"):
                    per_hour.setdefault(int(h), []).extend(g["R"].tolist())
        allR = np.concatenate(pooled) if pooled else np.array([])
        n, mean, t = _pooled(allR)
        print(f"  {label:<34}{n:>10}{mean:>9.3f}{t:>9.2f}{n_pos:>5}/{len(panel)}")
        if entry == "limit" and slip == 0.5:
            best = per_hour
    if best:
        print("\n  Limit (slip=0.5t) by Denver hour:")
        print(f"  {'hour':>5}{'N':>8}{'meanR':>9}{'t_stat':>9}")
        for h in sorted(best):
            arr = np.array(best[h])
            n, mean, t = _pooled(arr)
            flag = "  <-- net positive" if mean > 0 else ""
            print(f"  {h:>5}{n:>8}{mean:>9.3f}{t:>9.2f}{flag}")


def run_profile(bars_by_inst, panel, *, k, target_mult, stop_mult, max_bars,
                rth_only, commission, slippage_ticks):
    """Condition the MR-stretch signal by regime (ER) and hour-of-day, with costs.

    Goal: find a slice where edge-per-trade clears cost AND holds 4/4 instruments.
    """
    print(f"\nProfiling MR k{k} tp{target_mult} sl{stop_mult} mb{max_bars} "
          f"(costs: comm={commission} slip={slippage_ticks}t)\n")

    # --- 1) Regime gate sweep (efficiency ratio) ---------------------------
    print("REGIME GATE (fade only when efficiency ratio <= er_max; 1.0=off)")
    print(f"  {'er_max':>7}{'pooled_N':>10}{'meanR':>9}{'t_stat':>9}{'inst+':>8}")
    best_er = 1.0
    for er_max in (1.0, 0.5, 0.35, 0.25):
        pooled, n_pos = [], 0
        for inst in panel:
            pend = setup_mean_reversion(bars_by_inst[inst], sma_len=20, k=k,
                                        atr_len=14, rth_only=rth_only, er_max=er_max)
            tr = simulate_trades(pend, bars_by_inst[inst], INSTRUMENTS[inst],
                                 target_mult=target_mult, stop_mult=stop_mult,
                                 max_bars=max_bars, commission=commission,
                                 slippage_ticks=slippage_ticks)
            R = tr["R"].values if not tr.empty else np.array([])
            pooled.append(R)
            if R.size and R.mean() > 0:
                n_pos += 1
        allR = np.concatenate(pooled) if pooled else np.array([])
        n, mean, t = _pooled(allR)
        print(f"  {er_max:>7.2f}{n:>10}{mean:>9.3f}{t:>9.2f}{n_pos:>5}/{len(panel)}")

    # --- 2) Hour-of-day profile (regime gate off, all entries) -------------
    print("\nHOUR-OF-DAY (Denver), regime gate off, costs on:")
    print(f"  {'hour':>5}{'pooled_N':>10}{'meanR':>9}{'t_stat':>9}{'inst+':>8}")
    by_hour: Dict[int, List[np.ndarray]] = {}
    for inst in panel:
        pend = setup_mean_reversion(bars_by_inst[inst], sma_len=20, k=k,
                                    atr_len=14, rth_only=rth_only, er_max=1.0)
        tr = simulate_trades(pend, bars_by_inst[inst], INSTRUMENTS[inst],
                             target_mult=target_mult, stop_mult=stop_mult,
                             max_bars=max_bars, commission=commission,
                             slippage_ticks=slippage_ticks)
        if tr.empty:
            continue
        tr["_inst"] = inst
        for h, g in tr.groupby("hour"):
            by_hour.setdefault(int(h), []).append(g[["R"]].assign(_inst=inst))
    for h in sorted(by_hour):
        df = pd.concat(by_hour[h], ignore_index=True)
        n, mean, t = _pooled(df["R"].values)
        n_pos = sum(1 for _, g in df.groupby("_inst") if g["R"].mean() > 0)
        n_inst = df["_inst"].nunique()
        flag = "  <-- positive 4/4" if (mean > 0 and n_pos == n_inst == len(panel)) else (
               "  <-- net positive" if mean > 0 else "")
        print(f"  {h:>5}{n:>10}{mean:>9.3f}{t:>9.2f}{n_pos:>5}/{n_inst}{flag}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["sweep", "profile", "compare-entry"], default="sweep")
    ap.add_argument("--min-pooled-trades", type=int, default=200)
    ap.add_argument("--min-pooled-t", type=float, default=2.0)
    ap.add_argument("--rth-only", action="store_true", help="entries only in RTH (07:30-13:55 MT)")
    ap.add_argument("--no-costs", action="store_true", help="zero commission/slippage (decompose cost drag)")
    ap.add_argument("--profile-k", type=float, default=3.0)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args(argv)
    commission = 0.0 if args.no_costs else 2.09
    slippage_ticks = 0 if args.no_costs else 1

    print("Loading bars ...", flush=True)
    bars_by_inst: Dict[str, pd.DataFrame] = {}
    for inst in INSTRUMENTS:
        b = load_bars(inst)
        bars_by_inst[inst] = b
        if b.empty:
            print(f"  {inst}: NO DATA")
        else:
            print(f"  {inst}: {len(b):,} bars  {b['dt'].min().date()} -> {b['dt'].max().date()}")
    panel = [i for i, b in bars_by_inst.items() if not b.empty]
    print(f"Panel: {panel}\n", flush=True)

    if args.mode == "profile":
        run_profile(bars_by_inst, panel, k=args.profile_k, target_mult=1.5,
                    stop_mult=1.0, max_bars=30, rth_only=args.rth_only,
                    commission=commission, slippage_ticks=slippage_ticks)
        return 0

    if args.mode == "compare-entry":
        run_compare_entry(bars_by_inst, panel, k=args.profile_k, target_mult=1.5,
                          stop_mult=1.0, max_bars=30, rth_only=args.rth_only,
                          commission=commission)
        return 0

    combos = build_combos(args.rth_only)
    rows = []
    for c in combos:
        per_inst_R: Dict[str, np.ndarray] = {}
        for inst in panel:
            bars = bars_by_inst[inst]
            pending = c.builder(bars)
            R = simulate_R(pending, bars, INSTRUMENTS[inst],
                           target_mult=c.target_mult, stop_mult=c.stop_mult,
                           max_bars=c.max_bars, commission=commission,
                           slippage_ticks=slippage_ticks)
            per_inst_R[inst] = R
        verdict = evaluate_cross_instrument(
            {k: v.tolist() for k, v in per_inst_R.items()},
            min_pooled_t=args.min_pooled_t,
            min_pooled_trades=args.min_pooled_trades,
        )
        rows.append((c, verdict, per_inst_R))

    rows.sort(key=lambda r: (r[1].pooled_t_stat if np.isfinite(r[1].pooled_t_stat) else -1e9),
              reverse=True)

    print("=" * 110)
    print(f"{'family':<7}{'label':<30}{'pooled_N':>9}{'t_stat':>9}{'PF':>7}{'meanR':>9}"
          f"{'inst+':>7}  per-instrument N (mean R)")
    print("-" * 110)
    for c, v, per in rows[: args.top]:
        per_str = "  ".join(
            f"{i}:{len(per[i])}({np.mean(per[i]):+.3f})" if len(per[i]) else f"{i}:0"
            for i in panel
        )
        flag = "  <-- ROBUST EDGE" if v.is_robust_edge else ""
        print(f"{c.family:<7}{c.label:<30}{v.pooled_trades:>9}{v.pooled_t_stat:>9.2f}"
              f"{v.pooled_profit_factor:>7.2f}{v.pooled_mean_ticks:>9.3f}"
              f"{v.instruments_profitable:>4}/{v.instruments_total:<2}  {per_str}{flag}")
    print("=" * 110)

    robust = [r for r in rows if r[1].is_robust_edge]
    print(f"\n{len(robust)} of {len(rows)} candidates passed ALL cross-instrument gates "
          f"(pooled t>={args.min_pooled_t}, PF>=1.2, >=75% inst profitable, "
          f"N>={args.min_pooled_trades}).")
    if robust:
        print("\nTop robust candidate gate detail:")
        for line in robust[0][1].reasons:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
