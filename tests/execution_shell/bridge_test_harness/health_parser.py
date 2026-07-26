"""
HEALTH log event parser and position state helpers.

The shell writes HEALTH snapshots via LogReconciliationSnapshot().
Log line format (msg field):
    reason={r} shell_mode={m} intake={b} heartbeat_faulted={b} daily_lockout={b}
    active_position_id={id} pos_side={side} pos_qty={N} avg={price}
    entry_order={id} stop_order={id} target_order={id} exit_order={id}
    pending_stop={price} pending_target_ticks={N} queue_depth={N} last_msg_utc={utc}

Additionally, every log line header carries:
    pos={MarketPosition}  qty={Quantity}
which reflects the live NT8 position at write time and is available on ALL records.

Phase 2 uses these fields to observe actual position state without direct broker access.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bridge_test_harness import log_parser
from bridge_test_harness.log_parser import LogRecord


# ---------------------------------------------------------------------------
# Key signal names used by the shell
# ---------------------------------------------------------------------------

SIGNAL_ENTRY_LONG = "TF_ENTER_LONG"
SIGNAL_ENTRY_SHORT = "TF_ENTER_SHORT"
SIGNAL_EXIT = "TF_EXIT_ALL"
SIGNAL_PARTIAL = "TF_PARTIAL"
SIGNAL_STOP_LONG = "TF_STOP_LONG"
SIGNAL_STOP_SHORT = "TF_STOP_SHORT"

# Position sides as reported by NT8 MarketPosition.ToString()
SIDE_LONG = "Long"
SIDE_SHORT = "Short"
SIDE_FLAT = "Flat"


# ---------------------------------------------------------------------------
# HEALTH snapshot dataclass
# ---------------------------------------------------------------------------

@dataclass
class HealthSnapshot:
    """Parsed representation of a HEALTH log event message field."""
    raw_record: LogRecord

    # From log header (available on every record)
    pos_side: str = ""    # Long / Short / Flat
    pos_qty: int = 0

    # From HEALTH message field
    reason: str = ""
    shell_mode: str = ""
    intake: bool = True
    heartbeat_faulted: bool = False
    daily_lockout: bool = False
    active_position_id: str = ""
    pos_side_detail: str = ""   # pos_side from message (same as header pos)
    avg_price: float = 0.0
    entry_order: str = ""       # "<null>" when no order
    stop_order: str = ""        # "<null>" when no order
    target_order: str = ""
    exit_order: str = ""
    pending_stop: float = 0.0
    pending_target_ticks: int = 0
    queue_depth: int = 0

    @property
    def stop_order_live(self) -> bool:
        """True if stop_order is not null/empty."""
        return bool(self.stop_order) and self.stop_order != "<null>"

    @property
    def entry_order_live(self) -> bool:
        return bool(self.entry_order) and self.entry_order != "<null>"

    def __str__(self) -> str:
        return (
            f"HealthSnapshot(mode={self.shell_mode!r}, side={self.pos_side!r}, qty={self.pos_qty}, "
            f"stop_order={self.stop_order!r}, pending_stop={self.pending_stop}, "
            f"intake={self.intake})"
        )


def _parse_kv_message(message: str) -> dict[str, str]:
    """Parse a space-separated key=value string from HEALTH message field."""
    result: dict[str, str] = {}
    for token in message.split():
        if "=" in token:
            k, _, v = token.partition("=")
            result[k.strip()] = v.strip()
    return result


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _safe_bool(s: str) -> bool:
    return s.strip().lower() in ("true", "1", "yes")


def parse_health_record(rec: LogRecord) -> HealthSnapshot:
    """Parse a HEALTH LogRecord into a HealthSnapshot."""
    snap = HealthSnapshot(
        raw_record=rec,
        pos_side=rec.pos,
        pos_qty=_safe_int(rec.qty),
    )
    kv = _parse_kv_message(rec.message)
    snap.reason = kv.get("reason", "")
    snap.shell_mode = kv.get("shell_mode", rec.mode)
    snap.intake = _safe_bool(kv.get("intake", "true"))
    snap.heartbeat_faulted = _safe_bool(kv.get("heartbeat_faulted", "false"))
    snap.daily_lockout = _safe_bool(kv.get("daily_lockout", "false"))
    snap.active_position_id = kv.get("active_position_id", "")
    snap.pos_side_detail = kv.get("pos_side", rec.pos)
    snap.avg_price = _safe_float(kv.get("avg", "0"))
    snap.entry_order = kv.get("entry_order", "<null>")
    snap.stop_order = kv.get("stop_order", "<null>")
    snap.target_order = kv.get("target_order", "<null>")
    snap.exit_order = kv.get("exit_order", "<null>")
    snap.pending_stop = _safe_float(kv.get("pending_stop", "0"))
    snap.pending_target_ticks = _safe_int(kv.get("pending_target_ticks", "0"))
    snap.queue_depth = _safe_int(kv.get("queue_depth", "0"))
    return snap


def get_latest_health(
    log_path: Path,
    since_line: int = 0,
) -> Optional[HealthSnapshot]:
    """Return the most recent HEALTH snapshot from the log, or None if not present."""
    records = log_parser.read_records(log_path, since_line)
    health_records = [r for r in records if r.event_type == "HEALTH"]
    if not health_records:
        return None
    return parse_health_record(health_records[-1])


# ---------------------------------------------------------------------------
# Outbox detail parser (for Phase 2 event details)
# ---------------------------------------------------------------------------

def parse_detail(detail: str) -> dict[str, str]:
    """Parse the semicolon-separated key=value detail string in an outbox event."""
    result: dict[str, str] = {}
    for pair in detail.split(";"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# Position polling helpers (use log record header fields for live position)
# ---------------------------------------------------------------------------

def wait_for_position_side(
    log_path: Path,
    side: str,
    since_line: int = 0,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.5,
) -> Optional[LogRecord]:
    """
    Poll log until a record with `pos={side}` appears after `since_line`.
    `side` is "Long", "Short", or "Flat" as written by NT8 MarketPosition.ToString().
    Returns the matching LogRecord or None on timeout.
    """
    # The log format puts pos in |pos={side}| between pipes.
    fragment = f"|pos={side}|"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for rec in log_parser.read_records(log_path, since_line):
            if f"|pos={side}|" in rec.raw:
                return rec
        time.sleep(poll_interval)
    return None


def wait_for_flat(
    log_path: Path,
    since_line: int = 0,
    timeout_seconds: float = 30.0,
) -> bool:
    """Wait for position to become Flat (as reflected in log records)."""
    return wait_for_position_side(log_path, SIDE_FLAT, since_line, timeout_seconds) is not None


def wait_for_long(
    log_path: Path,
    since_line: int = 0,
    timeout_seconds: float = 30.0,
) -> bool:
    """Wait for position to become Long."""
    return wait_for_position_side(log_path, SIDE_LONG, since_line, timeout_seconds) is not None


def wait_for_short(
    log_path: Path,
    since_line: int = 0,
    timeout_seconds: float = 30.0,
) -> bool:
    """Wait for position to become Short."""
    return wait_for_position_side(log_path, SIDE_SHORT, since_line, timeout_seconds) is not None


def get_current_position_from_log(
    log_path: Path,
    since_line: int = 0,
) -> tuple[str, int]:
    """
    Return (side, qty) from the most recent log record after since_line.
    Returns ("Unknown", 0) if no records found.
    """
    records = log_parser.read_records(log_path, since_line)
    if not records:
        return "Unknown", 0
    last = records[-1]
    return last.pos, _safe_int(last.qty)


def wait_for_qty(
    log_path: Path,
    expected_qty: int,
    since_line: int = 0,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.5,
) -> Optional[LogRecord]:
    """
    Poll until a log record where `qty` field == expected_qty appears.
    Useful for verifying partial exit (qty reduced) or full exit (qty=0).
    """
    fragment = f"|qty={expected_qty}|"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for rec in log_parser.read_records(log_path, since_line):
            if fragment in rec.raw:
                return rec
        time.sleep(poll_interval)
    return None


def count_events_of_type(
    log_path: Path,
    event_type: str,
    since_line: int = 0,
) -> int:
    """Count log records of a given event_type after since_line."""
    return sum(
        1 for r in log_parser.read_records(log_path, since_line)
        if r.event_type == event_type
    )
