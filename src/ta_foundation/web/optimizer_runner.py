from __future__ import annotations

"""
Bridge between /optimizer web sessions and the NinjaTrader batch AddOn.

The AddOn at ``D:\\ninjatraderOptimizer`` watches ``C:\\temp\\nt8_command.json``
for a ``RunBatch`` action and runs every ``*.xml`` in ``sourceFolder``, writing
per-template result folders (``Summary.csv``, ``Trades.csv``,
``<template>_Optimization.csv`` etc.) under ``destFolder``.

This module:

- writes the IPC command file so the AddOn picks the run up
- persists a ``run.json`` per session capturing run state + payload
- derives live progress from the AddOn's ``nt8_status.json`` heartbeat when
  available, with a folder-watching fallback that counts completed
  ``Summary.csv`` exports under the session's ``nt_output/`` folder.

No subprocess is spawned — NinjaTrader is expected to be running with the
AddOn loaded. If the command file already exists from a prior run, it is
overwritten with the new payload.
"""

import csv
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ta_foundation.web.optimizer_preflight import run_preflight
from ta_foundation.web.optimizer_session import OptimizerSession


# ---------------------------------------------------------------------------
# Filesystem constants — overridable in tests
# ---------------------------------------------------------------------------

REAL_NT_COMMAND_FILE = Path(r"C:\temp\nt8_command.json")
DEFAULT_COMMAND_FILE = REAL_NT_COMMAND_FILE
DEFAULT_STATUS_FILE = Path(r"C:\temp\nt8_status.json")
GENERATED_DIRNAME = "generated_templates"
NT_OUTPUT_DIRNAME = "nt_output"
RUN_FILENAME = "run.json"
STALE_AFTER_SECONDS = 300  # 5 minutes with no new file => stale
HEARTBEAT_STALE_AFTER_SECONDS = 30  # heartbeat older than this => fall back


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OptimizerRunnerError(Exception):
    pass


class BridgeBusyError(OptimizerRunnerError):
    """Raised when another run still owns the shared NT command file.

    All dispatch paths (plain optimizer runs, recipe stages, promoted runs)
    write the single ``C:\\temp\\nt8_command.json`` bridge. Only one run may
    own it at a time; this is the cross-session/cross-process guard that the
    per-session run-record checks cannot provide.
    """

    def __init__(self, *, owner_run_id: str, state: str | None, command_file: str):
        self.owner_run_id = owner_run_id
        self.state = state
        self.command_file = command_file
        super().__init__(
            f"NinjaTrader command bridge is busy: run {owner_run_id!r} "
            f"({state or 'submitted, awaiting AddOn'}) still owns "
            f"{command_file}. Wait for it to finish or cancel it before "
            "starting another run."
        )


# A run is no longer holding the bridge once its status reaches any of these.
_BRIDGE_TERMINAL_STATES = frozenset({
    "complete", "completed", "success", "finished", "done",
    "failed", "error", "cancelled", "canceled", "timed_out", "stale",
})
# A command with no terminal status and no heartbeat this old is treated as
# abandoned (e.g. the web app died mid-run) and may be reclaimed. Generous so
# a legitimately long batch is never stolen out from under a live run.
BRIDGE_STALE_AFTER_SECONDS = 6 * 3600


