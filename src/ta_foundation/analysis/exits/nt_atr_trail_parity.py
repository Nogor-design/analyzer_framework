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


# NT Trades.csv exit names that mean "the (trailed) protective stop fired".
NT_TRAIL_EXIT_NAMES = {"stop loss", "stop", "trail stop", "trailing stop"}


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
