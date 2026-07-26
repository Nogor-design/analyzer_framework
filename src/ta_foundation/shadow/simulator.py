"""Shadow signal generation + open-position resolution.

The shadow simulator is deliberately a thin shell over the existing
discovery code path: ``detect_orb`` for signal generation, ``emit_entries``
for pending-entry placement, ``_fill_limit_order`` for limit fills, and
``_resolve_outcome`` for forward stop/target/timeout resolution. Sharing
the code is a non-negotiable in the Phase D design — if shadow uses a
different code path from backtest the comparison is meaningless.

For Phase D.1 only the ``orb_breakout`` and ``orb_failure_reclaim``
families have an adapter. Other families raise ``ShadowNotSupported``.
The dispatch is family-keyed on ``Hypothesis.family`` and reconstructs the
detector config from ``Candidate.params_json`` + ``Candidate.notes_json``
+ family-appropriate defaults from the locked discovery YAMLs.

Signal identity / idempotency
-----------------------------
A signal is uniquely keyed on (candidate_id, signal_bar_ts_utc, direction).
The runner relies on the unique index added in migration 0004 to make
re-runs over the same data a no-op. Crucially this means the planned
entry / stop / target must be a *deterministic function of the signal
bar* — not of any forward-scan fill. For limit-order modes we therefore
record ``limit_price`` as the planned entry; whether the limit actually
filled lives in ``realized_outcome_json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.orb.signals import detect_orb
from ta_foundation.analysis.entry_strategies.outcome.simulator import (
    _fill_limit_order,
    _net_profit,
    _resolve_outcome,
)
from ta_foundation.research_ledger import Candidate, Hypothesis, ShadowSignal


SUPPORTED_FAMILIES = frozenset({"orb_breakout", "orb_failure_reclaim"})


class ShadowNotSupported(RuntimeError):
    """Raised when no shadow adapter exists for a candidate's family."""


@dataclass(frozen=True)
class PlannedSignal:
    """One signal the runner should persist (and later try to resolve)."""
    signal_bar_ts_utc: str
    direction: str  # "long" | "short"
    planned_entry: float
    planned_stop: float
    planned_target: float
    timing_mode: str
    context: dict[str, Any]


def is_supported(family: str) -> bool:
    return family in SUPPORTED_FAMILIES


def generate_signals(
    *,
    candidate: Candidate,
    hypothesis: Hypothesis,
    bars_1m: pd.DataFrame,
) -> tuple[list[PlannedSignal], dict[str, Any]]:
    """Run the family detector + entry emitter against ``bars_1m``.

    Returns (signals, runtime_config). The runtime_config carries the
    fill-timeout and outcome cfg the runner needs to resolve open
    positions on subsequent passes.
    """
    if not is_supported(hypothesis.family):
        raise ShadowNotSupported(
            f"No shadow adapter for family {hypothesis.family!r} "
            f"(candidate_id={candidate.candidate_id})"
        )
    cfg = _orb_signal_config(candidate, hypothesis)

    if bars_1m is None or bars_1m.empty:
        return [], cfg

    signals_df = detect_orb(bars_1m, cfg["detector_params"])
    if signals_df is None or signals_df.empty:
        return [], cfg

    pending = emit_entries(
        signals_df,
        cfg["timing_mode"],
        bars=bars_1m,
        params=cfg["timing_params"],
    )
    if pending is None or pending.empty:
        return [], cfg

    tick_size = float(cfg["outcome"]["tick_size"])
    tp_ticks = float(cfg["outcome"]["take_profit"])
    sl_ticks = float(cfg["outcome"]["stop"])

    out: list[PlannedSignal] = []
    for _, row in pending.iterrows():
        direction = int(row["direction"])
        planned_entry = _row_planned_entry(row)
        if planned_entry is None:
            continue
        stop_price, target_price = _stop_target_from_ticks(
            entry_price=planned_entry,
            direction=direction,
            tp_ticks=tp_ticks,
            sl_ticks=sl_ticks,
            tick_size=tick_size,
        )
        signal_bar_ts = pd.Timestamp(row.get("signal_dt") or row.get("dt"))
        out.append(
            PlannedSignal(
                signal_bar_ts_utc=_to_utc_iso(signal_bar_ts),
                direction=("long" if direction == 1 else "short"),
                planned_entry=float(planned_entry),
                planned_stop=stop_price,
                planned_target=target_price,
                timing_mode=cfg["timing_mode"],
                context={
                    "signal_dt_denver": _to_denver_iso(signal_bar_ts),
                    "orb_high": _float_or_none(row.get("orb_high")),
                    "orb_low": _float_or_none(row.get("orb_low")),
                    "orb_range_ticks": _float_or_none(row.get("orb_range_ticks")),
                    "limit_price": _float_or_none(row.get("limit_price")),
                    "fill_timeout_bars": _int_or_none(row.get("fill_timeout_bars")),
                    "tp_ticks": tp_ticks,
                    "sl_ticks": sl_ticks,
                    "timing_mode": cfg["timing_mode"],
                },
            )
        )
    return out, cfg