def read_bridge_command(command_file: Path) -> dict[str, Any] | None:
    """Return the parsed command file, or ``None`` if absent/unreadable."""
    if not command_file.exists():
        return None
    try:
        payload = json.loads(command_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _bridge_owner(
    command_file: Path, status_file: Path, *, now: datetime
) -> tuple[str, str | None] | None:
    """Return ``(owner_run_id, state)`` if a live run owns the bridge, else None.

    A command file whose owning run has reached a terminal state — or that has
    no heartbeat at all and is older than ``BRIDGE_STALE_AFTER_SECONDS`` — does
    not count as owning the bridge (it's a leftover or abandoned command).
    """
    cmd = read_bridge_command(command_file)
    if not cmd:
        return None
    owner = str(cmd.get("runId") or "")

    state: str | None = None
    if status_file.exists():
        try:
            payload = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and str(payload.get("runId") or "") == owner:
            state = str(payload.get("state") or "").strip().lower() or None

    if state in _BRIDGE_TERMINAL_STATES:
        return None
    if state is None:
        try:
            age = now.timestamp() - command_file.stat().st_mtime
        except OSError:
            age = 0.0
        if age > BRIDGE_STALE_AFTER_SECONDS:
            return None
    return owner, state


def ensure_bridge_available(
    command_file: Path | str,
    status_file: Path | str,
    *,
    requesting_run_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raise :class:`BridgeBusyError` if a different live run owns the bridge.

    ``requesting_run_id`` lets a run re-acquire the bridge it already owns
    (idempotent re-dispatch) without tripping the guard.
    """
    moment = now or datetime.now(timezone.utc)
    owner = _bridge_owner(Path(command_file), Path(status_file), now=moment)
    if owner is None:
        return
    owner_run_id, state = owner
    if requesting_run_id and owner_run_id == requesting_run_id:
        return
    raise BridgeBusyError(
        owner_run_id=owner_run_id, state=state, command_file=str(command_file)
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    """Persisted view of a run. Lives at ``<session>/run.json``."""
    run_id: str
    state: str  # "requested" | "starting" | "running" | "finished" | "timed_out" | "stale" | "cancelled"
    started_at: str
    finished_at: str | None = None
    source_folder: str = ""
    dest_folder: str = ""
    command_file: str = ""
    status_file: str = ""
    total_templates: int = 0
    last_observed_completed: int = 0
    last_error: str | None = None
    cancelled_at: str | None = None
    timeout_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(data.get("run_id") or ""),
            state=str(data.get("state") or "requested"),
            started_at=str(data.get("started_at") or ""),
            finished_at=data.get("finished_at"),
            source_folder=str(data.get("source_folder") or ""),
            dest_folder=str(data.get("dest_folder") or ""),
            command_file=str(data.get("command_file") or ""),
            status_file=str(data.get("status_file") or ""),
            total_templates=int(data.get("total_templates") or 0),
            last_observed_completed=int(data.get("last_observed_completed") or 0),
            last_error=data.get("last_error"),
            cancelled_at=data.get("cancelled_at"),
            timeout_seconds=_safe_optional_int(data.get("timeout_seconds")),
        )


@dataclass
class RunStatus:
    """Live status snapshot the UI polls for. Derived per request."""
    run_id: str
    state: str
    total: int
    completed: int
    current_template: str | None
    finished_templates: list[str] = field(default_factory=list)
    last_error: str | None = None
    heartbeat_age_seconds: float | None = None
    started_at: str = ""
    finished_at: str | None = None
    source_folder: str = ""
    dest_folder: str = ""
    source: str = "folder"  # "heartbeat" | "folder"
    ui_findings: list[dict[str, str]] = field(default_factory=list)
    batch_run_statuses: list[dict[str, str]] = field(default_factory=list)
    timeout_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def start_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
    status_file: Path | None = None,
    now: datetime | None = None,
) -> RunRecord:
    """Write the IPC command file so the AddOn picks up the session's
    generated templates. Returns the persisted ``RunRecord``.

    Raises ``OptimizerRunnerError`` if the session has no generated XMLs.
    """
    source = (session.directory / GENERATED_DIRNAME).resolve()
    xmls = sorted(source.glob("*.xml"))
    if not xmls:
        raise OptimizerRunnerError(
            f"No generated templates in {source}; generate XMLs before running."
        )
    preflight = run_preflight(session)
    if not preflight.ok:
        details = "; ".join(preflight.blocking_errors)
        raise OptimizerRunnerError(f"Optimizer preflight failed: {details}")

    cmd_path = Path(command_file) if command_file else DEFAULT_COMMAND_FILE
    status_path = Path(status_file) if status_file else DEFAULT_STATUS_FILE
    # Cross-session/cross-process guard: refuse before touching nt_output if a
    # different live run still owns the shared NT command file. Raised as
    # OptimizerRunnerError so existing callers' error handling is unchanged.
    ensure_bridge_available(cmd_path, status_path)

    dest = (session.directory / NT_OUTPUT_DIRNAME).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    moment = now or datetime.now(timezone.utc)
    run_id = _make_run_id(moment)

    # Clear any stale heartbeat from a prior run before publishing the
    # command. If the AddOn reacts quickly, clearing after the command can
    # erase the first live heartbeat.
    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    payload = {
        "action": "RunBatch",
        "runId": run_id,
        "sourceFolder": str(source),
        "destFolder": str(dest),
        # Always close per-template tabs in the Strategy Analyzer after each
        # run. Optimizer batches generate one tab per template; leaving them
        # open accumulates memory and crashes NinjaTrader on long runs.
        "closeTempTabs": True,
    }
    timeout_seconds = _timeout_seconds_for(session)
    if timeout_seconds:
        payload["timeoutSeconds"] = timeout_seconds
    instrument = _command_instrument_for(session)
    if instrument:
        payload["instrument"] = instrument
    _write_command_file(cmd_path, payload)

    run = RunRecord(
        run_id=run_id,
        state="requested",
        started_at=moment.isoformat(timespec="microseconds"),
        source_folder=str(source),
        dest_folder=str(dest),
        command_file=str(cmd_path),
        status_file=str(status_path),
        total_templates=len(xmls),
        timeout_seconds=timeout_seconds,
    )
    _save_run(session, run)
    return run


def cancel_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
    now: datetime | None = None,
) -> RunRecord | None:
    """Best-effort cancel.

    Since 2026-05-17 the AddOn subscribes to ``Deleted`` events on
    ``C:\\temp\\nt8_command.json``: unlinking the file flips
    ``cancelRequested`` mid-batch, equivalent to clicking the local
    CANCEL button on the NinjaTrader batch panel. The currently-running
    template still completes (NT has no public Stop API on the Strategy
    Analyzer ``RunCommand``), but no further templates dispatch.

    This function:

    - deletes the command file so the AddOn sees the cancel signal
    - flips our local ``RunRecord`` to ``cancelled`` so the UI stops polling
      as if the run is alive.
    """
    run = load_run(session)
    if run is None:
        return None
    cmd_path = Path(command_file) if command_file else Path(run.command_file or DEFAULT_COMMAND_FILE)
    try:
        if cmd_path.exists():
            cmd_path.unlink()
    except OSError:
        pass
    moment = now or datetime.now(timezone.utc)
    run.state = "cancelled"
    run.cancelled_at = moment.isoformat(timespec="microseconds")
    run.finished_at = run.cancelled_at
    _save_run(session, run)
    return run


def get_status(
    session: OptimizerSession,
    *,
    now: datetime | None = None,
) -> RunStatus | None:
    """Compute a fresh status snapshot from disk. Returns ``None`` if no
    run has been requested for this session yet.

    Preference order:

    1. ``nt8_status.json`` heartbeat from the AddOn (slice 2). The runId in
       the heartbeat must match our run's ``run_id``; mismatches are ignored
       so we don't show a different run's progress.
    2. Folder-watching fallback — count ``Summary.csv`` files under
       ``nt_output/``.

    State machine:

    - ``cancelled`` is sticky once set locally.
    - if everything has a Summary.csv (or AddOn says ``finished``), ``finished``.
    - if heartbeat fresh, use its state.
    - if heartbeat older than HEARTBEAT_STALE_AFTER_SECONDS but nt_output is
      still receiving writes (< STALE_AFTER_SECONDS old), ``running``.
    - if nothing has happened for STALE_AFTER_SECONDS, ``stale``.
    - else ``requested``.
    """
    run = load_run(session)
    if run is None:
        return None
    if run.state == "cancelled":
        return _status_from_run(session, run, state="cancelled", now=now)

    moment = now or datetime.now(timezone.utc)
    dest = Path(run.dest_folder or (session.directory / NT_OUTPUT_DIRNAME))
    finished, current = _scan_dest_folder(dest)
    folder_age = _latest_write_age(dest, now=moment)
    completed_folder = len(finished)
    total = run.total_templates or completed_folder

    heartbeat = _load_heartbeat(run, now=moment)

    # ------------------------------------------------------------------
    # Reconcile heartbeat with folder evidence
    # ------------------------------------------------------------------
    if heartbeat is not None:
        hb_state = str(heartbeat.get("state") or "running")
        hb_completed = int(heartbeat.get("completed") or 0)
        hb_total = int(heartbeat.get("total") or total)
        hb_current = heartbeat.get("currentTemplate") or current
        hb_last_error = heartbeat.get("lastError")
        hb_age = float(heartbeat.get("_age_seconds") or 0.0)

        # Trust the AddOn's terminal states. For in-flight states, trust the
        # higher of heartbeat-completed vs. folder-completed (the AddOn
        # increments after export, which can lag behind Summary.csv writes
        # in pathological cases — pick the larger).
        completed = max(hb_completed, completed_folder)
        total = max(hb_total, total, 1)

        if hb_state == "finished" or completed >= total:
            state = "finished"
        elif hb_state == "cancelled":
            state = "cancelled"
        elif hb_age <= HEARTBEAT_STALE_AFTER_SECONDS:
            state = "running" if hb_state in {"starting", "running"} else hb_state
        elif folder_age is not None and folder_age <= STALE_AFTER_SECONDS:
            # Heartbeat went quiet but files are still landing — keep
            # showing "running" rather than scaring the user with "stale".
            state = "running"
        else:
            state = "stale"

        status = RunStatus(
            run_id=run.run_id,
            state=state,
            total=total,
            completed=completed,
            current_template=hb_current,
            finished_templates=finished,
            last_error=hb_last_error or run.last_error,
            heartbeat_age_seconds=hb_age,
            started_at=run.started_at,
            finished_at=run.finished_at,
            source_folder=run.source_folder,
            dest_folder=run.dest_folder,
            source="heartbeat",
            timeout_seconds=run.timeout_seconds,
        )
    else:
        # No heartbeat — original folder-watching state machine.
        completed = completed_folder
        if total > 0 and completed >= total:
            state = "finished"
        elif folder_age is None:
            state = "requested"
        elif folder_age <= STALE_AFTER_SECONDS:
            state = "running"
        else:
            state = "stale"
        status = RunStatus(
            run_id=run.run_id,
            state=state,
            total=total,
            completed=completed,
            current_template=current,
            finished_templates=finished,
            last_error=run.last_error,
            heartbeat_age_seconds=folder_age,
            started_at=run.started_at,
            finished_at=run.finished_at,
            source_folder=run.source_folder,
            dest_folder=run.dest_folder,
            source="folder",
            timeout_seconds=run.timeout_seconds,
        )

    _attach_batch_run_summary(status, run)

    # Persist progress so cancel/restart can see how far we got.
    dirty = False
    if status.completed != run.last_observed_completed:
        run.last_observed_completed = status.completed
        dirty = True
    if run.state != status.state:
        run.state = status.state
        dirty = True
    if status.last_error and run.last_error != status.last_error:
        run.last_error = status.last_error
        dirty = True
    if status.state in {"finished", "timed_out", "finished_with_issues"} and not run.finished_at:
        run.finished_at = moment.isoformat(timespec="microseconds")
        status.finished_at = run.finished_at
        dirty = True
    if dirty:
        _save_run(session, run)

    _attach_ui_findings(status, run)
    return status


def load_run(session: OptimizerSession) -> RunRecord | None:
    path = session.directory / RUN_FILENAME
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return RunRecord.from_dict(data)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _make_run_id(moment: datetime) -> str:
    return "run_" + moment.strftime("%Y%m%d_%H%M%S")


def _command_instrument_for(session: OptimizerSession) -> str:
    """Return the NinjaTrader contract to send with the IPC command.

    Older optimizer sessions may have saved only the instrument root
    (``NQ``). NinjaTrader needs the full contract name (``NQ 06-26``), so
    prefer the seed template's ``InstrumentOrInstrumentList`` whenever the
    saved value is blank or generic.
    """
    try:
        doc = session.load_document()
    except Exception:
        return ""

    saved = str(doc.instrument or "").strip()
    if saved and not _is_generic_instrument(saved):
        return saved

    seed_instrument = _instrument_from_seed_template(doc.seed_template_path)
    if seed_instrument:
        return seed_instrument
    return saved


def _timeout_seconds_for(session: OptimizerSession) -> int | None:
    """Return the per-template AddOn timeout requested by this session.

    The optimizer UI stores the knob as minutes because that is how traders
    think about a Strategy Analyzer chunk. The AddOn command carries seconds.
    ``None`` means "let the installed AddOn use its default".
    """
    try:
        doc = session.load_document()
    except Exception:
        return None
    minutes = doc.chunking.max_runtime_minutes_per_chunk
    if minutes is None:
        return None
    try:
        seconds = int(float(minutes) * 60)
    except (TypeError, ValueError):
        return None
    return max(5, seconds)


def _is_generic_instrument(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return " " not in text and re.search(r"\d{2}-\d{2}", text) is None


def _instrument_from_seed_template(path_value: str | Path | None) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return ""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return ""
    node = root.find(".//InstrumentOrInstrumentList")
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _write_command_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
        # Nudge FileSystemWatcher listeners that are subscribed to Changed.
        # Atomic replace can surface as a rename-style event on Windows, while
        # older AddOn builds only listened for Changed/Created.
        try:
            os.utime(path, None)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _save_run(session: OptimizerSession, run: RunRecord) -> None:
    target = session.directory / RUN_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(run.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _scan_dest_folder(dest: Path) -> tuple[list[str], str | None]:
    """Return (finished_template_names, current_template_or_None).

    A template is 'finished' when its subfolder contains ``Summary.csv``.
    The 'current' guess is the most recently modified subfolder that does
    not yet have a Summary.csv.
    """
    if not dest.exists():
        return [], None
    finished: list[str] = []
    pending: list[tuple[float, str]] = []
    for child in dest.iterdir():
        if not child.is_dir():
            continue
        summary = child / "Summary.csv"
        if summary.exists():
            finished.append(child.name)
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            mtime = 0.0
        pending.append((mtime, child.name))
    finished.sort()
    pending.sort(reverse=True)
    current = pending[0][1] if pending else None
    return finished, current


def _load_heartbeat(run: RunRecord, *, now: datetime) -> dict[str, Any] | None:
    """Read and validate the AddOn's heartbeat file for this run.

    Returns ``None`` if the file is missing, unparseable, or its ``runId``
    doesn't match ``run.run_id`` (a different run is in progress). On
    success the dict includes a synthetic ``_age_seconds`` key.
    """
    path = Path(run.status_file or DEFAULT_STATUS_FILE)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # runId may legitimately be missing if the AddOn started without an IPC
    # command (manual button click). In that case, accept the heartbeat —
    # the user is intentionally driving the same run.
    hb_run_id = data.get("runId")
    if hb_run_id and run.run_id and hb_run_id != run.run_id:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    data["_age_seconds"] = max(0.0, now.timestamp() - mtime)
    return data


def _latest_write_age(dest: Path, *, now: datetime) -> float | None:
    """Seconds since the most-recent file write under ``dest``. ``None`` if
    nothing has been written yet."""
    if not dest.exists():
        return None
    latest_mtime: float | None = None
    for path in dest.rglob("*"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
    if latest_mtime is None:
        return None
    return max(0.0, now.timestamp() - latest_mtime)


def _status_from_run(
    session: OptimizerSession,
    run: RunRecord,
    *,
    state: str,
    now: datetime | None,
) -> RunStatus:
    dest = Path(run.dest_folder or (session.directory / NT_OUTPUT_DIRNAME))
    finished, current = _scan_dest_folder(dest)
    moment = now or datetime.now(timezone.utc)
    return RunStatus(
        run_id=run.run_id,
        state=state,
        total=run.total_templates or len(finished),
        completed=len(finished),
        current_template=current,
        finished_templates=finished,
        last_error=run.last_error,
        heartbeat_age_seconds=_latest_write_age(dest, now=moment),
        started_at=run.started_at,
        finished_at=run.finished_at,
        source_folder=run.source_folder,
        dest_folder=run.dest_folder,
        source="folder",
        timeout_seconds=run.timeout_seconds,
    )


def _attach_batch_run_summary(status: RunStatus, run: RunRecord) -> None:
    if str(status.state or "").strip().lower() in {"requested", "running", "starting"}:
        return
    rows = _batch_run_statuses(Path(run.dest_folder or ""))
    if not rows:
        return
    status.batch_run_statuses = rows

    incomplete = [
        row for row in rows
        if str(row.get("status") or "").strip().lower() not in {"", "completed"}
    ]
    timed_out = [
        row for row in rows
        if str(row.get("status") or "").strip().lower() == "timedout"
    ]
    if timed_out:
        status.state = "timed_out"
        if not status.last_error:
            status.last_error = _format_batch_issue(timed_out[0])
        return

    if incomplete and status.state == "finished":
        status.state = "finished_with_issues"
        if not status.last_error:
            status.last_error = _format_batch_issue(incomplete[0])


def _batch_run_statuses(dest: Path) -> list[dict[str, str]]:
    path = dest / "BatchRunSummary.csv"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                {
                    "template": str(row.get("Template") or "").strip(),
                    "status": str(row.get("Status") or "").strip(),
                    "strategy": str(row.get("Strategy") or "").strip(),
                    "instrument": str(row.get("Instrument") or "").strip(),
                    "backtest_start": str(row.get("Backtest start") or "").strip(),
                    "backtest_end": str(row.get("Backtest end") or "").strip(),
                    "output_folder": str(row.get("Output folder") or "").strip(),
                    "error": str(row.get("Error") or "").strip(),
                }
                for row in reader
            ]
    except OSError:
        return []


def _format_batch_issue(row: dict[str, str]) -> str:
    template = row.get("template") or "unknown template"
    status = row.get("status") or "unknown status"
    error = row.get("error") or ""
    if error:
        return f"{template}: {status} ({error})"
    return f"{template}: {status}"


def _attach_ui_findings(status: RunStatus, run: RunRecord) -> None:
    if status.state not in {"running", "stale", "requested"}:
        return
    if status.total and status.completed >= status.total:
        return
    if run.command_file and Path(run.command_file) != REAL_NT_COMMAND_FILE:
        return
    try:
        from ta_foundation.nt_strategy_loop.nt_ui_monitor import scan_ninjatrader_error_text

        findings = [finding.to_dict() for finding in scan_ninjatrader_error_text()]
    except Exception:
        findings = []
    if not findings:
        return
    status.ui_findings = findings
    status.state = "blocked"
    if not status.last_error:
        status.last_error = findings[0].get("text") or "NinjaTrader is waiting on a modal/error dialog."


def _safe_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
