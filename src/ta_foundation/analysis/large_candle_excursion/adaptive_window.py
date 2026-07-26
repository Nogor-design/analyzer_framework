from __future__ import annotations

"""Adaptive, walk-forward large-candle window analysis.

This module answers a different question from the pooled LCE reports:

    "Given only outcomes that were already known at this signal, was this
    time-of-day window recently better for continuation, reversion, or neither?"

It also provides an optional CandleCenterBotV2 parity signal emitter.  That
strategy differs from the generic LCE/LCR engines in important ways:

* its rolling average includes the current candle;
* it can signal on both a fresh oversized candle and a prior center-zone pierce;
* its zone is a configurable percentage centered on the source candle;
* its bracket is fixed ticks and allows overlapping entries.

The output is JSON-safe and intentionally keeps parameter lanes separate.
Mixing duplicate representations of the same physical candle across
timeframes/lookbacks/thresholds creates artificial win clusters, so no pooled
"best lane" verdict is produced here.
"""

from collections import deque
from datetime import time
from math import exp, floor, isfinite, log, sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars


DEFAULT_ADAPTIVE_WINDOW_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "timeframes": [1, 2],
    "lookbacks": [5],
    "bases": ["range"],
    "multipliers": [1.5],
    "tick_size": 0.25,
    "tick_value": 5.0,
    "average_mode": "include_current",  # CandleCenterBotV2 parity
    "signal_direction": "bear_only",  # both | bull_only | bear_only
    "bars_required": 20,
    "signals": {
        "fresh_large_candle": True,
        "center_zone_break": True,
        # Zone-lifecycle triggers ported from the LCR region engine
        # (analysis/entry_strategies/lcr/regions.py). Default OFF: they are
        # additional research hypotheses, not part of CandleCenterBotV2 parity,
        # and enabling them would change recorded baseline lane results.
        #   zone_touch   — zone was tested and HELD; trade with the zone.
        #   zone_retrace — price returns to an already-broken zone within
        #                  retrace_window_bars; trade the break direction
        #                  (broken support becomes resistance).
        "zone_touch": False,
        "zone_retrace": False,
        "retrace_window_bars": 20,
        "region_percent": 30.0,
        "invalidate_on_close": False,
        "max_active_zones": 50,
        "max_zone_age_bars": None,
        # CandleCenterBotV2 leaves ``enterTrade`` set when a trigger occurs
        # outside the time window while flat. The pending attempt is consumed
        # by the first in-window bar, whose candle direction controls the order.
        "latch_outside_window_triggers": True,
    },
    "time_filter": {
        "enabled": True,
        "start": "10:00",
        "end": "13:20",
        "timezone": "America/Denver",
    },
    "outcome": {
        "target_ticks": 75.0,
        "stop_ticks": 150.0,
        "max_hold_minutes": 300,
        "max_concurrent_per_direction": 3,
        "same_bar_policy": "stop_first",  # stop_first | target_first
        "round_trip_cost_ticks": 0.0,
    },
    "adaptive": {
        "training_days": 10,
        "time_bin_minutes": 30,
        "neighbor_bins": 0,
        "min_local_signals": 8,
        "half_life_days": 5.0,
        "prior_strength": 5.0,
        "confidence_z": 0.5,
        "min_expected_net_ticks": 0.0,
        "min_lower_bound_ticks": 0.0,
        "mode_margin_ticks": 5.0,
        "max_weekly_rows": 12,
        "max_decision_rows": 250,
    },
}


