from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union, Tuple, List, Dict

import math
import numpy as np
import pandas as pd

from ta_foundation.analysis.features.regime import atr_wilder
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.exits.policies import (
    TrailStopTargetPolicy,
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    TimeStopNoProgressPolicy,
    GivebackAfterMfePolicy,
    FixedAtrThenAtrTrailPolicy,
    FixedAtrThenChandelierPolicy,  # NEW
)

Policy = Union[
    TrailStopTargetPolicy,
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    TimeStopNoProgressPolicy,
    GivebackAfterMfePolicy,
    FixedAtrThenAtrTrailPolicy,
    FixedAtrThenChandelierPolicy,  # NEW
]


def _snap_price(price: float, tick_size: float, mode: str) -> float:
    """
    Snap 'price' to the tick grid defined by tick_size.

    mode:
      - "down": floor to tick
      - "up":   ceil to tick
      - "nearest": nearest tick
    """
    if price is None or (isinstance(price, float) and math.isnan(price)) or tick_size <= 0:
        return price
    x = price / tick_size
    if mode == "down":
        return math.floor(x) * tick_size
    if mode == "up":
        return math.ceil(x) * tick_size
    return round(x) * tick_size


def _snap_stop(price: float, tick_size: float, direction: int) -> float:
    # Conservative stop fill:
    # long stop below -> round DOWN (worse)
    # short stop above -> round UP (worse)
    return _snap_price(price, tick_size, "down" if direction > 0 else "up")


def _snap_target(price: float, tick_size: float, direction: int) -> float:
    # Conservative target fill:
    # long target above -> round DOWN (less profit)
    # short target below -> round UP (less profit)
    return _snap_price(price, tick_size, "down" if direction > 0 else "up")


def _ensure_series_tz(s: pd.Series, tz: str) -> pd.Series:
    """Ensure datetime Series is tz-aware in tz (localize if naive, convert if aware)."""
    s = pd.to_datetime(s, errors="coerce")
    if getattr(s.dt, "tz", None) is None:
        return s.dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
    return s.dt.tz_convert(tz)


def _ticks_window_from_ns(
    ticks: pd.DataFrame, tick_ns: np.ndarray, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    if ticks is None or ticks.empty:
        return ticks

    s_ns = int(pd.Timestamp(start).value)
    e_ns = int(pd.Timestamp(end).value)
    if e_ns <= s_ns:
        return ticks.iloc[0:0]

    s = np.searchsorted(tick_ns, s_ns, side="left")
    e = np.searchsorted(tick_ns, e_ns, side="right")
    if e <= s:
        return ticks.iloc[0:0]
    return ticks.iloc[s:e]


@dataclass(frozen=True)
class ExitSimConfig:
    tick_size: float = 0.25
    atr_tf: str = "5m"
    atr_period: int = 14

    bounded_to_original_exit: bool = True
    max_minutes_unbounded: int = 180

    # Used by TrailStopTargetPolicy
    start_trail_ticks: float = 20.0
    trail_amount: float = 10.0

    # NOTE:
    # We keep the name for backward compatibility, but interpret it as:
    # - market exits (time/giveback/time_no_progress) execute at bid/ask when True
    # - stop/target "touch" checks ALWAYS use last (see _simulate_one_trade)
    use_bid_ask_triggers: bool = True

    price_col_last: str = "last"
    price_col_bid: str = "bid"
    price_col_ask: str = "ask"
    entry_time_slop_seconds: int = 60

    # Debug trace (used by trade debug section)
    collect_trace: bool = False
    trace_every_n: int = 1  # 1 = every tick, 5 = sample


def _diag_row(run_id: str, instrument: str, contract: str, reason: str, detail: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "trade_idx": -1,
                "policy": "DIAGNOSTIC",
                "entry_dt": pd.NaT,
                "exit_dt": pd.NaT,
                "entry_price": np.nan,
                "exit_price": np.nan,
                "exit_reason": reason,
                "pnl_ticks": np.nan,
                "mae_ticks": np.nan,
                "mfe_ticks": np.nan,
                "atr_entry": np.nan,
                "detail": f"{reason}: {detail} | instrument={instrument} contract={contract}",
            }
        ]
    )


