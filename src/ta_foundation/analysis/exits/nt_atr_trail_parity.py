"""NT PantheonMaster AtrTrail — backtest-parity replica + comparison harness.

**Trail-parity check, Parity A (Python replica ↔ NT backtest).** Validates that a
Python model of PantheonMaster's AtrTrail exit reproduces NT's actual backtest
exits on the same trades — the prerequisite the design docs flag as "highest
risk" before trusting the exit (and before Parity B, NT-backtest ↔ NT-live).

WHY THIS IS A SEPARATE MODEL FROM ``analysis/exits/simulate.py`` (do not merge):
``simulate.py`` trails the stop **continuously on ticks** — the right model for
ranking hypothetical exit policies over the tick cache. NT's PantheonMaster
AtrTrail trails **once per bar close** off the *bar* high/low and a *bar* ATR,
then fills intrabar on touch. Different mechanics → different model. This module
mirrors the NT bar-close logic exactly so a per-trade price comparison is
meaningful; ``simulate.py`` would diverge by construction.

NT mechanics replicated (PantheonMaster.cs, audited 2026-06-08):
  - SetupBacktestExit (L792-811): initial protective stop = StopTicks below/above
    entry (default StopTicks=60); trailing policies start there.
  - ManageHistoricalOrBarCloseDynamicStops / AtrTrail (L1128-1132): each bar close
    proposed stop = highSinceEntry − AtrTrailMultiple·currentAtr (long) /
    lowSinceEntry + AtrTrailMultiple·currentAtr (short).
  - MoveHistoricalStopIfImproved (L1076-1098): RoundToTick, then move ONLY if it
    improves (ratchet) by > 0.5·TickSize.
  - currentAtr = ATR(AtrPeriod)[0] (L990,400; AtrPeriod default 14). NT's ATR
    smoothing is the open parity question — we run the replica under BOTH Wilder
    and SMA ATR and report which matches (the documented "NT ATR Wilder-vs-SMA"
    trap). Managed stop fills at the stop price on intrabar touch (Low≤stop long).

This module uses MINUTE BARS only (no tick file needed): the stop price is set at
bar close and a managed stop fills *at* that price on touch, so bar high/low
suffices to reproduce the exit price. Tick data would only refine sub-bar fill
*timing*, not the price the parity check turns on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.features.regime import atr_wilder

AtrMode = Literal["wilder", "sma"]


@dataclass(frozen=True)
class NtAtrTrailConfig:
    """PantheonMaster AtrTrail params (defaults = the strategy's SetDefaults)."""
    atr_period: int = 14
    atr_multiple: float = 2.0
    stop_ticks: int = 60          # initial protective stop (CalculationMode.Ticks)
    tick_size: float = 0.25       # NQ
    atr_mode: AtrMode = "wilder"  # which ATR smoothing to test for parity
    fill_tolerance_ticks: float = 1.0  # |replica_exit − nt_exit| <= this == match
    max_hold_minutes: int = 1440  # cap the per-trade replay window (intraday trades)


def _round_to_tick(price: float, tick: float) -> float:
    return round(price / tick) * tick


def _sma_atr(bars: pd.DataFrame, period: int) -> pd.Series:
    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")
    c = pd.to_numeric(bars["close"], errors="coerce")
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(window=int(period), min_periods=int(period)).mean()


def compute_atr(bars: pd.DataFrame, period: int, mode: AtrMode) -> pd.Series:
    """ATR series aligned to ``bars`` rows. ``wilder`` reuses the shared
    ``atr_wilder``; ``sma`` is a simple rolling mean of true range."""
    if mode == "sma":
        return _sma_atr(bars, period)
    return atr_wilder(bars, period=period)


def replicate_nt_atr_trail(
    *,
    entry_dt: datetime,
    entry_price: float,
    direction: int,                 # +1 long, -1 short
    bars: pd.DataFrame,             # dt-sorted; columns dt/high/low/close + 'atr'
    cfg: NtAtrTrailConfig,
) -> dict[str, Any]:
    """Replay NT's bar-close AtrTrail from ``entry_dt`` and return the modeled
    stop exit (or ``no_exit_in_window`` if the trail never fills before bars run
    out — in NT that trade exited via session-close / opposite-signal, not the
    trail, so it's excluded from trail parity)."""
    tick = cfg.tick_size
    bdt = pd.to_datetime(bars["dt"])
    entry_ts = pd.to_datetime(entry_dt)
    horizon = entry_ts + pd.Timedelta(minutes=cfg.max_hold_minutes)
    after = bars[(bdt > entry_ts) & (bdt <= horizon)].reset_index(drop=True)
    initial_stop = entry_price - direction * cfg.stop_ticks * tick
    stop = initial_stop
    high_since = entry_price
    low_since = entry_price

    for row in after.itertuples(index=False):
        hi, lo, atr = float(row.high), float(row.low), row.atr
        # 1) fill check against the stop active for this bar (set at prior close)
        if direction > 0 and lo <= stop:
            return {"exit_dt": row.dt, "exit_price": stop, "reason": "trail_stop"}
        if direction < 0 and hi >= stop:
            return {"exit_dt": row.dt, "exit_price": stop, "reason": "trail_stop"}
        # 2) update favorable extreme with this bar, recompute trail for next bar
        high_since = max(high_since, hi)
        low_since = min(low_since, lo)
        if atr is None or (isinstance(atr, float) and np.isnan(atr)):
            continue
        if direction > 0:
            proposed = _round_to_tick(high_since - cfg.atr_multiple * float(atr), tick)
            if proposed > stop + tick * 0.5:   # ratchet up only
                stop = proposed
        else:
            proposed = _round_to_tick(low_since + cfg.atr_multiple * float(atr), tick)
            if proposed < stop - tick * 0.5:   # ratchet down only
                stop = proposed

    return {"exit_dt": None, "exit_price": None, "reason": "no_exit_in_window"}


def replicate_nt_atr_trail_tick(
    *,
    entry_price: float,
    direction: int,
    tick_prices: np.ndarray,
    tick_dts: np.ndarray,
    tick_atr: np.ndarray,
    cfg: NtAtrTrailConfig,
) -> dict[str, Any]:
    """LIVE analog of the trail (Parity B): identical to the bar replica EXCEPT
    the favorable reference is the **tick** high/low (running max/min over ticks
    since entry), mirroring NT's live ``ManageLiveDynamicStop`` (OnMarketData,
    tick-by-tick) vs the backtest's bar-close ``highSinceEntry``. ATR is still the
    bar-close ATR (NT updates ``currentAtr`` at bar close in both paths). Fully
    vectorized: the resting stop a tick fills against is the stop set by *prior*
    ticks (a long never trips its own freshly-raised stop). This isolates the
    single live-vs-backtest difference: tick-high vs bar-high reference."""
    p = np.asarray(tick_prices, dtype=float)
    if p.size == 0:
        return {"exit_dt": None, "exit_price": None, "reason": "no_ticks"}
    a = np.nan_to_num(np.asarray(tick_atr, dtype=float), nan=1e9)  # NaN ATR => no trail pull
    tick = cfg.tick_size
    initial = entry_price - direction * cfg.stop_ticks * tick
    if direction > 0:
        run_ext = np.maximum.accumulate(p)
        raw = np.round((run_ext - cfg.atr_multiple * a) / tick) * tick
        stop_set = np.maximum(initial, np.maximum.accumulate(raw))      # ratchet up, floored
        stop_active = np.concatenate(([initial], stop_set[:-1]))        # resting stop (prior tick)
        hit = p <= stop_active
    else:
        run_ext = np.minimum.accumulate(p)
        raw = np.round((run_ext + cfg.atr_multiple * a) / tick) * tick
        stop_set = np.minimum(initial, np.minimum.accumulate(raw))      # ratchet down, capped
        stop_active = np.concatenate(([initial], stop_set[:-1]))
        hit = p >= stop_active
    if not hit.any():
        return {"exit_dt": None, "exit_price": None, "reason": "no_exit_in_window"}
    idx = int(np.argmax(hit))
    return {"exit_dt": tick_dts[idx], "exit_price": float(stop_active[idx]), "reason": "trail_stop"}


# NT Trades.csv exit names that mean "the (trailed) protective stop fired".
# Backtest (managed) reports "stop loss"; live (unmanaged) reports the explicit
# stop SIGNAL name (PantheonMaster.cs L838-839: LongStopSignal/ShortStopSignal,
# default "Stop"/"StopLong"/"StopShort"). Keep both vocabularies here so the
# live↔backtest diff tags the same population on either side.
NT_TRAIL_EXIT_NAMES = {
    "stop loss", "stop", "trail stop", "trailing stop",
    "stoplong", "stopshort", "long stop", "short stop",
}


def diff_backtest_vs_live_trades(
    bt: pd.DataFrame,
    live: pd.DataFrame,
    *,
    match_tolerance_s: float = 2.0,
    point_value: float = 20.0,
    trail_only: bool = False,
) -> dict[str, Any]:
    """Parity B Step 2 core: per-trade diff of an NT **backtest** Trades.csv
    against the matching NT **live/replay** Trades.csv, matched on entry time.

    This is the source-of-truth measurement of the AtrTrail backtest→live gap
    (the ``ChangeOrder`` Output prints are diagnostic only, and carry no
    timestamp). Both frames must have columns: ``entry_dt`` (tz-aware or naive,
    compared as datetimes), ``exit_dt``, ``entry_price``, ``exit_price``,
    ``direction`` (+1/-1), ``profit`` ($ per trade as NT reports it), and
    ``exit_name``. Matching is greedy one-to-one: each backtest trade pairs with
    the *nearest-entry-time* live trade of the same direction within
    ``match_tolerance_s`` seconds (entries are signal-driven at bar close, so
    they line up tightly even when exits diverge — which is the whole point).

    Returns ``summary`` (matched counts, aggregate P&L both sides, the haircut
    $ and %, per-trade exit-price/exit-time/P&L delta stats) plus a ``detail``
    DataFrame and the unmatched-trade counts on each side. A negative
    ``haircut_pct`` means live made less than backtest (the predicted direction:
    live trails tighter → exits trend runners earlier).
    """
    def _norm(df: pd.DataFrame, tag: str) -> pd.DataFrame:
        d = df.copy()
        d["entry_dt"] = pd.to_datetime(d["entry_dt"])
        d["exit_dt"] = pd.to_datetime(d["exit_dt"])
        d["direction"] = d["direction"].astype(int)
        d["trail_flag"] = d["exit_name"].astype(str).str.strip().str.lower().isin(NT_TRAIL_EXIT_NAMES)
        return d.sort_values("entry_dt").reset_index(drop=True)

    b = _norm(bt, "bt")
    lv = _norm(live, "live")
    tol = pd.Timedelta(seconds=match_tolerance_s)

    used_live: set[int] = set()
    rows: list[dict[str, Any]] = []
    for bt_row in b.itertuples(index=False):
        cands = lv[(lv["direction"] == bt_row.direction)
                   & ((lv["entry_dt"] - bt_row.entry_dt).abs() <= tol)]
        cands = cands[~cands.index.isin(used_live)]
        if cands.empty:
            rows.append({"matched": False, **_bt_only_row(bt_row, point_value)})
            continue
        gap = (cands["entry_dt"] - bt_row.entry_dt).abs()
        j = gap.idxmin()
        used_live.add(j)
        lr = lv.loc[j]
        # favorable-signed exit-price delta: + means live exited at a better price
        exit_px_delta_ticks = (float(lr["exit_price"]) - float(bt_row.exit_price)) * bt_row.direction
        rows.append({
            "matched": True,
            "entry_dt": bt_row.entry_dt, "direction": int(bt_row.direction),
            "is_trail": bool(bt_row.trail_flag),
            "bt_exit_name": bt_row.exit_name, "live_exit_name": lr["exit_name"],
            "bt_exit_price": float(bt_row.exit_price), "live_exit_price": float(lr["exit_price"]),
            "exit_px_delta": float(lr["exit_price"]) - float(bt_row.exit_price),
            "exit_px_delta_fav": exit_px_delta_ticks,
            "exit_time_delta_s": (lr["exit_dt"] - bt_row.exit_dt).total_seconds(),
            "bt_profit": float(bt_row.profit), "live_profit": float(lr["profit"]),
            "pnl_delta": float(lr["profit"]) - float(bt_row.profit),
        })

    detail = pd.DataFrame(rows)
    matched = detail[detail["matched"]] if len(detail) else detail
    if trail_only and len(matched):
        matched = matched[matched["is_trail"]]
    n_match = int(len(matched))

    bt_pnl = float(matched["bt_profit"].sum()) if n_match else 0.0
    live_pnl = float(matched["live_profit"].sum()) if n_match else 0.0
    haircut = live_pnl - bt_pnl
    summary = {
        "n_bt": int(len(b)), "n_live": int(len(lv)),
        "n_matched": n_match,
        "n_bt_unmatched": int((~detail["matched"]).sum()) if len(detail) else 0,
        "n_live_unmatched": int(len(lv) - len(used_live)),
        "bt_pnl": bt_pnl, "live_pnl": live_pnl,
        "haircut": haircut,
        "haircut_pct": (haircut / bt_pnl * 100.0) if bt_pnl else float("nan"),
        "n_worse_live": int((matched["pnl_delta"] < 0).sum()) if n_match else 0,
        "median_pnl_delta": float(matched["pnl_delta"].median()) if n_match else float("nan"),
        "median_exit_time_delta_s": float(matched["exit_time_delta_s"].median()) if n_match else float("nan"),
    }
    return {"summary": summary, "detail": detail}


def _bt_only_row(bt_row: Any, point_value: float) -> dict[str, Any]:
    return {
        "entry_dt": bt_row.entry_dt, "direction": int(bt_row.direction),
        "is_trail": bool(bt_row.trail_flag),
        "bt_exit_name": bt_row.exit_name, "live_exit_name": None,
        "bt_exit_price": float(bt_row.exit_price), "live_exit_price": None,
        "exit_px_delta": None, "exit_px_delta_fav": None, "exit_time_delta_s": None,
        "bt_profit": float(bt_row.profit), "live_profit": None, "pnl_delta": None,
    }


def compare_nt_atr_trail(
    trades: pd.DataFrame,
    bars: pd.DataFrame,
    cfg: NtAtrTrailConfig,
) -> dict[str, Any]:
    """Per-trade comparison of the replica vs NT's actual stop exits.

    ``trades`` columns: entry_dt, entry_price, direction (+1/-1), nt_exit_price,
    nt_exit_name. Only trades NT closed via a stop are graded (trail parity is
    about the trail; session-close/opposite-signal exits are reported separately).
    """
    work = bars.sort_values("dt").reset_index(drop=True).copy()
    work["atr"] = compute_atr(work, cfg.atr_period, cfg.atr_mode).values

    rows: list[dict[str, Any]] = []
    for t in trades.itertuples(index=False):
        name = str(getattr(t, "nt_exit_name", "") or "").strip().lower()
        is_stop = name in NT_TRAIL_EXIT_NAMES
        rep = replicate_nt_atr_trail(
            entry_dt=t.entry_dt, entry_price=float(t.entry_price),
            direction=int(t.direction), bars=work, cfg=cfg,
        )
        nt_px = getattr(t, "nt_exit_price", None)
        diff_ticks = None
        match = None
        if is_stop and rep["exit_price"] is not None and nt_px is not None:
            diff_ticks = abs(float(rep["exit_price"]) - float(nt_px)) / cfg.tick_size
            match = diff_ticks <= cfg.fill_tolerance_ticks
        rows.append({
            "entry_dt": t.entry_dt, "direction": int(t.direction),
            "nt_exit_name": name, "graded": is_stop,
            "nt_exit_price": nt_px, "replica_exit_price": rep["exit_price"],
            "replica_reason": rep["reason"], "diff_ticks": diff_ticks, "match": match,
        })
    detail = pd.DataFrame(rows)
    graded = detail[detail["graded"] & detail["match"].notna()]
    n_graded = int(len(graded))
    n_match = int(graded["match"].sum()) if n_graded else 0
    return {
        "atr_mode": cfg.atr_mode,
        "n_trades": int(len(detail)),
        "n_stop_exits": int(detail["graded"].sum()),
        "n_graded": n_graded,
        "n_match": n_match,
        "match_rate": (n_match / n_graded) if n_graded else None,
        "median_diff_ticks": float(graded["diff_ticks"].median()) if n_graded else None,
        "detail": detail,
    }
