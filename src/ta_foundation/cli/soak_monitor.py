from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BRIDGE_ROOT = Path("C:/ta_foundation/bridge")
REPO_BRIDGE_ROOT = Path(__file__).resolve().parents[1] / "strategies" / "TaFoundationExecutionBridge"

IMPORTANT_OUTBOX_STATUSES = {
    "ACCEPTED",
    "ENTRY_SUBMITTED",
    "FILLED",
    "STOP_ATTACHED",
    "STOP_WORKING",
    "REJECTED",
    "ORDER_REJECTED",
    "ORDER_CANCELLED",
    "EXIT_SUBMITTED",
    "FLATTENED",
    "HEARTBEAT_LOST",
    "SHELL_READY",
    "HEARTBEAT",
}

IMPORTANT_LOG_TYPES = {
    "HEALTH",
    "STARTUP_AUTO_RESET",
    "STARTUP_STATE",
    "STARTUP_INBOX",
    "REJECT",
    "REJECT_GATE",
    "ORDER_REJECT",
    "ORDER_CANCEL",
    "HEARTBEAT_LOST",
    "WARN",
    "DISABLE",
}


@dataclass(frozen=True)
class BridgePaths:
    root: Path
    inbox: Path
    archive: Path
    rejected: Path
    outbox: Path
    logs: Path
    state: Path
    log_file: Path
    state_file: Path


@dataclass
class LogRecord:
    line_number: int
    raw: str
    timestamp: datetime | None = None
    event_type: str = ""
    instr_id: str = ""
    template: str = ""
    mode: str = ""
    pos: str = ""
    qty: int | None = None
    message: str = ""


@dataclass
class HealthSnapshot:
    raw_record: LogRecord
    reason: str = ""
    shell_mode: str = ""
    intake: bool = True
    heartbeat_faulted: bool = False
    daily_lockout: bool = False
    active_position_id: str = ""
    pos_side: str = ""
    pos_qty: int = 0
    avg_price: float = 0.0
    entry_order: str = ""
    stop_order: str = ""
    target_order: str = ""
    exit_order: str = ""
    pending_stop: float = 0.0
    pending_target_ticks: int = 0
    queue_depth: int = 0
    last_msg_utc: datetime | None = None


@dataclass
class OutboxEvent:
    file_path: Path
    timestamp: datetime | None = None
    status: str = ""
    instruction_id: str = ""
    shell_mode: str = ""
    position: str = ""
    quantity: int = 0
    detail: str = ""


@dataclass
class MessageFileInfo:
    file_path: Path
    modified_utc: datetime | None
    message_id: str = ""
    thesis_id: str = ""
    action: str = ""
    instrument: str = ""
    timestamp: datetime | None = None


@dataclass
class Alert:
    severity: str
    message: str


@dataclass
class MonitorSnapshot:
    paths: BridgePaths
    generated_at: datetime
    state_data: dict[str, Any]
    latest_log: LogRecord | None
    latest_health: HealthSnapshot | None
    last_ready_at: datetime | None
    last_ready_reason: str
    current_mode: str
    current_position: str
    current_qty: int
    intake_enabled: bool | None
    heartbeat_faulted: bool | None
    daily_lockout: bool | None
    active_position_id: str
    last_message_utc: datetime | None
    queue_depth: int | None
    current_message_id: str
    current_thesis_id: str
    current_action: str
    trade_state_guess: str
    last_by_status: dict[str, datetime | None]
    inbox_count: int
    archive_recent_count: int
    rejected_recent_count: int
    outbox_recent_count: int
    stuck_inbox_files: list[Path]
    log_freshness_seconds: float | None
    recent_feed: list[str]
    alerts: list[Alert] = field(default_factory=list)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stat_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _file_age_seconds(path: Path, now: datetime) -> float | None:
    mtime = _stat_mtime_utc(path)
    if mtime is None:
        return None
    return max(0.0, (now - mtime).total_seconds())


