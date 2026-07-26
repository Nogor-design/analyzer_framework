"""Volume / VWAP / session-structure scout — what discretionary day traders use.

Why this exists (2026-06-17)
----------------------------
The prior intraday hunt (cross_instrument_scout, tick_orderflow_scout,
multi_timeframe_scout) covered only PURE-PRICE mechanisms: mean-reversion,
breakout, FVG, large-candle, order-flow. The runbook's own honest bound flags the
gap: "Still-untested, non-pure-price mechanisms a discretionary trader uses
(VWAP/volume-profile/POC, session-open structure, MTF confluence, order-flow at
levels) remain open." This scout closes that gap. It is the obvious untested
sibling to the ICT-style FVG/large-candle family — the volume/VWAP/session family
real intraday traders actually trade off.

Families tested (each is session-anchored — VWAP, opening range, and POC all reset
each RTH day, which is the whole point and is what makes them NOT pure price):

  * VWAP_FADE     -- fade a >= k*ATR stretch from the session VWAP, toward VWAP.
  * VWAP_RECLAIM  -- trade a fresh close-cross of session VWAP in the direction of
                     VWAP slope (the classic "reclaim / loss of VWAP" continuation).
  * ORB           -- opening-range break after the first OR_minutes of RTH, with an
                     optional VOLUME/LIQUIDITY filter (break must come on elevated
                     volume vs the session's running median) — "proper" ORB.
  * ORB_RETEST    -- break, then a passive limit retest of the OR edge, continuation.
  * POC_FADE      -- fade toward the PRIOR session's volume-profile POC (point of
                     control = highest-volume price) when price is >= k*ATR away.
  * MTF confluence-- ORB and VWAP_RECLAIM additionally gated by a HIGHER-timeframe
                     trend (HTF SMA slope), using only the last COMPLETED HTF bar
                     (no lookahead). Emitted side-by-side with the ungated version
                     so "does confluence help?" is read directly off the table.

SAME anti-overfit methodology as the prior scouts (this is the load-bearing part):
  * cost-aware simulation (commission + slippage, conservative tie-breaks)
  * pooled across NQ/ES/YM/RTY in net R-multiples — a fitted artifact dies on 4
    independent instruments
  * ranked by pooled t-stat, NOT profit factor (PF-first selection overfits)
  * --mode holdout: select on a chronological IS split, judge on OOS, SAME params.

Reuses the proven pieces rather than rebuilding:
  * cross_instrument_scout.load_bars / _add_indicators / simulate_trades / _pooled
  * multi_timeframe_scout._market_pending / _limit_pending / _news_mask
  * marketdata.resample.ohlcv_resample_from_bars (1m -> base TF, and -> HTF)
  * analysis.strategy_discovery.cross_instrument.evaluate_cross_instrument

Run:
    python scripts/volume_session_scout.py                       # sweep, base 5m, costs on
    python scripts/volume_session_scout.py --rth-only            # RTH entries only (recommended)
    python scripts/volume_session_scout.py --no-costs            # decompose cost drag
    python scripts/volume_session_scout.py --timeframes 3m,5m,15m
    python scripts/volume_session_scout.py --mode holdout --rth-only
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.strategy_discovery.cross_instrument import evaluate_cross_instrument

from cross_instrument_scout import (  # type: ignore
    INSTRUMENTS,
    RTH_START,
    RTH_END,
    load_bars,
    _add_indicators,
    simulate_trades,
    _pooled,
)
from multi_timeframe_scout import (  # type: ignore
    _market_pending,
    _limit_pending,
)

RTH_START_MIN = RTH_START[0] * 60 + RTH_START[1]   # 07:30 MT
RTH_END_MIN = RTH_END[0] * 60 + RTH_END[1]         # 13:55 MT


# --------------------------------------------------------------------------- #
# Session feature engineering (VWAP, prior-session POC, opening range, vol)    #
# --------------------------------------------------------------------------- #
def _prep(bars: pd.DataFrame, atr_len: int, *, or_minutes: int,
          poc_bin_ticks: int, tick_size: float, vol_med_len: int) -> pd.DataFrame:
    """Attach ATR/next_open (via _add_indicators) plus all session features.

    Everything below is anchored to the RTH session (the discretionary
    convention): VWAP accumulates from the RTH open, the opening range is the
    first OR_minutes of RTH, and the POC is the prior RTH session's
    highest-volume price. Features are NaN outside RTH so those bars never fire.
    """
    b = _add_indicators(bars, atr_len).reset_index(drop=True)
    mins = b["dt"].dt.hour * 60 + b["dt"].dt.minute
    rth = (mins >= RTH_START_MIN) & (mins <= RTH_END_MIN)
    b["_rth"] = rth
    b["date"] = b["dt"].dt.date
    typ = (b["high"] + b["low"] + b["close"]) / 3.0

    # --- Session VWAP (cumulative within each RTH day) ---------------------- #
    pv = (typ * b["volume"]).where(rth, 0.0)
    vol_r = b["volume"].where(rth, 0.0)
    cum_pv = pv.groupby(b["date"]).cumsum()
    cum_vol = vol_r.groupby(b["date"]).cumsum()
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = cum_pv / cum_vol
    b["vwap"] = vwap.where(rth)
    b["vwap_slope"] = b.groupby("date")["vwap"].diff()

    # --- Opening range (first OR_minutes of RTH), mapped to the whole day --- #
    in_or = rth & (mins < RTH_START_MIN + or_minutes)
    after_or = rth & (mins >= RTH_START_MIN + or_minutes)
    or_hi = b["high"].where(in_or).groupby(b["date"]).transform("max")
    or_lo = b["low"].where(in_or).groupby(b["date"]).transform("min")
    b["or_high"] = or_hi
    b["or_low"] = or_lo
    b["_after_or"] = after_or

    # --- Running volume median (liquidity filter reference) ----------------- #
    b["vol_med"] = b["volume"].rolling(vol_med_len, min_periods=vol_med_len // 2).median()
    with np.errstate(divide="ignore", invalid="ignore"):
        b["vol_ratio"] = b["volume"] / b["vol_med"]

    # --- Prior-session volume-profile POC ---------------------------------- #
    bin_size = tick_size * poc_bin_ticks
    poc_by_date: Dict[object, float] = {}
    price_bin = (typ / bin_size).round() * bin_size
    for d, idx in b.groupby("date").groups.items():
        g = b.loc[idx]
        vp = g["volume"].groupby(price_bin.loc[idx]).sum()
        poc_by_date[d] = float(vp.idxmax()) if not vp.empty and vp.max() > 0 else np.nan
    dates_sorted = sorted(poc_by_date)
    prior = {dates_sorted[i]: poc_by_date[dates_sorted[i - 1]]
             for i in range(1, len(dates_sorted))}
    b["prior_poc"] = b["date"].map(prior)
    return b


def _add_htf_trend(b: pd.DataFrame, htf_tf: str, sma_len: int) -> pd.Series:
    """Sign of the higher-timeframe SMA slope, attached to each base bar using
    only the LAST COMPLETED HTF bar (shift(1) before merge_asof => no lookahead).
    +1 = HTF uptrend, -1 = downtrend, 0 = flat/unknown."""
    htf = ohlcv_resample_from_bars(
        b[["dt", "open", "high", "low", "close", "volume"]], htf_tf)
    if htf.empty:
        return pd.Series(0, index=b.index)
    sma = htf["close"].rolling(sma_len, min_periods=sma_len).mean()
    trend = np.sign(sma.diff()).fillna(0)
    # Use the previous completed HTF bar's trend (avoid using the in-progress bar).
    htf_prev = pd.DataFrame({"dt": htf["dt"], "htf_trend": trend.shift(1).fillna(0)})
    merged = pd.merge_asof(
        b[["dt"]].sort_values("dt"), htf_prev.sort_values("dt"),
        on="dt", direction="backward")
    return merged["htf_trend"].fillna(0).to_numpy()


# --------------------------------------------------------------------------- #
# Setups -> pending entries                                                    #
# --------------------------------------------------------------------------- #
def setup_vwap_fade(b: pd.DataFrame, *, k: float, rth_only: bool,
                    news_pad: int) -> pd.DataFrame:
    """Fade a >= k*ATR stretch from session VWAP, market entry toward VWAP."""
    dev = (b["close"] - b["vwap"]) / b["atr"]
    long_sig = (dev <= -k) & b["vwap"].notna()
    short_sig = (dev >= k) & b["vwap"].notna()
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


def setup_vwap_reclaim(b: pd.DataFrame, *, rth_only: bool, news_pad: int,
                       htf_trend: Optional[np.ndarray] = None) -> pd.DataFrame:
    """Fresh close-cross of session VWAP, in the direction of VWAP slope.
    With htf_trend supplied, also require agreement with the higher-TF trend
    (MTF confluence)."""
    above = b["close"] > b["vwap"]
    prev_above = above.shift(1, fill_value=False)
    cross_up = above & ~prev_above & b["vwap"].notna() & (b["vwap_slope"] > 0)
    cross_dn = ~above & prev_above & b["vwap"].notna() & (b["vwap_slope"] < 0)
    if htf_trend is not None:
        ht = pd.Series(htf_trend, index=b.index)
        cross_up &= ht > 0
        cross_dn &= ht < 0
    sig = cross_up | cross_dn
    direction = pd.Series(np.where(cross_up, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


def setup_orb(b: pd.DataFrame, *, vol_mult: float, rth_only: bool, news_pad: int,
              htf_trend: Optional[np.ndarray] = None) -> pd.DataFrame:
    """Opening-range break after the OR window, market entry in break direction.
    vol_mult>0 adds the liquidity filter: the breaking bar's volume must be
    >= vol_mult * the running median volume (real participation, not a drift).
    htf_trend adds MTF confluence (break must agree with HTF trend)."""
    long_sig = b["_after_or"] & (b["close"] > b["or_high"]) & b["or_high"].notna()
    short_sig = b["_after_or"] & (b["close"] < b["or_low"]) & b["or_low"].notna()
    if vol_mult > 0:
        liq = b["vol_ratio"] >= vol_mult
        long_sig &= liq
        short_sig &= liq
    if htf_trend is not None:
        ht = pd.Series(htf_trend, index=b.index)
        long_sig &= ht > 0
        short_sig &= ht < 0
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


def setup_orb_retest(b: pd.DataFrame, *, fill_timeout_bars: int, rth_only: bool,
                     news_pad: int) -> pd.DataFrame:
    """Opening-range break, then a passive limit retest of the OR edge,
    continuation. Long: after a break above OR high, post a limit at or_high;
    fills only if price pulls back to it within fill_timeout_bars."""
    long_sig = b["_after_or"] & (b["close"] > b["or_high"]) & b["or_high"].notna()
    short_sig = b["_after_or"] & (b["close"] < b["or_low"]) & b["or_low"].notna()
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    limit_price = pd.Series(
        np.where(long_sig, b["or_high"], np.where(short_sig, b["or_low"], np.nan)),
        index=b.index)
    return _limit_pending(b, sig, direction, limit_price, fill_timeout_bars,
                          rth_only, news_pad)


def setup_poc_fade(b: pd.DataFrame, *, k: float, rth_only: bool,
                   news_pad: int) -> pd.DataFrame:
    """Fade toward the prior session's volume-profile POC when price is
    >= k*ATR away from it (POC acts as a magnet / value-area pull)."""
    dev = (b["close"] - b["prior_poc"]) / b["atr"]
    long_sig = (dev <= -k) & b["prior_poc"].notna()
    short_sig = (dev >= k) & b["prior_poc"].notna()
    sig = long_sig | short_sig
    direction = pd.Series(np.where(long_sig, 1, -1), index=b.index)
    return _market_pending(b, sig, direction, rth_only, news_pad)


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
    is_limit: bool


def build_combos(rth_only: bool, news_pad: int, *, htf_tf: str,
                 htf_sma: int) -> List[Combo]:
    combos: List[Combo] = []

    # VWAP fade (mean reversion to VWAP)
    for k in (1.0, 1.5, 2.0):
        for tm, sm, mb in ((1.5, 1.0, 12), (2.0, 1.5, 20)):
            combos.append(Combo("VWAP_F", f"VWAPfade k{k} tp{tm} sl{sm}",
                lambda b, kk=k: setup_vwap_fade(b, k=kk, rth_only=rth_only,
                                                news_pad=news_pad),
                tm, sm, mb, is_limit=False))

    # VWAP reclaim (continuation), ungated and HTF-gated (MTF confluence)
    for tm, sm, mb in ((2.0, 1.0, 16), (3.0, 1.5, 24)):
        combos.append(Combo("VWAP_R", f"VWAPreclaim tp{tm} sl{sm}",
            lambda b, t=tm, s=sm: setup_vwap_reclaim(b, rth_only=rth_only,
                                                     news_pad=news_pad),
            tm, sm, mb, is_limit=False))
        combos.append(Combo("VWAP_R+MTF", f"VWAPreclaim+HTF tp{tm} sl{sm}",
            lambda b, t=tm, s=sm: setup_vwap_reclaim(
                b, rth_only=rth_only, news_pad=news_pad,
                htf_trend=_add_htf_trend(b, htf_tf, htf_sma)),
            tm, sm, mb, is_limit=False))

    # Opening-range break: no filter, volume filter, and HTF-confluence
    for tm, sm, mb in ((2.0, 1.0, 16), (3.0, 1.5, 24)):
        combos.append(Combo("ORB", f"ORB tp{tm} sl{sm}",
            lambda b, t=tm, s=sm: setup_orb(b, vol_mult=0.0, rth_only=rth_only,
                                            news_pad=news_pad),
            tm, sm, mb, is_limit=False))
        for vm in (1.5, 2.0):
            combos.append(Combo("ORB_V", f"ORB vol>={vm} tp{tm} sl{sm}",
                lambda b, t=tm, s=sm, v=vm: setup_orb(
                    b, vol_mult=v, rth_only=rth_only, news_pad=news_pad),
                tm, sm, mb, is_limit=False))
        combos.append(Combo("ORB+MTF", f"ORB+HTF tp{tm} sl{sm}",
            lambda b, t=tm, s=sm: setup_orb(
                b, vol_mult=0.0, rth_only=rth_only, news_pad=news_pad,
                htf_trend=_add_htf_trend(b, htf_tf, htf_sma)),
            tm, sm, mb, is_limit=False))

    # Opening-range break-retest (passive limit)
    for tm, sm, mb in ((2.0, 1.0, 20), (3.0, 1.5, 28)):
        combos.append(Combo("ORB_RT", f"ORBretest tp{tm} sl{sm}",
            lambda b, t=tm, s=sm: setup_orb_retest(
                b, fill_timeout_bars=5, rth_only=rth_only, news_pad=news_pad),
            tm, sm, mb, is_limit=True))

    # Prior-session POC fade (volume-profile magnet)
    for k in (1.0, 1.5, 2.5):
        for tm, sm, mb in ((1.5, 1.0, 16), (2.5, 1.5, 24)):
            combos.append(Combo("POC_F", f"POCfade k{k} tp{tm} sl{sm}",
                lambda b, kk=k: setup_poc_fade(b, k=kk, rth_only=rth_only,
                                               news_pad=news_pad),
                tm, sm, mb, is_limit=False))
    return combos


# --------------------------------------------------------------------------- #
# Sweep / holdout drivers                                                      #
# --------------------------------------------------------------------------- #
def _prep_panel(bars_tf, panel, tf, *, atr_len, or_minutes, poc_bin_ticks,
                vol_med_len) -> Dict[str, pd.DataFrame]:
    """_prep every instrument's TF frame once (features are expensive)."""
    out = {}
    for inst in panel:
        b = bars_tf[tf][inst]
        out[inst] = _prep(b, atr_len, or_minutes=or_minutes,
                          poc_bin_ticks=poc_bin_ticks,
                          tick_size=INSTRUMENTS[inst]["tick_size"],
                          vol_med_len=vol_med_len) if not b.empty else b
    return out