def _simulate_one_trade(
    *,
    ticks: pd.DataFrame,
    entry_dt: pd.Timestamp,
    entry_price: float,
    direction: int,
    atr_entry: float,
    policy: Any,
    cfg: ExitSimConfig,
) -> dict:
    """
    Key behavioral rules:
    - Stop/target TOUCH logic uses LAST (price path).
    - Stop/target FILL price uses the stop/target level.
    - Market-style exits (time / giveback / time_no_progress) execute at bid/ask when enabled.
    - Watermark (MFE) + MAE + BE arming are based on LAST to avoid bid/ask microstructure poisoning.
    """
    # Trace setup
    trace: List[Dict[str, Any]] = []
    trace_n = max(int(cfg.trace_every_n or 1), 1)
    trace_i = 0

    def _trace_row(
        *,
        dt: pd.Timestamp,
        last_px: float,
        exec_px: float,
        best_px: float,
        pnl_ticks_last: float,
        mfe_ticks: float,
        mae_ticks: float,
        stop_px: Optional[float],
        target_px: Optional[float],
        stop_changed: bool,
        be_armed: bool,
        be_armed_now: bool,
        exit_now: bool,
        exit_reason: Optional[str],
    ) -> None:
        nonlocal trace_i
        if not cfg.collect_trace:
            return
        if (trace_i % trace_n) != 0 and (stop_changed is False) and (be_armed_now is False) and (exit_now is False):
            trace_i += 1
            return
        trace.append(
            {
                "dt": dt,
                "last": float(last_px) if pd.notna(last_px) else np.nan,
                "exec_px": float(exec_px) if pd.notna(exec_px) else np.nan,
                "best_price": float(best_px) if pd.notna(best_px) else np.nan,
                "pnl_ticks_last": float(pnl_ticks_last),
                "mfe_ticks": float(mfe_ticks),
                "mae_ticks": float(mae_ticks),
                "stop_price": float(stop_px) if stop_px is not None and pd.notna(stop_px) else np.nan,
                "target_price": float(target_px) if target_px is not None and pd.notna(target_px) else np.nan,
                "stop_changed": bool(stop_changed),
                "be_armed": bool(be_armed),
                "be_armed_now": bool(be_armed_now),
                "exit_now": bool(exit_now),
                "exit_reason": exit_reason,
            }
        )
        trace_i += 1

    if ticks is None or ticks.empty:
        out = {
            "exit_dt": pd.NaT,
            "exit_price": np.nan,
            "exit_reason": "no_ticks",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
            "best_fav_ticks": np.nan,
            "worst_adv_ticks": np.nan,
            "be_armed": False,
            "be_arm_dt": pd.NaT,
            "stop_at_arm": np.nan,
            "best_fav_ticks_exec": np.nan,
            "best_fav_ticks_last": np.nan,
            "first_tick_dt_after_entry": pd.NaT,
        }
        if cfg.collect_trace:
            out["trace"] = pd.DataFrame(trace)
        return out

    stop_pts: Optional[float] = None
    target_pts: Optional[float] = None
    trail_mult: Optional[float] = None
    be_trigger_mult: Optional[float] = None
    be_offset_ticks: Optional[float] = None

    time_stop_minutes = None
    time_stop_min_mfe_ticks = None

    giveback_arm_mfe_ticks = None
    giveback_ticks = None

    seen_entry_tick = False
    first_tick_dt_after_entry = pd.NaT

    # For TrailStopTargetPolicy
    start_trail_ticks: Optional[float] = None
    trail_amount: Optional[float] = None

    fixed_then_arm_mfe_ticks: Optional[float] = None

    if isinstance(policy, FixedStopTargetPolicy):
        # Prefer ATR distances if multipliers are provided; otherwise tick-based
        if policy.stop_atr_mult is not None:
            stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        elif policy.stop_ticks is not None:
            stop_pts = float(policy.stop_ticks) * float(cfg.tick_size)

        if policy.target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.target_atr_mult)
        elif policy.target_ticks is not None:
            target_pts = float(policy.target_ticks) * float(cfg.tick_size)

    elif isinstance(policy, FixedAtrThenAtrTrailPolicy):
        stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        trail_mult = float(policy.trail_atr_mult)
        fixed_then_arm_mfe_ticks = float(policy.arm_mfe_ticks)
        if policy.profit_target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.profit_target_atr_mult)

    elif isinstance(policy, FixedAtrThenChandelierPolicy):
        stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        trail_mult = float(policy.trail_atr_mult)
        fixed_then_arm_mfe_ticks = float(policy.arm_mfe_ticks)
        if policy.profit_target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.profit_target_atr_mult)

    elif isinstance(policy, AtrTrailPolicy):
        stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        trail_mult = float(policy.trail_atr_mult)
        if policy.profit_target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.profit_target_atr_mult)

    elif isinstance(policy, TrailStopTargetPolicy):
        start_trail_ticks = float(policy.start_trail_ticks)
        trail_amount = float(policy.trail_amount)
        stop_pts = float(policy.stop_ticks) * float(cfg.tick_size)

    elif isinstance(policy, BreakEvenAtrTrailPolicy):
        stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        trail_mult = float(policy.trail_atr_mult)
        be_trigger_mult = float(policy.be_trigger_atr_mult)
        be_offset_ticks = float(policy.be_offset_ticks)
        if policy.profit_target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.profit_target_atr_mult)

    elif isinstance(policy, ChandelierAtrTrailPolicy):
        stop_pts = float(atr_entry) * float(policy.stop_atr_mult)
        trail_mult = float(policy.trail_atr_mult)
        if policy.profit_target_atr_mult is not None:
            target_pts = float(atr_entry) * float(policy.profit_target_atr_mult)

    elif isinstance(policy, TimeStopNoProgressPolicy):
        time_stop_minutes = float(policy.max_minutes)
        time_stop_min_mfe_ticks = float(policy.min_mfe_ticks)

    elif isinstance(policy, GivebackAfterMfePolicy):
        giveback_arm_mfe_ticks = float(policy.arm_mfe_ticks)
        giveback_ticks = float(policy.giveback_ticks)

    # Watermark / excursion tracking (LAST-based)
    best_favorable_pts = 0.0
    worst_adverse_pts = 0.0

    best_fav_pts_last = 0.0  # diagnostic
    best_fav_pts_exec = 0.0  # diagnostic using executable market px

    trail_distance_pts = (float(atr_entry) * float(trail_mult)) if trail_mult is not None else None

    stop_price: Optional[float] = None
    target_price: Optional[float] = None

    if stop_pts is not None:
        stop_price = entry_price - (float(stop_pts) * direction)
        stop_price = _snap_stop(float(stop_price), float(cfg.tick_size), direction)

    if target_pts is not None:
        target_price = entry_price + (float(target_pts) * direction)
        target_price = _snap_target(float(target_price), float(cfg.tick_size), direction)

    be_armed = False
    be_arm_dt = pd.NaT
    stop_at_arm = np.nan

    exit_dt = pd.NaT
    exit_price = np.nan
    exit_reason: Optional[str] = None

    last_dt = pd.NaT
    last_last_px = np.nan
    last_exec_px = np.nan
    best_price = entry_price

    for _, row in ticks.iterrows():
        dt = row["dt"]
        if dt < entry_dt:
            continue

        if not seen_entry_tick:
            seen_entry_tick = True
            first_tick_dt_after_entry = dt

        last = row.get(cfg.price_col_last, np.nan)
        bid = row.get(cfg.price_col_bid, np.nan)
        ask = row.get(cfg.price_col_ask, np.nan)

        # If bid/ask missing, fall back to last
        if pd.isna(bid):
            bid = last
        if pd.isna(ask):
            ask = last

        if pd.isna(last):
            # If last is missing, fallback to executable px
            last = bid if direction > 0 else ask

        if pd.isna(last):
            continue

        # Executable market exit price
        exec_px = (bid if direction > 0 else ask) if cfg.use_bid_ask_triggers else last
        if pd.isna(exec_px):
            exec_px = last

        last_dt = dt
        last_last_px = float(last)
        last_exec_px = float(exec_px)

        # LAST-based PnL path (used for watermark, MAE/MFE, BE arming)
        pnl_pts_last = (last_last_px - entry_price) if direction > 0 else (entry_price - last_last_px)

        # Track excursions
        if pnl_pts_last > best_favorable_pts:
            best_favorable_pts = pnl_pts_last
        if pnl_pts_last < worst_adverse_pts:
            worst_adverse_pts = pnl_pts_last

        best_fav_pts_last = max(best_fav_pts_last, pnl_pts_last)

        # Diagnostic: executable px excursion
        pnl_pts_exec = (last_exec_px - entry_price) * direction
        best_fav_pts_exec = max(best_fav_pts_exec, pnl_pts_exec)

        # ---- Break-even arming ----
        be_armed_now = False
        trigger_ticks = getattr(policy, "be_trigger_ticks", None)
        if seen_entry_tick and (be_trigger_mult is not None or trigger_ticks is not None) and (not be_armed):
            if trigger_ticks is not None:
                arm = (pnl_pts_last / float(cfg.tick_size)) >= float(trigger_ticks)
            else:
                arm = pnl_pts_last >= float(atr_entry) * float(be_trigger_mult)

            if arm:
                be_armed = True
                be_armed_now = True
                be_arm_dt = dt

                be_px = entry_price + (float(be_offset_ticks or 0.0) * float(cfg.tick_size) * direction)
                be_px = _snap_stop(float(be_px), float(cfg.tick_size), direction)
                stop_at_arm = float(be_px)

                if stop_price is None:
                    stop_price = float(be_px)
                else:
                    stop_price = max(float(stop_price), float(be_px)) if direction > 0 else min(float(stop_price), float(be_px))

        # ---- Trailing stop update (watermark is LAST-based) ----
        if direction > 0:
            best_price = max(best_price, last_last_px)
        else:
            best_price = min(best_price, last_last_px)

        best_fav_pts_from_price = (best_price - entry_price) if direction > 0 else (entry_price - best_price)
        best_fav_ticks_dbg = best_fav_pts_from_price / float(cfg.tick_size)

        stop_changed = False
        stop_before = stop_price

        # --- Tick-based trail policy: only after start_trail_ticks is reached ---
        if seen_entry_tick and start_trail_ticks is not None and trail_amount is not None:
            if best_fav_ticks_dbg >= float(start_trail_ticks):
                if direction > 0:
                    new_trail_stop = best_price - float(trail_amount) * float(cfg.tick_size)
                else:
                    new_trail_stop = best_price + float(trail_amount) * float(cfg.tick_size)
                new_trail_stop = _snap_stop(float(new_trail_stop), float(cfg.tick_size), direction)

                if stop_price is None:
                    stop_price = float(new_trail_stop)
                else:
                    stop_price = max(float(stop_price), float(new_trail_stop)) if direction > 0 else min(float(stop_price), float(new_trail_stop))

        # --- ATR-based trail policies ---
        # For FixedAtrThen* policies: only trail after MFE >= arm threshold
        can_trail = True
        if fixed_then_arm_mfe_ticks is not None:
            can_trail = best_fav_ticks_dbg >= float(fixed_then_arm_mfe_ticks)

        if seen_entry_tick and trail_distance_pts is not None and can_trail:
            if direction > 0:
                new_trail_stop = best_price - float(trail_distance_pts)
            else:
                new_trail_stop = best_price + float(trail_distance_pts)
            new_trail_stop = _snap_stop(float(new_trail_stop), float(cfg.tick_size), direction)

            if stop_price is None:
                stop_price = float(new_trail_stop)
            else:
                stop_price = max(float(stop_price), float(new_trail_stop)) if direction > 0 else min(
                    float(stop_price), float(new_trail_stop)
                )

        if stop_before is None and stop_price is not None:
            stop_changed = True
        elif stop_before is not None and stop_price is not None and float(stop_price) != float(stop_before):
            stop_changed = True

        # ---- Time stop (no progress) ----
        if seen_entry_tick and time_stop_minutes is not None and time_stop_min_mfe_ticks is not None:
            mins = (dt - entry_dt).total_seconds() / 60.0
            if mins >= float(time_stop_minutes):
                if best_fav_ticks_dbg < float(time_stop_min_mfe_ticks):
                    exit_dt = dt
                    exit_price = last_exec_px
                    exit_reason = "time_no_progress"
                    _trace_row(
                        dt=dt,
                        last_px=last_last_px,
                        exec_px=last_exec_px,
                        best_px=best_price,
                        pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                        mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                        mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                        stop_px=stop_price,
                        target_px=target_price,
                        stop_changed=stop_changed,
                        be_armed=be_armed,
                        be_armed_now=be_armed_now,
                        exit_now=True,
                        exit_reason=exit_reason,
                    )
                    break

        # ---- Giveback stop ----
        if seen_entry_tick and giveback_arm_mfe_ticks is not None and giveback_ticks is not None:
            if best_fav_ticks_dbg >= float(giveback_arm_mfe_ticks):
                cur_ticks = pnl_pts_last / float(cfg.tick_size)
                giveback = best_fav_ticks_dbg - cur_ticks
                if giveback >= float(giveback_ticks):
                    exit_dt = dt
                    exit_price = last_exec_px
                    exit_reason = "giveback"
                    _trace_row(
                        dt=dt,
                        last_px=last_last_px,
                        exec_px=last_exec_px,
                        best_px=best_price,
                        pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                        mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                        mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                        stop_px=stop_price,
                        target_px=target_price,
                        stop_changed=stop_changed,
                        be_armed=be_armed,
                        be_armed_now=be_armed_now,
                        exit_now=True,
                        exit_reason=exit_reason,
                    )
                    break

        # ---- Stop/Target touch checks (ALWAYS LAST-based) ----
        if seen_entry_tick and stop_price is not None:
            if direction > 0 and last_last_px <= stop_price:
                exit_dt = dt
                exit_price = float(stop_price)
                exit_reason = "stop"
                _trace_row(
                    dt=dt,
                    last_px=last_last_px,
                    exec_px=last_exec_px,
                    best_px=best_price,
                    pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                    mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                    mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                    stop_px=stop_price,
                    target_px=target_price,
                    stop_changed=stop_changed,
                    be_armed=be_armed,
                    be_armed_now=be_armed_now,
                    exit_now=True,
                    exit_reason=exit_reason,
                )
                break
            if direction < 0 and last_last_px >= stop_price:
                exit_dt = dt
                exit_price = float(stop_price)
                exit_reason = "stop"
                _trace_row(
                    dt=dt,
                    last_px=last_last_px,
                    exec_px=last_exec_px,
                    best_px=best_price,
                    pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                    mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                    mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                    stop_px=stop_price,
                    target_px=target_price,
                    stop_changed=stop_changed,
                    be_armed=be_armed,
                    be_armed_now=be_armed_now,
                    exit_now=True,
                    exit_reason=exit_reason,
                )
                break

        if seen_entry_tick and target_price is not None:
            if direction > 0 and last_last_px >= target_price:
                exit_dt = dt
                exit_price = float(target_price)
                exit_reason = "target"
                _trace_row(
                    dt=dt,
                    last_px=last_last_px,
                    exec_px=last_exec_px,
                    best_px=best_price,
                    pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                    mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                    mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                    stop_px=stop_price,
                    target_px=target_price,
                    stop_changed=stop_changed,
                    be_armed=be_armed,
                    be_armed_now=be_armed_now,
                    exit_now=True,
                    exit_reason=exit_reason,
                )
                break
            if direction < 0 and last_last_px <= target_price:
                exit_dt = dt
                exit_price = float(target_price)
                exit_reason = "target"
                _trace_row(
                    dt=dt,
                    last_px=last_last_px,
                    exec_px=last_exec_px,
                    best_px=best_price,
                    pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
                    mfe_ticks=best_favorable_pts / float(cfg.tick_size),
                    mae_ticks=worst_adverse_pts / float(cfg.tick_size),
                    stop_px=stop_price,
                    target_px=target_price,
                    stop_changed=stop_changed,
                    be_armed=be_armed,
                    be_armed_now=be_armed_now,
                    exit_now=True,
                    exit_reason=exit_reason,
                )
                break

        # Trace (non-exit tick)
        _trace_row(
            dt=dt,
            last_px=last_last_px,
            exec_px=last_exec_px,
            best_px=best_price,
            pnl_ticks_last=pnl_pts_last / float(cfg.tick_size),
            mfe_ticks=best_favorable_pts / float(cfg.tick_size),
            mae_ticks=worst_adverse_pts / float(cfg.tick_size),
            stop_px=stop_price,
            target_px=target_price,
            stop_changed=stop_changed,
            be_armed=be_armed,
            be_armed_now=be_armed_now,
            exit_now=False,
            exit_reason=None,
        )

    # End-of-window exit
    if exit_reason is None:
        if pd.notna(last_dt) and not pd.isna(last_exec_px):
            pnl_ticks = (last_exec_px - entry_price) * direction / float(cfg.tick_size)
            out = {
                "exit_dt": last_dt,
                "exit_price": float(last_exec_px),
                "exit_reason": "time",
                "pnl_ticks": float(pnl_ticks),
                "mae_ticks": float(worst_adverse_pts / float(cfg.tick_size)),
                "mfe_ticks": float(best_favorable_pts / float(cfg.tick_size)),
                "best_fav_ticks": float(best_favorable_pts / float(cfg.tick_size)),
                "worst_adv_ticks": float(worst_adverse_pts / float(cfg.tick_size)),
                "be_armed": bool(be_armed),
                "be_arm_dt": be_arm_dt,
                "stop_at_arm": float(stop_at_arm) if not pd.isna(stop_at_arm) else np.nan,
                "best_fav_ticks_exec": float(best_fav_pts_exec / float(cfg.tick_size)),
                "best_fav_ticks_last": float(best_fav_pts_last / float(cfg.tick_size)),
                "first_tick_dt_after_entry": first_tick_dt_after_entry,
            }
            if cfg.collect_trace:
                out["trace"] = pd.DataFrame(trace)
            return out

        out = {
            "exit_dt": pd.NaT,
            "exit_price": np.nan,
            "exit_reason": "no_valid_ticks",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
            "best_fav_ticks": np.nan,
            "worst_adv_ticks": np.nan,
            "be_armed": False,
            "be_arm_dt": pd.NaT,
            "stop_at_arm": np.nan,
            "best_fav_ticks_exec": float(best_fav_pts_exec / float(cfg.tick_size)),
            "best_fav_ticks_last": float(best_fav_pts_last / float(cfg.tick_size)),
            "first_tick_dt_after_entry": first_tick_dt_after_entry,
        }
        if cfg.collect_trace:
            out["trace"] = pd.DataFrame(trace)
        return out

    pnl_ticks = (float(exit_price) - entry_price) * direction / float(cfg.tick_size)
    mae_ticks = float(worst_adverse_pts / float(cfg.tick_size))
    mfe_ticks = float(best_favorable_pts / float(cfg.tick_size))

    out = {
        "exit_dt": exit_dt,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "pnl_ticks": float(pnl_ticks),
        "mae_ticks": mae_ticks,
        "mfe_ticks": mfe_ticks,
        "best_fav_ticks": float(mfe_ticks),
        "worst_adv_ticks": float(mae_ticks),
        "be_armed": bool(be_armed),
        "be_arm_dt": be_arm_dt,
        "stop_at_arm": float(stop_at_arm) if not pd.isna(stop_at_arm) else np.nan,
        "best_fav_ticks_exec": float(best_fav_pts_exec / float(cfg.tick_size)),
        "best_fav_ticks_last": float(best_fav_pts_last / float(cfg.tick_size)),
        "first_tick_dt_after_entry": first_tick_dt_after_entry,
    }
    if cfg.collect_trace:
        out["trace"] = pd.DataFrame(trace)
    return out


