from __future__ import annotations

"""
On-disk persistence for /optimizer web sessions.

Each session is one directory under
    .ta_artifacts/web_optimizer/sessions/<session_id>/

containing:

- session.json   strategy, seed template, parameter config, guardrails
- plan.json      generated chunk plan (combination estimates per chunk)
- chunks/        generated optimizer template XMLs (written by later phases)

Mirrors the design of ``discovery_session`` — atomic writes, per-session
reentrant locks, configurable storage root for tests.

Phase 1 stops at "plan preview"; no NinjaTrader execution or template
generation happens here.
"""

import hashlib
import json
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

_DEFAULT_ROOT = Path(".ta_artifacts") / "web_optimizer" / "sessions"
_root: Path = _DEFAULT_ROOT
_root_lock = threading.Lock()


def set_storage_root(path: Path | str | None) -> None:
    global _root
    with _root_lock:
        _root = Path(path) if path else _DEFAULT_ROOT


def get_storage_root() -> Path:
    with _root_lock:
        return _root


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParameterConfig:
    """One parameter's user-facing config — fixed value or a sweep range.

    Booleans use {min: false, max: true, increment: 1} when swept.
    """
    name: str
    type_name: str
    mode: str  # "fixed" | "optimize"
    fixed_value: Any = None
    minimum: Any = None
    maximum: Any = None
    increment: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParameterConfig":
        mode = str(data.get("mode") or "fixed")
        if mode not in {"fixed", "optimize"}:
            raise OptimizerSessionError(f"Invalid parameter mode: {mode!r}")
        return cls(
            name=str(data["name"]),
            type_name=str(data.get("type_name") or ""),
            mode=mode,
            fixed_value=data.get("fixed_value"),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            increment=data.get("increment"),
        )


@dataclass
class Guardrails:
    max_drawdown_dollars: float | None = None
    min_trades: int | None = None
    min_percent_days_traded: float | None = None
    min_profit_factor: float | None = None
    min_net_profit: float | None = None
    max_trades_per_day: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Guardrails":
        data = data or {}
        def _num(key: str) -> float | None:
            value = data.get(key)
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _int(key: str) -> int | None:
            value = data.get(key)
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        return cls(
            max_drawdown_dollars=_num("max_drawdown_dollars"),
            min_trades=_int("min_trades"),
            min_percent_days_traded=_num("min_percent_days_traded"),
            min_profit_factor=_num("min_profit_factor"),
            min_net_profit=_num("min_net_profit"),
            max_trades_per_day=_int("max_trades_per_day"),
        )


@dataclass
class ChunkingConfig:
    max_combinations_per_chunk: int = 5000
    max_runtime_minutes_per_chunk: int | None = None
    keep_best_results: int = 500

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ChunkingConfig":
        data = data or {}
        try:
            max_combo = int(data.get("max_combinations_per_chunk") or 5000)
        except (TypeError, ValueError):
            max_combo = 5000
        runtime_raw = data.get("max_runtime_minutes_per_chunk")
        try:
            max_runtime = int(runtime_raw) if runtime_raw not in (None, "") else None
        except (TypeError, ValueError):
            max_runtime = None
        keep_raw = data.get("keep_best_results")
        try:
            keep_best = int(keep_raw) if keep_raw not in (None, "") else 500
        except (TypeError, ValueError):
            keep_best = 500
        return cls(
            max_combinations_per_chunk=max(1, max_combo),
            max_runtime_minutes_per_chunk=max_runtime,
            keep_best_results=max(1, keep_best),
        )