def resolve_open_signal(
    *,
    signal: ShadowSignal,
    bars_1m: pd.DataFrame,
    outcome_cfg: dict[str, Any],
    fill_timeout_bars: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Try to advance an open shadow signal.

    State machine encoded in ``realized_outcome_json.status``:
        - pending: signal fired, fill scan not yet conclusive
        - no_fill: limit window expired without a fill
        - open: filled, stop/target unresolved
        - resolved: stop, target, or timeout reached

    Returns the new realized-outcome payload to persist, or ``None`` if
    nothing has changed (not enough new bars to advance the state).
    """
    if bars_1m is None or bars_1m.empty:
        return None

    payload: dict[str, Any] = {}
    if signal.realized_outcome_json:
        try:
            payload = json.loads(signal.realized_outcome_json)
        except json.JSONDecodeError:
            payload = {}

    status = str(payload.get("status") or "pending")
    if status in {"resolved", "no_fill"}:
        return None

    bars_sorted = bars_1m.sort_values("dt").reset_index(drop=True)
    bar_dts = pd.to_datetime(bars_sorted["dt"])
    signal_ts = pd.Timestamp(signal.ts)
    if bar_dts.dt.tz is not None and signal_ts.tzinfo is None:
        signal_ts = signal_ts.tz_localize("UTC")
    elif bar_dts.dt.tz is None and signal_ts.tzinfo is not None:
        signal_ts = signal_ts.tz_localize(None)

    direction = 1 if signal.direction == "long" else -1
    tick_size = float(outcome_cfg.get("tick_size", 0.25))
    tick_value = float(outcome_cfg.get("tick_value", 5.0))
    commission = float(outcome_cfg.get("commission_per_side", 0.0))
    slippage_ticks = float(outcome_cfg.get("slippage_ticks", 0))
    max_bars = int(outcome_cfg.get("max_bars_timeout", 90))
    timeout_result = str(outcome_cfg.get("timeout_result", "at_close"))

    # ---------- Fill phase (limit-order timing modes) ----------------------
    if status == "pending" and "fill_ts_utc" not in payload:
        signal_idx = _locate_signal_bar(bar_dts, signal_ts)
        if signal_idx is None:
            return None

        if signal.planned_entry is None:
            return None

        fill_timeout = int(
            payload.get("fill_timeout_bars")
            if payload.get("fill_timeout_bars") is not None
            else (fill_timeout_bars if fill_timeout_bars is not None else 5)
        )

        fill_idx, fill_dt = _fill_limit_order(
            limit_price=float(signal.planned_entry),
            direction=direction,
            bars_1m=bars_sorted,
            signal_idx=signal_idx,
            fill_timeout_bars=fill_timeout,
        )
        if fill_idx is None:
            bars_after = bars_sorted.iloc[signal_idx + 1:]
            if len(bars_after) >= fill_timeout:
                return {
                    "status": "no_fill",
                    "fill_timeout_bars": fill_timeout,
                    "fill_window_end_ts_utc": _to_utc_iso(
                        pd.Timestamp(bars_sorted.iloc[min(signal_idx + fill_timeout, len(bars_sorted) - 1)]["dt"])
                    ),
                }
            return None  # not enough bars to declare no-fill yet

        payload = {
            "status": "open",
            "fill_ts_utc": _to_utc_iso(pd.Timestamp(fill_dt)),
            "fill_bar_idx_from_signal": int(fill_idx - signal_idx),
            "fill_price": float(signal.planned_entry),
            "fill_timeout_bars": fill_timeout,
        }
        status = "open"

    # ---------- Resolution phase --------------------------------------------
    if status == "open":
        fill_ts = pd.Timestamp(payload["fill_ts_utc"])
        if bar_dts.dt.tz is None and fill_ts.tzinfo is not None:
            fill_ts = fill_ts.tz_localize(None)
        fill_idx = _locate_signal_bar(bar_dts, fill_ts)
        if fill_idx is None:
            return None

        bars_after = bars_sorted.iloc[fill_idx:].reset_index(drop=True)
        result, profit_ticks, exit_price, exit_dt = _resolve_outcome(
            entry_price=float(payload["fill_price"]),
            direction=direction,
            stop_price=float(signal.planned_stop),
            target_price=float(signal.planned_target),
            bars_1m=bars_after,
            start_idx=0,
            max_bars=max_bars,
            timeout_result=timeout_result,
            tick_size=tick_size,
            tick_value=tick_value,
            commission_per_side=commission,
            slippage_ticks=slippage_ticks,
        )

        available_bars = len(bars_after)
        if result == "timeout" and available_bars < max_bars:
            return payload if "fill_ts_utc" in payload and "status" not in (signal.realized_outcome_json or "") else None

        net = _net_profit(
            profit_ticks=profit_ticks,
            tick_value=tick_value,
            commission_per_side=commission,
            slippage_ticks=slippage_ticks,
            tick_size=tick_size,
        )
        payload.update({
            "status": "resolved",
            "result": result,
            "exit_price": float(exit_price),
            "exit_ts_utc": _to_utc_iso(pd.Timestamp(exit_dt)),
            "profit_ticks": float(profit_ticks),
            "profit_net": float(net),
        })
        return payload

    return payload or None


# ----- ORB family adapter --------------------------------------------------


def _orb_signal_config(candidate: Candidate, hypothesis: Hypothesis) -> dict[str, Any]:
    params = json.loads(candidate.params_json) if candidate.params_json else {}
    notes = json.loads(candidate.notes_json) if candidate.notes_json else {}

    discovery_params: dict[str, Any] = notes.get("discovery_params") or {}

    def _pick(key: str, default: Any) -> Any:
        if key in discovery_params:
            return discovery_params[key]
        if key in params:
            return params[key]
        return default

    session_filter = notes.get("session_filter") or {}
    open_h = int(session_filter.get("hour_from") or session_filter.get("session_open_hour") or 7)
    open_m = int(session_filter.get("minute_from") or session_filter.get("session_open_minute") or 30)
    close_h = int(
        session_filter.get("hour_to") or session_filter.get("session_close_hour") or 16
    )

    detector_params: dict[str, Any] = {
        "signal_type": (
            "failure_reclaim" if hypothesis.family == "orb_failure_reclaim" else "breakout"
        ),
        "orb_minutes": int(_pick("orb_minutes", 5)),
        "session_open_hour": open_h,
        "session_open_minute": open_m,
        "session_close_hour": close_h,
        "direction": int(_pick("direction", 0)),
        "min_range_ticks": float(_pick("min_range_ticks", 8)),
        "tick_size": float(_pick("tick_size", 0.25)),
        "atr_period": int(_pick("atr_period", 14)),
        "require_close_beyond": bool(_pick("require_close_beyond", True)),
        "one_signal_per_side": bool(_pick("one_signal_per_side", True)),
        "min_sweep_ticks": float(_pick("min_sweep_ticks", 4.0)),
        "close_back_ticks": float(_pick("close_back_ticks", 0.0)),
        "max_reclaim_bars": int(_pick("max_reclaim_bars", 1)),
    }

    timing_mode, timing_params = _extract_entry_timing(
        notes.get("entry_timing"), tick_size=detector_params["tick_size"]
    )

    outcome_notes = notes.get("outcome") or {}
    tp_ticks = float(
        params.get("target_ticks")
        or _pick("target_ticks", _first_or(outcome_notes.get("take_profit"), 150))
    )
    sl_ticks = float(
        params.get("stop_ticks")
        or _pick("stop_ticks", _first_or(outcome_notes.get("stop"), 20))
    )

    outcome: dict[str, Any] = {
        "take_profit": tp_ticks,
        "stop": sl_ticks,
        "max_bars_timeout": int(outcome_notes.get("max_bars_timeout", 90)),
        "timeout_result": str(outcome_notes.get("timeout_result", "at_close")),
        "tick_size": detector_params["tick_size"],
        "tick_value": float(outcome_notes.get("tick_value", 5.0)),
        "commission_per_side": float(outcome_notes.get("commission_per_side", 2.09)),
        "slippage_ticks": float(outcome_notes.get("slippage_ticks", 1)),
    }

    return {
        "detector_params": detector_params,
        "timing_mode": timing_mode,
        "timing_params": timing_params,
        "outcome": outcome,
    }


def _extract_entry_timing(raw: Any, *, tick_size: float) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, str):
        return raw, {"tick_size": tick_size}
    if isinstance(raw, dict):
        for mode, body in raw.items():
            if isinstance(body, dict) and body.get("enabled"):
                return mode, {**body, "tick_size": tick_size}
        for mode, body in raw.items():
            if isinstance(body, dict):
                return mode, {**body, "tick_size": tick_size}
    return "body_midpoint", {"fill_timeout_bars": 5, "tick_size": tick_size}


def _row_planned_entry(row: pd.Series) -> Optional[float]:
    # next_open carries entry_price; limit modes carry limit_price.
    candidates = (row.get("entry_price"), row.get("limit_price"))
    for c in candidates:
        if c is None:
            continue
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if v == v:  # not NaN
            return v
    return None


def _stop_target_from_ticks(
    *,
    entry_price: float,
    direction: int,
    tp_ticks: float,
    sl_ticks: float,
    tick_size: float,
) -> tuple[float, float]:
    if direction == 1:
        stop = entry_price - sl_ticks * tick_size
        target = entry_price + tp_ticks * tick_size
    else:
        stop = entry_price + sl_ticks * tick_size
        target = entry_price - tp_ticks * tick_size
    return float(stop), float(target)


def _locate_signal_bar(bar_dts: pd.Series, signal_ts: pd.Timestamp) -> Optional[int]:
    mask = bar_dts == signal_ts
    if mask.any():
        return int(mask.idxmax())
    fallback = bar_dts >= signal_ts
    if not fallback.any():
        return None
    return int(fallback.idxmax())


def _first_or(value: Any, default: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    if value is None:
        return default
    return value


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _int_or_none(v: Any) -> Optional[int]:
    f = _float_or_none(v)
    return int(f) if f is not None else None


def _to_utc_iso(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("America/Denver")
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_denver_iso(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("America/Denver")
    return t.tz_convert("America/Denver").isoformat()
