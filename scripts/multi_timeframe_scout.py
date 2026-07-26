"""Multi-timeframe, multi-mechanism scout — the UNTESTED directions.

Why this exists (2026-06-16)
----------------------------
The earlier intraday edge hunt (scripts/cross_instrument_scout.py +
tick_orderflow_scout.py) tested only a NARROW slice: mean-reversion, breakout,
and order-flow at <=1-minute / 10-second timescales. It found those lose net of
a fixed ~$2/side cost because a 1-min move is tiny relative to that cost. That
result was over-generalized to "intraday is dead" — it is NOT. The runbook's own
honest bound: "limited to the mechanisms tried, <=1-min timescales ... 'No edge
found here' != 'no edge exists.'"

The logical next direction (which the cost analysis itself points at) is a
BIGGER MOVE PER TRADE, so fixed cost becomes a rounding error:

  * HIGHER TIMEFRAMES  -- 2m / 3m / 5m / 15m / 30m, where the captured move is
                          multiples of the 1-min move but cost is unchanged.
  * RICHER SETUPS      -- FVG (fair-value-gap) retest continuation, and
                          large-candle retracement after a momentum move with
                          min/max size gates.
  * NEWS FILTERING     -- optionally drop scheduled high-impact-release windows
                          that inject noise an edge can hide behind.

Same anti-overfit methodology as before (this is the part that matters):
  * cost-aware simulation (commission + slippage, conservative tie-breaks)
  * pooled across NQ/ES/YM/RTY -- a fitted artifact dies on 4 instruments
  * ranked by pooled t-stat, NOT profit factor (PF-first selection overfits)

This reuses the proven pieces:
  * cross_instrument_scout.load_bars / simulate_trades / _pooled / _add_indicators
  * marketdata.resample.ohlcv_resample_from_bars   (1m -> Nm)
  * analysis.strategy_discovery.cross_instrument.evaluate_cross_instrument

Run:
    python scripts/multi_timeframe_scout.py                 # sweep all TFs x setups
    python scripts/multi_timeframe_scout.py --rth-only --news-filter
    python scripts/multi_timeframe_scout.py --timeframes 5m,15m,30m
    python scripts/multi_timeframe_scout.py --no-costs      # decompose cost drag
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.strategy_discovery.cross_instrument import evaluate_cross_instrument

# Reuse the EXACT loaders, cost model, and pooling from the original scout so
# results are comparable and there is one cost model, not two.
from cross_instrument_scout import (  # type: ignore
    INSTRUMENTS,
    RTH_START,
    RTH_END,
    load_bars,
    _add_indicators,
    simulate_trades,
    _pooled,
)


# --------------------------------------------------------------------------- #
# News-window proxy filter                                                     #
# --------------------------------------------------------------------------- #
# Times in America/Denver (MT). These are the recurring high-impact US release
# clocks: 06:30 MT = 08:30 ET (CPI/PPI/NFP/jobless/retail), 08:00 MT = 10:00 ET
# (ISM/consumer sentiment), 12:00 MT = 14:00 ET (FOMC statement). This is a
# TIME-OF-DAY PROXY, not a dated calendar -- it answers "does removing the
# obvious release windows help"; a dated econ calendar would be the next refine.
NEWS_WINDOWS_MT = [(6, 30), (8, 0), (12, 0)]


def _news_mask(dt: pd.Series, pad_minutes: int) -> pd.Series:
    """True where dt is INSIDE a news window (to be EXCLUDED)."""
    mins = dt.dt.hour * 60 + dt.dt.minute
    bad = pd.Series(False, index=dt.index)
    for h, m in NEWS_WINDOWS_MT:
        center = h * 60 + m
        bad |= (mins >= center - pad_minutes) & (mins <= center + pad_minutes)
    return bad


# --------------------------------------------------------------------------- #
# Pending-frame assembly helpers                                               #
# --------------------------------------------------------------------------- #
def _session_mask(b: pd.DataFrame, rth_only: bool) -> pd.Series:
    ok = pd.Series(True, index=b.index)
    if rth_only:
        mins = b["dt"].dt.hour * 60 + b["dt"].dt.minute
        ok &= (mins >= RTH_START[0] * 60 + RTH_START[1]) & (mins <= RTH_END[0] * 60 + RTH_END[1])
    return ok


def _market_pending(b: pd.DataFrame, sig: pd.Series, direction: pd.Series,
                    rth_only: bool, news_pad: int) -> pd.DataFrame:
    """next_open market entry (crosses the spread; full slippage)."""
    fresh = sig & ~sig.shift(1, fill_value=False)
    ok = fresh & b["atr"].notna() & (b["atr"] > 0) & b["next_open"].notna() & _session_mask(b, rth_only)
    if news_pad > 0:
        ok &= ~_news_mask(b["dt"], news_pad)
    return pd.DataFrame({
        "signal_dt": b.loc[ok, "dt"],
        "dt": b.loc[ok, "dt"],
        "direction": direction.loc[ok].astype(int),
        "signal_atr": b.loc[ok, "atr"],
        "timing_mode": "next_open",
        "entry_price": b.loc[ok, "next_open"],
    }).reset_index(drop=True)


def _limit_pending(b: pd.DataFrame, sig: pd.Series, direction: pd.Series,
                   limit_price: pd.Series, fill_timeout_bars: int,
                   rth_only: bool, news_pad: int) -> pd.DataFrame:
    """Passive limit entry (body_midpoint): fills only if price trades back to
    the level within fill_timeout_bars, else the signal is skipped (honest
    selection effect). Liquidity-providing -> entry slippage ~0."""
    fresh = sig & ~sig.shift(1, fill_value=False)
    ok = fresh & b["atr"].notna() & (b["atr"] > 0) & limit_price.notna() & _session_mask(b, rth_only)
    if news_pad > 0:
        ok &= ~_news_mask(b["dt"], news_pad)
    return pd.DataFrame({
        "signal_dt": b.loc[ok, "dt"],
        "dt": b.loc[ok, "dt"],
        "direction": direction.loc[ok].astype(int),
        "signal_atr": b.loc[ok, "atr"],
        "timing_mode": "body_midpoint",
        "limit_price": limit_price.loc[ok],
        "entry_price": limit_price.loc[ok],
        "fill_timeout_bars": fill_timeout_bars,
    }).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Setups                                                                       #
# --------------------------------------------------------------------------- #
def setup_mr(bars: pd.DataFrame, *, sma_len: int, k: float, atr_len: int,
             rth_only: bool, news_pad: int) -> pd.DataFrame:
    """Fade a >= k*ATR stretch from a short SMA (market entry, continuation OFF)."""
    b = _add_indicators(bars, atr_len, er_len=sma_len)
    sma = b["close"].rolling(sma_len, min_periods=sma_len).mean()
    dev = (b["close"] - sma) / b["atr"]
    long_sig, short_sig = (dev <= -k), (dev >= k)
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


def setup_breakout(bars: pd.DataFrame, *, lookback: int, atr_len: int,
                   rth_only: bool, news_pad: int) -> pd.DataFrame:
    """Break of an N-bar range, in the break direction (market entry)."""
    b = _add_indicators(bars, atr_len)
    prior_high = b["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    prior_low = b["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    long_sig = b["close"] > prior_high
    short_sig = b["close"] < prior_low
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


def setup_fvg(bars: pd.DataFrame, *, atr_len: int, gap_min_atr: float,
              gap_max_atr: float, fill_timeout_bars: int,
              rth_only: bool, news_pad: int) -> pd.DataFrame:
    """Fair-Value-Gap retest continuation.

    A 3-bar bullish FVG forms at bar i when low[i] > high[i-2] (an unfilled gap
    left by a displacement candle i-1). Standard play: buy the retrace INTO the
    gap and continue up. We place a passive limit at the gap's near edge
    (high[i-2] for bullish; low[i-2] for bearish), valid for the next
    fill_timeout_bars, direction = continuation. Gap size is gated to
    [gap_min_atr, gap_max_atr] * ATR (min: meaningful displacement; max: avoid
    blow-off gaps that don't fill).
    """
    b = _add_indicators(bars, atr_len).reset_index(drop=True)
    high2 = b["high"].shift(2)
    low2 = b["low"].shift(2)
    bull_gap = b["low"] - high2          # >0 => bullish FVG size (price units)
    bear_gap = low2 - b["high"]          # >0 => bearish FVG size
    lo, hi = gap_min_atr * b["atr"], gap_max_atr * b["atr"]
    bull = (bull_gap > 0) & (bull_gap >= lo) & (bull_gap <= hi)
    bear = (bear_gap > 0) & (bear_gap >= lo) & (bear_gap <= hi)
    sig = bull | bear
    direction = pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=b.index)
    # near edge of the gap = level price must retrace to in order to fill
    limit_price = pd.Series(np.where(bull, high2, np.where(bear, low2, np.nan)), index=b.index)
    return _limit_pending(b, sig, direction, limit_price, fill_timeout_bars, rth_only, news_pad)


def setup_large_candle_retrace(bars: pd.DataFrame, *, atr_len: int,
                               size_min_atr: float, size_max_atr: float,
                               retrace_frac: float, fill_timeout_bars: int,
                               rth_only: bool, news_pad: int) -> pd.DataFrame:
    """Large-candle retracement after a momentum move (your spec).

    Trigger: a single momentum candle whose RANGE is in
    [size_min_atr, size_max_atr] * ATR (min: it is actually a momentum candle;
    max: avoid exhaustion blow-offs), and whose close is in the move direction.
    Entry: passive limit at a retrace_frac pullback of the candle body, in the
    CONTINUATION direction; fills only if price pulls back within
    fill_timeout_bars (honest selection).
    """
    b = _add_indicators(bars, atr_len).reset_index(drop=True)
    rng = (b["high"] - b["low"])
    body = (b["close"] - b["open"])
    lo, hi = size_min_atr * b["atr"], size_max_atr * b["atr"]
    is_big = (rng >= lo) & (rng <= hi) & (b["atr"] > 0)
    bull = is_big & (body > 0)
    bear = is_big & (body < 0)
    sig = bull | bear
    direction = pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=b.index)
    # retrace into the body: long pullback = close - frac*body; short = close - frac*body (body<0 -> above)
    limit_price = pd.Series(
        np.where(sig, b["close"] - retrace_frac * body, np.nan), index=b.index
    )
    return _limit_pending(b, sig, direction, limit_price, fill_timeout_bars, rth_only, news_pad)


# --------------------------------------------------------------------------- #
# Combo definitions                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Combo:
    family: str
    label: str
    builder: Callable[[pd.DataFrame], pd.DataFrame]
    target_mult: float
    stop_mult: float
    max_bars: int


def build_combos(rth_only: bool, news_pad: int) -> List[Combo]:
    atr_len = 14
    combos: List[Combo] = []

    # MR fade -- now on HIGHER TFs, where reversion move >> 1-min move
    for k in (1.5, 2.0, 3.0):
        for tm, sm, mb in ((2.0, 1.5, 12), (3.0, 2.0, 20)):
            combos.append(Combo("MR", f"MR k{k} tp{tm} sl{sm}",
                lambda bars, kk=k: setup_mr(bars, sma_len=20, k=kk, atr_len=atr_len,
                                            rth_only=rth_only, news_pad=news_pad),
                tm, sm, mb))

    # Breakout continuation
    for lb in (10, 20):
        for tm, sm in ((2.0, 1.0), (3.0, 1.5)):
            combos.append(Combo("MO", f"MO lb{lb} tp{tm} sl{sm}",
                lambda bars, l=lb: setup_breakout(bars, lookback=l, atr_len=atr_len,
                                                  rth_only=rth_only, news_pad=news_pad),
                tm, sm, 15))

    # FVG retest continuation
    for gmin, gmax in ((0.25, 2.0), (0.5, 3.0)):
        for tm, sm in ((2.0, 1.0), (3.0, 1.5)):
            combos.append(Combo("FVG", f"FVG g{gmin}-{gmax} tp{tm} sl{sm}",
                lambda bars, a=gmin, z=gmax: setup_fvg(bars, atr_len=atr_len,
                    gap_min_atr=a, gap_max_atr=z, fill_timeout_bars=5,
                    rth_only=rth_only, news_pad=news_pad),
                tm, sm, 20))

    # Large-candle retracement continuation (min/max size gate + retrace frac)
    for smin, smax in ((1.0, 3.0), (1.5, 4.0)):
        for rf in (0.4, 0.6):
            for tm, sm in ((2.0, 1.0), (3.0, 1.5)):
                combos.append(Combo("LCR", f"LCR s{smin}-{smax} r{rf} tp{tm} sl{sm}",
                    lambda bars, a=smin, z=smax, r=rf: setup_large_candle_retrace(
                        bars, atr_len=atr_len, size_min_atr=a, size_max_atr=z,
                        retrace_frac=r, fill_timeout_bars=5,
                        rth_only=rth_only, news_pad=news_pad),
                    tm, sm, 20))
    return combos


# --------------------------------------------------------------------------- #
# Holdout drill-down (in-sample / out-of-sample on the same shared params)     #
# --------------------------------------------------------------------------- #
import math


def _candidate_combos(rth_only: bool, news_pad: int) -> List["TFCombo"]:
    """Finer grid AROUND the promising shapes (15m MR, 5m FVG), across nearby TFs."""
    atr_len = 14
    out: List[TFCombo] = []
    mb_by_tf = {"3m": 24, "5m": 20, "15m": 12, "30m": 8}
    # Mean-reversion fade
    for tf in ("5m", "15m", "30m"):
        for k in (1.0, 1.25, 1.5, 2.0):
            for tm, sm in ((2.0, 1.5), (3.0, 2.0)):
                out.append(TFCombo(tf, "MR", f"MR k{k} tp{tm} sl{sm}",
                    lambda bars, kk=k: setup_mr(bars, sma_len=20, k=kk, atr_len=atr_len,
                                                rth_only=rth_only, news_pad=news_pad),
                    tm, sm, mb_by_tf[tf], is_limit=False))
    # FVG retest continuation
    for tf in ("3m", "5m", "15m"):
        for gmin, gmax in ((0.5, 3.0), (0.75, 4.0)):
            for tm, sm in ((2.0, 1.0), (3.0, 1.5)):
                out.append(TFCombo(tf, "FVG", f"FVG g{gmin}-{gmax} tp{tm} sl{sm}",
                    lambda bars, a=gmin, z=gmax: setup_fvg(bars, atr_len=atr_len,
                        gap_min_atr=a, gap_max_atr=z, fill_timeout_bars=5,
                        rth_only=rth_only, news_pad=news_pad),
                    tm, sm, mb_by_tf[tf], is_limit=True))
    return out


@dataclass
class TFCombo:
    tf: str
    family: str
    label: str
    builder: Callable[[pd.DataFrame], pd.DataFrame]
    target_mult: float
    stop_mult: float
    max_bars: int
    is_limit: bool


def run_holdout(bars_tf, panel, *, frac, rth_only, news_pad, commission,
                slip_market, slip_limit, min_is_t) -> int:
    """Select on IS, judge on OOS. A combo HOLDS only if it is significant
    in-sample AND still positive across >=75% of instruments out-of-sample on
    the SAME shared parameters (no per-split refitting)."""
    print(f"\nHOLDOUT: per-instrument chronological split, IS=first {frac:.0%} / "
          f"OOS=last {1 - frac:.0%}.  costs={'OFF' if commission == 0 else 'ON'}  "
          f"news_filter={'ON pad=%d' % news_pad if news_pad else 'OFF'}  "
          f"rth_only={rth_only}\n")
    combos = _candidate_combos(rth_only, news_pad)
    need_oos_inst = math.ceil(0.75 * len(panel))
    rows = []
    for c in combos:
        is_R: Dict[str, List[float]] = {}
        oos_R: Dict[str, List[float]] = {}
        for inst in panel:
            bars = bars_tf[c.tf][inst]
            if bars.empty:
                is_R[inst], oos_R[inst] = [], []
                continue
            cutoff = bars["dt"].iloc[int(len(bars) * frac)]
            pending = c.builder(bars)
            tr = simulate_trades(
                pending, bars, INSTRUMENTS[inst],
                target_mult=c.target_mult, stop_mult=c.stop_mult,
                max_bars=c.max_bars, commission=commission,
                slippage_ticks=slip_limit if c.is_limit else slip_market,
            )
            if tr.empty:
                is_R[inst], oos_R[inst] = [], []
                continue
            sig = pd.to_datetime(tr["signal_dt"])
            is_R[inst] = tr.loc[sig < cutoff, "R"].tolist()
            oos_R[inst] = tr.loc[sig >= cutoff, "R"].tolist()
        v_is = evaluate_cross_instrument(is_R, min_pooled_t=min_is_t, min_pooled_trades=80)
        v_oos = evaluate_cross_instrument(oos_R, min_pooled_t=min_is_t, min_pooled_trades=1)
        holds = (
            v_is.pooled_t_stat >= min_is_t
            and v_oos.pooled_mean_ticks > 0
            and v_oos.instruments_profitable >= need_oos_inst
            and v_oos.pooled_trades >= 40
        )
        rows.append((c, v_is, v_oos, holds))

    rows.sort(key=lambda r: (r[1].pooled_t_stat if np.isfinite(r[1].pooled_t_stat) else -1e9),
              reverse=True)
    print("=" * 124)
    print(f"{'TF':<4}{'fam':<5}{'label':<24}"
          f"|{'IS_N':>7}{'IS_t':>7}{'IS_PF':>6}{'IS_R':>8}{'i+':>4} "
          f"|{'OOS_N':>7}{'OOS_t':>7}{'OOS_PF':>7}{'OOS_R':>8}{'i+':>4}  verdict")
    print("-" * 124)
    for c, vi, vo, holds in rows:
        verdict = "HOLDS <==" if holds else ""
        print(f"{c.tf:<4}{c.family:<5}{c.label:<24}"
              f"|{vi.pooled_trades:>7}{vi.pooled_t_stat:>7.2f}{vi.pooled_profit_factor:>6.2f}"
              f"{vi.pooled_mean_ticks:>8.3f}{vi.instruments_profitable:>4} "
              f"|{vo.pooled_trades:>7}{vo.pooled_t_stat:>7.2f}{vo.pooled_profit_factor:>7.2f}"
              f"{vo.pooled_mean_ticks:>8.3f}{vo.instruments_profitable:>4}  {verdict}")
    print("=" * 124)
    held = [r for r in rows if r[3]]
    print(f"\n{len(held)} of {len(rows)} candidates HOLD out-of-sample "
          f"(IS t>={min_is_t} AND OOS meanR>0 AND OOS {need_oos_inst}/{len(panel)} inst+ AND OOS N>=40).")
    for c, vi, vo, _ in held:
        print(f"  {c.tf} {c.family} {c.label}: "
              f"IS t={vi.pooled_t_stat:.2f} R={vi.pooled_mean_ticks:+.3f} | "
              f"OOS t={vo.pooled_t_stat:.2f} PF={vo.pooled_profit_factor:.2f} "
              f"R={vo.pooled_mean_ticks:+.3f} inst={vo.instruments_profitable}/{len(panel)}")
    return 0


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["sweep", "holdout"], default="sweep")
    ap.add_argument("--holdout-frac", type=float, default=0.65,
                    help="fraction of each instrument's history used as in-sample")
    ap.add_argument("--min-is-t", type=float, default=2.0,
                    help="minimum in-sample pooled t to consider a candidate")
    ap.add_argument("--timeframes", default="2m,3m,5m,15m,30m",
                    help="comma list of timeframes to resample 1m bars to")
    ap.add_argument("--min-pooled-trades", type=int, default=150)
    ap.add_argument("--min-pooled-t", type=float, default=2.0)
    ap.add_argument("--rth-only", action="store_true")
    ap.add_argument("--news-filter", action="store_true",
                    help="exclude entries inside the recurring news windows (proxy)")
    ap.add_argument("--news-pad", type=int, default=10,
                    help="minutes +/- each news window to exclude")
    ap.add_argument("--no-costs", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)

    commission = 0.0 if args.no_costs else 2.09
    slippage_market = 0 if args.no_costs else 1
    slippage_limit = 0 if args.no_costs else 0  # limit = liquidity-providing
    news_pad = args.news_pad if args.news_filter else 0
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    print("Loading 1m bars ...", flush=True)
    bars1m: Dict[str, pd.DataFrame] = {}
    for inst in INSTRUMENTS:
        b = load_bars(inst)
        bars1m[inst] = b
        print(f"  {inst}: {0 if b.empty else len(b):,} 1m bars"
              + ("" if b.empty else f"  {b['dt'].min().date()} -> {b['dt'].max().date()}"))
    panel = [i for i, b in bars1m.items() if not b.empty]
    print(f"Panel: {panel}   timeframes: {timeframes}   "
          f"costs={'OFF' if args.no_costs else 'ON'}   "
          f"news_filter={'ON pad=%d' % news_pad if news_pad else 'OFF'}\n", flush=True)

    # Holdout mode needs the candidate timeframes regardless of --timeframes.
    if args.mode == "holdout":
        for tf in ("3m", "5m", "15m", "30m"):
            if tf not in timeframes:
                timeframes.append(tf)

    # Pre-resample once per (instrument, timeframe)
    bars_tf: Dict[str, Dict[str, pd.DataFrame]] = {}
    for tf in timeframes:
        bars_tf[tf] = {}
        for inst in panel:
            bars_tf[tf][inst] = ohlcv_resample_from_bars(bars1m[inst], tf)

    if args.mode == "holdout":
        return run_holdout(
            bars_tf, panel, frac=args.holdout_frac, rth_only=args.rth_only,
            news_pad=news_pad, commission=commission,
            slip_market=slippage_market, slip_limit=slippage_limit,
            min_is_t=args.min_is_t,
        )

    rows = []
    for tf in timeframes:
        combos = build_combos(args.rth_only, news_pad)
        for c in combos:
            per_inst_R: Dict[str, List[float]] = {}
            for inst in panel:
                bars = bars_tf[tf][inst]
                pending = c.builder(bars)
                is_limit = c.family in ("FVG", "LCR")
                tr = simulate_trades(
                    pending, bars, INSTRUMENTS[inst],
                    target_mult=c.target_mult, stop_mult=c.stop_mult,
                    max_bars=c.max_bars, commission=commission,
                    slippage_ticks=slippage_limit if is_limit else slippage_market,
                )
                per_inst_R[inst] = tr["R"].tolist() if not tr.empty else []
            verdict = evaluate_cross_instrument(
                per_inst_R, min_pooled_t=args.min_pooled_t,
                min_pooled_trades=args.min_pooled_trades,
            )
            rows.append((tf, c, verdict, per_inst_R))

    rows.sort(key=lambda r: (r[2].pooled_t_stat if np.isfinite(r[2].pooled_t_stat) else -1e9),
              reverse=True)

    print("=" * 118)
    print(f"{'TF':<5}{'family':<6}{'label':<28}{'pooled_N':>9}{'t_stat':>9}{'PF':>7}"
          f"{'meanR':>9}{'inst+':>7}  per-instrument N(meanR)")
    print("-" * 118)
    for tf, c, v, per in rows[: args.top]:
        per_str = "  ".join(
            f"{i}:{len(per[i])}({np.mean(per[i]):+.3f})" if per[i] else f"{i}:0"
            for i in panel
        )
        flag = "  <-- ROBUST" if v.is_robust_edge else ""
        print(f"{tf:<5}{c.family:<6}{c.label:<28}{v.pooled_trades:>9}{v.pooled_t_stat:>9.2f}"
              f"{v.pooled_profit_factor:>7.2f}{v.pooled_mean_ticks:>9.3f}"
              f"{v.instruments_profitable:>4}/{v.instruments_total:<2}  {per_str}{flag}")
    print("=" * 118)

    robust = [r for r in rows if r[2].is_robust_edge]
    pos_t2 = [r for r in rows if np.isfinite(r[2].pooled_t_stat)
              and r[2].pooled_t_stat >= args.min_pooled_t
              and r[2].pooled_trades >= args.min_pooled_trades]
    print(f"\n{len(robust)} of {len(rows)} combos passed ALL gates "
          f"(pooled t>={args.min_pooled_t}, PF>=1.2, >=75% inst profitable, "
          f"N>={args.min_pooled_trades}).")
    print(f"{len(pos_t2)} combos cleared pooled t>={args.min_pooled_t} & N gate "
          f"(t-stat only, ignoring PF/inst gates).")
    if pos_t2:
        print("\nTop t-stat survivors (even if PF/inst gates not met):")
        for tf, c, v, per in pos_t2[:10]:
            print(f"  {tf:<4}{c.family:<5}{c.label:<26} t={v.pooled_t_stat:6.2f} "
                  f"PF={v.pooled_profit_factor:5.2f} meanR={v.pooled_mean_ticks:+.3f} "
                  f"N={v.pooled_trades} inst={v.instruments_profitable}/{v.instruments_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
