"""
Bridge file I/O helpers.

Handles:
- Atomic message injection into inbox (write .tmp then rename to .json)
- Polling bridge directories for expected files
- Waiting for inbox drain
- Directory management for test setup
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------

def inject_message(inbox: Path, payload: dict, msg_id: Optional[str] = None) -> Path:
    """
    Atomically write a JSON payload into the inbox using the .tmp -> .json rename
    protocol that the NT8 shell expects.

    Args:
        inbox:   The inbox directory.
        payload: The message dict to serialise.
        msg_id:  File stem to use.  Defaults to payload["message_id"].
                 Override when message_id is absent (e.g. missing-id tests).
    Returns:
        The final .json path that was written.
    """
    if msg_id is None:
        msg_id = str(payload.get("message_id", "unknown"))

    inbox.mkdir(parents=True, exist_ok=True)
    tmp_path = inbox / f"{msg_id}.tmp"
    final_path = inbox / f"{msg_id}.json"

    tmp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path


# ---------------------------------------------------------------------------
# Directory polling
# ---------------------------------------------------------------------------

def wait_for_file_in_dir(
    directory: Path,
    fragment: str,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> Optional[Path]:
    """
    Poll `directory` until any file whose name contains `fragment` exists.
    Returns the matching Path or None on timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if directory.exists():
            for f in directory.iterdir():
                if f.is_file() and fragment in f.name:
                    return f
        time.sleep(poll_interval)
    return None


def wait_for_inbox_empty(
    inbox: Path,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> bool:
    """Wait until no *.json files remain in inbox. Returns True if empty before timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not inbox.exists():
            return True
        if not list(inbox.glob("*.json")):
            return True
        time.sleep(poll_interval)
    return False


def wait_for_outbox_count(
    outbox: Path,
    min_count: int,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> list[Path]:
    """Wait until outbox holds at least `min_count` .evt.json files."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if outbox.exists():
            files = list(outbox.glob("*.evt.json"))
            if len(files) >= min_count:
                return sorted(files)
        time.sleep(poll_interval)
    return list(outbox.glob("*.evt.json")) if outbox.exists() else []


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def list_dir_files(directory: Path) -> list[Path]:
    """Return sorted list of files (not subdirs) in directory."""
    if not directory.exists():
        return []
    return sorted(f for f in directory.iterdir() if f.is_file())


def clear_directory(directory: Path) -> None:
    """Delete all files (not subdirs) in directory. Creates it if absent."""
    directory.mkdir(parents=True, exist_ok=True)
    for f in directory.iterdir():
        if f.is_file():
            f.unlink(missing_ok=True)


def prepare_bridge_dirs(root: Path) -> dict[str, Path]:
    """
    Ensure all expected bridge subdirectories exist under `root`.
    Returns a dict mapping role -> Path.
    """
    roles = ["inbox", "archive", "rejected", "outbox", "templates", "logs", "state"]
    dirs: dict[str, Path] = {}
    for role in roles:
        d = root / role
        d.mkdir(parents=True, exist_ok=True)
        dirs[role] = d
    return dirs