def _read_text_shared(path: Path, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    if os.name != "nt":
        return path.read_text(encoding=encoding, errors=errors)

    import msvcrt

    generic_read = 0x80000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle_value = ctypes.c_void_p(-1).value

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p

    handle = create_file(
        str(path),
        generic_read,
        share_read | share_write | share_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), f"unable to open shared-read handle for {path}")

    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except Exception:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise

    try:
        with os.fdopen(fd, "r", encoding=encoding, errors=errors) as stream:
            return stream.read()
    except Exception:
        os.close(fd)
        raise


def resolve_bridge_paths(bridge_root: Path | None) -> BridgePaths:
    root = bridge_root or _detect_bridge_root()
    return BridgePaths(
        root=root,
        inbox=root / "inbox",
        archive=root / "archive",
        rejected=root / "rejected",
        outbox=root / "outbox",
        logs=root / "logs",
        state=root / "state",
        log_file=root / "logs" / "execution_shell.log",
        state_file=root / "state" / "shell_state.json",
    )


def _detect_bridge_root() -> Path:
    env_root = os.environ.get("TA_FOUNDATION_BRIDGE_ROOT")
    candidates = [Path(env_root)] if env_root else []
    candidates.extend([DEFAULT_BRIDGE_ROOT, REPO_BRIDGE_ROOT])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return DEFAULT_BRIDGE_ROOT


def parse_log_line(line: str, line_number: int) -> LogRecord | None:
    raw = line.rstrip("\r\n")
    if not raw:
        return None
    parts = raw.split("|", 7)
    if len(parts) != 8:
        return None
    if not parts[2].startswith("instr=") or not parts[7].startswith("msg="):
        return None
    return LogRecord(
        line_number=line_number,
        raw=raw,
        timestamp=_parse_dt(parts[0]),
        event_type=parts[1].strip(),
        instr_id=parts[2][len("instr="):].strip(),
        template=parts[3][len("template="):].strip() if parts[3].startswith("template=") else "",
        mode=parts[4][len("mode="):].strip() if parts[4].startswith("mode=") else "",
        pos=parts[5][len("pos="):].strip() if parts[5].startswith("pos=") else "",
        qty=_safe_int(parts[6][len("qty="):].strip(), default=0) if parts[6].startswith("qty=") else 0,
        message=parts[7][len("msg="):].strip(),
    )


def read_log_records(log_file: Path) -> list[LogRecord]:
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records: list[LogRecord] = []
    for line_number, line in enumerate(lines):
        record = parse_log_line(line, line_number)
        if record:
            records.append(record)
    return records


