"""
NT8 shell log parser.

Log line format written by AppendLog():
    {timestamp:o}|{eventType}|instr={lastInstructionId}|template={activeTemplate}|mode={shellMode}|pos={posSide}|qty={posQty}|msg={message}

This module provides:
- Structured LogRecord parsing
- Polling search helpers with timeout
- Baseline-aware search (search only lines after a captured offset)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOG_RE = re.compile(
    r"^(?P<timestamp>[^\|]+)\|"
    r"(?P<event_type>[^\|]+)\|"
    r"instr=(?P<instr_id>[^\|]*)\|"
    r"template=(?P<template>[^\|]*)\|"
    r"mode=(?P<mode>[^\|]*)\|"
    r"pos=(?P<pos>[^\|]*)\|"
    r"qty=(?P<qty>[^\|]*)\|"
    r"msg=(?P<message>.*)$"
)


@dataclass
class LogRecord:
    line_number: int          # 0-based index in the file
    raw: str
    timestamp: str = ""
    event_type: str = ""
    instr_id: str = ""
    template: str = ""
    mode: str = ""
    pos: str = ""
    qty: str = ""
    message: str = ""


def parse_line(line: str, line_number: int = 0) -> Optional[LogRecord]:
    line = line.rstrip("\n\r")
    if not line:
        return None
    m = _LOG_RE.match(line)
    if not m:
        return None
    return LogRecord(
        line_number=line_number,
        raw=line,
        timestamp=m.group("timestamp").strip(),
        event_type=m.group("event_type").strip(),
        instr_id=m.group("instr_id").strip(),
        template=m.group("template").strip(),
        mode=m.group("mode").strip(),
        pos=m.group("pos").strip(),
        qty=m.group("qty").strip(),
        message=m.group("message").strip(),
    )


def read_records(log_path: Path, since_line: int = 0) -> list[LogRecord]:
    """
    Parse all structured log records from `log_path`.
    If `since_line > 0`, only return records at or after that line number.
    """
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    records: list[LogRecord] = []
    for i, line in enumerate(lines):
        if i < since_line:
            continue
        rec = parse_line(line, i)
        if rec:
            records.append(rec)
    return records


def line_count(log_path: Path) -> int:
    """Return current line count of the log file (0 if absent)."""
    if not log_path.exists():
        return 0
    try:
        return len(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Polling search helpers
# ---------------------------------------------------------------------------

def find_fragment(
    log_path: Path,
    fragment: str,
    since_line: int = 0,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[LogRecord]:
    """
    Poll log until a record whose `raw` text contains `fragment` appears.
    Only considers lines at or after `since_line`.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for rec in read_records(log_path, since_line):
            if fragment in rec.raw:
                return rec
        time.sleep(poll_interval)
    return None


def find_event_for_msg(
    log_path: Path,
    event_type: str,
    msg_id: str,
    since_line: int = 0,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[LogRecord]:
    """
    Poll log until a record matching both `event_type` and containing `msg_id`
    in the raw line appears. This uniquely identifies an event for a specific message.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for rec in read_records(log_path, since_line):
            if rec.event_type == event_type and msg_id in rec.raw:
                return rec
        time.sleep(poll_interval)
    return None


def find_event_type(
    log_path: Path,
    event_type: str,
    since_line: int = 0,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[LogRecord]:
    """Poll until any record of the given event_type appears after since_line."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for rec in read_records(log_path, since_line):
            if rec.event_type == event_type:
                return rec
        time.sleep(poll_interval)
    return None


def absent_after_wait(
    log_path: Path,
    fragment: str,
    since_line: int = 0,
    wait_seconds: float = 3.0,
) -> bool:
    """
    Wait `wait_seconds` then confirm `fragment` has NOT appeared in log since `since_line`.
    Returns True if absent (good), False if found (unexpected).
    """
    time.sleep(wait_seconds)
    for rec in read_records(log_path, since_line):
        if fragment in rec.raw:
            return False
    return True


def tail_lines(log_path: Path, n: int = 20) -> list[str]:
    """Return the last `n` raw lines of the log file for diagnostic output."""
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except OSError:
        return []
