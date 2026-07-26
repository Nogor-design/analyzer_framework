from __future__ import annotations

"""Causal, decision-time context for adaptive large-candle research.

Every feature in this module is available at the close of the context row.
The helpers deliberately do not score or select trades; they provide the
small, interpretable context surface consumed by the adaptive context gate.
"""

from datetime import time
from math import isfinite
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


DEFAULT_ADAPTIVE_CONTEXT_CONFIG: Dict[str, Any] = {
    "timezone": "America/Denver",
    "session_anchor": "07:30",
    "time_bucket_minutes": 30,
    "vwap_price": "typical",
    "vwap_slope_bars": 15,
    "return_lookback_minutes": 60,
    "vote_epsilon": 0.0,
}


CONTEXT_EVENT_FIELDS = (
    "session_id",
    "time_bucket",
    "session_vwap",
    "close_vs_vwap",
    "vwap_slope_15",
    "return_60m",
    "close_vs_vwap_vote",
    "vwap_slope_15_vote",
    "return_60m_vote",
    "trend_votes",
    "trend_state",
    "context_history_complete",
)


def build_intraday_context(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Build bar-close context without using any later bar.

    Session VWAP resets at ``session_anchor`` in the configured timezone.
    VWAP slope is based on completed bars in the same anchored session, while
    the return requires a bar exactly ``return_lookback_minutes`` earlier in
    that session. Missing history therefore remains missing instead of being
    silently bridged across a session boundary or data gap.
    """
    cfg = _resolve_context_config(config)
    columns = ["dt", *CONTEXT_EVENT_FIELDS]
    if bars_1m is None or bars_1m.empty:
        return pd.DataFrame(columns=columns)

    bars = _prepare_context_bars(bars_1m, cfg)
    anchor = _parse_clock(cfg["session_anchor"])
    anchor_offset = pd.Timedelta(hours=anchor.hour, minutes=anchor.minute)

    # Remove the timezone before subtracting the wall-clock anchor. A timed
    # subtraction on the aware timestamps would be an elapsed-time operation
    # and move the reset by an hour on a DST transition day.
    local_wall_time = bars["dt"].dt.tz_localize(None)
    bars["session_id"] = (
        (local_wall_time - anchor_offset).dt.strftime("%Y-%m-%d")
    )

    price_mode = str(cfg.get("vwap_price", "typical")).strip().lower()
    if price_mode == "close":
        vwap_price = bars["close"]
    elif price_mode in {"typical", "hlc3"}:
        vwap_price = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    else:
        raise ValueError("vwap_price must be 'typical'/'hlc3' or 'close'")

    grouped = bars.groupby("session_id", sort=False)
    cumulative_volume = grouped["volume"].cumsum()
    cumulative_price_volume = (vwap_price * bars["volume"]).groupby(
        bars["session_id"], sort=False
    ).cumsum()
    bars["session_vwap"] = cumulative_price_volume.div(
        cumulative_volume.where(cumulative_volume > 0)
    )
    bars["close_vs_vwap"] = bars["close"] - bars["session_vwap"]

    slope_bars = max(1, int(cfg.get("vwap_slope_bars", 15)))
    prior_vwap = bars.groupby("session_id", sort=False)["session_vwap"].shift(
        slope_bars
    )
    bars["vwap_slope_15"] = bars["session_vwap"] - prior_vwap

    return_minutes = max(1, int(cfg.get("return_lookback_minutes", 60)))
    prior_close = bars.groupby("session_id", sort=False)["close"].shift(
        return_minutes
    )
    prior_dt = bars.groupby("session_id", sort=False)["dt"].shift(return_minutes)
    exact_elapsed = (bars["dt"] - prior_dt) == pd.Timedelta(
        minutes=return_minutes
    )
    valid_denominator = prior_close.notna() & prior_close.ne(0) & exact_elapsed
    bars["return_60m"] = np.where(
        valid_denominator,
        bars["close"].div(prior_close) - 1.0,
        np.nan,
    )

    bucket_width = max(1, int(cfg.get("time_bucket_minutes", 30)))
    bucket_start = (
        (bars["dt"].dt.hour * 60 + bars["dt"].dt.minute) // bucket_width
    ) * bucket_width
    bars["time_bucket"] = bucket_start.map(
        lambda minute: _bucket_label(int(minute), bucket_width)
    )

    epsilon = max(0.0, float(cfg.get("vote_epsilon", 0.0)))
    bars["close_vs_vwap_vote"] = bars["close_vs_vwap"].map(
        lambda value: _sign_vote(value, epsilon)
    )
    bars["vwap_slope_15_vote"] = bars["vwap_slope_15"].map(
        lambda value: _sign_vote(value, epsilon)
    )
    bars["return_60m_vote"] = bars["return_60m"].map(
        lambda value: _sign_vote(value, epsilon)
    )
    bars["trend_votes"] = list(
        zip(
            bars["close_vs_vwap_vote"],
            bars["vwap_slope_15_vote"],
            bars["return_60m_vote"],
        )
    )
    bars["context_history_complete"] = bars[
        ["close_vs_vwap", "vwap_slope_15", "return_60m"]
    ].notna().all(axis=1)
    bars["trend_state"] = bars.apply(
        lambda row: classify_trend_state(row, cfg),
        axis=1,
    )

    return bars[columns].reset_index(drop=True)


def attach_context_to_events(
    events: Sequence[Mapping[str, Any]],
    context_frame: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Attach the last context row observable at each event's signal close.

    The input order is preserved. ``context_dt`` records the matched bar and is
    guaranteed to be less than or equal to the configured event time field
    (``signal_dt`` by default).
    """
    if not events:
        return []

    cfg = _resolve_context_config(config)
    event_time_field = str(cfg.get("event_time_field", "signal_dt"))
    if context_frame is None or context_frame.empty:
        return [_with_missing_context(event) for event in events]
    if "dt" not in context_frame.columns:
        raise ValueError("context_frame missing required column: dt")

    context = context_frame.copy()
    context["dt"] = _coerce_aware_datetimes(
        context["dt"], str(cfg["timezone"]), label="context_frame.dt"
    )
    context = (
        context.sort_values("dt")
        .drop_duplicates("dt", keep="last")
        .reset_index(drop=True)
    )
    context_ns = _utc_nanoseconds(context["dt"])

    attached: List[Dict[str, Any]] = []
    for event in events:
        if event_time_field not in event:
            raise ValueError(
                f"event missing configured time field: {event_time_field}"
            )
        event_dt = _coerce_aware_timestamp(
            event[event_time_field],
            str(cfg["timezone"]),
            label=f"event.{event_time_field}",
        )
        match_index = int(
            np.searchsorted(
                context_ns,
                _utc_nanoseconds_value(event_dt),
                side="right",
            )
            - 1
        )
        if match_index < 0:
            attached.append(_with_missing_context(event))
            continue

        row = context.iloc[match_index]
        record = dict(event)
        record["context_dt"] = pd.Timestamp(row["dt"])
        for field in CONTEXT_EVENT_FIELDS:
            record[field] = _python_scalar(row[field]) if field in row else None
        attached.append(record)
    return attached


def classify_trend_state(
    row: Mapping[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify ``up``, ``down``, or ``mixed`` from the three fixed votes."""
    cfg = _resolve_context_config(config)
    raw_fields = ("close_vs_vwap", "vwap_slope_15", "return_60m")
    raw_values = [_finite_float(row.get(field)) for field in raw_fields]
    if any(value is None for value in raw_values):
        return "mixed"

    epsilon = max(0.0, float(cfg.get("vote_epsilon", 0.0)))
    votes = [_sign_vote(value, epsilon) for value in raw_values]
    positive = sum(vote > 0 for vote in votes)
    negative = sum(vote < 0 for vote in votes)
    if positive >= 2 and negative < 2:
        return "up"
    if negative >= 2 and positive < 2:
        return "down"
    return "mixed"


def structurally_aligned_mode(
    trend_state: str,
    signal_side: str,
) -> Optional[str]:
    """Return the mode whose trade direction agrees with the trend state."""
    trend = str(trend_state).strip().lower()
    side = str(signal_side).strip().lower()
    side = {"bullish": "bull", "bearish": "bear"}.get(side, side)
    if trend not in {"up", "down", "mixed"}:
        raise ValueError("trend_state must be 'up', 'down', or 'mixed'")
    if side not in {"bull", "bear"}:
        raise ValueError("signal_side must be 'bull' or 'bear'")
    if trend == "mixed":
        return None
    if (trend == "up" and side == "bull") or (
        trend == "down" and side == "bear"
    ):
        return "continuation"
    return "reversion"


def _resolve_context_config(
    config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    supplied = config or {}
    context = supplied.get("context")
    override = context if isinstance(context, dict) else supplied
    cfg = {**DEFAULT_ADAPTIVE_CONTEXT_CONFIG, **override}

    # Full adaptive-window configs already use ``adaptive.time_bin_minutes``.
    # Honor it unless the context block declares its own bucket width.
    adaptive = supplied.get("adaptive")
    if (
        isinstance(adaptive, dict)
        and "time_bucket_minutes" not in override
        and "time_bin_minutes" in adaptive
    ):
        cfg["time_bucket_minutes"] = adaptive["time_bin_minutes"]
    return cfg


def _prepare_context_bars(
    bars: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    required = ("dt", "high", "low", "close", "volume")
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"adaptive_context missing bar columns: {missing}")

    out = bars.copy()
    out["dt"] = _coerce_aware_datetimes(
        out["dt"], str(config["timezone"]), label="bars.dt"
    )
    for column in ("high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    if not np.isfinite(out[["high", "low", "close", "volume"]].to_numpy()).all():
        raise ValueError("adaptive_context bar values must be finite")
    if (out["volume"] < 0).any():
        raise ValueError("adaptive_context volume must be non-negative")
    return (
        out.sort_values("dt")
        .drop_duplicates("dt", keep="last")
        .reset_index(drop=True)
    )


def _coerce_aware_datetimes(
    values: pd.Series,
    timezone: str,
    *,
    label: str,
) -> pd.Series:
    timestamps = [
        _coerce_aware_timestamp(value, timezone, label=label) for value in values
    ]
    return pd.Series(pd.DatetimeIndex(timestamps), index=values.index)


def _coerce_aware_timestamp(value: Any, timezone: str, *, label: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(
            f"{label} must be timezone-aware; expected {timezone}"
        )
    return timestamp.tz_convert(timezone)


def _parse_clock(value: Any) -> time:
    text = str(value or "00:00").strip()
    parts = text.split(":")
    if len(parts) not in {1, 2}:
        raise ValueError(f"invalid clock value: {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) == 2 else 0
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid clock value: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid clock value: {value!r}")
    return time(hour=hour, minute=minute)


def _bucket_label(start_minute: int, width: int) -> str:
    return (
        f"{_minute_label(start_minute)}-"
        f"{_minute_label(start_minute + width)}"
    )


def _minute_label(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _sign_vote(value: Any, epsilon: float) -> int:
    number = _finite_float(value)
    if number is None:
        return 0
    if number > epsilon:
        return 1
    if number < -epsilon:
        return -1
    return 0


def _with_missing_context(event: Mapping[str, Any]) -> Dict[str, Any]:
    record = dict(event)
    record["context_dt"] = None
    for field in CONTEXT_EVENT_FIELDS:
        record[field] = None
    record["trend_votes"] = (0, 0, 0)
    record["trend_state"] = "mixed"
    record["context_history_complete"] = False
    return record


def _utc_nanoseconds(values: pd.Series) -> np.ndarray:
    return (
        values.dt.tz_convert("UTC")
        .dt.tz_localize(None)
        .astype("datetime64[ns]")
        .astype("int64")
        .to_numpy()
    )


def _utc_nanoseconds_value(value: pd.Timestamp) -> int:
    return int(value.tz_convert("UTC").tz_localize(None).value)


def _python_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value
