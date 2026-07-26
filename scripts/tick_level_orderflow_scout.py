"""Order-flow AT LEVELS scout — the last unexplored intraday lever.

Why this exists (2026-06-17, see memory project-edge-sample-power)
-----------------------------------------------------------------
Every prior scout came back negative net of cost:
  * cross_instrument_scout / multi_timeframe_scout  -> pure-price MR/breakout/FVG/
    large-candle, 1m..30m  -> no edge survives cost under OOS.
  * tick_orderflow_scout  -> OFI continuation/reversion + sweep-reversal in OPEN
    SPACE  -> gross predictive move ~0, loses net of cost.
  * volume_session_scout  -> VWAP/POC/ORB/MTF on bars  -> 0 survive cost/OOS.

The runbook's remaining named gap: "order-flow at LEVELS (tick/DOM absorption at
VWAP/POC/OR edges), which needs the tick parquet not bars." That is the one
mechanism a discretionary order-flow trader actually claims an edge from and that
NONE of the above tested: not "does flow predict in general" (it doesn't, net of
cost) but "is flow informative SPECIFICALLY when price is AT a structural level."
Two opposite, mechanism-grounded plays:

  * ABSORPTION (fade) -- price reaches a level, aggressive flow pushes INTO it
    (one-sided imbalance toward the level) but price fails to break: passive
    limit orders at the level are absorbing the aggression -> reversal AWAY from
    the level. The classic "trapped aggressors at a level" read.
  * INITIATIVE (continue) -- price breaks THROUGH the level WITH confirming
    one-sided flow: real initiative, not a stop-run -> continuation.

Levels tested (session-anchored, same as volume_session_scout): session VWAP,
prior-session volume-profile POC, opening-range high/low.

SAME anti-overfit discipline as every prior scout (the load-bearing part):
  * NET OF COST from the start (entry spread cross + round-trip commission),
  * pooled across NQ/ES/RTY (the index futures with tick parquet), vol-normalized
    net ticks so instruments pool cleanly,
  * ranked by pooled t-stat, NOT hit-rate / PF,
  * --mode holdout: per-instrument chronological IS/OOS on the SAME params.

Reuses tick_orderflow_scout's tick loading + signing; extends build_bars to also
carry high/low (needed to detect a level *touch*), cached to its own parquet so
the GB tick scan happens once.

Run:
    python scripts/tick_level_orderflow_scout.py                 # absorption + initiative sweep
    python scripts/tick_level_orderflow_scout.py --no-costs      # decompose cost drag
    python scripts/tick_level_orderflow_scout.py --mode holdout
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.tick_cache import TickCacheConfig, load_tick_cache_parquet

from tick_orderflow_scout import (  # type: ignore
    CACHE,
    BAR_SECONDS,
    COMMISSION_PER_SIDE,
    INSTRUMENTS,
    RTH_START_MIN,
    RTH_END_MIN,
    pooled_stats,
)

OR_MINUTES = 30
POC_BIN_TICKS = 8


# --------------------------------------------------------------------------- #
# Bars with OFI + high/low (extends tick_orderflow_scout.build_bars)          #
# --------------------------------------------------------------------------- #
def build_bars_hl(inst: str) -> pd.DataFrame:
    """10s RTH bars with close/high/low/volume/signed(OFI). Cached separately
    from the base scout's bars because we additionally need high/low to detect a
    level touch."""
    bar_cache = CACHE / f"{inst}_ofilvl{BAR_SECONDS}s_rth.parquet"
    if bar_cache.exists():
        b = pd.read_parquet(bar_cache)
        b["date"] = pd.to_datetime(b["dt"]).dt.date
        return b
    cfg = TickCacheConfig()
    df = load_tick_cache_parquet(CACHE / INSTRUMENTS[inst]["file"], cfg=cfg)
    if df is None or df.empty:
        return pd.DataFrame()
    last = df["last"].to_numpy(np.float64)
    bid = df["bid"].to_numpy(np.float64)
    ask = df["ask"].to_numpy(np.float64)
    vol = df["volume"].to_numpy(np.float64)
    agg = np.zeros(len(df), dtype=np.float64)
    has_q = (ask > 0) & (bid > 0) & (ask >= bid)
    agg[(last >= ask) & has_q] = 1.0
    agg[(last <= bid) & has_q] = -1.0
    need = agg == 0
    if need.any():
        tick_sign = np.sign(np.diff(last, prepend=last[0]))
        agg[need] = tick_sign[need]
    df = df.assign(signed=agg * vol, vol=vol)
    df = df.set_index("dt")
    bars = df.resample(f"{BAR_SECONDS}s").agg(
        open=("last", "first"),
        high=("last", "max"),
        low=("last", "min"),
        close=("last", "last"),
        volume=("vol", "sum"),
        signed=("signed", "sum"),
    ).dropna(subset=["close"]).reset_index()
    mins = bars["dt"].dt.hour * 60 + bars["dt"].dt.minute
    bars = bars[(mins >= RTH_START_MIN) & (mins <= RTH_END_MIN)].reset_index(drop=True)
    try:
        bars.to_parquet(bar_cache, index=False)
    except Exception:
        pass
    bars["date"] = bars["dt"].dt.date
    return bars


# --------------------------------------------------------------------------- #
# Session levels                                                               #
# --------------------------------------------------------------------------- #
def add_levels(bars: pd.DataFrame, tick_size: float) -> pd.DataFrame:
    """Attach session VWAP, prior-session POC, opening-range high/low + masks."""
    b = bars.copy()
    b["date"] = b["dt"].dt.date
    mins = b["dt"].dt.hour * 60 + b["dt"].dt.minute
    typ = (b["high"] + b["low"] + b["close"]) / 3.0

    # Session VWAP (cumulative within each RTH day)
    cum_pv = (typ * b["volume"]).groupby(b["date"]).cumsum()
    cum_v = b["volume"].groupby(b["date"]).cumsum()
    with np.errstate(divide="ignore", invalid="ignore"):
        b["vwap"] = cum_pv / cum_v

    # Opening range
    in_or = mins < RTH_START_MIN + OR_MINUTES
    b["or_high"] = b["high"].where(in_or).groupby(b["date"]).transform("max")
    b["or_low"] = b["low"].where(in_or).groupby(b["date"]).transform("min")
    b["after_or"] = mins >= RTH_START_MIN + OR_MINUTES

    # Prior-session volume-profile POC
    bin_size = tick_size * POC_BIN_TICKS
    price_bin = (typ / bin_size).round() * bin_size
    poc: Dict[object, float] = {}
    for d, idx in b.groupby("date").groups.items():
        vp = b.loc[idx, "volume"].groupby(price_bin.loc[idx]).sum()
        poc[d] = float(vp.idxmax()) if not vp.empty and vp.max() > 0 else np.nan
    dates = sorted(poc)
    prior = {dates[i]: poc[dates[i - 1]] for i in range(1, len(dates))}
    b["prior_poc"] = b["date"].map(prior)
    return b


# --------------------------------------------------------------------------- #
# Event engine — order flow at a level                                         #
# --------------------------------------------------------------------------- #
def level_events(bars: pd.DataFrame, tick_size: float, *, level_col: str,
                 active_col: Optional[str], lookback: int, hold: int, thr: float,
                 tol_ticks: float, mode: str, cost_ticks: float,
                 return_dt: bool = False):
    """Per-event NET ticks (and optional signal dt) for one (level, mode) combo.

    mode="absorption": fade aggressive flow that pushes into a level but fails to
    break it (price stays on its side). mode="initiative": continue a break
    through the level confirmed by one-sided flow. Side (resistance vs support)
    is inferred per bar from the PRIOR close vs the level, so one function covers
    both directions and every level type.
    """
    nets: List[np.ndarray] = []
    dts: List[np.ndarray] = []
    tol = tol_ticks * tick_size
    for _, day in bars.groupby("date"):
        n = len(day)
        if n < lookback + hold + 1:
            continue
        close = day["close"].to_numpy(np.float64)
        high = day["high"].to_numpy(np.float64)
        low = day["low"].to_numpy(np.float64)
        signed = day["signed"].to_numpy(np.float64)
        volume = day["volume"].to_numpy(np.float64)
        L = day[level_col].to_numpy(np.float64)
        active = (day[active_col].to_numpy(bool) if active_col else
                  np.ones(n, dtype=bool))
        dt_arr = day["dt"].to_numpy()

        # rolling imbalance over `lookback` bars, aligned to the ending bar i
        csig = np.concatenate([[0.0], np.cumsum(signed)])
        cvol = np.concatenate([[0.0], np.cumsum(volume)])
        imb = np.full(n, np.nan)
        rs = csig[lookback:] - csig[:-lookback]
        rv = cvol[lookback:] - cvol[:-lookback]
        with np.errstate(divide="ignore", invalid="ignore"):
            imb[lookback - 1:] = np.where(rv > 0, rs / rv, 0.0)

        prev_close = np.concatenate([[np.nan], close[:-1]])
        is_res = prev_close < L          # level sits above -> resistance
        is_sup = prev_close > L          # level sits below -> support
        # "at the level" = the bar's range entered the [L-tol, L+tol] zone.
        # (A literal straddle low<=L<=high almost never fires for a slow-moving
        # VWAP and collapses N back into the no-power regime.)
        touched = (low <= L + tol) & (high >= L - tol)
        base = active & np.isfinite(L) & np.isfinite(imb)

        if mode == "absorption":
            short_ev = base & is_res & touched & (imb >= thr) & (close <= L + tol)
            long_ev = base & is_sup & touched & (imb <= -thr) & (close >= L - tol)
        else:  # initiative
            short_ev = base & is_sup & (close < L - tol) & (imb <= -thr)
            long_ev = base & is_res & (close > L + tol) & (imb >= thr)

        ev_dir = np.where(long_ev, 1.0, np.where(short_ev, -1.0, 0.0))
        fire = (long_ev | short_ev)
        idx = np.arange(n)
        fwd_ok = idx + hold < n
        fire &= fwd_ok
        if not fire.any():
            continue
        fi = np.where(fire)[0]
        fwd_ticks = (close[fi + hold] - close[fi]) / tick_size
        net = ev_dir[fi] * fwd_ticks - cost_ticks
        nets.append(net)
        if return_dt:
            dts.append(dt_arr[fi])
    net_all = np.concatenate(nets) if nets else np.array([], dtype=float)
    if return_dt:
        dt_all = np.concatenate(dts) if dts else np.array([], dtype="datetime64[ns]")
        return net_all, dt_all
    return net_all


# --------------------------------------------------------------------------- #
# Combo grid                                                                   #
# --------------------------------------------------------------------------- #
LEVELS = [
    ("vwap", "VWAP", None),
    ("prior_poc", "POC", None),
    ("or_high", "ORhi", "after_or"),
    ("or_low", "ORlo", "after_or"),
]


def build_grid(lookbacks, holds, thrs, tols):
    grid = []
    for mode in ("absorption", "initiative"):
        for lvl_col, lvl_name, active in LEVELS:
            for lb in lookbacks:
                for hold in holds:
                    for thr in thrs:
                        for tol in tols:
                            grid.append((mode, lvl_col, lvl_name, active,
                                         lb, hold, thr, tol))
    return grid


# --------------------------------------------------------------------------- #
# Drivers                                                                      #
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["sweep", "holdout"], default="sweep")
    ap.add_argument("--lookback", type=int, nargs="+", default=[6, 18])
    ap.add_argument("--hold", type=int, nargs="+", default=[6, 18, 36])
    ap.add_argument("--thr", type=float, nargs="+", default=[0.3, 0.5])
    ap.add_argument("--tol-ticks", type=float, nargs="+", default=[2.0, 4.0])
    ap.add_argument("--spread-ticks", type=float, default=1.0)
    ap.add_argument("--no-costs", action="store_true")
    ap.add_argument("--holdout-frac", type=float, default=0.65)
    ap.add_argument("--min-pooled-t", type=float, default=2.0)
    ap.add_argument("--min-pooled-n", type=int, default=150)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)

    print(f"Building {BAR_SECONDS}s OFI+HL bars (RTH) ...", flush=True)
    bars_by_inst: Dict[str, pd.DataFrame] = {}
    cost_by_inst: Dict[str, float] = {}
    for inst, cfg in INSTRUMENTS.items():
        b = build_bars_hl(inst)
        if not b.empty:
            b = add_levels(b, cfg["tick_size"])
        bars_by_inst[inst] = b
        comm_ticks = 2 * COMMISSION_PER_SIDE / cfg["tick_value"]
        cost_by_inst[inst] = 0.0 if args.no_costs else args.spread_ticks + comm_ticks
        if b.empty:
            print(f"  {inst}: NO DATA")
        else:
            print(f"  {inst}: {len(b):,} bars  {b['dt'].min().date()} -> "
                  f"{b['dt'].max().date()}  cost={cost_by_inst[inst]:.2f}t")
    panel = [i for i, b in bars_by_inst.items() if not b.empty]
    print(f"Panel: {panel}   costs={'OFF' if args.no_costs else 'ON'}\n", flush=True)
    if not panel:
        print("No tick data available — nothing to test.")
        return 1

    # vol scale per instrument (std of 1-bar tick return) for pooling
    vol_scale = {}
    for inst in panel:
        c = bars_by_inst[inst]["close"].to_numpy(np.float64)
        r = np.diff(c) / INSTRUMENTS[inst]["tick_size"]
        vol_scale[inst] = np.std(r[np.isfinite(r)]) or 1.0

    grid = build_grid(args.lookback, args.hold, args.thr, args.tol_ticks)

    if args.mode == "holdout":
        return run_holdout(bars_by_inst, panel, grid, cost_by_inst, vol_scale,
                           frac=args.holdout_frac, min_t=args.min_pooled_t)

    rows = []
    for (mode, lvl_col, lvl_name, active, lb, hold, thr, tol) in grid:
        per_inst, pooled_norm = {}, []
        for inst in panel:
            net = level_events(
                bars_by_inst[inst], INSTRUMENTS[inst]["tick_size"],
                level_col=lvl_col, active_col=active, lookback=lb, hold=hold,
                thr=thr, tol_ticks=tol, mode=mode, cost_ticks=cost_by_inst[inst])
            per_inst[inst] = net
            pooled_norm.append(net / vol_scale[inst])
        alln = np.concatenate(pooled_norm) if pooled_norm else np.array([])
        n, _, t = pooled_stats(alln)
        n_pos = sum(1 for i in panel if per_inst[i].size and per_inst[i].mean() > 0)
        rows.append((mode, lvl_name, lb, hold, thr, tol, n, t, n_pos, per_inst))

    rows.sort(key=lambda r: r[7], reverse=True)
    print("=" * 120)
    print(f"{'mode':<11}{'lvl':<6}{'lkbk':>5}{'hold':>5}{'thr':>5}{'tol':>5}"
          f"{'N':>9}{'t_stat':>8}{'inst+':>7}   per-instrument mean NET ticks (N)")
    print("-" * 120)
    for mode, lvl, lb, hold, thr, tol, n, t, n_pos, per in rows[:args.top]:
        per_str = "  ".join(
            f"{i}:{per[i].mean():+.2f}t({per[i].size})" if per[i].size else f"{i}:0"
            for i in panel)
        flag = ("  <-- +ALL t>2" if n_pos == len(panel) and t > args.min_pooled_t
                and n >= args.min_pooled_n else "")
        print(f"{mode:<11}{lvl:<6}{lb:>5}{hold:>5}{thr:>5.2f}{tol:>5.1f}{n:>9}"
              f"{t:>8.2f}{n_pos:>4}/{len(panel)}   {per_str}{flag}")
    print("=" * 120)
    survivors = [r for r in rows if r[7] >= args.min_pooled_t
                 and r[6] >= args.min_pooled_n and r[8] == len(panel)]
    print(f"\n{len(survivors)} of {len(rows)} combos cleared pooled t>="
          f"{args.min_pooled_t}, N>={args.min_pooled_n}, AND all {len(panel)} "
          f"instruments net-positive.")
    print("\nNET ticks already subtract entry spread + round-trip commission. "
          "t-stat is on vol-normalized pooled net (cross-instrument consistency).")
    return 0


def run_holdout(bars_by_inst, panel, grid, cost_by_inst, vol_scale, *, frac, min_t):
    print(f"\nHOLDOUT: per-instrument chronological split IS=first {frac:.0%} / "
          f"OOS=last {1 - frac:.0%}; select on IS, judge on OOS, SAME params.\n")
    need_oos = math.ceil(0.75 * len(panel))
    # precompute per-instrument date cutoff
    cutoff = {}
    for inst in panel:
        dts = np.sort(bars_by_inst[inst]["date"].unique())
        cutoff[inst] = dts[int(len(dts) * frac)]

    rows = []
    for (mode, lvl_col, lvl_name, active, lb, hold, thr, tol) in grid:
        is_norm, oos_norm = [], []
        is_pos = oos_pos = 0
        for inst in panel:
            net, dt = level_events(
                bars_by_inst[inst], INSTRUMENTS[inst]["tick_size"],
                level_col=lvl_col, active_col=active, lookback=lb, hold=hold,
                thr=thr, tol_ticks=tol, mode=mode, cost_ticks=cost_by_inst[inst],
                return_dt=True)
            if net.size == 0:
                continue
            d = pd.to_datetime(dt).date
            is_m = np.array([x < cutoff[inst] for x in d])
            ni, no = net[is_m], net[~is_m]
            is_norm.append(ni / vol_scale[inst])
            oos_norm.append(no / vol_scale[inst])
            if ni.size and ni.mean() > 0:
                is_pos += 1
            if no.size and no.mean() > 0:
                oos_pos += 1
        ia = np.concatenate(is_norm) if is_norm else np.array([])
        oa = np.concatenate(oos_norm) if oos_norm else np.array([])
        nis, _, tis = pooled_stats(ia)
        nos, mos, tos = pooled_stats(oa)
        holds = (tis >= min_t and nos >= 60 and mos > 0 and oos_pos >= need_oos)
        rows.append((mode, lvl_name, lb, hold, thr, tol, nis, tis, is_pos,
                     nos, tos, mos, oos_pos, holds))

    rows.sort(key=lambda r: r[7], reverse=True)
    print("=" * 126)
    print(f"{'mode':<11}{'lvl':<6}{'lkbk':>5}{'hold':>5}{'thr':>5}{'tol':>5}"
          f"|{'IS_N':>7}{'IS_t':>7}{'i+':>4} |{'OOS_N':>7}{'OOS_t':>7}{'OOSmean':>9}{'i+':>4}  verdict")
    print("-" * 126)
    for (mode, lvl, lb, hold, thr, tol, nis, tis, ip, nos, tos, mos, op, holds) in rows[:30]:
        v = "HOLDS <==" if holds else ""
        print(f"{mode:<11}{lvl:<6}{lb:>5}{hold:>5}{thr:>5.2f}{tol:>5.1f}"
              f"|{nis:>7}{tis:>7.2f}{ip:>4} |{nos:>7}{tos:>7.2f}{mos:>9.3f}{op:>4}  {v}")
    print("=" * 126)
    held = [r for r in rows if r[13]]
    print(f"\n{len(held)} of {len(rows)} candidates HOLD out-of-sample "
          f"(IS t>={min_t} AND OOS mean>0 AND OOS {need_oos}/{len(panel)} inst+ AND OOS N>=60).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