def run_sweep(prepped_by_tf, panel, timeframes, *, rth_only, news_pad,
              commission, slip_market, slip_limit, htf_tf, htf_sma,
              min_pooled_t, min_pooled_trades, top) -> int:
    rows = []
    for tf in timeframes:
        prepped = prepped_by_tf[tf]
        combos = build_combos(rth_only, news_pad, htf_tf=htf_tf, htf_sma=htf_sma)
        for c in combos:
            per_inst_R: Dict[str, List[float]] = {}
            for inst in panel:
                b = prepped[inst]
                if b.empty:
                    per_inst_R[inst] = []
                    continue
                pending = c.builder(b)
                tr = simulate_trades(
                    pending, b, INSTRUMENTS[inst],
                    target_mult=c.target_mult, stop_mult=c.stop_mult,
                    max_bars=c.max_bars, commission=commission,
                    slippage_ticks=slip_limit if c.is_limit else slip_market)
                per_inst_R[inst] = tr["R"].tolist() if not tr.empty else []
            verdict = evaluate_cross_instrument(
                per_inst_R, min_pooled_t=min_pooled_t,
                min_pooled_trades=min_pooled_trades)
            rows.append((tf, c, verdict, per_inst_R))

    rows.sort(key=lambda r: (r[2].pooled_t_stat if np.isfinite(r[2].pooled_t_stat)
                             else -1e9), reverse=True)

    print("=" * 122)
    print(f"{'TF':<5}{'family':<11}{'label':<28}{'pooled_N':>9}{'t_stat':>9}"
          f"{'PF':>7}{'meanR':>9}{'inst+':>7}  per-instrument N(meanR)")
    print("-" * 122)
    for tf, c, v, per in rows[:top]:
        per_str = "  ".join(
            f"{i}:{len(per[i])}({np.mean(per[i]):+.3f})" if per[i] else f"{i}:0"
            for i in panel)
        flag = "  <-- ROBUST" if v.is_robust_edge else ""
        print(f"{tf:<5}{c.family:<11}{c.label:<28}{v.pooled_trades:>9}"
              f"{v.pooled_t_stat:>9.2f}{v.pooled_profit_factor:>7.2f}"
              f"{v.pooled_mean_ticks:>9.3f}{v.instruments_profitable:>4}/"
              f"{v.instruments_total:<2}  {per_str}{flag}")
    print("=" * 122)

    robust = [r for r in rows if r[2].is_robust_edge]
    pos_t = [r for r in rows if np.isfinite(r[2].pooled_t_stat)
             and r[2].pooled_t_stat >= min_pooled_t
             and r[2].pooled_trades >= min_pooled_trades]
    print(f"\n{len(robust)} of {len(rows)} combos passed ALL gates "
          f"(pooled t>={min_pooled_t}, PF>=1.2, >=75% inst profitable, "
          f"N>={min_pooled_trades}).")
    print(f"{len(pos_t)} combos cleared pooled t>={min_pooled_t} & N gate "
          f"(t-stat only).")
    if pos_t:
        print("\nTop t-stat survivors (even if PF/inst gates not met):")
        for tf, c, v, per in pos_t[:10]:
            print(f"  {tf:<4}{c.family:<11}{c.label:<26} t={v.pooled_t_stat:6.2f} "
                  f"PF={v.pooled_profit_factor:5.2f} meanR={v.pooled_mean_ticks:+.3f} "
                  f"N={v.pooled_trades} inst={v.instruments_profitable}/"
                  f"{v.instruments_total}")
    return 0


