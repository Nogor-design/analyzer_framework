"""
Shell state and processed-IDs file parsers.

State file (shell_state.json) is written by SavePersistentState() using
JavaScriptSerializer which preserves C# property name casing:
    ActiveTemplate, LastInstructionId, SignalIntakeEnabled, HeartbeatFaulted,
    DailyLockout, CurrentTradingDay, LastBridgeMessageUtc, ShellMode,
    PositionId, PendingStopPrice, PendingTargetTicks, ProcessedIds,
    LastHealthSnapshotUtc

ProcessedIds file is a plain text file — one message_id per line.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ShellState:
    active_template: str = ""
    last_instruction_id: str = ""
    signal_intake_enabled: bool = True
    heartbeat_faulted: bool = False
    daily_lockout: bool = False
    current_trading_day: str = ""
    last_bridge_message_utc: str = ""
    shell_mode: str = ""           # "Idle", "InPosition", "EntryPending", "Disabled", ...
    position_id: str = ""
    pending_stop_price: float = 0.0
    pending_target_ticks: int = 0
    processed_ids: list[str] = field(default_factory=list)
    last_health_snapshot_utc: str = ""

    def __str__(self) -> str:
        return (
            f"ShellState(mode={self.shell_mode!r}, intake={self.signal_intake_enabled}, "
            f"hb_faulted={self.heartbeat_faulted}, template={self.active_template!r}, "
            f"pending_stop={self.pending_stop_price})"
        )


def read_state(state_file: Path) -> Optional[ShellState]:
    """Parse state JSON. Returns None if file absent or unreadable."""
    if not state_file.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(state_file.read_text(encoding="utf-8"))
        return ShellState(
            active_template=raw.get("ActiveTemplate") or "",
            last_instruction_id=raw.get("LastInstructionId") or "",
            signal_intake_enabled=bool(raw.get("SignalIntakeEnabled", True)),
            heartbeat_faulted=bool(raw.get("HeartbeatFaulted", False)),
            daily_lockout=bool(raw.get("DailyLockout", False)),
            current_trading_day=raw.get("CurrentTradingDay") or "",
            last_bridge_message_utc=raw.get("LastBridgeMessageUtc") or "",
            shell_mode=raw.get("ShellMode") or "",
            position_id=raw.get("PositionId") or "",
            pending_stop_price=float(raw.get("PendingStopPrice") or 0.0),
            pending_target_ticks=int(raw.get("PendingTargetTicks") or 0),
            processed_ids=list(raw.get("ProcessedIds") or []),
            last_health_snapshot_utc=raw.get("LastHealthSnapshotUtc") or "",
        )
    except Exception:
        return None


def poll_state_field(
    state_file: Path,
    field_name: str,
    expected: Any,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> tuple[bool, Any]:
    """
    Poll state file until `field_name` equals `expected`.
    Returns (True, actual_value) on match, (False, last_actual) on timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    last_actual: Any = None
    while time.monotonic() < deadline:
        state = read_state(state_file)
        if state is not None:
            last_actual = getattr(state, field_name, None)
            if last_actual == expected:
                return True, last_actual
        time.sleep(poll_interval)
    return False, last_actual


def read_processed_ids(ids_file: Path) -> list[str]:
    """Parse processed IDs file — one ID per line."""
    if not ids_file.exists():
        return []
    try:
        return [
            line.strip()
            for line in ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception:
        return []