def simulate_exit_policy_for_trade_debug(
    *,
    ticks: pd.DataFrame,
    entry_dt: pd.Timestamp,
    entry_price: float,
    direction: int,
    atr_entry: float,
    policy: Policy,
    cfg: ExitSimConfig,
) -> Tuple[dict, pd.DataFrame]:
    """
    Public helper for the HTML debug section.

    Returns:
      (result_dict, trace_df)

    trace_df columns:
      dt,last,exec_px,best_price,pnl_ticks_last,mfe_ticks,mae_ticks,
      stop_price,target_price,stop_changed,be_armed,be_armed_now,exit_now,exit_reason
    """
    cfg2 = ExitSimConfig(**{**cfg.__dict__, "collect_trace": True})
    res = _simulate_one_trade(
        ticks=ticks,
        entry_dt=entry_dt,
        entry_price=entry_price,
        direction=direction,
        atr_entry=atr_entry,
        policy=policy,
        cfg=cfg2,
    )
    trace = res.pop("trace", pd.DataFrame())
    if trace is None:
        trace = pd.DataFrame()
    return res, trace


# (rest of file unchanged)
# simulate_exit_policies_for_run(...) remains unchanged from your current version



def simulate_exit_policies_for_run(
    *,
    run_id: str,
    trades: pd.DataFrame,
    market: MarketDataStore,
    instrument: str,
    contract: str,
    policies: Sequence[Policy],
    cfg: ExitSimConfig,
) -> pd.DataFrame:
    if trades is None or trades.empty:
        return _diag_row(run_id, instrument, contract, "no_trades", "Trades dataframe was empty.")

    def _norm(s: str) -> str:
        s = (s or "").lower()
        return "".join([c for c in s if c.isalnum()])

    def _find_col(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
        if df is None or df.empty:
            return None
        m = {_norm(c): c for c in df.columns}
        for cand in candidates:
            k = _norm(cand)
            if k in m:
                return m[k]
        cols = list(df.columns)
        for cand in candidates:
            lc = str(cand).lower()
            for c in cols:
                if lc in str(c).lower():
                    return c
        return None

    def _direction_sign(trade_row: pd.Series) -> int:
        # Primary: Market position column
        for key in ("Market pos.", "market_pos", "Market position", "Market Position"):
            if key in trade_row.index and pd.notna(trade_row[key]):
                s = str(trade_row[key]).strip().lower()
                if s.startswith("short"):
                    return -1
                if s.startswith("long"):
                    return +1
        # Default long (backward compatible)
        return +1

    entry_dt_col = _find_col(trades, ["entry_dt", "entry_time", "Entry time", "Entry Time"])
    exit_dt_col = _find_col(trades, ["exit_dt", "exit_time", "Exit time", "Exit Time"])
    entry_px_col = _find_col(trades, ["entry_price", "Entry price", "Entry Price"])
    exit_px_col = _find_col(trades, ["exit_price", "Exit price", "Exit Price"])

    if entry_dt_col is None or entry_px_col is None:
        return _diag_row(
            run_id,
            instrument,
            contract,
            "missing_trade_columns",
            f"Could not find entry_dt/entry_px. cols={list(trades.columns)}",
        )

    t = trades.copy()

    # Enforce tz-aware America/Denver for trades
    t["_entry_dt"] = _ensure_series_tz(t[entry_dt_col], "America/Denver")
    t["_exit_dt"] = _ensure_series_tz(t[exit_dt_col], "America/Denver") if exit_dt_col else pd.NaT
    t["_entry_px"] = pd.to_numeric(t[entry_px_col], errors="coerce")

    if exit_px_col:
        t["_exit_px"] = pd.to_numeric(t[exit_px_col], errors="coerce")
    else:
        t["_exit_px"] = np.nan

    t = t[t["_entry_dt"].notna() & t["_entry_px"].notna()].copy()
    if t.empty:
        return _diag_row(run_id, instrument, contract, "no_valid_trades", "All trades missing entry_dt or entry_px after parsing.")

    ticks_all = market.get_ticks(instrument, contract)
    if ticks_all is None or ticks_all.empty:
        return _diag_row(run_id, instrument, contract, "missing_ticks", "MarketDataStore has no ticks for this instrument/contract.")

    if "dt" not in ticks_all.columns:
        return _diag_row(run_id, instrument, contract, "ticks_missing_dt", f"Tick dataframe missing 'dt'. cols={list(ticks_all.columns)}")

    ticks_all = ticks_all.copy()
    ticks_all["dt"] = _ensure_series_tz(ticks_all["dt"], "America/Denver")
    ticks_all = ticks_all[ticks_all["dt"].notna()].sort_values("dt").reset_index(drop=True)

    dt_utc_naive = ticks_all["dt"].dt.tz_convert("UTC").dt.tz_localize(None)
    tick_ns = dt_utc_naive.astype("int64").to_numpy(dtype="int64", copy=False)
    if tick_ns.size and tick_ns.max() < 10**17:
        tick_ns = tick_ns * 1000

    bars = market.get_bars(instrument, contract, timeframe=cfg.atr_tf, source="ticks")
    if bars is None or bars.empty:
        return _diag_row(
            run_id,
            instrument,
            contract,
            "missing_bars",
            f"get_bars(timeframe={cfg.atr_tf}, source='ticks') returned empty. "
            f"ticks_dt=[{ticks_all['dt'].min()}..{ticks_all['dt'].max()}]",
        )

    bars = bars.copy()
    if "dt" not in bars.columns:
        return _diag_row(run_id, instrument, contract, "bars_missing_dt", f"Bars columns={list(bars.columns)}")

    bars["dt"] = _ensure_series_tz(bars["dt"], "America/Denver")
    bars = bars.sort_values("dt").reset_index(drop=True)

    bars["atr"] = atr_wilder(bars, period=cfg.atr_period)
    if "atr" not in bars.columns:
        return _diag_row(run_id, instrument, contract, "atr_failed", "atr_wilder did not produce 'atr' column.")

    if bars["atr"].dropna().empty:
        return _diag_row(
            run_id,
            instrument,
            contract,
            "atr_all_nan",
            f"ATR all NaN. bars_rows={len(bars)} timeframe={cfg.atr_tf} period={cfg.atr_period}",
        )

    tr_entry_min = t["_entry_dt"].min()
    tr_entry_max = t["_entry_dt"].max()
    tr_exit_max = t["_exit_dt"].max() if "_exit_dt" in t.columns else pd.NaT

    tk_min = ticks_all["dt"].min()
    tk_max = ticks_all["dt"].max()

    tr_max = tr_exit_max if pd.notna(tr_exit_max) else tr_entry_max

    if pd.notna(tr_entry_min) and pd.notna(tr_max) and pd.notna(tk_min) and pd.notna(tk_max):
        no_overlap = (tr_max < tk_min) or (tr_entry_min > tk_max)
        if no_overlap:
            return pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "trade_idx": -1,
                        "policy": "DIAGNOSTIC",
                        "entry_dt": tr_entry_min,
                        "exit_dt": tr_max,
                        "entry_price": np.nan,
                        "exit_price": np.nan,
                        "exit_reason": "tick_coverage_missing",
                        "pnl_ticks": np.nan,
                        "mae_ticks": np.nan,
                        "mfe_ticks": np.nan,
                        "atr_entry": np.nan,
                        "detail": (
                            f"NO_TICK_OVERLAP trades[{tr_entry_min}..{tr_max}] "
                            f"ticks[{tk_min}..{tk_max}] instrument={instrument} contract={contract}"
                        ),
                    }
                ]
            )

    atr_asof = pd.merge_asof(
        t[["_entry_dt"]].sort_values("_entry_dt").rename(columns={"_entry_dt": "ts"}),
        bars[["dt", "atr"]].dropna().sort_values("dt"),
        left_on="ts",
        right_on="dt",
        direction="backward",
        allow_exact_matches=True,
    )

    if pd.isna(atr_asof["atr"]).all():
        return pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "trade_idx": -1,
                    "policy": "DIAGNOSTIC",
                    "entry_dt": t["_entry_dt"].min(),
                    "exit_dt": t["_entry_dt"].max(),
                    "entry_price": np.nan,
                    "exit_price": np.nan,
                    "exit_reason": "atr_asof_all_nan",
                    "pnl_ticks": np.nan,
                    "mae_ticks": np.nan,
                    "mfe_ticks": np.nan,
                    "atr_entry": np.nan,
                    "detail": (
                        f"ATR merge_asof produced all NaN. "
                        f"trades_dt=[{t['_entry_dt'].min()}..{t['_entry_dt'].max()}] "
                        f"bars_dt=[{bars['dt'].min()}..{bars['dt'].max()}] "
                        f"ticks_dt=[{ticks_all['dt'].min()}..{ticks_all['dt'].max()}]"
                    ),
                }
            ]
        )

    t = t.sort_values("_entry_dt").reset_index(drop=True)
    t["_atr_entry"] = pd.to_numeric(atr_asof["atr"], errors="coerce").values

    rows = []
    empty_windows = 0

    for i, tr in t.iterrows():
        entry_dt = tr["_entry_dt"]
        entry_px = float(tr["_entry_px"])
        direction = _direction_sign(tr)

        if cfg.bounded_to_original_exit and pd.notna(tr.get("_exit_dt")):
            end_dt = tr["_exit_dt"]
        else:
            end_dt = entry_dt + pd.Timedelta(minutes=int(cfg.max_minutes_unbounded))

        if pd.isna(end_dt) or end_dt <= entry_dt:
            continue

        atr_entry = float(tr["_atr_entry"]) if pd.notna(tr["_atr_entry"]) else 0.0
        if atr_entry <= 0:
            continue

        start_dt = entry_dt - pd.Timedelta(seconds=int(cfg.entry_time_slop_seconds or 0))
        win = _ticks_window_from_ns(ticks_all, tick_ns, start_dt, end_dt)

        if win is None or win.empty:
            empty_windows += 1
            continue

        # --- ACTUAL trade row (must respect direction) ---
        if pd.notna(tr.get("_exit_px")) and pd.notna(tr.get("_exit_dt")):
            actual_exit_px = float(tr["_exit_px"])
            actual_exit_dt = tr["_exit_dt"]

            pnl_pts_actual = (actual_exit_px - entry_px) * direction
            pnl_ticks_actual = pnl_pts_actual / float(cfg.tick_size)

            rows.append(
                {
                    "run_id": run_id,
                    "trade_idx": int(i),
                    "policy": "actual",
                    "entry_dt": entry_dt,
                    "exit_dt": actual_exit_dt,
                    "entry_price": entry_px,
                    "exit_price": actual_exit_px,
                    "exit_reason": "actual_trade_exit",
                    "pnl_ticks": float(pnl_ticks_actual),
                    "atr_entry": atr_entry,
                    "best_fav_ticks": np.nan,
                    "worst_adv_ticks": np.nan,
                    "be_armed": np.nan,
                    "be_arm_dt": pd.NaT,
                    "stop_at_arm": np.nan,
                }
            )

        for p in policies:
            res = _simulate_one_trade(
                ticks=win,
                entry_dt=entry_dt,
                entry_price=entry_px,
                direction=direction,
                atr_entry=atr_entry,
                policy=p,
                cfg=cfg,
            )

            # --- BOUNDED FALLBACK ---
            if (
                cfg.bounded_to_original_exit
                and pd.notna(tr.get("_exit_dt"))
                and pd.notna(tr.get("_exit_px"))
                and res.get("exit_reason") == "time"
            ):
                forced_exit_dt = tr["_exit_dt"]
                forced_exit_px = float(tr["_exit_px"])
                pnl_ticks_forced = (forced_exit_px - entry_px) * direction / float(cfg.tick_size)

                res = dict(res)
                res["exit_dt"] = forced_exit_dt
                res["exit_price"] = forced_exit_px
                res["exit_reason"] = "bounded_to_actual_exit"
                res["pnl_ticks"] = float(pnl_ticks_forced)

            rows.append(
                {
                    "run_id": run_id,
                    "trade_idx": int(i),
                    "policy": getattr(p, "name", p.__class__.__name__),
                    "entry_dt": entry_dt,
                    "exit_dt": res.get("exit_dt"),
                    "entry_price": entry_px,
                    "exit_price": res.get("exit_price"),
                    "exit_reason": res.get("exit_reason"),
                    "pnl_ticks": res.get("pnl_ticks"),
                    "mae_ticks": res.get("mae_ticks"),
                    "mfe_ticks": res.get("mfe_ticks"),
                    "atr_entry": atr_entry,
                    "best_fav_ticks": res.get("best_fav_ticks"),
                    "worst_adv_ticks": res.get("worst_adv_ticks"),
                    "be_armed": res.get("be_armed"),
                    "be_arm_dt": res.get("be_arm_dt"),
                    "stop_at_arm": res.get("stop_at_arm"),
                    "best_fav_ticks_exec": res.get("best_fav_ticks_exec"),
                    "best_fav_ticks_last": res.get("best_fav_ticks_last"),
                }
            )

    if not rows:
        tick_min = ticks_all["dt"].min()
        tick_max = ticks_all["dt"].max()
        tr_min = t["_entry_dt"].min()
        tr_max = (t["_exit_dt"].max() if pd.notna(t["_exit_dt"]).any() else t["_entry_dt"].max())

        ex = t.iloc[0]
        ex_entry = ex["_entry_dt"]
        ex_end = ex_entry + pd.Timedelta(minutes=int(cfg.max_minutes_unbounded))
        s_ns = int(pd.Timestamp(ex_entry).value)
        e_ns = int(pd.Timestamp(ex_end).value)
        s = int(np.searchsorted(tick_ns, s_ns, side="left"))
        e = int(np.searchsorted(tick_ns, e_ns, side="right"))
        around = []
        for j in [s - 1, s, s + 1]:
            if 0 <= j < len(ticks_all):
                around.append(f"{j}:{ticks_all['dt'].iloc[j]}")
        around_str = " | ".join(around) if around else "(none)"

        return _diag_row(
            run_id,
            instrument,
            contract,
            "no_sim_rows",
            f"No simulation rows produced. trades_valid={len(t)} empty_tick_windows={empty_windows}. "
            f"ticks_dt=[{tick_min}..{tick_max}] trades_dt=[{tr_min}..{tr_max}]. "
            f"example_trade entry={ex_entry} end={ex_end} searchsorted s={s} e={e} around={around_str} "
            f"tick_ns[min]={int(tick_ns[0])} tick_ns[max]={int(tick_ns[-1])} "
            f"entry_ns={s_ns} end_ns={e_ns}",
        )

    out = pd.DataFrame(rows)

    if len(t) > 0 and empty_windows > 0 and (empty_windows / float(len(t))) > 0.20:
        out = pd.concat(
            [
                out,
                pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "trade_idx": -1,
                            "policy": "DIAGNOSTIC",
                            "entry_dt": t["_entry_dt"].min(),
                            "exit_dt": t["_entry_dt"].max(),
                            "entry_price": np.nan,
                            "exit_price": np.nan,
                            "exit_reason": "empty_tick_windows",
                            "pnl_ticks": np.nan,
                            "mae_ticks": np.nan,
                            "mfe_ticks": np.nan,
                            "atr_entry": np.nan,
                            "detail": f"Empty tick windows for {empty_windows}/{len(t)} trades. Check tick coverage vs trade timestamps.",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    return out