def run_adaptive_large_candle_windows(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run parity signal reconstruction, bracket simulation, and walk-forward gating."""
    cfg = _deep_merge(DEFAULT_ADAPTIVE_WINDOW_CONFIG, config or {})
    if not cfg.get("enabled", True):
        return {"enabled": False}
    if bars_1m is None or bars_1m.empty:
        return {"enabled": True, "message": "no minute bars", "streams": []}

    bars = _prepare_bars(bars_1m)
    event_streams = _build_adaptive_event_streams_from_prepared(bars, cfg)
    streams = [
        _analyze_stream(
            stream["events"],
            stream["lane_config"],
            lane_id=stream["lane_id"],
            signal_side=stream["signal_side"],
        )
        for stream in event_streams
    ]
    total_events = sum(len(stream["events"]) for stream in event_streams)

    return _json_safe({
        "enabled": True,
        "n_bars": int(len(bars)),
        "start": _iso(bars["dt"].min()),
        "end": _iso(bars["dt"].max()),
        "n_events": total_events,
        "n_streams": len(streams),
        "streams": streams,
        "config": cfg,
        "methodology": {
            "decision_timing": (
                "Each event is scored only from prior signals whose simulated exit "
                "was known before the current entry."
            ),
            "lane_isolation": (
                "Timeframe/lookback/basis/multiplier/signal-side lanes are never pooled."
            ),
            "trade_state": (
                "TRADE requires the recency-weighted lower confidence bound to clear "
                "zero after costs; LEAN has positive shrunken expectancy but weaker confidence."
            ),
            "path_ordering": (
                "TP/SL are scanned bar by bar. If both are touched in one 1m bar, "
                "the configured same-bar policy is applied (stop-first by default)."
            ),
            "limitations": [
                "One-minute OHLC cannot resolve the order of two levels touched inside the same minute.",
                "The parity emitter models CandleCenterBotV2 signal and bracket behavior, not exchange queue position.",
                "Weekly window replay freezes the window map at the start of each week.",
                "Parameter-lane comparison still requires a locked holdout because lane selection is a multiple test.",
            ],
        },
    })


def build_adaptive_event_streams(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build complete, lane-isolated counterfactual event/outcome streams.

    Unlike ``run_adaptive_large_candle_windows``, this reusable research
    surface does not score, sample, or JSON-serialize the rows. Every returned
    event retains both continuation and reversion outcomes, including their
    exact ``exit_known_dt`` values, so downstream causal gates can enforce
    decision-time outcome eligibility without relying on the UI's 100-row
    event sample.
    """
    cfg = _deep_merge(DEFAULT_ADAPTIVE_WINDOW_CONFIG, config or {})
    if not cfg.get("enabled", True) or bars_1m is None or bars_1m.empty:
        return []
    return _build_adaptive_event_streams_from_prepared(
        _prepare_bars(bars_1m),
        cfg,
    )


def _build_adaptive_event_streams_from_prepared(
    bars: pd.DataFrame,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    streams: List[Dict[str, Any]] = []
    for tf in _positive_ints(config.get("timeframes") or [1, 2]):
        tf_bars = (
            bars.copy()
            if tf == 1
            else ohlcv_resample_from_bars(bars, f"{tf}m")
        )
        if tf_bars is None or tf_bars.empty:
            continue
        tf_bars = _prepare_bars(tf_bars)

        for lookback in _positive_ints(config.get("lookbacks") or [5]):
            for basis in _bases(config.get("bases") or ["range"]):
                for multiplier in _positive_floats(
                    config.get("multipliers") or [1.5]
                ):
                    lane_cfg = {
                        **config,
                        "timeframe": tf,
                        "lookback": lookback,
                        "basis": basis,
                        "multiplier": multiplier,
                    }
                    events = emit_candle_center_events(tf_bars, lane_cfg)
                    outcomes = simulate_event_brackets(events, bars, lane_cfg)
                    if not outcomes:
                        continue

                    lane_id = _lane_id(tf, lookback, basis, multiplier)
                    for signal_side in ("bull", "bear"):
                        side_rows = sorted(
                            (
                                row
                                for row in outcomes
                                if row.get("signal_side") == signal_side
                            ),
                            key=lambda row: pd.Timestamp(row["entry_dt"]),
                        )
                        if not side_rows:
                            continue
                        streams.append({
                            "lane_id": lane_id,
                            "signal_side": signal_side,
                            "timeframe": tf,
                            "lookback": lookback,
                            "basis": basis,
                            "multiplier": multiplier,
                            "lane_config": lane_cfg,
                            "events": side_rows,
                        })
    return streams


def emit_candle_center_events(
    bars: pd.DataFrame,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Reconstruct fresh-large-candle and center-zone-break signals."""
    if bars is None or bars.empty:
        return []

    tf = int(config.get("timeframe", 1))
    lookback = max(1, int(config.get("lookback", 5)))
    basis = str(config.get("basis", "range")).lower()
    multiplier = float(config.get("multiplier", 1.5))
    tick_size = float(config.get("tick_size", 0.25))
    avg_mode = str(config.get("average_mode", "include_current"))
    direction_filter = str(config.get("signal_direction", "both"))
    bars_required = max(1, int(config.get("bars_required", 20)))
    sig_cfg = config.get("signals") or {}
    use_fresh = bool(sig_cfg.get("fresh_large_candle", True))
    use_break = bool(sig_cfg.get("center_zone_break", True))
    use_touch = bool(sig_cfg.get("zone_touch", False))
    use_retrace = bool(sig_cfg.get("zone_retrace", False))
    retrace_window = max(1, int(sig_cfg.get("retrace_window_bars", 20)))
    region_pct = max(0.0, float(sig_cfg.get("region_percent", 30.0)))
    on_close = bool(sig_cfg.get("invalidate_on_close", False))
    max_zones = max(1, int(sig_cfg.get("max_active_zones", 50)))
    max_age_raw = sig_cfg.get("max_zone_age_bars")
    max_age = int(max_age_raw) if max_age_raw not in (None, "") else None
    latch_outside = bool(sig_cfg.get("latch_outside_window_triggers", True))

    o = bars["open"].to_numpy(dtype=float)
    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    dts = [pd.Timestamp(v) for v in bars["dt"]]
    raw_size = np.abs(c - o) if basis == "body" else (h - lo)
    size_ticks = np.asarray([_round_away(v / tick_size) for v in raw_size], dtype=float)

    size_window: deque[float] = deque()
    size_sum = 0.0
    active_zones: List[Dict[str, Any]] = []
    broken_watch: List[Dict[str, Any]] = []
    pending_trigger: Optional[Dict[str, Any]] = None
    zone_id = 0
    events: List[Dict[str, Any]] = []

    for i in range(len(bars)):
        current_ticks = float(size_ticks[i])

        if avg_mode == "include_current":
            size_window.append(current_ticks)
            size_sum += current_ticks
            while len(size_window) > lookback:
                size_sum -= size_window.popleft()
            rolling_avg = size_sum / len(size_window) if size_window else 0.0
        else:
            rolling_avg = size_sum / len(size_window) if size_window else 0.0

        invalidated: List[Dict[str, Any]] = []
        still_active: List[Dict[str, Any]] = []
        touched: List[Dict[str, Any]] = []
        for zone in active_zones:
            if max_age is not None and i - int(zone["start_bar"]) > max_age:
                continue
            if bool(zone["bullish"]):
                broke = c[i] <= zone["bottom"] if on_close else lo[i] <= zone["bottom"]
            else:
                broke = c[i] >= zone["top"] if on_close else h[i] >= zone["top"]
            if broke:
                invalidated.append(zone)
                continue
            # Zone survived this bar. A "touch" is a test that HELD: the bar
            # overlapped the band but closed back out on the zone's own side.
            # Mirrors LCREvent(event_type="touch") in lcr/regions.py.
            if use_touch and lo[i] <= zone["top"] and h[i] >= zone["bottom"]:
                closed_outside = (
                    c[i] > zone["top"] if bool(zone["bullish"]) else c[i] < zone["bottom"]
                )
                if closed_outside:
                    zone["touch_count"] = int(zone.get("touch_count", 0)) + 1
                    touched.append(zone)
            still_active.append(zone)
        active_zones = still_active

        # Record broken zones so a later re-entry can fire a retrace signal.
        # next_zone_dist_ticks is measured at break time toward the nearest
        # surviving zone in the break direction — a structurally derived target
        # distance, as opposed to the fixed bracket.
        if use_retrace and invalidated:
            for zone in invalidated:
                break_dir = -1 if bool(zone["bullish"]) else 1
                ref_price = zone["bottom"] if bool(zone["bullish"]) else zone["top"]
                broken_watch.append({
                    "zone": zone,
                    "break_bar": i,
                    "break_dir": break_dir,
                    "next_zone_dist_ticks": _nearest_zone_dist_ticks(
                        ref_price, active_zones, break_dir, tick_size
                    ),
                })

        # Expire stale retrace candidates, and detect re-entries.
        retraced: List[Dict[str, Any]] = []
        if use_retrace and broken_watch:
            surviving_watch: List[Dict[str, Any]] = []
            for watch in broken_watch:
                bars_since = i - int(watch["break_bar"])
                if bars_since > retrace_window:
                    continue
                # The breaking bar necessarily touches the band it broke, so a
                # re-entry only counts from the FOLLOWING bar onward. Without
                # this guard every break fires an instant same-bar retrace.
                if bars_since < 1:
                    surviving_watch.append(watch)
                    continue
                z = watch["zone"]
                if lo[i] <= z["top"] and h[i] >= z["bottom"]:
                    retraced.append(watch)   # consumed: fires once per zone
                    continue
                surviving_watch.append(watch)
            broken_watch = surviving_watch

        is_large = bool(
            i >= bars_required
            and rolling_avg > 0
            and current_ticks >= rolling_avg * multiplier
        )
        trigger_fresh = bool(use_fresh and is_large)
        trigger_break = bool(use_break and invalidated)
        zone_bullish = bool(c[i] >= o[i])
        # GoLong() is selected only when Open[0] < Close[0]. A doji therefore
        # follows CandleCenterBotV2's short branch even though a newly-created
        # doji zone is classified as bullish by Close[0] >= Open[0].
        trade_bullish = bool(c[i] > o[i])
        signal_side = "bull" if trade_bullish else "bear"
        in_direction = (
            direction_filter == "both"
            or (direction_filter == "bull_only" and trade_bullish)
            or (direction_filter == "bear_only" and not trade_bullish)
        )
        entry_dt = dts[i] + pd.Timedelta(minutes=tf)
        in_time = _inside_time_filter(dts[i], config.get("time_filter") or {})

        current_trigger = bool(trigger_fresh or trigger_break)
        latched_trigger = pending_trigger if (latch_outside and in_time) else None
        attempt_entry = bool(current_trigger or latched_trigger)

        if attempt_entry and in_time:
            effective_fresh = bool(
                trigger_fresh
                or (latched_trigger and latched_trigger.get("fresh_trigger"))
            )
            effective_break = bool(
                trigger_break
                or (latched_trigger and latched_trigger.get("zone_break_trigger"))
            )
            trigger_type = (
                "fresh+zone_break" if effective_fresh and effective_break
                else ("fresh" if effective_fresh else "zone_break")
            )
            if in_direction:
                trigger_source_dt = (
                    latched_trigger.get("trigger_source_dt")
                    if latched_trigger is not None
                    else dts[i]
                )
                events.append({
                    "signal_dt": dts[i],
                    "entry_dt": entry_dt,
                    "trigger_source_dt": trigger_source_dt,
                    "latched_outside_window": latched_trigger is not None,
                    "source_bar_idx": i,
                    "tf_minutes": tf,
                    "lookback": lookback,
                    "basis": basis,
                    "multiplier": multiplier,
                    "average_mode": avg_mode,
                    "rolling_avg_ticks": round(rolling_avg, 3),
                    "signal_size_ticks": current_ticks,
                    "signal_ratio": round(current_ticks / rolling_avg, 4) if rolling_avg else None,
                    "signal_direction": 1 if trade_bullish else -1,
                    "signal_side": signal_side,
                    "trigger_type": trigger_type,
                    "fresh_trigger": effective_fresh,
                    "zone_break_trigger": effective_break,
                    "zones_broken": (
                        len(invalidated)
                        if current_trigger
                        else int(latched_trigger.get("zones_broken") or 0)
                    ),
                    "open": float(o[i]),
                    "high": float(h[i]),
                    "low": float(lo[i]),
                    "close": float(c[i]),
                })
            # The strategy clears enterTrade after trying the direction, even
            # when Long/Short settings reject that order.
            pending_trigger = None
        elif current_trigger and latch_outside and not in_time:
            # The C# time filter does not clear enterTrade while flat. Retain
            # the latest outside-window cause for diagnostics; the strategy
            # itself retains only the boolean pending state.
            pending_trigger = {
                "trigger_source_dt": dts[i],
                "fresh_trigger": trigger_fresh,
                "zone_break_trigger": trigger_break,
                "zones_broken": len(invalidated),
            }

        if is_large:
            thickness = _round_away(current_ticks * region_pct / 100.0)
            if thickness >= 1:
                half_ticks = max(1, thickness // 2)
                center = (o[i] + c[i]) * 0.5 if basis == "body" else (h[i] + lo[i]) * 0.5
                center = _round_price(center, tick_size)
                active_zones.append({
                    "zone_id": zone_id,
                    "start_bar": i,
                    "bullish": zone_bullish,
                    "touch_count": 0,
                    "top": _round_price(center + half_ticks * tick_size, tick_size),
                    "bottom": _round_price(center - half_ticks * tick_size, tick_size),
                })
                zone_id += 1
                if len(active_zones) > max_zones:
                    active_zones = active_zones[-max_zones:]

        # ---- zone-lifecycle events (LCR port) -----------------------------
        # Emitted independently of the fresh/break trigger above: these are
        # zone-state signals, not candle-close signals, so they do not consume
        # or interact with the strategy's pending-trigger latch.
        if in_time and (touched or retraced):
            for zone in touched:
                # The zone HELD, so trade with it: a bullish zone that held is
                # support (long), a bearish zone that held is resistance (short).
                if not _passes_direction(bool(zone["bullish"]), direction_filter):
                    continue
                events.append(_zone_event(
                    trigger_type="zone_touch",
                    direction=1 if bool(zone["bullish"]) else -1,
                    zone=zone, i=i, dts=dts, entry_dt=entry_dt, tf=tf,
                    lookback=lookback, basis=basis, multiplier=multiplier,
                    avg_mode=avg_mode, rolling_avg=rolling_avg,
                    current_ticks=current_ticks, o=o, h=h, lo=lo, c=c,
                    touch_count=int(zone.get("touch_count", 0)),
                    zone_age_bars=i - int(zone["start_bar"]),
                    next_zone_dist_ticks=None,
                ))
            for watch in retraced:
                # The zone FAILED and price came back to it: trade the break
                # direction (broken support becomes resistance).
                zone = watch["zone"]
                dir_bullish = int(watch["break_dir"]) > 0
                if not _passes_direction(dir_bullish, direction_filter):
                    continue
                events.append(_zone_event(
                    trigger_type="zone_retrace",
                    direction=int(watch["break_dir"]),
                    zone=zone, i=i, dts=dts, entry_dt=entry_dt, tf=tf,
                    lookback=lookback, basis=basis, multiplier=multiplier,
                    avg_mode=avg_mode, rolling_avg=rolling_avg,
                    current_ticks=current_ticks, o=o, h=h, lo=lo, c=c,
                    touch_count=int(zone.get("touch_count", 0)),
                    zone_age_bars=i - int(zone["start_bar"]),
                    next_zone_dist_ticks=watch.get("next_zone_dist_ticks"),
                ))

        if avg_mode != "include_current":
            size_window.append(current_ticks)
            size_sum += current_ticks
            while len(size_window) > lookback:
                size_sum -= size_window.popleft()

    events.sort(key=lambda r: (pd.Timestamp(r["entry_dt"]), str(r["trigger_type"])))
    return events


def _passes_direction(bullish: bool, direction_filter: str) -> bool:
    return (
        direction_filter == "both"
        or (direction_filter == "bull_only" and bullish)
        or (direction_filter == "bear_only" and not bullish)
    )


def _nearest_zone_dist_ticks(
    price: float,
    zones: Sequence[Dict[str, Any]],
    break_dir: int,
    tick_size: float,
) -> Optional[float]:
    """Ticks from ``price`` to the nearest zone edge in the break direction.

    Port of ``_nearest_region_dist`` from lcr/regions.py. Gives a structurally
    derived target distance to compare against a fixed bracket.
    """
    best: Optional[float] = None
    for z in zones:
        edge = float(z["top"]) if break_dir > 0 else float(z["bottom"])
        delta = (edge - float(price)) * break_dir
        if delta > 0 and (best is None or delta < best):
            best = delta
    if best is None or tick_size <= 0:
        return None
    return round(best / tick_size, 3)


def _zone_event(
    *,
    trigger_type: str,
    direction: int,
    zone: Dict[str, Any],
    i: int,
    dts: Sequence[pd.Timestamp],
    entry_dt: pd.Timestamp,
    tf: int,
    lookback: int,
    basis: str,
    multiplier: float,
    avg_mode: str,
    rolling_avg: float,
    current_ticks: float,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    touch_count: int,
    zone_age_bars: int,
    next_zone_dist_ticks: Optional[float],
) -> Dict[str, Any]:
    """Build a zone-lifecycle event with the same schema as candle triggers."""
    return {
        "signal_dt": dts[i],
        "entry_dt": entry_dt,
        "trigger_source_dt": dts[i],
        "latched_outside_window": False,
        "source_bar_idx": i,
        "tf_minutes": tf,
        "lookback": lookback,
        "basis": basis,
        "multiplier": multiplier,
        "average_mode": avg_mode,
        "rolling_avg_ticks": round(rolling_avg, 3),
        "signal_size_ticks": current_ticks,
        "signal_ratio": round(current_ticks / rolling_avg, 4) if rolling_avg else None,
        "signal_direction": int(direction),
        "signal_side": "bull" if direction > 0 else "bear",
        "trigger_type": trigger_type,
        "fresh_trigger": False,
        "zone_break_trigger": False,
        "zones_broken": 0,
        # zone-lifecycle features (no equivalent on candle triggers)
        "zone_id": int(zone.get("zone_id", -1)),
        "zone_touch_count": int(touch_count),
        "zone_age_bars": int(zone_age_bars),
        "zone_top": float(zone["top"]),
        "zone_bottom": float(zone["bottom"]),
        "next_zone_dist_ticks": next_zone_dist_ticks,
        "open": float(o[i]),
        "high": float(h[i]),
        "low": float(lo[i]),
        "close": float(c[i]),
    }


def simulate_event_brackets(
    events: Sequence[Dict[str, Any]],
    bars_1m: pd.DataFrame,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Simulate continuation and reversion brackets for every signal."""
    if not events or bars_1m is None or bars_1m.empty:
        return []

    bars = _prepare_bars(bars_1m)
    dt_ns = _dt_ns(bars["dt"])
    opens = bars["open"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    dts = [pd.Timestamp(v) for v in bars["dt"]]

    out: List[Dict[str, Any]] = []
    for ev in events:
        rec = dict(ev)
        for mode in ("continuation", "reversion"):
            rec[mode] = _simulate_one(
                ev, mode, dt_ns, opens, highs, lows, closes, dts, config
            )
        if rec["continuation"].get("available") or rec["reversion"].get("available"):
            out.append(rec)

    max_concurrent = max(
        1,
        int((config.get("outcome") or {}).get("max_concurrent_per_direction", 3)),
    )
    for mode in ("continuation", "reversion"):
        _mark_capacity_eligibility(out, mode, max_concurrent)
    return out


def _simulate_one(
    event: Dict[str, Any],
    mode: str,
    dt_ns: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    dts: Sequence[pd.Timestamp],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    outcome_cfg = config.get("outcome") or {}
    tick_size = float(config.get("tick_size", 0.25))
    target_ticks = max(0.0, float(outcome_cfg.get("target_ticks", 75.0)))
    stop_ticks = max(0.0, float(outcome_cfg.get("stop_ticks", 150.0)))
    cost_ticks = max(0.0, float(outcome_cfg.get("round_trip_cost_ticks", 0.0)))
    max_hold = max(1, int(outcome_cfg.get("max_hold_minutes", 120)))
    same_bar = str(outcome_cfg.get("same_bar_policy", "stop_first"))
    tf_minutes = max(1, int(config.get("timeframe", 1)))

    entry_dt = pd.Timestamp(event["entry_dt"])
    entry_ns = _ts_ns(entry_dt)
    entry_idx = int(np.searchsorted(dt_ns, entry_ns, side="left"))
    if entry_idx >= len(dt_ns):
        return {"available": False}

    direction = int(event.get("signal_direction") or 1)
    if mode == "reversion":
        direction *= -1
    entry_price = float(opens[entry_idx])
    target_price = entry_price + direction * target_ticks * tick_size
    stop_price = entry_price - direction * stop_ticks * tick_size

    hold_end = entry_dt + pd.Timedelta(minutes=max_hold)
    session_exit_dt = _session_exit_fill_for(
        entry_dt,
        config.get("time_filter") or {},
        tf_minutes,
    )
    exits_on_time = bool(session_exit_dt is not None and session_exit_dt <= hold_end)
    scan_end = session_exit_dt if exits_on_time else hold_end
    # A time-filter market exit fills at the open of session_exit_dt, so that
    # bar's high/low must not be scanned before the fill. A timeout exits after
    # its final bar and therefore includes that bar in the path scan.
    scan_side = "left" if exits_on_time else "right"
    end_idx = int(np.searchsorted(dt_ns, _ts_ns(scan_end), side=scan_side))
    end_idx = max(entry_idx + 1, min(end_idx, len(dt_ns)))

    exit_idx: Optional[int] = None
    exit_price: Optional[float] = None
    reason = "timeout"
    ambiguous = False

    for j in range(entry_idx, end_idx):
        if direction == 1:
            target_hit = highs[j] >= target_price
            stop_hit = lows[j] <= stop_price
        else:
            target_hit = lows[j] <= target_price
            stop_hit = highs[j] >= stop_price

        if target_hit and stop_hit:
            ambiguous = True
            exit_idx = j
            if same_bar == "target_first":
                exit_price = target_price
                reason = "ambiguous_target_first"
            else:
                exit_price = stop_price
                reason = "ambiguous_stop_first"
            break
        if stop_hit:
            exit_idx = j
            exit_price = stop_price
            reason = "stop"
            break
        if target_hit:
            exit_idx = j
            exit_price = target_price
            reason = "target"
            break

    if exit_idx is None:
        if exits_on_time:
            exit_idx = int(np.searchsorted(dt_ns, _ts_ns(session_exit_dt), side="left"))
            if exit_idx >= len(dt_ns):
                return {"available": False}
            exit_price = float(opens[exit_idx])
            reason = "time_filter_exit"
        else:
            exit_idx = end_idx - 1
            exit_price = float(closes[exit_idx])

    gross_ticks = direction * (float(exit_price) - entry_price) / tick_size
    path_high = float(np.nanmax(highs[entry_idx:exit_idx + 1]))
    path_low = float(np.nanmin(lows[entry_idx:exit_idx + 1]))
    if direction == 1:
        mfe = max(0.0, (path_high - entry_price) / tick_size)
        mae = max(0.0, (entry_price - path_low) / tick_size)
    else:
        mfe = max(0.0, (entry_price - path_low) / tick_size)
        mae = max(0.0, (path_high - entry_price) / tick_size)

    exit_dt = dts[exit_idx]
    exit_known = exit_dt + pd.Timedelta(minutes=1)
    return {
        "available": True,
        "trade_direction": direction,
        "entry_dt": entry_dt,
        "exit_dt": exit_dt,
        "exit_known_dt": exit_known,
        "entry_price": round(entry_price, 4),
        "exit_price": round(float(exit_price), 4),
        "gross_pnl_ticks": round(gross_ticks, 3),
        "net_pnl_ticks": round(gross_ticks - cost_ticks, 3),
        "mfe_ticks": round(mfe, 3),
        "mae_ticks": round(mae, 3),
        "exit_reason": reason,
        "ambiguous_same_bar": ambiguous,
        "bars_held_1m": int(exit_idx - entry_idx + 1),
        "capacity_eligible": True,
    }


def _analyze_stream(
    rows: List[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    lane_id: str,
    signal_side: str,
) -> Dict[str, Any]:
    ordered = sorted(rows, key=lambda r: pd.Timestamp(r["entry_dt"]))
    adaptive_cfg = config.get("adaptive") or {}
    max_decisions = int(adaptive_cfg.get("max_decision_rows", 250))
    tick_value = float(config.get("tick_value", 5.0))

    decisions: List[Dict[str, Any]] = []
    active_by_direction: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
    for row in ordered:
        asof = pd.Timestamp(row["entry_dt"])
        bucket = _bucket_index(asof, adaptive_cfg)
        score = _score_modes(ordered, asof, bucket, adaptive_cfg)
        decision = {
            "dt": asof,
            "time_bucket": _bucket_label(bucket, adaptive_cfg),
            "state": score["state"],
            "selected_mode": score.get("selected_mode"),
            "local_n": score.get("local_n", 0),
            "continuation": score["continuation"],
            "reversion": score["reversion"],
            "advantage_ticks": score.get("advantage_ticks"),
            "actual_net_ticks": None,
            "capacity_skipped": False,
            "trigger_type": row.get("trigger_type"),
        }
        if score["state"] == "trade" and score.get("selected_mode"):
            selected = row[score["selected_mode"]]
            direction = int(selected["trade_direction"])
            active = [x for x in active_by_direction[direction] if x > asof]
            active_by_direction[direction] = active
            max_concurrent = max(
                1,
                int((config.get("outcome") or {}).get("max_concurrent_per_direction", 3)),
            )
            if len(active) >= max_concurrent:
                decision["capacity_skipped"] = True
            else:
                decision["actual_net_ticks"] = selected.get("net_pnl_ticks")
                active.append(pd.Timestamp(selected["exit_known_dt"]))
                active_by_direction[direction] = active
        decisions.append(decision)

    asof = max(
        max(pd.Timestamp(r["entry_dt"]) for r in ordered) + pd.Timedelta(minutes=1),
        max(pd.Timestamp(r[m]["exit_known_dt"]) for r in ordered for m in ("continuation", "reversion")),
    )
    buckets = sorted({_bucket_index(pd.Timestamp(r["entry_dt"]), adaptive_cfg) for r in ordered})
    current_map = []
    for bucket in buckets:
        score = _score_modes(ordered, asof, bucket, adaptive_cfg)
        current_map.append({
            "time_bucket": _bucket_label(bucket, adaptive_cfg),
            "bucket_index": bucket,
            **score,
        })

    weekly = _weekly_replay(ordered, adaptive_cfg, config)
    cont_rows = [r["continuation"] for r in ordered if r["continuation"].get("capacity_eligible")]
    rev_rows = [r["reversion"] for r in ordered if r["reversion"].get("capacity_eligible")]
    traded = [
        {"net_pnl_ticks": d["actual_net_ticks"]}
        for d in decisions
        if d.get("actual_net_ticks") is not None and not d.get("capacity_skipped")
    ]

    return {
        "lane_id": lane_id,
        "signal_side": signal_side,
        "tf": int(config.get("timeframe", 1)),
        "lookback": int(config.get("lookback", 5)),
        "basis": str(config.get("basis", "range")),
        "multiplier": float(config.get("multiplier", 1.5)),
        "average_mode": str(config.get("average_mode", "include_current")),
        "n_events": len(ordered),
        "date_start": _iso(pd.Timestamp(ordered[0]["entry_dt"])),
        "date_end": _iso(pd.Timestamp(ordered[-1]["entry_dt"])),
        "breakeven_win_rate_pct": _breakeven_win_rate(config),
        "baseline": {
            "continuation": _performance(cont_rows, tick_value),
            "reversion": _performance(rev_rows, tick_value),
        },
        "trigger_breakdown": _trigger_breakdown(ordered, tick_value),
        "walk_forward": {
            **_performance(traded, tick_value),
            "signals_seen": len(ordered),
            "model_ready": sum(1 for d in decisions if d["state"] != "insufficient"),
            "trade_decisions": sum(1 for d in decisions if d["state"] == "trade"),
            "lean_decisions": sum(1 for d in decisions if d["state"] == "lean"),
            "capacity_skips": sum(1 for d in decisions if d.get("capacity_skipped")),
            "coverage_pct": round(
                100.0 * sum(1 for d in decisions if d.get("actual_net_ticks") is not None) / len(ordered),
                1,
            ) if ordered else 0.0,
        },
        "current_asof": _iso(asof),
        "current_map": current_map,
        "current_windows": _merge_window_rows(current_map),
        "weekly_replay": weekly[-int(adaptive_cfg.get("max_weekly_rows", 12)):],
        "decision_sample": [_serialize_decision(d) for d in decisions[-max_decisions:]],
        "event_sample": [_serialize_event(r) for r in ordered[-100:]],
    }


def _score_modes(
    rows: Sequence[Dict[str, Any]],
    asof: pd.Timestamp,
    bucket: int,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    training_days = max(1, int(cfg.get("training_days", 10)))
    neighbor_bins = max(0, int(cfg.get("neighbor_bins", 0)))
    earliest = asof - pd.Timedelta(days=training_days)
    completed = [
        r for r in rows
        if earliest <= pd.Timestamp(r["entry_dt"]) < asof
        and all(
            r[m].get("available")
            and pd.Timestamp(r[m]["exit_known_dt"]) < asof
            for m in ("continuation", "reversion")
        )
    ]
    local = [
        r for r in completed
        if abs(_bucket_index(pd.Timestamp(r["entry_dt"]), cfg) - bucket) <= neighbor_bins
    ]

    estimates = {}
    for mode in ("continuation", "reversion"):
        estimates[mode] = _estimate_mode(local, completed, mode, asof, cfg)

    local_n = len(local)
    min_n = max(1, int(cfg.get("min_local_signals", 8)))
    if local_n < min_n:
        return {
            "state": "insufficient",
            "selected_mode": None,
            "local_n": local_n,
            "advantage_ticks": None,
            **estimates,
        }

    best_mode = max(estimates, key=lambda m: float(estimates[m].get("posterior_mean_ticks") or -1e18))
    other_mode = "reversion" if best_mode == "continuation" else "continuation"
    best = estimates[best_mode]
    other = estimates[other_mode]
    advantage = float(best.get("posterior_mean_ticks") or 0.0) - float(other.get("posterior_mean_ticks") or 0.0)
    min_exp = float(cfg.get("min_expected_net_ticks", 0.0))
    min_lcb = float(cfg.get("min_lower_bound_ticks", 0.0))
    min_margin = float(cfg.get("mode_margin_ticks", 5.0))

    if float(best.get("posterior_mean_ticks") or 0.0) <= min_exp or advantage < min_margin:
        state = "abstain"
        selected = None
    elif float(best.get("lower_bound_ticks") or -1e18) >= min_lcb:
        state = "trade"
        selected = best_mode
    else:
        state = "lean"
        selected = best_mode

    return {
        "state": state,
        "selected_mode": selected,
        "local_n": local_n,
        "advantage_ticks": round(advantage, 3),
        **estimates,
    }


def _estimate_mode(
    local: Sequence[Dict[str, Any]],
    global_rows: Sequence[Dict[str, Any]],
    mode: str,
    asof: pd.Timestamp,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    half_life = max(0.1, float(cfg.get("half_life_days", 5.0)))
    prior_strength = max(0.0, float(cfg.get("prior_strength", 5.0)))
    z = max(0.0, float(cfg.get("confidence_z", 0.5)))

    def _weighted(source: Sequence[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
        vals: List[float] = []
        weights: List[float] = []
        for row in source:
            pnl = row[mode].get("net_pnl_ticks")
            if pnl is None:
                continue
            age_days = max(0.0, (asof - pd.Timestamp(row["entry_dt"])).total_seconds() / 86400.0)
            vals.append(float(pnl))
            weights.append(exp(-log(2.0) * age_days / half_life))
        return np.asarray(vals, dtype=float), np.asarray(weights, dtype=float)

    local_vals, local_w = _weighted(local)
    global_vals, global_w = _weighted(global_rows)
    global_mean = _weighted_mean(global_vals, global_w)
    local_mean = _weighted_mean(local_vals, local_w)
    local_weight = float(local_w.sum()) if len(local_w) else 0.0
    posterior = (
        (local_weight * local_mean + prior_strength * global_mean)
        / (local_weight + prior_strength)
        if local_weight + prior_strength > 0
        else 0.0
    )
    n_eff = (
        float(local_w.sum() ** 2 / np.square(local_w).sum())
        if len(local_w) and float(np.square(local_w).sum()) > 0
        else 0.0
    )
    if len(local_vals) >= 2 and local_weight > 0:
        variance = float(np.average(np.square(local_vals - local_mean), weights=local_w))
        se = sqrt(max(variance, 0.0) / max(n_eff, 1.0))
    else:
        se = 0.0
    win_rate = (
        100.0 * float(local_w[local_vals > 0].sum()) / local_weight
        if len(local_vals) and local_weight > 0
        else 0.0
    )
    return {
        "n": int(len(local_vals)),
        "effective_n": round(n_eff, 2),
        "weighted_mean_ticks": round(local_mean, 3),
        "global_recent_mean_ticks": round(global_mean, 3),
        "posterior_mean_ticks": round(posterior, 3),
        "lower_bound_ticks": round(posterior - z * se, 3),
        "weighted_win_rate_pct": round(win_rate, 1),
    }


def _weekly_replay(
    rows: Sequence[Dict[str, Any]],
    adaptive_cfg: Dict[str, Any],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not rows:
        return []
    tick_value = float(config.get("tick_value", 5.0))
    week_starts = sorted({
        (pd.Timestamp(r["entry_dt"]).normalize() - pd.Timedelta(days=pd.Timestamp(r["entry_dt"]).weekday()))
        for r in rows
    })
    buckets = sorted({_bucket_index(pd.Timestamp(r["entry_dt"]), adaptive_cfg) for r in rows})
    out = []

    for week_start in week_starts:
        week_end = week_start + pd.Timedelta(days=7)
        week_rows = [r for r in rows if week_start <= pd.Timestamp(r["entry_dt"]) < week_end]
        if not week_rows:
            continue
        map_rows = []
        score_by_bucket = {}
        for bucket in buckets:
            score = _score_modes(rows, week_start, bucket, adaptive_cfg)
            score_by_bucket[bucket] = score
            map_rows.append({
                "bucket_index": bucket,
                "time_bucket": _bucket_label(bucket, adaptive_cfg),
                **score,
            })

        trades = []
        active_by_direction: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
        max_concurrent = max(
            1,
            int((config.get("outcome") or {}).get("max_concurrent_per_direction", 3)),
        )
        for row in sorted(week_rows, key=lambda r: pd.Timestamp(r["entry_dt"])):
            bucket = _bucket_index(pd.Timestamp(row["entry_dt"]), adaptive_cfg)
            score = score_by_bucket.get(bucket) or {}
            if score.get("state") != "trade" or not score.get("selected_mode"):
                continue
            selected = row[str(score["selected_mode"])]
            entry_dt = pd.Timestamp(row["entry_dt"])
            direction = int(selected["trade_direction"])
            active = [x for x in active_by_direction[direction] if x > entry_dt]
            if len(active) >= max_concurrent:
                active_by_direction[direction] = active
                continue
            active.append(pd.Timestamp(selected["exit_known_dt"]))
            active_by_direction[direction] = active
            trades.append(selected)

        out.append({
            "week": week_start.date().isoformat(),
            "signals": len(week_rows),
            "windows": _merge_window_rows(map_rows),
            **_performance(trades, tick_value),
        })
    return out


def _trigger_breakdown(rows: Sequence[Dict[str, Any]], tick_value: float) -> List[Dict[str, Any]]:
    out = []
    # Known types first for stable ordering, then any others that appear, so a
    # newly added trigger type is never silently dropped from the breakdown.
    preferred = ("fresh", "zone_break", "fresh+zone_break", "zone_touch", "zone_retrace")
    seen = {str(r.get("trigger_type")) for r in rows}
    ordered = [t for t in preferred if t in seen] + sorted(seen - set(preferred))
    for trigger in ordered:
        subset = [r for r in rows if r.get("trigger_type") == trigger]
        if not subset:
            continue
        out.append({
            "trigger_type": trigger,
            "n_events": len(subset),
            "continuation": _performance([r["continuation"] for r in subset], tick_value),
            "reversion": _performance([r["reversion"] for r in subset], tick_value),
        })
    return out


def _performance(rows: Sequence[Dict[str, Any]], tick_value: float) -> Dict[str, Any]:
    pnls = [
        float(r.get("net_pnl_ticks"))
        for r in rows
        if r.get("net_pnl_ticks") is not None and isfinite(float(r.get("net_pnl_ticks")))
    ]
    if not pnls:
        return {
            "n_trades": 0,
            "win_rate_pct": None,
            "profit_factor": None,
            "avg_trade_ticks": None,
            "total_net_ticks": 0.0,
            "total_net_dollars": 0.0,
            "max_drawdown_ticks": 0.0,
        }
    wins = [v for v in pnls if v > 0]
    losses = [v for v in pnls if v < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "n_trades": len(pnls),
        "win_rate_pct": round(100.0 * len(wins) / len(pnls), 1),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "avg_trade_ticks": round(sum(pnls) / len(pnls), 3),
        "total_net_ticks": round(sum(pnls), 3),
        "total_net_dollars": round(sum(pnls) * tick_value, 2),
        "max_drawdown_ticks": round(max_dd, 3),
    }


def _merge_window_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [
        r for r in sorted(rows, key=lambda x: int(x.get("bucket_index", 0)))
        if r.get("state") in {"trade", "lean"} and r.get("selected_mode")
    ]
    if not active:
        return []
    merged: List[Dict[str, Any]] = []
    cur = dict(active[0])
    cur["start_bucket"] = int(active[0]["bucket_index"])
    cur["end_bucket"] = int(active[0]["bucket_index"])
    for row in active[1:]:
        adjacent = int(row["bucket_index"]) == int(cur["end_bucket"]) + 1
        same = row.get("state") == cur.get("state") and row.get("selected_mode") == cur.get("selected_mode")
        if adjacent and same:
            cur["end_bucket"] = int(row["bucket_index"])
            cur["local_n"] = int(cur.get("local_n") or 0) + int(row.get("local_n") or 0)
        else:
            merged.append(_window_payload(cur, rows))
            cur = dict(row)
            cur["start_bucket"] = int(row["bucket_index"])
            cur["end_bucket"] = int(row["bucket_index"])
    merged.append(_window_payload(cur, rows))
    return merged


def _window_payload(row: Dict[str, Any], all_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    cfg_bin = 30
    if len(all_rows) >= 2:
        diffs = sorted(
            abs(int(b.get("bucket_index", 0)) - int(a.get("bucket_index", 0)))
            for a, b in zip(all_rows, all_rows[1:])
            if int(b.get("bucket_index", 0)) != int(a.get("bucket_index", 0))
        )
        if diffs:
            cfg_bin = diffs[0]
    start = int(row["start_bucket"])
    end = int(row["end_bucket"]) + cfg_bin
    return {
        "window": f"{_minute_label(start)}-{_minute_label(end)}",
        "state": row.get("state"),
        "mode": row.get("selected_mode"),
        "local_n": row.get("local_n"),
    }


def _mark_capacity_eligibility(
    rows: Sequence[Dict[str, Any]],
    mode: str,
    max_concurrent: int,
) -> None:
    active_by_direction: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
    for row in sorted(rows, key=lambda r: pd.Timestamp(r["entry_dt"])):
        outcome = row[mode]
        if not outcome.get("available"):
            continue
        entry_dt = pd.Timestamp(outcome["entry_dt"])
        direction = int(outcome["trade_direction"])
        active = [x for x in active_by_direction[direction] if x > entry_dt]
        if len(active) >= max_concurrent:
            outcome["capacity_eligible"] = False
        else:
            outcome["capacity_eligible"] = True
            active.append(pd.Timestamp(outcome["exit_known_dt"]))
        active_by_direction[direction] = active


def _serialize_event(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        k: v for k, v in row.items()
        if k not in {"continuation", "reversion"}
    }
    for mode in ("continuation", "reversion"):
        m = row.get(mode) or {}
        out[mode] = {
            k: v for k, v in m.items()
            if k not in {"entry_price", "exit_price"}
        }
    return _json_safe(out)


def _serialize_decision(row: Dict[str, Any]) -> Dict[str, Any]:
    return _json_safe(row)


def _breakeven_win_rate(config: Dict[str, Any]) -> float:
    outcome = config.get("outcome") or {}
    target = max(0.0, float(outcome.get("target_ticks", 75.0)))
    stop = max(0.0, float(outcome.get("stop_ticks", 150.0)))
    cost = max(0.0, float(outcome.get("round_trip_cost_ticks", 0.0)))
    net_win = target - cost
    net_loss = stop + cost
    denom = net_win + net_loss
    return round(100.0 * net_loss / denom, 2) if denom > 0 else 100.0


def _inside_time_filter(dt: pd.Timestamp, cfg: Dict[str, Any]) -> bool:
    if not cfg.get("enabled", True):
        return True
    start = _parse_clock(cfg.get("start", "00:00"))
    end = _parse_clock(cfg.get("end", "23:59"))
    minute = dt.hour * 60 + dt.minute
    start_m = start.hour * 60 + start.minute
    end_m = end.hour * 60 + end.minute
    if start_m <= end_m:
        return start_m <= minute <= end_m
    return minute >= start_m or minute <= end_m


def _session_end_for(dt: pd.Timestamp, cfg: Dict[str, Any]) -> Optional[pd.Timestamp]:
    if not cfg.get("enabled", True):
        return None
    end = _parse_clock(cfg.get("end", "23:59"))
    result = dt.normalize() + pd.Timedelta(hours=end.hour, minutes=end.minute)
    start = _parse_clock(cfg.get("start", "00:00"))
    if end <= start and dt.time() >= start:
        result += pd.Timedelta(days=1)
    return result


def _session_exit_fill_for(
    dt: pd.Timestamp,
    cfg: Dict[str, Any],
    timeframe_minutes: int,
) -> Optional[pd.Timestamp]:
    """Return the next-bar market-fill time for CandleCenterBotV2's time exit.

    The configured end bar is allowed to signal. On the following strategy bar,
    ``HandleTimeFilter`` submits ``ExitLong``/``ExitShort``; under historical
    ``OnBarClose`` processing that market order fills at the open of the next
    bar. The resulting fill is therefore two strategy bars after the inclusive
    signal-window endpoint.
    """
    session_end = _session_end_for(dt, cfg)
    if session_end is None:
        return None
    return session_end + pd.Timedelta(minutes=2 * max(1, timeframe_minutes))


def _bucket_index(dt: pd.Timestamp, cfg: Dict[str, Any]) -> int:
    width = max(1, int(cfg.get("time_bin_minutes", 30)))
    minute = dt.hour * 60 + dt.minute
    return (minute // width) * width


def _bucket_label(bucket: int, cfg: Dict[str, Any]) -> str:
    width = max(1, int(cfg.get("time_bin_minutes", 30)))
    return f"{_minute_label(bucket)}-{_minute_label(bucket + width)}"


def _minute_label(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_clock(value: Any) -> time:
    text = str(value or "00:00").strip()
    parts = text.split(":")
    return time(hour=max(0, min(23, int(parts[0]))), minute=max(0, min(59, int(parts[1] if len(parts) > 1 else 0))))


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    total = float(weights.sum())
    return float(np.average(values, weights=weights)) if total > 0 else float(values.mean())


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = ("dt", "open", "high", "low", "close")
    missing = [c for c in required if c not in bars.columns]
    if missing:
        raise ValueError(f"adaptive_window missing bar columns: {missing}")
    out = bars.copy()
    out["dt"] = pd.to_datetime(out["dt"])
    return out.sort_values("dt").drop_duplicates("dt", keep="last").reset_index(drop=True)


def _dt_ns(series: pd.Series) -> np.ndarray:
    s = pd.to_datetime(series)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.astype("datetime64[ns]").astype("int64").to_numpy()


def _ts_ns(value: Any) -> int:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return int(ts.value)


def _round_away(value: float) -> int:
    return int(floor(float(value) + 0.5))


def _round_price(value: float, tick_size: float) -> float:
    return _round_away(float(value) / tick_size) * tick_size


def _lane_id(tf: int, lookback: int, basis: str, multiplier: float) -> str:
    return f"tf{tf}m|lb{lookback}|{basis}|x{multiplier:g}"


def _positive_ints(values: Iterable[Any]) -> List[int]:
    return sorted({int(v) for v in values if int(v) > 0})


def _positive_floats(values: Iterable[Any]) -> List[float]:
    return sorted({float(v) for v in values if float(v) > 0})


def _bases(values: Iterable[Any]) -> List[str]:
    out = []
    for value in values:
        basis = str(value).lower()
        if basis in {"body", "range"} and basis not in out:
            out.append(basis)
    return out or ["range"]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return pd.Timestamp(value).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if isfinite(f) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value
