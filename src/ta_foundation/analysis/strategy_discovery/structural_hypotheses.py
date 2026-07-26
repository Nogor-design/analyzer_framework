from __future__ import annotations

"""Pre-registered structural-hypothesis edge discovery.

The parameter-sweep approach (sweep N strategies, pick the best) is
structurally data-hungry: multiple-testing inflation demands roughly
sqrt(2*ln(N)) sigma per test to keep a false-discovery rate, so on 3-5
months of intraday data a 500+ combo sweep cannot reliably distinguish a
real edge from noise (an exhaustive 8-family / 4-instrument scan
2026-05-22 confirmed this -- zero genuine edges, see
docs/runbooks/manual_pipeline_proof.md "Edge search").

This module is the alternative architecture:

  * Few hypotheses, theory-grounded, with **fixed parameters** -- no sweep.
    Each test gets full statistical power (~12x data-efficiency vs an N=500
    sweep).
  * Validated cross-instrument via `evaluate_cross_instrument` -- ~4x trades
    for the same calendar window.

Combined effective data efficiency vs the sweep: roughly 50x. Five months
of NQ becomes the detection-equivalent of ~20 years for these hypotheses.

To add a hypothesis: write `fn(bars, tick_size, **fixed_kwargs) -> list[Trade]`
that defines the *full* trade rule (entry + exit, fixed parameters derived
from market structure -- not tuned to data), register it in HYPOTHESES, and
verify it on the panel. The discipline is: few, fixed, theory-grounded.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence

import numpy as np
import pandas as pd
from scipy import stats as _stats

from ta_foundation.analysis.strategy_discovery.cross_instrument import (
    CrossInstrumentVerdict,
    evaluate_cross_instrument,
)


def _one_tail_p_from_t(t_stat: float, df: int) -> float:
    """One-tail (upper) p-value for a t-statistic on a candidate-edge hypothesis.

    For a directionally pre-registered hypothesis we test H0: mean <= 0
    against H1: mean > 0 -- so a *negative* t makes p > 0.5. Clipped to
    [1e-12, 1.0 - 1e-12] for numerical stability.
    """
    if df < 1:
        return 0.5
    p = float(_stats.t.sf(t_stat, df))
    return max(min(p, 1.0 - 1e-12), 1e-12)


def _fisher_combined_p(p1: float, p2: float) -> float:
    """Fisher's method to combine two independent one-tail p-values."""
    chi2 = -2.0 * (np.log(p1) + np.log(p2))
    return float(_stats.chi2.sf(chi2, df=4))


@dataclass(frozen=True)
class Trade:
    """One round-trip trade produced by a structural hypothesis."""

    day: str
    direction: int  # +1 long, -1 short
    entry_dt: pd.Timestamp
    entry_price: float
    exit_dt: pd.Timestamp
    exit_price: float
    result: str  # "tp" | "sl" | "timeout"

    def profit_ticks(self, tick_size: float) -> float:
        return (self.exit_price - self.entry_price) * self.direction / tick_size


@dataclass(frozen=True)
class StructuralHypothesis:
    name: str
    description: str
    fn: Callable[..., List[Trade]]


# --- helpers -----------------------------------------------------------------

