"""Tick / order-flow microstructure scout (Lever 4).

Why (2026-06-16, see memory project-edge-sample-power)
------------------------------------------------------
Lever 2 proved a real but tiny 1-min mean-reversion edge (~+0.04R) that costs
(~0.4R) swamp. The only structural way to beat fixed per-trade cost is a bigger
edge PER EVENT. Order flow is a candidate: the ticks carry bid/ask, so each
trade can be signed as an aggressive buy/sell -> true order-flow imbalance (OFI),
the foundational microstructure signal.

Discipline carried from Lever 2:
  * measure NET OF COST from the start (spread cross + commission),
  * pool across NQ/ES/RTY (the index futures with tick parquet), same period,
  * rank by pooled t-stat (vol-normalized so instruments pool cleanly),
  * answer the FOUNDATIONAL question first -- does OFI predict forward return,
    and does the predicted move beat cost -- in an idealized fixed-hold form,
    BEFORE building any entry/exit. If the edge can't beat cost even with a
    perfect fixed hold, no exit policy will save it.

Method
------
  1. Load tick parquet (dt, last, bid, ask, volume), one instrument at a time.
  2. Sign each trade: aggressor = +1 if last>=ask, -1 if last<=bid (tick-rule
     fallback when no quote). signed_vol = aggressor * volume.
  3. Resample to fixed BAR_SECONDS bars: close, volume, signed-volume (OFI).
  4. Per RTH session: imbalance = rolling sum(signed) / rolling sum(volume) over
     LOOKBACK bars; forward return over HOLD bars (in ticks).
  5. Event when |imbalance| >= threshold. Test CONTINUATION (trade with flow) and
     REVERSION (fade flow). Net ticks = dir*fwd_ret_ticks - cost_ticks.
  6. Pool vol-normalized net across instruments -> pooled t-stat + per-inst means.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from ta_foundation.marketdata.tick_cache import TickCacheConfig, load_tick_cache_parquet

CACHE = Path(r"D:\MarketData\.ta_cache")
BAR_SECONDS = 10
COMMISSION_PER_SIDE = 2.09

# Index-futures panel with tick parquet, same ~Dec-2025..Mar-2026 period.
INSTRUMENTS: Dict[str, Dict] = {
    "NQ":  {"tick_size": 0.25, "tick_value": 5.00,  "file": "NQ 03-26 Tick.Last.txt.parquet"},
    "ES":  {"tick_size": 0.25, "tick_value": 12.50, "file": "ES 03-26 Tick.Last.txt.parquet"},
    "RTY": {"tick_size": 0.10, "tick_value": 5.00,  "file": "RTY 03-26 Tick.Last.txt.parquet"},
}

RTH_START_MIN = 7 * 60 + 30   # 07:30 MT
RTH_END_MIN = 14 * 60         # 14:00 MT


def build_bars(inst: str) -> pd.DataFrame:
    """Load ticks, sign trades, resample to BAR_SECONDS bars with OFI. RTH only.

    Caches the resampled OFI bars (small) so repeat runs skip the GB parquet scan.
    """
    bar_cache = CACHE / f"{inst}_ofi{BAR_SECONDS}s_rth.parquet"
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
    # tick-rule fallback where no valid quote / midpoint trade
    need = agg == 0
    if need.any():
        tick_sign = np.sign(np.diff(last, prepend=last[0]))
        agg[need] = tick_sign[need]
    df = df.assign(signed=agg * vol, vol=vol)

    df = df.set_index("dt")
    bars = df.resample(f"{BAR_SECONDS}s").agg(
        close=("last", "last"),
        volume=("vol", "sum"),
        signed=("signed", "sum"),
    ).dropna(subset=["close"])
    bars = bars.reset_index()
    mins = bars["dt"].dt.hour * 60 + bars["dt"].dt.minute
    bars = bars[(mins >= RTH_START_MIN) & (mins <= RTH_END_MIN)].copy()
    bars["date"] = bars["dt"].dt.date
    bars = bars.reset_index(drop=True)
    try:
        bars.drop(columns=["date"]).to_parquet(CACHE / f"{inst}_ofi{BAR_SECONDS}s_rth.parquet", index=False)
    except Exception:
        pass
    return bars


def events_for_combo(bars: pd.DataFrame, tick_size: float, *, lookback: int,
                     hold: int, thr: float, direction: str,
                     cost_ticks: float) -> np.ndarray:
    """Per-event NET ticks for one combo, computed within each RTH session."""
    out: List[np.ndarray] = []
    for _, day in bars.groupby("date"):
        close = day["close"].to_numpy(np.float64)
        signed = day["signed"].to_numpy(np.float64)
        volume = day["volume"].to_numpy(np.float64)
        n = len(day)
        if n < lookback + hold + 1:
            continue
        # rolling sums via cumsum
        csig = np.concatenate([[0.0], np.cumsum(signed)])
        cvol = np.concatenate([[0.0], np.cumsum(volume)])
        roll_sig = csig[lookback:] - csig[:-lookback]          # len n-lookback+1
        roll_vol = cvol[lookback:] - cvol[:-lookback]
        with np.errstate(divide="ignore", invalid="ignore"):
            imb = np.where(roll_vol > 0, roll_sig / roll_vol, 0.0)
        # signal bar index i ranges over [lookback-1 .. n-1]; align imb to it
        idx = np.arange(lookback - 1, n)
        imb_at = imb  # imb[j] corresponds to window ending at bar lookback-1+j
        # forward return over `hold` bars, in ticks
        fwd_end = idx + hold
        valid = fwd_end < n
        idx_v = idx[valid]
        imb_v = imb_at[valid]
        fwd_ticks = (close[idx_v + hold] - close[idx_v]) / tick_size
        if direction == "cont":
            ev_dir = np.sign(imb_v)
        else:  # reversion
            ev_dir = -np.sign(imb_v)
        fire = np.abs(imb_v) >= thr
        net = ev_dir[fire] * fwd_ticks[fire] - cost_ticks
        out.append(net)
    return np.concatenate(out) if out else np.array([], dtype=float)


def events_sweep_reversal(bars: pd.DataFrame, tick_size: float, *, burst: int,
                          k_ticks: float, flow_thr: float, hold: int,
                          cost_ticks: float) -> np.ndarray:
    """Fade a sharp price dislocation driven by one-sided aggressive flow.

    Sweep = price moved >= k_ticks over `burst` bars AND order flow over that
    window is one-sided in the same direction (|imbalance|>=flow_thr). Mechanism:
    late aggressors get trapped at the extreme; forced-cover reversion. Bigger
    move per event than raw OFI, so a better shot at beating fixed cost.
    """
    out: List[np.ndarray] = []
    for _, day in bars.groupby("date"):
        close = day["close"].to_numpy(np.float64)
        signed = day["signed"].to_numpy(np.float64)
        volume = day["volume"].to_numpy(np.float64)
        n = len(day)
        if n < burst + hold + 1:
            continue
        csig = np.concatenate([[0.0], np.cumsum(signed)])
        cvol = np.concatenate([[0.0], np.cumsum(volume)])
        roll_sig = csig[burst:] - csig[:-burst]
        roll_vol = cvol[burst:] - cvol[:-burst]
        with np.errstate(divide="ignore", invalid="ignore"):
            imb = np.where(roll_vol > 0, roll_sig / roll_vol, 0.0)   # ends at bar burst-1+j
        idx = np.arange(burst - 1, n)                                 # signal bar
        # price move over the burst window (bar idx-burst .. idx)
        move = (close[idx] - close[np.maximum(idx - burst, 0)]) / tick_size
        fwd_end = idx + hold
        valid = fwd_end < n
        idx_v, imb_v, move_v = idx[valid], imb[valid], move[valid]
        one_sided = (np.abs(imb_v) >= flow_thr) & (np.sign(move_v) == np.sign(imb_v))
        fire = one_sided & (np.abs(move_v) >= k_ticks)
        ev_dir = -np.sign(move_v)                                     # fade the sweep
        fwd_ticks = (close[idx_v + hold] - close[idx_v]) / tick_size
        net = ev_dir[fire] * fwd_ticks[fire] - cost_ticks
        out.append(net)
    return np.concatenate(out) if out else np.array([], dtype=float)


def pooled_stats(arr: np.ndarray):
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n == 0:
        return 0, 0.0, 0.0
    sd = arr.std(ddof=1) if n > 1 else 0.0
    t = arr.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return n, float(arr.mean()), float(t)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["ofi", "sweep"], default="ofi")
    ap.add_argument("--lookback", type=int, nargs="+", default=[6, 18])
    ap.add_argument("--hold", type=int, nargs="+", default=[6, 18])
    ap.add_argument("--thr", type=float, nargs="+", default=[0.3, 0.5, 0.7])
    ap.add_argument("--burst", type=int, nargs="+", default=[3, 6])
    ap.add_argument("--k-ticks", type=float, nargs="+", default=[8, 15, 25])
    ap.add_argument("--flow-thr", type=float, default=0.3)
    ap.add_argument("--spread-ticks", type=float, default=1.0,
                    help="entry spread-cross cost in ticks (exit assumed passive)")
    args = ap.parse_args(argv)

    print(f"Building {BAR_SECONDS}s OFI bars (RTH) ...", flush=True)
    bars_by_inst: Dict[str, pd.DataFrame] = {}
    cost_ticks_by_inst: Dict[str, float] = {}
    for inst, cfg in INSTRUMENTS.items():
        b = build_bars(inst)
        bars_by_inst[inst] = b
        comm_ticks = 2 * COMMISSION_PER_SIDE / cfg["tick_value"]
        cost_ticks_by_inst[inst] = args.spread_ticks + comm_ticks
        if b.empty:
            print(f"  {inst}: NO DATA")
        else:
            print(f"  {inst}: {len(b):,} bars  {b['dt'].min().date()} -> {b['dt'].max().date()}"
                  f"  cost={cost_ticks_by_inst[inst]:.2f}t")
    panel = [i for i, b in bars_by_inst.items() if not b.empty]
    print(f"Panel: {panel}\n", flush=True)

    # per-instrument vol scale (std of HOLD-agnostic 1-bar return in ticks) for pooling
    vol_scale = {}
    for inst in panel:
        c = bars_by_inst[inst]["close"].to_numpy(np.float64)
        r = np.diff(c) / INSTRUMENTS[inst]["tick_size"]
        vol_scale[inst] = np.std(r[np.isfinite(r)]) or 1.0

    rows = []
    if args.mode == "sweep":
        for burst in args.burst:
            for k in args.k_ticks:
                for hold in args.hold:
                    per_inst, pooled_norm = {}, []
                    for inst in panel:
                        net = events_sweep_reversal(
                            bars_by_inst[inst], INSTRUMENTS[inst]["tick_size"],
                            burst=burst, k_ticks=k, flow_thr=args.flow_thr,
                            hold=hold, cost_ticks=cost_ticks_by_inst[inst])
                        per_inst[inst] = net
                        pooled_norm.append(net / vol_scale[inst])
                    alln = np.concatenate(pooled_norm) if pooled_norm else np.array([])
                    n, _, t = pooled_stats(alln)
                    n_pos = sum(1 for i in panel if per_inst[i].size and per_inst[i].mean() > 0)
                    rows.append((f"swp", burst, hold, k, n, t, n_pos, per_inst))
        hdr2 = "burst"
    else:
        for direction in ("cont", "rev"):
            for lb in args.lookback:
                for hold in args.hold:
                    for thr in args.thr:
                        per_inst, pooled_norm = {}, []
                        for inst in panel:
                            net = events_for_combo(
                                bars_by_inst[inst], INSTRUMENTS[inst]["tick_size"],
                                lookback=lb, hold=hold, thr=thr, direction=direction,
                                cost_ticks=cost_ticks_by_inst[inst])
                            per_inst[inst] = net
                            pooled_norm.append(net / vol_scale[inst])
                        alln = np.concatenate(pooled_norm) if pooled_norm else np.array([])
                        n, _, t = pooled_stats(alln)
                        n_pos = sum(1 for i in panel if per_inst[i].size and per_inst[i].mean() > 0)
                        rows.append((direction, lb, hold, thr, n, t, n_pos, per_inst))
        hdr2 = "lkbk"

    rows.sort(key=lambda r: r[5], reverse=True)
    print("=" * 112)
    thr_hdr = "k_tks" if args.mode == "sweep" else "thr"
    print(f"{'dir':<5}{hdr2:>5}{'hold':>5}{thr_hdr:>6}{'N':>9}{'t_stat':>9}{'inst+':>7}"
          f"   per-instrument mean NET ticks (N)")
    print("-" * 112)
    for direction, lb, hold, thr, n, t, n_pos, per in rows[:20]:
        per_str = "  ".join(
            f"{i}:{per[i].mean():+.2f}t({per[i].size})" if per[i].size else f"{i}:0"
            for i in panel
        )
        flag = "  <-- +ALL" if n_pos == len(panel) and t > 2 else ""
        print(f"{direction:<5}{lb:>5}{hold:>5}{thr:>5.2f}{n:>9}{t:>9.2f}{n_pos:>4}/{len(panel)}"
              f"   {per_str}{flag}")
    print("=" * 112)
    print("\nNET ticks already subtract entry spread + round-trip commission. A positive "
          "per-instrument mean = the predicted move beats cost. t-stat is on vol-normalized "
          "pooled net (consistency across NQ/ES/RTY).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
