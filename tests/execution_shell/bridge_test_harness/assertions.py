"""
Assertion helpers for the NT8 bridge test harness.

Each assertion returns an AssertionResult with:
- passed: bool
- label: str  (short identifier)
- detail: str (explanation of failure)
- evidence: str (relevant diagnostic data)

All assertions that wait for events use timeout polling rather than fixed sleeps.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from bridge_test_harness import bridge_io, log_parser, outbox_parser, state_parser


@dataclass
class AssertionResult:
    passed: bool
    label: str
    detail: str = ""
    evidence: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        base = f"[{status}] {self.label}"
        if not self.passed:
            base += f"\n       Reason : {self.detail}"
            if self.evidence:
                base += f"\n       Evidence: {self.evidence}"
        return base


# ---------------------------------------------------------------------------
# File-location assertions
# ---------------------------------------------------------------------------

def assert_file_in_archive(
    archive_dir: Path,
    msg_id: str,
    timeout: float = 10.0,
) -> AssertionResult:
    """File with `msg_id` in its name exists in archive directory."""
    found = bridge_io.wait_for_file_in_dir(archive_dir, msg_id, timeout_seconds=timeout)
    if found:
        return AssertionResult(True, "file_in_archive", evidence=found.name)
    names = [f.name for f in bridge_io.list_dir_files(archive_dir)]
    return AssertionResult(
        False,
        "file_in_archive",
        detail=f"No archive file containing '{msg_id}' found after {timeout:.0f}s",
        evidence=f"Archive contents ({len(names)} files): {names[:10]}",
    )


def assert_file_in_rejected(
    rejected_dir: Path,
    fragment: str,
    timeout: float = 10.0,
) -> AssertionResult:
    """File with `fragment` in its name exists in rejected directory."""
    found = bridge_io.wait_for_file_in_dir(rejected_dir, fragment, timeout_seconds=timeout)
    if found:
        return AssertionResult(True, "file_in_rejected", evidence=found.name)
    names = [f.name for f in bridge_io.list_dir_files(rejected_dir)]
    return AssertionResult(
        False,
        "file_in_rejected",
        detail=f"No rejected file containing '{fragment}' found after {timeout:.0f}s",
        evidence=f"Rejected contents ({len(names)} files): {names[:10]}",
    )


def assert_not_in_archive(
    archive_dir: Path,
    msg_id: str,
    wait_seconds: float = 3.0,
) -> AssertionResult:
    """Confirms file does NOT appear in archive (after a short wait)."""
    time.sleep(wait_seconds)
    found = bridge_io.wait_for_file_in_dir(archive_dir, msg_id, timeout_seconds=0.1)
    if found:
        return AssertionResult(
            False,
            "not_in_archive",
            detail=f"Unexpected file '{found.name}' found in archive",
        )
    return AssertionResult(True, "not_in_archive")


# ---------------------------------------------------------------------------
# Log assertions
# ---------------------------------------------------------------------------

def assert_log_event_for_msg(
    log_path: Path,
    event_type: str,
    msg_id: str,
    since_line: int = 0,
    timeout: float = 10.0,
) -> AssertionResult:
    """
    A log record with event_type=`event_type` AND containing `msg_id` in its raw
    text exists after `since_line`.  Uniquely matches events for a specific message.
    """
    label = f"log:{event_type}@{msg_id[:8]}"
    rec = log_parser.find_event_for_msg(
        log_path, event_type, msg_id, since_line=since_line, timeout_seconds=timeout
    )
    if rec:
        return AssertionResult(True, label, evidence=rec.raw[:200])
    tail = "\n".join(log_parser.tail_lines(log_path, 15))
    return AssertionResult(
        False,
        label,
        detail=f"No log event '{event_type}' for msg_id '{msg_id}' after {timeout:.0f}s",
        evidence=f"Last 15 log lines:\n{tail}",
    )


def assert_log_fragment(
    log_path: Path,
    fragment: str,
    since_line: int = 0,
    timeout: float = 10.0,
    label: str = "",
) -> AssertionResult:
    """Any log record containing `fragment` appears after `since_line`."""
    label = label or f"log_contains:{fragment[:40]}"
    rec = log_parser.find_fragment(
        log_path, fragment, since_line=since_line, timeout_seconds=timeout
    )
    if rec:
        return AssertionResult(True, label, evidence=rec.raw[:200])
    tail = "\n".join(log_parser.tail_lines(log_path, 15))
    return AssertionResult(
        False,
        label,
        detail=f"Fragment '{fragment}' not found in log after {timeout:.0f}s",
        evidence=f"Last 15 log lines:\n{tail}",
    )


def assert_log_fragment_absent(
    log_path: Path,
    fragment: str,
    since_line: int = 0,
    wait_seconds: float = 3.0,
) -> AssertionResult:
    """
    Waits `wait_seconds` then asserts `fragment` has NOT appeared since `since_line`.
    Use for negative assertions (e.g. HEARTBEAT_LOST should NOT appear).
    """
    absent = log_parser.absent_after_wait(
        log_path, fragment, since_line=since_line, wait_seconds=wait_seconds
    )
    label = f"log_absent:{fragment[:40]}"
    if absent:
        return AssertionResult(True, label)
    # Find the offending lines for the report.
    offending = [
        r.raw for r in log_parser.read_records(log_path, since_line) if fragment in r.raw
    ]
    return AssertionResult(
        False,
        label,
        detail=f"Unexpected fragment '{fragment}' found in log after {since_line}",
        evidence="\n".join(offending[:5]),
    )


# ---------------------------------------------------------------------------
# Outbox assertions
# ---------------------------------------------------------------------------

def assert_outbox_event(
    outbox_dir: Path,
    status: str,
    msg_id: Optional[str] = None,
    since_count: int = 0,
    timeout: float = 10.0,
) -> AssertionResult:
    """An outbox event with `status` (and optionally matching `msg_id`) exists."""
    label = f"outbox:{status}"
    if msg_id:
        label += f"@{msg_id[:8]}"
    evt = outbox_parser.find_event(
        outbox_dir, status, msg_id=msg_id, since_count=since_count, timeout_seconds=timeout
    )
    if evt:
        return AssertionResult(True, label, evidence=str(evt))
    evts = outbox_parser.events_since(outbox_dir, since_count)
    summary = [f"{e.status}:{e.instruction_id[:8]}" for e in evts[-10:]]
    return AssertionResult(
        False,
        label,
        detail=f"Outbox event status='{status}' not found after {timeout:.0f}s",
        evidence=f"Recent outbox events: {summary}",
    )


def assert_outbox_event_absent(
    outbox_dir: Path,
    status: str,
    since_count: int = 0,
    wait_seconds: float = 3.0,
) -> AssertionResult:
    """Assert an outbox event of `status` has NOT appeared after `since_count`."""
    time.sleep(wait_seconds)
    found = outbox_parser.events_by_status(outbox_dir, status, since_count)
    label = f"outbox_absent:{status}"
    if not found:
        return AssertionResult(True, label)
    return AssertionResult(
        False,
        label,
        detail=f"Unexpected outbox event status='{status}' appeared",
        evidence=str(found[0]),
    )


# ---------------------------------------------------------------------------
# State assertions
# ---------------------------------------------------------------------------

def assert_state_field(
    state_file: Path,
    field_name: str,
    expected: Any,
    timeout: float = 10.0,
) -> AssertionResult:
    """State file field `field_name` equals `expected` within timeout."""
    label = f"state:{field_name}={expected}"
    matched, actual = state_parser.poll_state_field(
        state_file, field_name, expected, timeout_seconds=timeout
    )
    if matched:
        return AssertionResult(True, label, evidence=f"actual={actual!r}")
    state = state_parser.read_state(state_file)
    state_summary = str(state) if state else "<state file absent>"
    return AssertionResult(
        False,
        label,
        detail=f"State field '{field_name}' expected={expected!r}, got={actual!r}",
        evidence=state_summary,
    )


def assert_processed_id_present(
    ids_file: Path,
    msg_id: str,
    timeout: float = 10.0,
    poll_interval: float = 0.25,
) -> AssertionResult:
    """The processed IDs file contains `msg_id` within timeout."""
    label = f"processed_id:{msg_id[:12]}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = state_parser.read_processed_ids(ids_file)
        if msg_id in ids:
            return AssertionResult(True, label, evidence=f"Found in {ids_file.name}")
        time.sleep(poll_interval)
    ids = state_parser.read_processed_ids(ids_file)
    return AssertionResult(
        False,
        label,
        detail=f"msg_id '{msg_id}' not found in processed IDs file after {timeout:.0f}s",
        evidence=f"File has {len(ids)} entries; last 5: {ids[-5:]}",
    )