def run_holdout(prepped_by_tf, panel, timeframes, *, frac, rth_only, news_pad,
                commission, slip_market, slip_limit, htf_tf, htf_sma,
                min_is_t) -> int:
    print(f"\nHOLDOUT: per-instrument chronological split, IS=first {frac:.0%} / "
          f"OOS=last {1 - frac:.0%}.  costs={'OFF' if commission == 0 else 'ON'}  "
          f"news_filter={'ON pad=%d' % news_pad if news_pad else 'OFF'}  "
          f"rth_only={rth_only}\n")
    need_oos_inst = math.ceil(0.75 * len(panel))
    rows = []
    for tf in timeframes:
        prepped = prepped_by_tf[tf]
        combos = build_combos(rth_only, news_pad, htf_tf=htf_tf, htf_sma=htf_sma)
        for c in combos:
            is_R: Dict[str, List[float]] = {}
            oos_R: Dict[str, List[float]] = {}
            for inst in panel:
                b = prepped[inst]
                if b.empty:
                    is_R[inst], oos_R[inst] = [], []
                    continue
                cutoff = b["dt"].iloc[int(len(b) * frac)]
                pending = c.builder(b)
                tr = simulate_trades(
                    pending, b, INSTRUMENTS[inst],
                    target_mult=c.target_mult, stop_mult=c.stop_mult,
                    max_bars=c.max_bars, commission=commission,
                    slippage_ticks=slip_limit if c.is_limit else slip_market)
                if tr.empty:
                    is_R[inst], oos_R[inst] = [], []
                    continue
                sig = pd.to_datetime(tr["signal_dt"])
                is_R[inst] = tr.loc[sig < cutoff, "R"].tolist()
                oos_R[inst] = tr.loc[sig >= cutoff, "R"].tolist()
            v_is = evaluate_cross_instrument(is_R, min_pooled_t=min_is_t,
                                             min_pooled_trades=60)
            v_oos = evaluate_cross_instrument(oos_R, min_pooled_t=min_is_t,
                                              min_pooled_trades=1)
            holds = (v_is.pooled_t_stat >= min_is_t
                     and v_oos.pooled_mean_ticks > 0
                     and v_oos.instruments_profitable >= need_oos_inst
                     and v_oos.pooled_trades >= 30)
            rows.append((tf, c, v_is, v_oos, holds))

    rows.sort(key=lambda r: (r[2].pooled_t_stat if np.isfinite(r[2].pooled_t_stat)
                             else -1e9), reverse=True)
    print("=" * 132)
    print(f"{'TF':<4}{'family':<11}{'label':<26}"
          f"|{'IS_N':>7}{'IS_t':>7}{'IS_PF':>6}{'IS_R':>8}{'i+':>4} "
          f"|{'OOS_N':>7}{'OOS_t':>7}{'OOS_PF':>7}{'OOS_R':>8}{'i+':>4}  verdict")
    print("-" * 132)
    for tf, c, vi, vo, holds in rows:
        verdict = "HOLDS <==" if holds else ""
        print(f"{tf:<4}{c.family:<11}{c.label:<26}"
              f"|{vi.pooled_trades:>7}{vi.pooled_t_stat:>7.2f}{vi.pooled_profit_factor:>6.2f}"
              f"{vi.pooled_mean_ticks:>8.3f}{vi.instruments_profitable:>4} "
              f"|{vo.pooled_trades:>7}{vo.pooled_t_stat:>7.2f}{vo.pooled_profit_factor:>7.2f}"
              f"{vo.pooled_mean_ticks:>8.3f}{vo.instruments_profitable:>4}  {verdict}")
    print("=" * 132)
    held = [r for r in rows if r[4]]
    print(f"\n{len(held)} of {len(rows)} candidates HOLD out-of-sample "
          f"(IS t>={min_is_t} AND OOS meanR>0 AND OOS {need_oos_inst}/{len(panel)} "
          f"inst+ AND OOS N>=30).")
    for tf, c, vi, vo, _ in held:
        print(f"  {tf} {c.family} {c.label}: IS t={vi.pooled_t_stat:.2f} "
              f"R={vi.pooled_mean_ticks:+.3f} | OOS t={vo.pooled_t_stat:.2f} "
              f"PF={vo.pooled_profit_factor:.2f} R={vo.pooled_mean_ticks:+.3f} "
              f"inst={vo.instruments_profitable}/{len(panel)}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["sweep", "holdout"], default="sweep")
    ap.add_argument("--timeframes", default="5m",
                    help="comma list of base timeframes for the session setups")
    ap.add_argument("--holdout-frac", type=float, default=0.65)
    ap.add_argument("--min-is-t", type=float, default=2.0)
    ap.add_argument("--min-pooled-trades", type=int, default=150)
    ap.add_argument("--min-pooled-t", type=float, default=2.0)
    ap.add_argument("--or-minutes", type=int, default=30,
                    help="opening-range window length in minutes from RTH open")
    ap.add_argument("--poc-bin-ticks", type=int, default=8,
                    help="price-bin width (in ticks) for the volume-profile POC")
    ap.add_argument("--vol-med-len", type=int, default=20,
                    help="lookback bars for the running median volume (liquidity filter)")
    ap.add_argument("--htf", default="30m", help="higher timeframe for MTF confluence")
    ap.add_argument("--htf-sma", type=int, default=10, help="SMA length on the HTF")
    ap.add_argument("--rth-only", action="store_true")
    ap.add_argument("--news-filter", action="store_true")
    ap.add_argument("--news-pad", type=int, default=10)
    ap.add_argument("--no-costs", action="store_true")
    ap.add_argument("--top", type=int, default=24)
    args = ap.parse_args(argv)

    commission = 0.0 if args.no_costs else 2.09
    slip_market = 0 if args.no_costs else 1
    slip_limit = 0  # passive limit = liquidity-providing
    news_pad = args.news_pad if args.news_filter else 0
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    atr_len = 14

    print("Loading 1m bars ...", flush=True)
    bars1m: Dict[str, pd.DataFrame] = {}
    for inst in INSTRUMENTS:
        b = load_bars(inst)
        bars1m[inst] = b
        print(f"  {inst}: {0 if b.empty else len(b):,} 1m bars"
              + ("" if b.empty else f"  {b['dt'].min().date()} -> {b['dt'].max().date()}"))
    panel = [i for i, b in bars1m.items() if not b.empty]
    print(f"Panel: {panel}   base timeframes: {timeframes}   HTF: {args.htf}   "
          f"costs={'OFF' if args.no_costs else 'ON'}   "
          f"news_filter={'ON pad=%d' % news_pad if news_pad else 'OFF'}   "
          f"rth_only={args.rth_only}\n", flush=True)

    # Resample + feature-prep once per (tf, instrument).
    bars_tf: Dict[str, Dict[str, pd.DataFrame]] = {}
    prepped_by_tf: Dict[str, Dict[str, pd.DataFrame]] = {}
    for tf in timeframes:
        bars_tf[tf] = {inst: ohlcv_resample_from_bars(bars1m[inst], tf) for inst in panel}
        print(f"  prepping session features for {tf} ...", flush=True)
        prepped_by_tf[tf] = _prep_panel(
            bars_tf, panel, tf, atr_len=atr_len, or_minutes=args.or_minutes,
            poc_bin_ticks=args.poc_bin_ticks, vol_med_len=args.vol_med_len)
    print(flush=True)

    if args.mode == "holdout":
        return run_holdout(
            prepped_by_tf, panel, timeframes, frac=args.holdout_frac,
            rth_only=args.rth_only, news_pad=news_pad, commission=commission,
            slip_market=slip_market, slip_limit=slip_limit, htf_tf=args.htf,
            htf_sma=args.htf_sma, min_is_t=args.min_is_t)

    return run_sweep(
        prepped_by_tf, panel, timeframes, rth_only=args.rth_only,
        news_pad=news_pad, commission=commission, slip_market=slip_market,
        slip_limit=slip_limit, htf_tf=args.htf, htf_sma=args.htf_sma,
        min_pooled_t=args.min_pooled_t, min_pooled_trades=args.min_pooled_trades,
        top=args.top)


if __name__ == "__main__":
    raise SystemExit(main())
