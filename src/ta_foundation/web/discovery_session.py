from __future__ import annotations

"""
On-disk persistence for Discovery web sessions.

Each browser session is one directory under
    .ta_artifacts/web_discovery/sessions/<session_id>/
containing three JSON documents:

- session.json    project context, instrument, current stage, per-stage form values
- stage_runs.json append-only history of every CLI run dispatched from this session
- promotions.json append-only list of combos the user clicked Promote on

Plus a stage_yaml/ subdirectory where every generated stage YAML is stored,
so the user can come back later and reconstruct exactly what was run.

Design choices:

- Writes are atomic: write to a temp file, then os.replace. A power loss
  mid-write leaves the previous good copy on disk.
- Per-session reentrant locks guard read-modify-write sequences (append_run,
  append_promotion, set_form_values).
- get_session() returns a fresh wrapper each time, but lock objects are
  cached at module level keyed by session id so locking actually works
  across calls.
- Storage root is configurable via set_storage_root() so tests can isolate.

This module does NOT serve HTTP. The route layer (app.py) wraps these calls
and adds cookie-based session id resolution.
"""

import json
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.discovery_instruments import default_instrument, get_instrument


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Storage root
# ---------------------------------------------------------------------------

_DEFAULT_ROOT = Path(".ta_artifacts") / "web_discovery" / "sessions"
_root: Path = _DEFAULT_ROOT
_root_lock = threading.Lock()


def set_storage_root(path: Path | str | None) -> None:
    """Override the storage root. Pass None to reset to the default."""
    global _root
    with _root_lock:
        _root = Path(path) if path else _DEFAULT_ROOT


def get_storage_root() -> Path:
    with _root_lock:
        return _root


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProjectContext:
    input_folder: str = ""
    output_folder: str = ""
    market_data_folder: str = ""
    recursive: bool = True
    no_tick_data: bool = True
    contract: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectContext":
        data = data or {}
        return cls(
            input_folder=str(data.get("input_folder") or ""),
            output_folder=str(data.get("output_folder") or ""),
            market_data_folder=str(data.get("market_data_folder") or ""),
            recursive=bool(data.get("recursive", True)),
            no_tick_data=bool(data.get("no_tick_data", True)),
            contract=str(data.get("contract") or ""),
        )


@dataclass
class SessionInstrument:
    symbol: str
    name: str
    tick_size: float
    tick_value: float
    point_value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionInstrument":
        return cls(
            symbol=str(data["symbol"]),
            name=str(data["name"]),
            tick_size=float(data["tick_size"]),
            tick_value=float(data["tick_value"]),
            point_value=float(data["point_value"]),
        )

    @classmethod
    def from_symbol(cls, symbol: str) -> "SessionInstrument":
        inst = get_instrument(symbol) or default_instrument()
        return cls(
            symbol=inst.symbol,
            name=inst.name,
            tick_size=inst.tick_size,
            tick_value=inst.tick_value,
            point_value=inst.point_value,
        )