def _denver_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Annotate bars with Denver-local clock fields for day/time grouping."""
    dt = pd.to_datetime(bars["dt"])
    if dt.dt.tz is not None:
        local = dt.dt.tz_convert("America/Denver").dt.tz_localize(None)
    else:
        local = dt
    out = bars.copy()
    out["_local"] = local
    out["_date"] = local.dt.date
    out["_hour"] = local.dt.hour
    out["_minute"] = local.dt.minute
    return out.sort_values("_local").reset_index(drop=True)


def _bar_index_at(day_bars: pd.DataFrame, hour: int, minute: int) -> int:
    """Positional index (relative to day_bars) of the bar at hour:minute, or -1."""
    mask = (day_bars["_hour"] == hour) & (day_bars["_minute"] == minute)
    hits = np.where(mask.to_numpy())[0]
    return int(hits[0]) if len(hits) else -1


def _walk_to_exit(
    bars_after: pd.DataFrame,
    entry_price: float,
    direction: int,
    tp_price: float,
    sl_price: float,
    max_bars: int,
) -> tuple:
    """Walk forward bar by bar; returns (exit_dt, exit_price, result).
    Conservative: same-bar TP+SL hit resolves as the loss.
    """
    end = min(len(bars_after), max_bars)
    for i in range(end):
        bar = bars_after.iloc[i]
        h, l = float(bar["high"]), float(bar["low"])
        if direction == 1:
            hit_tp, hit_sl = h >= tp_price, l <= sl_price
        else:
            hit_tp, hit_sl = l <= tp_price, h >= sl_price
        if hit_tp and hit_sl:
            return bar["dt"], sl_price, "sl"
        if hit_tp:
            return bar["dt"], tp_price, "tp"
        if hit_sl:
            return bar["dt"], sl_price, "sl"
    if end == 0:
        return None, None, "timeout"
    last = bars_after.iloc[end - 1]
    return last["dt"], float(last["close"]), "timeout"


def _build_trade(
    day, direction, entry_row, exit_dt, exit_price, result
) -> Trade:
    return Trade(
        day=str(day),
        direction=direction,
        entry_dt=entry_row["dt"],
        entry_price=float(entry_row["open"]),
        exit_dt=exit_dt,
        exit_price=float(exit_price),
        result=result,
    )


# --- hypotheses --------------------------------------------------------------

def gap_fade_at_cash_open(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    gap_threshold_ticks: int = 8,
    stop_ticks: int = 12,
    open_hour: int = 7,
    open_minute: int = 30,
    prior_close_hour: int = 14,
    prior_close_minute: int = 0,
    max_bars: int = 150,
) -> List[Trade]:
    """Overnight gap at 07:30 MT cash open fades toward the prior cash close.

    Theory: overnight gaps in equity-index futures are often driven by
    thin-liquidity reactions and revert when the cash session opens with
    full participation. Fixed parameters; not parameter-swept.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for di, day in enumerate(days):
        if di == 0:
            continue
        prior_bars = b[b["_date"] == days[di - 1]].reset_index(drop=True)
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        pc_i = _bar_index_at(prior_bars, prior_close_hour, prior_close_minute)
        op_i = _bar_index_at(day_bars, open_hour, open_minute)
        if pc_i < 0 or op_i < 0 or op_i + 1 >= len(day_bars):
            continue
        prior_close = float(prior_bars.iloc[pc_i]["close"])
        open_price = float(day_bars.iloc[op_i]["open"])
        gap = open_price - prior_close
        if abs(gap) < gap_threshold_ticks * tick_size:
            continue
        direction = -1 if gap > 0 else 1
        entry_row = day_bars.iloc[op_i + 1]
        entry_price = float(entry_row["open"])
        tp_price = prior_close  # close the gap
        sl_price = entry_price - direction * stop_ticks * tick_size
        bars_after = day_bars.iloc[op_i + 1:].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


def last_half_hour_reversal(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    open_hour: int = 7,
    open_minute: int = 30,
    trigger_hour: int = 13,
    trigger_minute: int = 30,
    trend_threshold_ticks: int = 10,
    stop_ticks: int = 8,
    target_revert_fraction: float = 0.5,
    max_bars: int = 30,
) -> List[Trade]:
    """After 13:30 MT, fade the day's intraday trend halfway back to day open.

    Theory: institutional rebalancing pressure in the last 30 minutes of the
    regular cash session compresses price toward intraday means. Fixed
    parameters.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for day in days:
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        op_i = _bar_index_at(day_bars, open_hour, open_minute)
        tr_i = _bar_index_at(day_bars, trigger_hour, trigger_minute)
        if op_i < 0 or tr_i < 0 or tr_i + 1 >= len(day_bars):
            continue
        day_open = float(day_bars.iloc[op_i]["open"])
        trigger_close = float(day_bars.iloc[tr_i]["close"])
        trend = trigger_close - day_open
        if abs(trend) < trend_threshold_ticks * tick_size:
            continue
        direction = -1 if trend > 0 else 1
        entry_row = day_bars.iloc[tr_i + 1]
        entry_price = float(entry_row["open"])
        tp_price = day_open + (1.0 - target_revert_fraction) * trend
        sl_price = entry_price - direction * stop_ticks * tick_size
        bars_after = day_bars.iloc[tr_i + 1:].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


def first_bar_follow_through(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    open_hour: int = 7,
    open_minute: int = 30,
    min_body_ticks: int = 3,
    tp_body_multiple: float = 2.0,
    sl_body_multiple: float = 1.0,
    max_bars: int = 60,
) -> List[Trade]:
    """The 07:30 MT cash-open bar's body direction continues over the next hour.

    Theory: the first cash-session bar reflects accumulated overnight demand
    plus institutional positioning; meaningful first-bar moves tend to
    continue as the imbalance drives further price discovery. TP/SL scaled
    to the bar's own body so the trade risk adapts to volatility.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for day in days:
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        op_i = _bar_index_at(day_bars, open_hour, open_minute)
        if op_i < 0 or op_i + 1 >= len(day_bars):
            continue
        open_bar = day_bars.iloc[op_i]
        body = float(open_bar["close"]) - float(open_bar["open"])
        if abs(body) < min_body_ticks * tick_size:
            continue
        direction = 1 if body > 0 else -1
        entry_row = day_bars.iloc[op_i + 1]
        entry_price = float(entry_row["open"])
        body_abs = abs(body)
        tp_price = entry_price + direction * tp_body_multiple * body_abs
        sl_price = entry_price - direction * sl_body_multiple * body_abs
        bars_after = day_bars.iloc[op_i + 1:].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


