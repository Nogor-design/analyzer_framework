"""
Outbox event file parser.

NT8 shell writes files to OutboxDirectory in this format:
    Filename: {yyyyMMdd_HHmmssfff}_{STATUS}_{instruction_id}.evt.json
    Content:  {"timestamp_utc":"...","status":"...","instruction_id":"...",
               "shell_mode":"...","position":"...","quantity":N,"detail":"..."}

This module provides structured reading and polling helpers.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class OutboxEvent:
    file_path: Path
    timestamp_utc: str = ""
    status: str = ""
    instruction_id: str = ""
    shell_mode: str = ""
    position: str = ""
    quantity: int = 0
    detail: str = ""

    def __str__(self) -> str:
        return (
            f"OutboxEvent(status={self.status!r}, instruction_id={self.instruction_id!r}, "
            f"shell_mode={self.shell_mode!r}, detail={self.detail!r})"
        )


def read_event(path: Path) -> Optional[OutboxEvent]:
    """Parse a single .evt.json file. Returns None if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return OutboxEvent(
            file_path=path,
            timestamp_utc=data.get("timestamp_utc", ""),
            status=data.get("status", ""),
            instruction_id=data.get("instruction_id", ""),
            shell_mode=data.get("shell_mode", ""),
            position=data.get("position", ""),
            quantity=int(data.get("quantity", 0)),
            detail=data.get("detail", ""),
        )
    except Exception:
        return None


def read_all_events(outbox_dir: Path) -> list[OutboxEvent]:
    """Read and return all valid events in outbox, sorted by filename (chronological)."""
    if not outbox_dir.exists():
        return []
    events: list[OutboxEvent] = []
    for f in sorted(outbox_dir.glob("*.evt.json")):
        evt = read_event(f)
        if evt:
            events.append(evt)
    return events


def events_since(outbox_dir: Path, baseline_count: int) -> list[OutboxEvent]:
    """Return events added after `baseline_count` files existed in outbox."""
    all_events = read_all_events(outbox_dir)
    return all_events[baseline_count:]


def count_events(outbox_dir: Path) -> int:
    """Return current count of .evt.json files in outbox."""
    if not outbox_dir.exists():
        return 0
    return sum(1 for _ in outbox_dir.glob("*.evt.json"))


def find_event(
    outbox_dir: Path,
    status: str,
    msg_id: Optional[str] = None,
    since_count: int = 0,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[OutboxEvent]:
    """
    Poll outbox until an event with matching `status` appears.
    If `msg_id` is given, also require the event's instruction_id or detail to contain it.
    Only considers events added after `since_count` existing events.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for evt in events_since(outbox_dir, since_count):
            if evt.status != status:
                continue
            if msg_id is not None:
                # instruction_id is set to lastInstructionId at time of write,
                # which equals msg_id for the message just processed.
                if msg_id not in evt.instruction_id and msg_id not in evt.detail:
                    continue
            return evt
        time.sleep(poll_interval)
    return None


def events_by_status(outbox_dir: Path, status: str, since_count: int = 0) -> list[OutboxEvent]:
    """Return all events with the given status after `since_count`."""
    return [e for e in events_since(outbox_dir, since_count) if e.status == status]