@dataclass
class SessionDocument:
    schema_version: int
    session_id: str
    created_at: str
    updated_at: str
    label: str
    instrument: SessionInstrument
    context: ProjectContext
    current_stage: str = ""
    stage_form_values: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "label": self.label,
            "instrument": self.instrument.to_dict(),
            "context": self.context.to_dict(),
            "current_stage": self.current_stage,
            "stage_form_values": dict(self.stage_form_values),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionDocument":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SessionSchemaError(
                f"Session document has schema_version {version!r}, expected {SCHEMA_VERSION}"
            )
        return cls(
            schema_version=int(version),
            session_id=str(data["session_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            label=str(data.get("label") or ""),
            instrument=SessionInstrument.from_dict(data["instrument"]),
            context=ProjectContext.from_dict(data.get("context")),
            current_stage=str(data.get("current_stage") or ""),
            stage_form_values=dict(data.get("stage_form_values") or {}),
        )


@dataclass(frozen=True)
class StageRun:
    stage_id: str
    job_id: str
    yaml_path: str
    report_html_path: str
    summary_json_path: str
    started_at: str
    finished_at: str | None = None
    status: str = "queued"   # queued | running | succeeded | failed | cancelled

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageRun":
        return cls(
            stage_id=str(data["stage_id"]),
            job_id=str(data["job_id"]),
            yaml_path=str(data.get("yaml_path") or ""),
            report_html_path=str(data.get("report_html_path") or ""),
            summary_json_path=str(data.get("summary_json_path") or ""),
            started_at=str(data["started_at"]),
            finished_at=(str(data["finished_at"]) if data.get("finished_at") else None),
            status=str(data.get("status") or "queued"),
        )


@dataclass(frozen=True)
class Promotion:
    from_stage: str
    to_stage: str
    rank: int
    promoted_at: str
    yaml_overrides: dict[str, Any]
    explain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "rank": self.rank,
            "promoted_at": self.promoted_at,
            "yaml_overrides": dict(self.yaml_overrides),
            "explain": self.explain,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Promotion":
        return cls(
            from_stage=str(data["from_stage"]),
            to_stage=str(data["to_stage"]),
            rank=int(data["rank"]),
            promoted_at=str(data["promoted_at"]),
            yaml_overrides=dict(data.get("yaml_overrides") or {}),
            explain=str(data.get("explain") or ""),
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SessionError(Exception):
    """Base class for session persistence errors."""


class SessionNotFoundError(SessionError):
    """Raised when a requested session id does not exist on disk."""


class SessionSchemaError(SessionError):
    """Raised when a document on disk has an unexpected schema_version."""


# ---------------------------------------------------------------------------
# Per-session lock registry — keeps locks alive across get_session() calls
# ---------------------------------------------------------------------------

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _locks[session_id] = lock
        return lock


def _drop_lock_for(session_id: str) -> None:
    with _locks_guard:
        _locks.pop(session_id, None)


# ---------------------------------------------------------------------------
# DiscoverySession — high-level interface backed by one directory
# ---------------------------------------------------------------------------

class DiscoverySession:
    """Wrapper for a single session directory. Cheap to construct;
    each operation reads/writes through the live filesystem under the lock.
    """

    SESSION_FILENAME = "session.json"
    RUNS_FILENAME = "stage_runs.json"
    PROMOTIONS_FILENAME = "promotions.json"
    YAML_DIRNAME = "stage_yaml"

    def __init__(self, session_dir: Path) -> None:
        self._dir = session_dir
        self._id = session_dir.name

    # --- identity ---

    @property
    def id(self) -> str:
        return self._id

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def stage_yaml_dir(self) -> Path:
        return self._dir / self.YAML_DIRNAME

    @property
    def session_path(self) -> Path:
        return self._dir / self.SESSION_FILENAME

    @property
    def runs_path(self) -> Path:
        return self._dir / self.RUNS_FILENAME

    @property
    def promotions_path(self) -> Path:
        return self._dir / self.PROMOTIONS_FILENAME

    # --- session document ---

    def load_document(self) -> SessionDocument:
        with _lock_for(self._id):
            data = _read_json(self.session_path)
            if data is None:
                raise SessionNotFoundError(f"No session at {self._dir}")
            return SessionDocument.from_dict(data)

    def save_document(self, doc: SessionDocument) -> None:
        if doc.session_id != self._id:
            raise SessionError(
                f"Session id mismatch: doc says {doc.session_id}, dir is {self._id}"
            )
        with _lock_for(self._id):
            doc.updated_at = _now_iso()
            self._dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self.session_path, doc.to_dict())

    def update_label(self, label: str) -> SessionDocument:
        with _lock_for(self._id):
            doc = self.load_document()
            doc.label = str(label)
            self.save_document(doc)
            return doc

    def update_context(self, **kwargs: Any) -> SessionDocument:
        """Update fields on the project context. Unknown keys are ignored."""
        with _lock_for(self._id):
            doc = self.load_document()
            current = doc.context.to_dict()
            for key, value in kwargs.items():
                if key in current:
                    current[key] = value
            doc.context = ProjectContext.from_dict(current)
            self.save_document(doc)
            return doc

    def set_instrument(self, symbol: str) -> SessionDocument:
        with _lock_for(self._id):
            doc = self.load_document()
            doc.instrument = SessionInstrument.from_symbol(symbol)
            self.save_document(doc)
            return doc

    def set_current_stage(self, stage_id: str) -> SessionDocument:
        with _lock_for(self._id):
            doc = self.load_document()
            doc.current_stage = str(stage_id)
            self.save_document(doc)
            return doc

    def set_form_values(self, stage_id: str, values: dict[str, Any]) -> SessionDocument:
        if not stage_id:
            raise SessionError("stage_id is required")
        with _lock_for(self._id):
            doc = self.load_document()
            doc.stage_form_values[str(stage_id)] = dict(values or {})
            self.save_document(doc)
            return doc

    # --- runs ---

    def list_runs(self) -> list[StageRun]:
        with _lock_for(self._id):
            data = _read_json(self.runs_path) or {}
            runs_data = data.get("runs") or []
            return [StageRun.from_dict(r) for r in runs_data if isinstance(r, dict)]

    def append_run(self, run: StageRun) -> StageRun:
        with _lock_for(self._id):
            existing = self.list_runs()
            existing.append(run)
            _atomic_write_json(
                self.runs_path,
                {"schema_version": SCHEMA_VERSION, "runs": [r.to_dict() for r in existing]},
            )
            return run

    def update_run_status(
        self,
        job_id: str,
        *,
        status: str,
        finished_at: str | None = None,
        report_html_path: str | None = None,
        summary_json_path: str | None = None,
    ) -> StageRun | None:
        """Mutate the run identified by job_id. Returns the updated run, or None if not found."""
        with _lock_for(self._id):
            existing = self.list_runs()
            updated: StageRun | None = None
            new_runs: list[StageRun] = []
            for run in existing:
                if run.job_id == job_id:
                    updated = StageRun(
                        stage_id=run.stage_id,
                        job_id=run.job_id,
                        yaml_path=run.yaml_path,
                        report_html_path=report_html_path if report_html_path is not None else run.report_html_path,
                        summary_json_path=summary_json_path if summary_json_path is not None else run.summary_json_path,
                        started_at=run.started_at,
                        finished_at=finished_at if finished_at is not None else run.finished_at,
                        status=str(status),
                    )
                    new_runs.append(updated)
                else:
                    new_runs.append(run)
            if updated is None:
                return None
            _atomic_write_json(
                self.runs_path,
                {"schema_version": SCHEMA_VERSION, "runs": [r.to_dict() for r in new_runs]},
            )
            return updated

    # --- promotions ---

    def list_promotions(
        self,
        *,
        from_stage: str | None = None,
        to_stage: str | None = None,
    ) -> list[Promotion]:
        with _lock_for(self._id):
            data = _read_json(self.promotions_path) or {}
            entries = [
                Promotion.from_dict(p)
                for p in (data.get("promotions") or [])
                if isinstance(p, dict)
            ]
            if from_stage:
                entries = [p for p in entries if p.from_stage == from_stage]
            if to_stage:
                entries = [p for p in entries if p.to_stage == to_stage]
            return entries

    def append_promotion(self, promotion: Promotion) -> Promotion:
        with _lock_for(self._id):
            existing = self.list_promotions()
            existing.append(promotion)
            _atomic_write_json(
                self.promotions_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "promotions": [p.to_dict() for p in existing],
                },
            )
            return promotion

    # --- stage yaml files ---

    def write_stage_yaml(self, stage_id: str, yaml_text: str) -> Path:
        """Write a generated stage YAML and return the path. The filename
        embeds a UTC timestamp so successive runs of the same stage don't
        clobber each other."""
        with _lock_for(self._id):
            self.stage_yaml_dir.mkdir(parents=True, exist_ok=True)
            ts = _filename_timestamp()
            target = self.stage_yaml_dir / f"{stage_id}_{ts}.yaml"
            target.write_text(yaml_text, encoding="utf-8")
            return target

    # --- summaries ---

    def summary(self) -> dict[str, Any]:
        """Lightweight session summary for /api/discovery/sessions list."""
        try:
            doc = self.load_document()
        except SessionError:
            return {
                "session_id": self._id,
                "label": "(unreadable)",
                "created_at": "",
                "updated_at": "",
                "current_stage": "",
                "instrument_symbol": "",
                "run_count": 0,
            }
        return {
            "session_id": doc.session_id,
            "label": doc.label,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "current_stage": doc.current_stage,
            "instrument_symbol": doc.instrument.symbol,
            "run_count": len(self.list_runs()),
        }


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def create_session(
    *,
    label: str = "",
    instrument_symbol: str = "NQ",
    context: ProjectContext | dict[str, Any] | None = None,
    current_stage: str = "",
) -> DiscoverySession:
    """Create a new session and persist its initial documents."""
    session_id = _new_session_id()
    session_dir = get_storage_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(context, dict):
        ctx = ProjectContext.from_dict(context)
    elif isinstance(context, ProjectContext):
        ctx = context
    else:
        ctx = ProjectContext()

    now = _now_iso()
    doc = SessionDocument(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        created_at=now,
        updated_at=now,
        label=str(label or ""),
        instrument=SessionInstrument.from_symbol(instrument_symbol),
        context=ctx,
        current_stage=str(current_stage or ""),
        stage_form_values={},
    )

    session = DiscoverySession(session_dir)
    session.save_document(doc)
    # Initialize empty companion files so the directory layout is complete
    _atomic_write_json(session.runs_path, {"schema_version": SCHEMA_VERSION, "runs": []})
    _atomic_write_json(
        session.promotions_path,
        {"schema_version": SCHEMA_VERSION, "promotions": []},
    )
    return session


def get_session(session_id: str) -> DiscoverySession | None:
    if not session_id:
        return None
    session_dir = get_storage_root() / str(session_id).strip()
    if not session_dir.exists() or not session_dir.is_dir():
        return None
    if not (session_dir / DiscoverySession.SESSION_FILENAME).exists():
        return None
    return DiscoverySession(session_dir)


def require_session(session_id: str) -> DiscoverySession:
    session = get_session(session_id)
    if session is None:
        raise SessionNotFoundError(f"No session: {session_id}")
    return session


def list_sessions() -> list[dict[str, Any]]:
    """Return summaries for every session under the current storage root,
    most recently updated first."""
    root = get_storage_root()
    if not root.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not (child / DiscoverySession.SESSION_FILENAME).exists():
            continue
        summaries.append(DiscoverySession(child).summary())
    summaries.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return summaries


def delete_session(session_id: str) -> bool:
    """Remove a session directory entirely. Returns False if it did not exist."""
    session_dir = get_storage_root() / str(session_id or "").strip()
    if not session_dir.exists():
        return False
    with _lock_for(session_id):
        _rm_tree(session_dir)
    _drop_lock_for(session_id)
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _new_session_id() -> str:
    return "ses_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _filename_timestamp() -> str:
    """Compact UTC timestamp safe for filenames: 2026-05-04T18-12-07Z"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SessionError(f"Corrupt JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SessionError(f"Expected JSON object at {path}, got {type(data).__name__}")
    return data


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically: stage to a temp file in the same directory,
    fsync, then os.replace into place. Same-directory replace is atomic on
    Windows and POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # Some filesystems / temp dirs don't support fsync; the
                # subsequent os.replace is still atomic enough.
                pass
        os.replace(tmp_name, path)
    except Exception:
        # Best effort: remove the temp file if replace failed
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _rm_tree(path: Path) -> None:
    """Recursively remove a directory. Tolerant of read-only files."""
    for child in path.iterdir():
        if child.is_dir():
            _rm_tree(child)
        else:
            try:
                child.unlink()
            except OSError:
                # On Windows, sometimes files linger; chmod and retry once.
                try:
                    child.chmod(0o600)
                    child.unlink()
                except OSError:
                    pass
    try:
        path.rmdir()
    except OSError:
        pass