def overnight_drift(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    close_hour: int = 14,
    close_minute: int = 0,
    open_hour: int = 7,
    open_minute: int = 30,
) -> List[Trade]:
    """Long the overnight session: enter at the 14:00 MT cash close, exit at
    the next day's 07:30 MT cash open.

    Theory: a large literature (Lou, Polk & Skouras 2019 "A tug of war:
    overnight vs intraday expected returns"; Cooper, Cliff, Gulen 2008;
    Bondarenko 2003) documents that the equity-index futures premium is
    concentrated in the overnight session; the cash-day session is on
    average flat or negative net of risk. Single direction (long), no
    parameters tuned to data, one statistical test.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for di in range(len(days) - 1):
        today_bars = b[b["_date"] == days[di]].reset_index(drop=True)
        next_bars = b[b["_date"] == days[di + 1]].reset_index(drop=True)
        close_i = _bar_index_at(today_bars, close_hour, close_minute)
        open_i = _bar_index_at(next_bars, open_hour, open_minute)
        if close_i < 0 or open_i < 0:
            continue
        close_bar = today_bars.iloc[close_i]
        open_bar = next_bars.iloc[open_i]
        entry_price = float(close_bar["close"])
        exit_price = float(open_bar["open"])
        ticks = (exit_price - entry_price) / tick_size
        result = "tp" if ticks > 0 else ("sl" if ticks < 0 else "timeout")
        trades.append(Trade(
            day=str(days[di]),
            direction=1,
            entry_dt=close_bar["dt"], entry_price=entry_price,
            exit_dt=open_bar["dt"], exit_price=exit_price,
            result=result,
        ))
    return trades


def closing_auction_reversal(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    trigger_hour: int = 13,
    trigger_minute: int = 50,
    lookback_minutes: int = 60,
    close_hour: int = 14,
    trend_threshold_ticks: int = 8,
    stop_ticks: int = 6,
    target_revert_fraction: float = 0.5,
    max_bars: int = 10,
) -> List[Trade]:
    """At 13:50 MT, fade the *prior 60-minute* direction into the cash close.

    Theory: the last 10 minutes of the US cash session carry concentrated
    rebalancing flow (institutional benchmark trades, ETF rebalancing,
    closing-auction matching pressure). Distinct from
    `last_half_hour_reversal` -- this fades only the recent hour's move
    (not the whole day's), uses a 10-minute hold, and rides
    closing-auction-specific flows. Fixed parameters.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for day in days:
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        tr_i = _bar_index_at(day_bars, trigger_hour, trigger_minute)
        if tr_i < 0 or tr_i + 1 >= len(day_bars):
            continue
        lookback_i = max(0, tr_i - lookback_minutes)
        prior_close = float(day_bars.iloc[lookback_i]["close"])
        trigger_close = float(day_bars.iloc[tr_i]["close"])
        trend = trigger_close - prior_close
        if abs(trend) < trend_threshold_ticks * tick_size:
            continue
        direction = -1 if trend > 0 else 1
        entry_row = day_bars.iloc[tr_i + 1]
        entry_price = float(entry_row["open"])
        # halfway back toward the prior-hour close
        tp_price = trigger_close - (1.0 - target_revert_fraction) * trend
        sl_price = entry_price - direction * stop_ticks * tick_size
        bars_after = day_bars.iloc[tr_i + 1:].reset_index(drop=True)
        bars_after = bars_after[bars_after["_hour"] < close_hour].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


def large_gap_continuation(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    gap_threshold_ticks: int = 30,
    sl_fraction: float = 0.3,
    tp_fraction: float = 0.5,
    open_hour: int = 7,
    open_minute: int = 30,
    prior_close_hour: int = 14,
    prior_close_minute: int = 0,
    max_bars: int = 60,
) -> List[Trade]:
    """Overnight gaps >= 30 ticks at the cash open *continue* (not fade).

    Theory: small/moderate overnight gaps in equity-index futures (covered
    by `gap_fade_at_cash_open` at 8 ticks) typically reflect liquidity-
    driven overshoots and mean-revert. *Large* gaps (>= 30 ticks ~ 7.5
    points on NQ) reflect real news / regime shift / earnings cluster /
    macro release, and tend to continue as more participants react during
    the cash session. Opposite-regime test from gap_fade.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for di, day in enumerate(days):
        if di == 0:
            continue
        prior_bars = b[b["_date"] == days[di - 1]].reset_index(drop=True)
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        pc_i = _bar_index_at(prior_bars, prior_close_hour, prior_close_minute)
        op_i = _bar_index_at(day_bars, open_hour, open_minute)
        if pc_i < 0 or op_i < 0 or op_i + 1 >= len(day_bars):
            continue
        prior_close = float(prior_bars.iloc[pc_i]["close"])
        open_price = float(day_bars.iloc[op_i]["open"])
        gap = open_price - prior_close
        if abs(gap) < gap_threshold_ticks * tick_size:
            continue
        direction = 1 if gap > 0 else -1   # CONTINUE in gap direction
        entry_row = day_bars.iloc[op_i + 1]
        entry_price = float(entry_row["open"])
        tp_price = entry_price + direction * tp_fraction * abs(gap)
        sl_price = entry_price - direction * sl_fraction * abs(gap)
        bars_after = day_bars.iloc[op_i + 1:].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


def intraday_range_reversion(
    bars: pd.DataFrame,
    tick_size: float,
    *,
    open_hour: int = 7,
    open_minute: int = 30,
    trigger_hour: int = 11,
    trigger_minute: int = 0,
    close_hour: int = 14,
    range_pct_threshold: float = 0.005,
    proximity_ticks: int = 5,
    stop_ticks: int = 8,
    max_bars: int = 120,
) -> List[Trade]:
    """At 11:00 MT, fade an instrument that has already exceeded its typical
    daily range and is sitting at an extreme.

    Theory: volatility mean reversion + saturation -- when intraday range
    has already exceeded ~0.5% of the open price by mid-session (a 'big
    range' day for liquid index futures), further extension is rare; the
    remaining session tends to compress toward the day's midpoint as
    institutional flows balance and short-term traders fade extremes.
    """
    b = _denver_frame(bars)
    days = list(dict.fromkeys(b["_date"].tolist()))
    trades: List[Trade] = []
    for day in days:
        day_bars = b[b["_date"] == day].reset_index(drop=True)
        op_i = _bar_index_at(day_bars, open_hour, open_minute)
        tr_i = _bar_index_at(day_bars, trigger_hour, trigger_minute)
        if op_i < 0 or tr_i < 0 or tr_i + 1 >= len(day_bars):
            continue
        day_open = float(day_bars.iloc[op_i]["open"])
        seg = day_bars.iloc[op_i:tr_i + 1]
        today_high = float(seg["high"].max())
        today_low = float(seg["low"].min())
        if (today_high - today_low) < range_pct_threshold * day_open:
            continue
        midpoint = (today_high + today_low) / 2.0
        trig_close = float(day_bars.iloc[tr_i]["close"])
        prox = proximity_ticks * tick_size
        if abs(trig_close - today_high) <= prox:
            direction = -1
            sl_price = today_high + stop_ticks * tick_size
        elif abs(trig_close - today_low) <= prox:
            direction = 1
            sl_price = today_low - stop_ticks * tick_size
        else:
            continue
        entry_row = day_bars.iloc[tr_i + 1]
        entry_price = float(entry_row["open"])
        tp_price = midpoint
        bars_after = day_bars.iloc[tr_i + 1:].reset_index(drop=True)
        bars_after = bars_after[bars_after["_hour"] < close_hour].reset_index(drop=True)
        exit_dt, exit_price, result = _walk_to_exit(
            bars_after, entry_price, direction, tp_price, sl_price, max_bars
        )
        if exit_dt is None:
            continue
        trades.append(_build_trade(day, direction, entry_row, exit_dt, exit_price, result))
    return trades


HYPOTHESES: List[StructuralHypothesis] = [
    StructuralHypothesis(
        name="gap_fade_at_cash_open",
        description="overnight gap (>= 8 ticks) at 07:30 MT cash open fades "
                    "toward the prior 14:00 MT cash close",
        fn=gap_fade_at_cash_open,
    ),
    StructuralHypothesis(
        name="last_half_hour_reversal",
        description="after 13:30 MT, an intraday trend (>= 10 ticks vs day "
                    "open) reverses halfway back",
        fn=last_half_hour_reversal,
    ),
    StructuralHypothesis(
        name="first_bar_follow_through",
        description="the 07:30 MT cash-open bar's body direction continues "
                    "(TP = 2x body, SL = 1x body, 60-bar timeout)",
        fn=first_bar_follow_through,
    ),
    StructuralHypothesis(
        name="overnight_drift",
        description="long the overnight session: 14:00 MT close -> 07:30 MT "
                    "next-day open (close-to-open equity premium)",
        fn=overnight_drift,
    ),
    StructuralHypothesis(
        name="intraday_range_reversion",
        description="at 11:00 MT, fade toward the day's midpoint when range "
                    "already exceeds ~0.5% of open price and current price "
                    "is at the extreme (volatility mean reversion)",
        fn=intraday_range_reversion,
    ),
    StructuralHypothesis(
        name="closing_auction_reversal",
        description="at 13:50 MT, fade the prior 60-minute direction "
                    "(closing-auction rebalancing pressure)",
        fn=closing_auction_reversal,
    ),
    StructuralHypothesis(
        name="large_gap_continuation",
        description="overnight gap >= 30 ticks continues in the gap direction "
                    "(news-driven regime, opposite of gap_fade_at_cash_open)",
        fn=large_gap_continuation,
    ),
]


# --- runner ------------------------------------------------------------------

def evaluate_hypothesis_on_panel(
    hyp: StructuralHypothesis,
    instrument_bars: Dict[str, pd.DataFrame],
    instrument_tick_sizes: Dict[str, float],
    *,
    cost_ticks: float = 3.0,
    **gate_kwargs,
) -> CrossInstrumentVerdict:
    """Run a hypothesis on a panel of instruments and apply the cross_instrument gate.

    `cost_ticks` is a round-trip slippage+commission proxy subtracted from
    each trade's tick P&L before pooling; the default (3 ticks) is a
    realistic-conservative for liquid index futures.
    """
    panel_pnl: Dict[str, List[float]] = {}
    for name, bars in instrument_bars.items():
        ts = instrument_tick_sizes[name]
        trades = hyp.fn(bars, ts)
        panel_pnl[name] = [t.profit_ticks(ts) - cost_ticks for t in trades]
    return evaluate_cross_instrument(panel_pnl, **gate_kwargs)


def run_all_hypotheses(
    instrument_bars: Dict[str, pd.DataFrame],
    instrument_tick_sizes: Dict[str, float],
    **kwargs,
) -> Dict[str, CrossInstrumentVerdict]:
    """Evaluate every pre-registered hypothesis on a panel."""
    return {
        h.name: evaluate_hypothesis_on_panel(h, instrument_bars, instrument_tick_sizes, **kwargs)
        for h in HYPOTHESES
    }


@dataclass(frozen=True)
class TemporalRobustnessVerdict:
    """Result of an in-sample / out-of-sample temporal robustness check.

    Cross-instrument validation (`evaluate_cross_instrument`) is the right
    test for a structural edge claim ('this works across correlated markets').
    Some edges are legitimately instrument-specific (a particular contract's
    microstructure -- order flow, participant mix, contract size); for those,
    the standard secondary test is temporal IS/OOS *within* the instrument:
    does the strategy that worked in the first window keep working in the
    next? Looser than cross-instrument but still requires the strategy to
    work twice in different periods.

    Two-stage gate:
      * Direction gate: both IS and OOS means positive, both PFs >= 1.2.
      * Significance gate: Fisher's combined one-tail p-value (combining the
        independent IS and OOS evidence under H1: mean > 0) below threshold.
        This is the correct way to score 'edge that worked twice' on a small
        sample -- the combined evidence can be significant even when neither
        window alone clears its own t bar.
    """

    candidate_edge: bool
    n: int
    is_stats: Dict[str, float]
    oos_stats: Dict[str, float]
    combined_p: float
    reasons: list[str]


def evaluate_temporal_robustness(
    hyp: StructuralHypothesis,
    bars: pd.DataFrame,
    tick_size: float,
    *,
    is_frac: float = 0.6,
    cost_ticks: float = 3.0,
    min_is_n: int = 20,
    min_oos_n: int = 15,
    min_pf: float = 1.2,
    max_combined_p: float = 0.10,
) -> TemporalRobustnessVerdict:
    """Within-instrument temporal IS/OOS check using Fisher's combined p.

    Splits the hypothesis's trades by entry date into IS (first `is_frac`)
    and OOS (remainder). Passes the hypothesis as a candidate edge only if:
      * both windows have enough trades (`min_is_n`, `min_oos_n`),
      * both window means are positive and PF >= `min_pf` (direction
        confirmed in both),
      * Fisher's combined one-tail p-value (under H1: mean > 0) is below
        `max_combined_p`.

    The combined p-value is the principled significance test for 'edge that
    worked in two independent periods': two modest signals (each p ~ 0.15)
    combine to a strong joint signal (p ~ 0.05) under independence. The
    default `max_combined_p = 0.10` is the conventional finance-research
    "exploratory candidate worth forward-testing" threshold; pass
    `max_combined_p = 0.05` for strict significance.
    """
    trades = hyp.fn(bars, tick_size)
    if not trades:
        return TemporalRobustnessVerdict(False, 0, {}, {}, 1.0, ["FAIL no trades"])
    trades = sorted(trades, key=lambda t: t.entry_dt)
    pnl = np.array([t.profit_ticks(tick_size) - cost_ticks for t in trades], dtype=float)
    cut = int(len(pnl) * is_frac)
    is_p, oos_p = pnl[:cut], pnl[cut:]

    def _window_stats(arr):
        n = int(len(arr))
        if n < 2:
            return {"n": n, "mean": 0.0, "pf": 0.0, "t": 0.0, "p_one_tail": 0.5}
        mean = float(arr.mean()); std = float(arr.std(ddof=1))
        gp = float(arr[arr > 0].sum()); gl = abs(float(arr[arr < 0].sum()))
        pf = gp / gl if gl > 0 else float("inf")
        t = mean / (std / np.sqrt(n)) if std > 0 else 0.0
        return {"n": n, "mean": mean, "pf": pf, "t": t,
                "p_one_tail": _one_tail_p_from_t(t, n - 1)}

    is_s, oos_s = _window_stats(is_p), _window_stats(oos_p)
    combined_p = _fisher_combined_p(is_s["p_one_tail"], oos_s["p_one_tail"])
    reasons: list[str] = []

    def gate(label, ok):
        reasons.append(f"{'PASS' if ok else 'FAIL'} {label}")
        return ok

    ok = (
        gate(f"is_n {is_s['n']} >= {min_is_n}", is_s["n"] >= min_is_n) &
        gate(f"oos_n {oos_s['n']} >= {min_oos_n}", oos_s["n"] >= min_oos_n) &
        gate(f"is_mean > 0 ({is_s['mean']:+.2f})", is_s["mean"] > 0) &
        gate(f"oos_mean > 0 ({oos_s['mean']:+.2f})", oos_s["mean"] > 0) &
        gate(f"is_pf {is_s['pf']:.2f} >= {min_pf}", is_s["pf"] >= min_pf) &
        gate(f"oos_pf {oos_s['pf']:.2f} >= {min_pf}", oos_s["pf"] >= min_pf) &
        gate(f"combined_p {combined_p:.3f} < {max_combined_p}", combined_p < max_combined_p)
    )
    return TemporalRobustnessVerdict(bool(ok), len(pnl), is_s, oos_s, combined_p, reasons)