def _parse_space_kv(message: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in message.split():
        if "=" in token:
            key, value = token.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def parse_health(record: LogRecord) -> HealthSnapshot | None:
    if record.event_type != "HEALTH":
        return None
    data = _parse_space_kv(record.message)
    return HealthSnapshot(
        raw_record=record,
        reason=data.get("reason", ""),
        shell_mode=data.get("shell_mode", record.mode),
        intake=bool(_safe_bool(data.get("intake")) if _safe_bool(data.get("intake")) is not None else True),
        heartbeat_faulted=bool(_safe_bool(data.get("heartbeat_faulted"))),
        daily_lockout=bool(_safe_bool(data.get("daily_lockout"))),
        active_position_id=data.get("active_position_id", ""),
        pos_side=data.get("pos_side", record.pos),
        pos_qty=_safe_int(data.get("pos_qty"), default=record.qty or 0),
        avg_price=_safe_float(data.get("avg")),
        entry_order=data.get("entry_order", ""),
        stop_order=data.get("stop_order", ""),
        target_order=data.get("target_order", ""),
        exit_order=data.get("exit_order", ""),
        pending_stop=_safe_float(data.get("pending_stop")),
        pending_target_ticks=_safe_int(data.get("pending_target_ticks")),
        queue_depth=_safe_int(data.get("queue_depth")),
        last_msg_utc=_parse_dt(data.get("last_msg_utc")),
    )


def _parse_detail(detail: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for chunk in detail.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parsed[key.strip()] = value.strip()
    return parsed


def read_outbox_events(outbox_dir: Path, limit: int = 400) -> list[OutboxEvent]:
    if not outbox_dir.exists():
        return []
    files = sorted(outbox_dir.glob("*.evt.json"))
    if limit > 0:
        files = files[-limit:]
    events: list[OutboxEvent] = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        events.append(
            OutboxEvent(
                file_path=path,
                timestamp=_parse_dt(raw.get("timestamp_utc")) or _stat_mtime_utc(path),
                status=str(raw.get("status") or "").strip().upper(),
                instruction_id=str(raw.get("instruction_id") or "").strip(),
                shell_mode=str(raw.get("shell_mode") or "").strip(),
                position=str(raw.get("position") or "").strip(),
                quantity=_safe_int(raw.get("quantity")),
                detail=str(raw.get("detail") or "").strip(),
            )
        )
    return events


def read_state_file(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {}
    try:
        raw = json.loads(_read_text_shared(state_file, encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _count_recent_files(directory: Path, pattern: str, now: datetime, recent_seconds: int) -> int:
    if not directory.exists():
        return 0
    count = 0
    for path in directory.glob(pattern):
        age = _file_age_seconds(path, now)
        if age is not None and age <= recent_seconds:
            count += 1
    return count


def _stuck_inbox_files(inbox_dir: Path, now: datetime, stuck_seconds: int) -> list[Path]:
    if not inbox_dir.exists():
        return []
    stuck: list[Path] = []
    for path in sorted(inbox_dir.glob("*.json")):
        age = _file_age_seconds(path, now)
        if age is not None and age >= stuck_seconds:
            stuck.append(path)
    return stuck


def _read_message_file(path: Path) -> MessageFileInfo | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    message_id = str(raw.get("message_id") or path.stem).strip()
    return MessageFileInfo(
        file_path=path,
        modified_utc=_stat_mtime_utc(path),
        message_id=message_id,
        thesis_id=str(raw.get("thesis_id") or "").strip(),
        action=str(raw.get("action") or "").strip(),
        instrument=str(raw.get("instrument") or "").strip(),
        timestamp=_parse_dt(raw.get("timestamp")),
    )


def read_recent_messages(paths: BridgePaths, per_dir_limit: int = 80) -> dict[str, MessageFileInfo]:
    by_id: dict[str, MessageFileInfo] = {}
    for directory in (paths.archive, paths.rejected, paths.inbox):
        if not directory.exists():
            continue
        files = [path for path in directory.glob("*.json") if path.is_file()]
        files.sort(key=lambda path: (path.stat().st_mtime if path.exists() else 0, path.name), reverse=True)
        for path in files[:per_dir_limit]:
            info = _read_message_file(path)
            if info and info.message_id not in by_id:
                by_id[info.message_id] = info
    return by_id


def _last_status_time(events: Iterable[OutboxEvent], status: str) -> datetime | None:
    matches = [event.timestamp for event in events if event.status == status and event.timestamp is not None]
    return matches[-1] if matches else None


def _last_event(events: Iterable[OutboxEvent], *statuses: str) -> OutboxEvent | None:
    status_set = {status.upper() for status in statuses}
    matches = [event for event in events if event.status in status_set]
    return matches[-1] if matches else None


def _is_ready_snapshot(snapshot: HealthSnapshot) -> bool:
    return (
        snapshot.shell_mode == "Idle"
        and snapshot.intake
        and not snapshot.heartbeat_faulted
        and not snapshot.daily_lockout
        and snapshot.pos_side == "Flat"
        and snapshot.pos_qty == 0
        and snapshot.raw_record.timestamp is not None
    )


def _is_ready_shell_event(event: OutboxEvent) -> bool:
    if event.status != "SHELL_READY" or event.timestamp is None:
        return False
    detail = _parse_detail(event.detail)
    intake = _safe_bool(detail.get("intake"))
    heartbeat_faulted = _safe_bool(detail.get("hb_faulted"))
    daily_lockout = _safe_bool(detail.get("daily_lockout"))
    mode = detail.get("mode") or event.shell_mode
    return (
        mode == "Idle"
        and event.position == "Flat"
        and intake is True
        and heartbeat_faulted is False
        and daily_lockout is False
    )


def _find_last_ready(
    health_snapshots: list[HealthSnapshot],
    outbox_events: list[OutboxEvent],
) -> tuple[datetime | None, str]:
    last_ready_at: datetime | None = None
    last_ready_reason = ""
    was_ready = False

    timeline: list[tuple[datetime, str, HealthSnapshot | OutboxEvent]] = []
    for snapshot in health_snapshots:
        if snapshot.raw_record.timestamp is not None:
            timeline.append((snapshot.raw_record.timestamp, "health", snapshot))
    for event in outbox_events:
        if event.timestamp is not None and event.status == "SHELL_READY":
            timeline.append((event.timestamp, "shell_ready", event))
    timeline.sort(key=lambda item: item[0])

    for _, kind, payload in timeline:
        if kind == "health":
            snapshot = payload
            is_ready = _is_ready_snapshot(snapshot)
            reason = snapshot.reason
            ts = snapshot.raw_record.timestamp
        else:
            event = payload
            is_ready = _is_ready_shell_event(event)
            reason = "SHELL_READY"
            ts = event.timestamp

        if is_ready and not was_ready and ts is not None:
            last_ready_at = ts
            last_ready_reason = reason
        was_ready = is_ready

    return last_ready_at, last_ready_reason


def _latest_session_start(
    outbox_events: list[OutboxEvent],
    health_snapshots: list[HealthSnapshot],
) -> datetime | None:
    candidates: list[datetime] = []
    for event in outbox_events:
        if event.status == "SHELL_READY" and event.timestamp is not None:
            candidates.append(event.timestamp)
    for snapshot in health_snapshots:
        if snapshot.reason == "STARTUP" and snapshot.raw_record.timestamp is not None:
            candidates.append(snapshot.raw_record.timestamp)
    return max(candidates) if candidates else None


def _health_feed_key(snapshot: HealthSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.reason,
        snapshot.shell_mode,
        snapshot.intake,
        snapshot.heartbeat_faulted,
        snapshot.daily_lockout,
        snapshot.pos_side,
        snapshot.pos_qty,
        snapshot.queue_depth,
        snapshot.active_position_id,
    )


def _build_recent_feed(
    *,
    log_records: list[LogRecord],
    outbox_events: list[OutboxEvent],
    health_snapshots: list[HealthSnapshot],
    tail_lines: int,
    recent_seconds: int,
    now: datetime,
) -> list[str]:
    session_start = _latest_session_start(outbox_events, health_snapshots)
    feed_cutoff = (session_start - timedelta(seconds=5)) if session_start else (now - timedelta(seconds=recent_seconds))

    feed_entries: list[tuple[datetime, str]] = []
    last_health_key: tuple[Any, ...] | None = None

    for event in outbox_events:
        if event.status not in IMPORTANT_OUTBOX_STATUSES or event.timestamp is None or event.timestamp < feed_cutoff:
            continue
        detail = event.detail
        if len(detail) > 110:
            detail = detail[:107] + "..."
        suffix = f" | id={event.instruction_id}" if event.instruction_id else ""
        feed_entries.append((event.timestamp, f"{_fmt_dt(event.timestamp)} OUTBOX {event.status}{suffix} | {detail}"))

    for record in log_records:
        if record.event_type not in IMPORTANT_LOG_TYPES or record.timestamp is None or record.timestamp < feed_cutoff:
            continue
        if record.event_type == "HEALTH":
            health = parse_health(record)
            if health is None:
                continue
            health_key = _health_feed_key(health)
            if health_key == last_health_key:
                continue
            last_health_key = health_key
            line = (
                f"{_fmt_dt(record.timestamp)} LOG HEALTH | reason={health.reason} "
                f"mode={health.shell_mode} intake={health.intake} hb_faulted={health.heartbeat_faulted} "
                f"queue={health.queue_depth}"
            )
        else:
            message = record.message
            if len(message) > 110:
                message = message[:107] + "..."
            line = f"{_fmt_dt(record.timestamp)} LOG {record.event_type} | {message}"
        feed_entries.append((record.timestamp, line))

    feed_entries.sort(key=lambda item: item[0])
    return [line for _, line in feed_entries[-tail_lines:]]


def _choose_current_identity(
    state_data: dict[str, Any],
    messages_by_id: dict[str, MessageFileInfo],
    relevant_events: list[OutboxEvent],
) -> tuple[str, str, str]:
    current_message_id = str(state_data.get("LastInstructionId") or "").strip()
    if not current_message_id:
        for event in reversed(relevant_events):
            if event.instruction_id:
                current_message_id = event.instruction_id
                break
    info = messages_by_id.get(current_message_id)
    if info is None:
        return current_message_id, "", ""
    return current_message_id, info.thesis_id, info.action


def _guess_trade_state(
    mode: str,
    position: str,
    qty: int,
    intake_enabled: bool | None,
    heartbeat_faulted: bool | None,
    daily_lockout: bool | None,
    last_ready_at: datetime | None,
    last_filled_at: datetime | None,
) -> str:
    if heartbeat_faulted or daily_lockout or mode == "Disabled":
        return "faulted"
    if mode == "Idle" and position == "Flat" and qty == 0 and intake_enabled is True and heartbeat_faulted is False:
        return "idle"
    if mode in {"EntryPending", "ExitPending"}:
        return "pending"
    if position in {"Long", "Short"} or qty > 0 or mode == "InPosition":
        return "active"
    if last_ready_at is not None:
        if last_filled_at is None or last_ready_at >= last_filled_at:
            return "idle"
    if intake_enabled is False:
        return "completed"
    return "completed"


def build_snapshot(
    paths: BridgePaths,
    *,
    recent_minutes: int,
    tail_lines: int,
    stuck_seconds: int,
    heartbeat_timeout_seconds: int,
    pending_seconds: int,
    ready_grace_seconds: int,
    reject_threshold: int,
) -> MonitorSnapshot:
    now = datetime.now(timezone.utc)
    state_data = read_state_file(paths.state_file)
    log_records = read_log_records(paths.log_file)
    outbox_events = read_outbox_events(paths.outbox, limit=max(200, tail_lines * 12))
    messages_by_id = read_recent_messages(paths)
    health_snapshots = [snapshot for snapshot in (parse_health(record) for record in log_records) if snapshot]

    latest_log = log_records[-1] if log_records else None
    latest_health = health_snapshots[-1] if health_snapshots else None
    last_ready_at, last_ready_reason = _find_last_ready(health_snapshots, outbox_events)
    latest_position_event = _last_event(outbox_events, "FILLED", "STOP_ATTACHED", "SHELL_READY")

    current_mode = (
        str(state_data.get("ShellMode") or "").strip()
        or (latest_health.shell_mode if latest_health else "")
        or (latest_log.mode if latest_log else "")
        or "Unknown"
    )
    current_position = (
        (latest_log.pos if latest_log and latest_log.pos else "")
        or (latest_health.pos_side if latest_health else "")
        or (latest_position_event.position if latest_position_event else "")
        or "Unknown"
    )
    if latest_log and latest_log.qty is not None:
        current_qty = latest_log.qty
    elif latest_health is not None:
        current_qty = latest_health.pos_qty
    elif latest_position_event is not None:
        current_qty = latest_position_event.quantity
    else:
        current_qty = 0
    intake_enabled = _safe_bool(state_data.get("SignalIntakeEnabled"))
    if intake_enabled is None and latest_health is not None:
        intake_enabled = latest_health.intake
    heartbeat_faulted = _safe_bool(state_data.get("HeartbeatFaulted"))
    if heartbeat_faulted is None and latest_health is not None:
        heartbeat_faulted = latest_health.heartbeat_faulted
    daily_lockout = _safe_bool(state_data.get("DailyLockout"))
    if daily_lockout is None and latest_health is not None:
        daily_lockout = latest_health.daily_lockout
    active_position_id = (
        (latest_health.active_position_id if latest_health else "")
        or str(state_data.get("PositionId") or "").strip()
    )
    last_message_utc = _parse_dt(state_data.get("LastBridgeMessageUtc"))
    if last_message_utc is None and latest_health is not None:
        last_message_utc = latest_health.last_msg_utc
    queue_depth = latest_health.queue_depth if latest_health else None

    last_by_status = {
        "ACCEPTED": _last_status_time(outbox_events, "ACCEPTED"),
        "ENTRY_SUBMITTED": _last_status_time(outbox_events, "ENTRY_SUBMITTED"),
        "FILLED": _last_status_time(outbox_events, "FILLED"),
        "STOP_ATTACHED": _last_status_time(outbox_events, "STOP_ATTACHED"),
        "REJECTED": max(
            [ts for ts in [_last_status_time(outbox_events, "REJECTED"), _last_status_time(outbox_events, "ORDER_REJECTED")] if ts is not None],
            default=None,
        ),
        "HEARTBEAT_LOST": _last_status_time(outbox_events, "HEARTBEAT_LOST"),
        "SHELL_READY": _last_status_time(outbox_events, "SHELL_READY"),
        "HEALTH": latest_health.raw_record.timestamp if latest_health and latest_health.raw_record.timestamp else None,
    }

    current_message_id, current_thesis_id, current_action = _choose_current_identity(state_data, messages_by_id, outbox_events)
    trade_state_guess = _guess_trade_state(
        current_mode,
        current_position,
        current_qty,
        intake_enabled,
        heartbeat_faulted,
        daily_lockout,
        last_ready_at,
        last_by_status["FILLED"],
    )
    current_is_ready = (
        current_mode == "Idle"
        and current_position == "Flat"
        and current_qty == 0
        and intake_enabled is True
        and heartbeat_faulted is False
        and daily_lockout is not True
    )

    recent_seconds = recent_minutes * 60
    inbox_count = sum(1 for _ in paths.inbox.glob("*.json")) if paths.inbox.exists() else 0
    archive_recent_count = _count_recent_files(paths.archive, "*.json", now, recent_seconds)
    rejected_recent_count = _count_recent_files(paths.rejected, "*.json", now, recent_seconds)
    outbox_recent_count = sum(
        1 for event in outbox_events
        if event.timestamp is not None and (now - event.timestamp).total_seconds() <= recent_seconds
    )
    stuck_inbox = _stuck_inbox_files(paths.inbox, now, stuck_seconds)
    log_freshness_seconds = _file_age_seconds(paths.log_file, now)

    alerts: list[Alert] = []
    if heartbeat_faulted:
        alerts.append(Alert("critical", "Heartbeat faulted - reset or check sender/strategy health."))
    if daily_lockout:
        alerts.append(Alert("critical", "Daily lockout active - shell will not accept new entries."))
    if intake_enabled is False and current_position == "Flat" and current_qty == 0:
        alerts.append(Alert("warning", "Intake disabled while flat - operator reset may be required."))
    if stuck_inbox:
        alerts.append(Alert("warning", f"Inbox file stuck > {stuck_seconds}s - check bridge polling or file move failures."))
    if log_freshness_seconds is not None and log_freshness_seconds > heartbeat_timeout_seconds * 2:
        alerts.append(Alert("warning", "Execution log is stale - shell may not be updating."))
    if current_mode in {"EntryPending", "ExitPending"}:
        latest_pending_at = max(
            [ts for ts in [last_by_status["ACCEPTED"], last_by_status["ENTRY_SUBMITTED"], last_by_status["FILLED"]] if ts is not None],
            default=None,
        )
        if latest_pending_at is not None and (now - latest_pending_at).total_seconds() > pending_seconds:
            alerts.append(Alert("warning", f"Pending activity unresolved for > {pending_seconds}s."))
    reject_events_recent = [
        event for event in outbox_events
        if event.status in {"REJECTED", "ORDER_REJECTED"}
        and event.timestamp is not None
        and (now - event.timestamp).total_seconds() <= recent_seconds
    ]
    if len(reject_events_recent) >= reject_threshold:
        alerts.append(Alert("warning", f"Repeated rejects detected ({len(reject_events_recent)} in last {recent_minutes}m)."))
    if (
        not current_is_ready
        and current_position == "Flat"
        and current_qty == 0
        and last_by_status["FILLED"] is not None
        and (
            last_ready_at is None
            or last_ready_at < last_by_status["FILLED"]
        )
        and (now - last_by_status["FILLED"]).total_seconds() > ready_grace_seconds
    ):
        alerts.append(Alert("warning", "Shell not back to Idle/ready after completed trade."))

    recent_feed = _build_recent_feed(
        log_records=log_records,
        outbox_events=outbox_events,
        health_snapshots=health_snapshots,
        tail_lines=tail_lines,
        recent_seconds=recent_seconds,
        now=now,
    )

    return MonitorSnapshot(
        paths=paths,
        generated_at=now,
        state_data=state_data,
        latest_log=latest_log,
        latest_health=latest_health,
        last_ready_at=last_ready_at,
        last_ready_reason=last_ready_reason,
        current_mode=current_mode,
        current_position=current_position,
        current_qty=current_qty,
        intake_enabled=intake_enabled,
        heartbeat_faulted=heartbeat_faulted,
        daily_lockout=daily_lockout,
        active_position_id=active_position_id,
        last_message_utc=last_message_utc,
        queue_depth=queue_depth,
        current_message_id=current_message_id,
        current_thesis_id=current_thesis_id,
        current_action=current_action,
        trade_state_guess=trade_state_guess,
        last_by_status=last_by_status,
        inbox_count=inbox_count,
        archive_recent_count=archive_recent_count,
        rejected_recent_count=rejected_recent_count,
        outbox_recent_count=outbox_recent_count,
        stuck_inbox_files=stuck_inbox,
        log_freshness_seconds=log_freshness_seconds,
        recent_feed=recent_feed,
        alerts=alerts,
    )


def render_snapshot(snapshot: MonitorSnapshot, *, recent_minutes: int, stuck_seconds: int) -> str:
    lines = [
        f"TaFoundation Soak Monitor  |  generated={_fmt_dt(snapshot.generated_at)}  |  bridge={snapshot.paths.root}",
        "",
        "CURRENT STATUS",
        f"  health={'ATTENTION' if snapshot.alerts else 'OK'}",
        f"  mode={snapshot.current_mode}  pos={snapshot.current_position}  qty={snapshot.current_qty}",
        f"  intake_enabled={snapshot.intake_enabled}  heartbeat_faulted={snapshot.heartbeat_faulted}  daily_lockout={snapshot.daily_lockout}",
        f"  active_position_id={snapshot.active_position_id or '-'}  queue_depth={snapshot.queue_depth if snapshot.queue_depth is not None else '-'}",
        f"  last_message_utc={_fmt_dt(snapshot.last_message_utc)}  last_health_reason={snapshot.latest_health.reason if snapshot.latest_health else '-'}",
        f"  last_ready={_fmt_dt(snapshot.last_ready_at)}  ready_reason={snapshot.last_ready_reason or '-'}",
        "",
        "LAST SIGNAL / TRADE",
        f"  message_id={snapshot.current_message_id or '-'}  thesis_id={snapshot.current_thesis_id or '-'}  action={snapshot.current_action or '-'}",
        f"  trade_state_guess={snapshot.trade_state_guess}",
        f"  last_ACCEPTED={_fmt_dt(snapshot.last_by_status['ACCEPTED'])}",
        f"  last_ENTRY_SUBMITTED={_fmt_dt(snapshot.last_by_status['ENTRY_SUBMITTED'])}",
        f"  last_FILLED={_fmt_dt(snapshot.last_by_status['FILLED'])}",
        f"  last_STOP_ATTACHED={_fmt_dt(snapshot.last_by_status['STOP_ATTACHED'])}",
        f"  last_REJECT={_fmt_dt(snapshot.last_by_status['REJECTED'])}",
        f"  last_HEARTBEAT_LOST={_fmt_dt(snapshot.last_by_status['HEARTBEAT_LOST'])}",
        f"  last_SHELL_READY={_fmt_dt(snapshot.last_by_status['SHELL_READY'])}",
        f"  last_HEALTH={_fmt_dt(snapshot.last_by_status['HEALTH'])}",
        "",
        "BRIDGE HEALTH",
        f"  inbox_count={snapshot.inbox_count}",
        f"  archive_recent_{recent_minutes}m={snapshot.archive_recent_count}  rejected_recent_{recent_minutes}m={snapshot.rejected_recent_count}  outbox_recent_{recent_minutes}m={snapshot.outbox_recent_count}",
        f"  stuck_inbox_over_{stuck_seconds}s={len(snapshot.stuck_inbox_files)}",
        f"  log_freshness={_fmt_age(snapshot.log_freshness_seconds)}",
    ]
    if snapshot.stuck_inbox_files:
        names = ", ".join(path.name for path in snapshot.stuck_inbox_files[:5])
        lines.append(f"  stuck_files={names}")
    lines.extend(["", "ALERTS"])
    if snapshot.alerts:
        for alert in snapshot.alerts:
            lines.append(f"  [{alert.severity.upper()}] {alert.message}")
    else:
        lines.append("  none")
    lines.extend(["", "RECENT EVENT FEED"])
    if snapshot.recent_feed:
        lines.extend(f"  {line}" for line in snapshot.recent_feed)
    else:
        lines.append("  no recent structured events found")
    return "\n".join(lines)


def _clear_terminal() -> None:
    if not sys.stdout.isatty():
        return
    print("\033[2J\033[H", end="")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lightweight local soak-run monitor for the existing NT8 bridge/log outputs."
    )
    parser.add_argument("--bridge-root", type=Path, default=None, help="Bridge root containing inbox/archive/rejected/outbox/logs/state")
    parser.add_argument("--refresh-seconds", type=float, default=5.0, help="Refresh interval for the terminal dashboard")
    parser.add_argument("--tail-lines", type=int, default=15, help="Number of recent feed lines to show")
    parser.add_argument("--recent-minutes", type=int, default=15, help="Window for archive/rejected/outbox recent counts")
    parser.add_argument("--stuck-seconds", type=int, default=60, help="Inbox age threshold for stuck-file alerts")
    parser.add_argument("--heartbeat-timeout-seconds", type=int, default=60, help="Heartbeat timeout used for freshness/health alerts")
    parser.add_argument("--pending-seconds", type=int, default=90, help="Alert when pending activity lasts longer than this")
    parser.add_argument("--ready-grace-seconds", type=int, default=30, help="Alert when the shell stays non-ready too long after a trade completes")
    parser.add_argument("--reject-threshold", type=int, default=2, help="Recent reject count that triggers an alert")
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser


def run_monitor(args: argparse.Namespace) -> int:
    paths = resolve_bridge_paths(args.bridge_root)
    while True:
        snapshot = build_snapshot(
            paths,
            recent_minutes=args.recent_minutes,
            tail_lines=args.tail_lines,
            stuck_seconds=args.stuck_seconds,
            heartbeat_timeout_seconds=args.heartbeat_timeout_seconds,
            pending_seconds=args.pending_seconds,
            ready_grace_seconds=args.ready_grace_seconds,
            reject_threshold=args.reject_threshold,
        )
        _clear_terminal()
        print(render_snapshot(snapshot, recent_minutes=args.recent_minutes, stuck_seconds=args.stuck_seconds))
        if args.once:
            return 0
        time.sleep(max(0.5, args.refresh_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return run_monitor(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
