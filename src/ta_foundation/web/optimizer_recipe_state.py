from __future__ import annotations

"""Durable state and event log for Recipe/Matrix optimizer runs."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


RECIPE_STATE_FILENAME = "recipe_state.json"
RECIPE_EVENTS_FILENAME = "recipe_events.jsonl"


@dataclass
class RecipeRunState:
    recipe_id: str
    state: str
    current_stage_id: str | None = None
    current_template_id: str | None = None
    pause_requested: bool = False
    stop_requested: bool = False
    last_error: str | None = None
    updated_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeRunState":
        return cls(
            recipe_id=str(data.get("recipe_id") or ""),
            state=str(data.get("state") or "draft"),
            current_stage_id=data.get("current_stage_id"),
            current_template_id=data.get("current_template_id"),
            pause_requested=bool(data.get("pause_requested") or False),
            stop_requested=bool(data.get("stop_requested") or False),
            last_error=data.get("last_error"),
            updated_at=str(data.get("updated_at") or _now_iso()),
        )


def load_recipe_state(session: OptimizerSession) -> RecipeRunState | None:
    path = session.directory / RECIPE_STATE_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return RecipeRunState.from_dict(payload)


def save_recipe_state(session: OptimizerSession, state: RecipeRunState) -> None:
    state.updated_at = _now_iso()
    _atomic_write_json(session.directory / RECIPE_STATE_FILENAME, state.to_dict())


def append_recipe_event(
    session: OptimizerSession,
    *,
    event_type: str,
    message: str,
    recipe_id: str = "",
    stage_id: str | None = None,
    template_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "timestamp": _now_iso(),
        "event_type": event_type,
        "recipe_id": recipe_id,
        "stage_id": stage_id,
        "template_id": template_id,
        "message": message,
        "details": details or {},
    }
    path = session.directory / RECIPE_EVENTS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def load_recipe_events(session: OptimizerSession, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = session.directory / RECIPE_EVENTS_FILENAME
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if limit is not None:
        lines = lines[-max(0, limit):]
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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

