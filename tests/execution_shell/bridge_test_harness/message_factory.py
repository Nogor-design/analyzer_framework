"""
Message factory for TaFoundationExecutionShell bridge tests.

Builds valid and intentionally-invalid JSON payloads matching the signal contract.
All factory functions return plain dict — callers pass them to bridge_io.inject_message().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

DENVER_TZ = ZoneInfo("America/Denver")

# Default values used across all builders — override via **kwargs
DEFAULT_INSTRUMENT = "NQ 06-26"
DEFAULT_TEMPLATE = "runner_reversal_template"
DEFAULT_THESIS_ID = "automated_test"
# Set signal_expiry_seconds generously so tests aren't flaked by processing latency.
DEFAULT_EXPIRY_SECONDS = 60


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(DENVER_TZ).isoformat()


def _offset(seconds: float) -> str:
    """Return ISO-8601 timestamp offset from now by `seconds` (negative = past)."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return dt.astimezone(DENVER_TZ).isoformat()


# ---------------------------------------------------------------------------
# Valid message builders
# ---------------------------------------------------------------------------

def base_enter_long(**overrides: Any) -> dict:
    """Valid ENTER_LONG with all required fields set."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "timeframe": "1m",
        "action": "ENTER_LONG",
        "side": "LONG",
        "template_name": DEFAULT_TEMPLATE,
        "confidence": 0.87,
        "entry_mode": "market",
        "quantity": 1,
        "stop_mode": "signal_extreme_capped",
        "stop_ticks": 20,
        "stop_price": None,
        "target_mode": "partial_then_runner",
        "target_ticks": 40,
        "partial_target_ticks": 18,
        "runner_mode": "trail_structure",
        "max_hold_bars": 20,
        "thesis_id": DEFAULT_THESIS_ID,
        "notes": "automated test message",
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


def base_enter_short(**overrides: Any) -> dict:
    """Valid ENTER_SHORT."""
    return base_enter_long(action="ENTER_SHORT", side="SHORT", **overrides)


def heartbeat(**overrides: Any) -> dict:
    """Valid HEARTBEAT message."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "action": "HEARTBEAT",
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


def exit_all(**overrides: Any) -> dict:
    """Valid EXIT_ALL message."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "action": "EXIT_ALL",
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


def move_stop(stop_price: Optional[float] = 19000.0, **overrides: Any) -> dict:
    """Valid MOVE_STOP with a positive stop price."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "action": "MOVE_STOP",
        "stop_price": stop_price,
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


def take_partial(quantity: int = 1, **overrides: Any) -> dict:
    """Valid TAKE_PARTIAL."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "action": "TAKE_PARTIAL",
        "quantity": quantity,
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


def flatten_and_disable(**overrides: Any) -> dict:
    """FLATTEN_AND_DISABLE — always executed regardless of intake state."""
    msg: dict = {
        "message_id": str(uuid.uuid4()),
        "timestamp": _now(),
        "instrument": DEFAULT_INSTRUMENT,
        "action": "FLATTEN_AND_DISABLE",
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }
    msg.update(overrides)
    return msg


# ---------------------------------------------------------------------------
# Invalid / adversarial message builders
# ---------------------------------------------------------------------------

def missing_message_id(**overrides: Any) -> dict:
    """Payload with message_id field entirely absent."""
    msg = base_enter_long(**overrides)
    msg.pop("message_id", None)
    return msg


def invalid_timestamp(**overrides: Any) -> dict:
    """Payload with a non-ISO timestamp that cannot be parsed."""
    return base_enter_long(timestamp="not-a-timestamp", **overrides)


def future_timestamp(seconds_ahead: int = 90, **overrides: Any) -> dict:
    """Timestamp > now + 30s (exceeds the shell's clock-skew guard)."""
    return base_enter_long(timestamp=_offset(seconds_ahead), **overrides)


def stale_timestamp(seconds_ago: int = 30, **overrides: Any) -> dict:
    """Timestamp in the past beyond StaleSignalSeconds (default 8s).
    Do NOT pass signal_expiry_seconds override — leave stale check active.
    """
    msg = base_enter_long(**overrides)
    msg["timestamp"] = _offset(-seconds_ago)
    # Remove any expiry override so the default stale check applies.
    msg.pop("signal_expiry_seconds", None)
    return msg


def missing_template_name(**overrides: Any) -> dict:
    """ENTER_SHORT without template_name — required for entry actions."""
    msg = base_enter_short(**overrides)
    msg["template_name"] = None
    return msg


def move_stop_missing_price(**overrides: Any) -> dict:
    """MOVE_STOP with null stop_price — invalid per contract."""
    return move_stop(stop_price=None, **overrides)


def entry_quantity_above_max(max_pos: int = 3, **overrides: Any) -> dict:
    """ENTER_LONG with quantity above MaxPositionSize."""
    return base_enter_long(quantity=max_pos + 5, **overrides)


def stop_ticks_above_cap(cap: int = 100, **overrides: Any) -> dict:
    """ENTER_LONG with stop_ticks above MaxStopTicksCap."""
    return base_enter_long(stop_ticks=cap + 50, **overrides)