@dataclass
class OptimizerSessionDocument:
    schema_version: int
    session_id: str
    created_at: str
    updated_at: str
    label: str
    strategy_id: str
    seed_template_path: str
    instrument: str
    market_suffix: str
    parameters: list[ParameterConfig] = field(default_factory=list)
    guardrails: Guardrails = field(default_factory=Guardrails)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    backtest_seed_template_path: str = ""
    oos_from_date: str = ""
    oos_to_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "label": self.label,
            "strategy_id": self.strategy_id,
            "seed_template_path": self.seed_template_path,
            "instrument": self.instrument,
            "market_suffix": self.market_suffix,
            "parameters": [p.to_dict() for p in self.parameters],
            "guardrails": self.guardrails.to_dict(),
            "chunking": self.chunking.to_dict(),
            "backtest_seed_template_path": self.backtest_seed_template_path,
            "oos_from_date": self.oos_from_date,
            "oos_to_date": self.oos_to_date,
        }

    def plan_hash(self) -> str:
        """Stable hash of the inputs that define an optimization plan.

        Two sessions with the same hash are running the same search and
        should be candidates for result reuse.
        """
        payload = {
            "strategy_id": self.strategy_id,
            "seed_template_path": self.seed_template_path,
            "instrument": self.instrument,
            "parameters": [
                {
                    "name": p.name,
                    "type_name": p.type_name,
                    "mode": p.mode,
                    "fixed_value": str(p.fixed_value) if p.fixed_value is not None else "",
                    "minimum": str(p.minimum) if p.minimum not in (None, "") else "",
                    "maximum": str(p.maximum) if p.maximum not in (None, "") else "",
                    "increment": str(p.increment) if p.increment not in (None, "") else "",
                }
                for p in sorted(self.parameters, key=lambda x: x.name)
            ],
            "guardrails": self.guardrails.to_dict(),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptimizerSessionDocument":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise OptimizerSessionSchemaError(
                f"Optimizer session has schema_version {version!r}, expected {SCHEMA_VERSION}"
            )
        return cls(
            schema_version=int(version),
            session_id=str(data["session_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            label=str(data.get("label") or ""),
            strategy_id=str(data.get("strategy_id") or ""),
            seed_template_path=str(data.get("seed_template_path") or ""),
            instrument=str(data.get("instrument") or ""),
            market_suffix=str(data.get("market_suffix") or "NQ"),
            parameters=[ParameterConfig.from_dict(p) for p in (data.get("parameters") or [])],
            guardrails=Guardrails.from_dict(data.get("guardrails")),
            chunking=ChunkingConfig.from_dict(data.get("chunking")),
            backtest_seed_template_path=str(data.get("backtest_seed_template_path") or ""),
            oos_from_date=str(data.get("oos_from_date") or ""),
            oos_to_date=str(data.get("oos_to_date") or ""),
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OptimizerSessionError(Exception):
    pass


class OptimizerSessionNotFoundError(OptimizerSessionError):
    pass


class OptimizerSessionSchemaError(OptimizerSessionError):
    pass


# ---------------------------------------------------------------------------
# Per-session locks
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
# Session wrapper
# ---------------------------------------------------------------------------

class OptimizerSession:
    SESSION_FILENAME = "session.json"
    PLAN_FILENAME = "plan.json"

    def __init__(self, session_dir: Path) -> None:
        self._dir = session_dir
        self._id = session_dir.name

    @property
    def id(self) -> str:
        return self._id

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def session_path(self) -> Path:
        return self._dir / self.SESSION_FILENAME

    @property
    def plan_path(self) -> Path:
        return self._dir / self.PLAN_FILENAME

    def load_document(self) -> OptimizerSessionDocument:
        with _lock_for(self._id):
            data = _read_json(self.session_path)
            if data is None:
                raise OptimizerSessionNotFoundError(f"No session at {self._dir}")
            return OptimizerSessionDocument.from_dict(data)

    def save_document(self, doc: OptimizerSessionDocument) -> None:
        if doc.session_id != self._id:
            raise OptimizerSessionError(
                f"Session id mismatch: doc says {doc.session_id}, dir is {self._id}"
            )
        with _lock_for(self._id):
            doc.updated_at = _now_iso()
            self._dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self.session_path, doc.to_dict())

    def update(self, **patch: Any) -> OptimizerSessionDocument:
        """Apply a sparse update to the session document. Unknown keys are
        ignored. ``parameters`` and ``guardrails`` and ``chunking`` accept
        their dict forms."""
        with _lock_for(self._id):
            doc = self.load_document()
            simple_keys = {
                "label", "strategy_id", "seed_template_path",
                "instrument", "market_suffix",
                "backtest_seed_template_path", "oos_from_date", "oos_to_date",
            }
            for key, value in patch.items():
                if key in simple_keys:
                    setattr(doc, key, str(value or ""))
                elif key == "parameters" and isinstance(value, list):
                    doc.parameters = [ParameterConfig.from_dict(p) for p in value]
                elif key == "guardrails" and isinstance(value, dict):
                    doc.guardrails = Guardrails.from_dict(value)
                elif key == "chunking" and isinstance(value, dict):
                    doc.chunking = ChunkingConfig.from_dict(value)
            self.save_document(doc)
            return doc

    def save_plan(self, plan: dict[str, Any]) -> None:
        with _lock_for(self._id):
            self._dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self.plan_path, plan)

    def load_plan(self) -> dict[str, Any] | None:
        with _lock_for(self._id):
            return _read_json(self.plan_path)

    def summary(self) -> dict[str, Any]:
        try:
            doc = self.load_document()
        except OptimizerSessionError:
            return {
                "session_id": self._id,
                "label": "(unreadable)",
                "created_at": "",
                "updated_at": "",
                "strategy_id": "",
            }
        plan = self.load_plan() or {}
        manifest = self._load_manifest()
        return {
            "session_id": doc.session_id,
            "label": doc.label,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "strategy_id": doc.strategy_id,
            "seed_template_path": doc.seed_template_path,
            "instrument": doc.instrument,
            "chunk_count": len(plan.get("chunks") or []),
            "combination_estimate": int(plan.get("combination_estimate") or 0),
            "decision_state": manifest.get("decision_state"),
            "final_validation_status": manifest.get("final_validation_status"),
            "final_recommendation_count": int(manifest.get("final_recommendation_count") or 0),
            "phase2_template_count": int(manifest.get("phase2_refinement_template_count") or 0),
            "phase3_template_count": int(manifest.get("phase3_risk_template_count") or 0),
            "final_template_count": int(manifest.get("final_backtest_template_count") or 0),
        }

    def _load_manifest(self) -> dict[str, Any]:
        manifest_path = self._dir / "deployment_package" / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def create_session(
    *,
    label: str = "",
    strategy_id: str = "",
    seed_template_path: str = "",
    instrument: str = "NQ",
    market_suffix: str = "NQ",
) -> OptimizerSession:
    session_id = "opt_" + secrets.token_hex(6)
    session_dir = get_storage_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    doc = OptimizerSessionDocument(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        created_at=now,
        updated_at=now,
        label=label or "",
        strategy_id=strategy_id or "",
        seed_template_path=seed_template_path or "",
        instrument=instrument or "",
        market_suffix=market_suffix or "NQ",
    )
    session = OptimizerSession(session_dir)
    session.save_document(doc)
    return session


def clone_session(
    source: OptimizerSession,
    *,
    label: str | None = None,
) -> OptimizerSession:
    """Create a fresh session that inherits the source's strategy, seed,
    parameter config, guardrails, chunking, instrument, and OOS dates.

    Plan, generated templates, NT outputs, and the deployment package are
    NOT copied — the clone starts at "configure & run". This is the entry
    point for Phase 6 refinement: clone a session, narrow specific
    parameter ranges, and run the refined plan.
    """
    src_doc = source.load_document()
    new_session_id = "opt_" + secrets.token_hex(6)
    new_dir = get_storage_root() / new_session_id
    new_dir.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    new_label = label if label is not None else (src_doc.label + " (refined)" if src_doc.label else "refined")
    new_doc = OptimizerSessionDocument(
        schema_version=SCHEMA_VERSION,
        session_id=new_session_id,
        created_at=now,
        updated_at=now,
        label=new_label,
        strategy_id=src_doc.strategy_id,
        seed_template_path=src_doc.seed_template_path,
        instrument=src_doc.instrument,
        market_suffix=src_doc.market_suffix,
        parameters=[ParameterConfig.from_dict(p.to_dict()) for p in src_doc.parameters],
        guardrails=Guardrails.from_dict(src_doc.guardrails.to_dict()),
        chunking=ChunkingConfig.from_dict(src_doc.chunking.to_dict()),
        backtest_seed_template_path=src_doc.backtest_seed_template_path,
        oos_from_date=src_doc.oos_from_date,
        oos_to_date=src_doc.oos_to_date,
    )
    new_session = OptimizerSession(new_dir)
    new_session.save_document(new_doc)
    return new_session


def get_session(session_id: str) -> OptimizerSession | None:
    if not session_id:
        return None
    session_dir = get_storage_root() / str(session_id).strip()
    if not session_dir.exists() or not session_dir.is_dir():
        return None
    if not (session_dir / OptimizerSession.SESSION_FILENAME).exists():
        return None
    return OptimizerSession(session_dir)


def require_session(session_id: str) -> OptimizerSession:
    session = get_session(session_id)
    if session is None:
        raise OptimizerSessionNotFoundError(f"No optimizer session: {session_id}")
    return session


def list_sessions() -> list[dict[str, Any]]:
    root = get_storage_root()
    if not root.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not (child / OptimizerSession.SESSION_FILENAME).exists():
            continue
        summaries.append(OptimizerSession(child).summary())
    summaries.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return summaries


def delete_session(session_id: str) -> bool:
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

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise OptimizerSessionError(f"Corrupt JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OptimizerSessionError(f"Expected JSON object at {path}, got {type(data).__name__}")
    return data


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _rm_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _rm_tree(child)
        else:
            try:
                child.unlink()
            except OSError:
                try:
                    child.chmod(0o600)
                    child.unlink()
                except OSError:
                    pass
    try:
        path.rmdir()
    except OSError:
        pass
