from __future__ import annotations

"""Stage-specific NinjaTrader RunBatch bridge for recipe mode."""

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    BridgeBusyError,
    _command_instrument_for,
    _timeout_seconds_for,
    ensure_bridge_available,
)
from ta_foundation.web.optimizer_session import OptimizerSession


GENERATED_DIRNAME = "generated_templates"
NT_OUTPUT_DIRNAME = "nt_output"
RECIPE_RUN_FILENAME = "recipe_run.json"
RECIPE_RUN_HISTORY_FILENAME = "recipe_run_history.json"


class RecipeRunnerError(Exception):
    pass


@dataclass
class RecipeStageRunRecord:
    run_id: str
    stage_id: str
    state: str
    started_at: str
    finished_at: str | None = None
    source_folder: str = ""
    dest_folder: str = ""
    command_file: str = ""
    status_file: str = ""
    total_templates: int = 0
    timeout_seconds: int | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipeStageRunRecord":
        return cls(
            run_id=str(data.get("run_id") or ""),
            stage_id=str(data.get("stage_id") or ""),
            state=str(data.get("state") or "requested"),
            started_at=str(data.get("started_at") or ""),
            finished_at=data.get("finished_at"),
            source_folder=str(data.get("source_folder") or ""),
            dest_folder=str(data.get("dest_folder") or ""),
            command_file=str(data.get("command_file") or ""),
            status_file=str(data.get("status_file") or ""),
            total_templates=int(data.get("total_templates") or 0),
            timeout_seconds=_optional_int(data.get("timeout_seconds")),
            last_error=data.get("last_error"),
        )


def start_recipe_stage_run(
    session: OptimizerSession,
    *,
    stage_id: str,
    command_file: Path | None = None,
    status_file: Path | None = None,
    now: datetime | None = None,
) -> RecipeStageRunRecord:
    _ensure_no_conflicting_promoted_run(session)

    cmd_path = Path(command_file) if command_file else DEFAULT_COMMAND_FILE
    status_path = Path(status_file) if status_file else DEFAULT_STATUS_FILE
    # Cross-session/cross-process guard: refuse before clearing artifacts if a
    # different live run still owns the shared NT command file.
    try:
        ensure_bridge_available(cmd_path, status_path)
    except BridgeBusyError as exc:
        raise RecipeRunnerError(str(exc)) from exc

    source = (session.directory / GENERATED_DIRNAME / stage_id).resolve()
    xmls = sorted(source.glob("*.xml"))
    if not xmls:
        raise RecipeRunnerError(f"No generated recipe templates in {source}.")
    dest = (session.directory / NT_OUTPUT_DIRNAME / stage_id).resolve()
    _clear_previous_stage_artifacts(session, stage_id=stage_id, dest=dest)
    dest.mkdir(parents=True, exist_ok=True)

    moment = now or datetime.now(timezone.utc)
    run_id = f"recipe_{stage_id}_" + moment.strftime("%Y%m%d_%H%M%S")

    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    payload: dict[str, Any] = {
        "action": "RunBatch",
        "runId": run_id,
        "sourceFolder": str(source),
        "destFolder": str(dest),
        # Force the AddOn to close each per-template tab after the run.
        # A recipe stage may launch hundreds of templates; leaving tabs open
        # exhausts Strategy Analyzer memory and crashes NinjaTrader.
        "closeTempTabs": True,
    }
    timeout_seconds = _timeout_seconds_for(session)
    if timeout_seconds:
        payload["timeoutSeconds"] = timeout_seconds
    instrument = _command_instrument_for(session)
    if instrument:
        payload["instrument"] = instrument

    _write_command_file(cmd_path, payload)
    record = RecipeStageRunRecord(
        run_id=run_id,
        stage_id=stage_id,
        state="requested",
        started_at=moment.isoformat(timespec="microseconds"),
        source_folder=str(source),
        dest_folder=str(dest),
        command_file=str(cmd_path),
        status_file=str(status_path),
        total_templates=len(xmls),
        timeout_seconds=timeout_seconds,
    )
    save_recipe_run(session, record)
    return record


def _ensure_no_conflicting_promoted_run(session: OptimizerSession) -> None:
    """Refuse to dispatch a recipe stage while a promoted run is still live.

    Mirror of the guard in ``optimizer_promotion_run``: both pipelines share
    the NinjaTrader command file, so the two must not be in flight at once.
    The promotion module is optional (in-flight feature) — import lazily and
    skip the check if it isn't present on this checkout.
    """
    try:
        from ta_foundation.web.optimizer_promotion_run import (
            TERMINAL_STATES,
            load_promoted_run,
        )
    except ImportError:
        return
    record = load_promoted_run(session)
    if record is None:
        return
    if record.state not in TERMINAL_STATES:
        raise RecipeRunnerError(
            f"A promoted run is active (state={record.state!r}). Wait for it to "
            "finish or cancel it before starting a recipe stage — both share the "
            "NinjaTrader command file."
        )


def load_recipe_run(session: OptimizerSession) -> RecipeStageRunRecord | None:
    path = session.directory / RECIPE_RUN_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return RecipeStageRunRecord.from_dict(payload)


def load_recipe_run_history(session: OptimizerSession) -> list[RecipeStageRunRecord]:
    path = session.directory / RECIPE_RUN_HISTORY_FILENAME
    if not path.exists():
        current = load_recipe_run(session)
        return [current] if current else []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("runs") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    history: list[RecipeStageRunRecord] = []
    for item in rows:
        if isinstance(item, dict):
            history.append(RecipeStageRunRecord.from_dict(item))
    return history


def save_recipe_run(session: OptimizerSession, record: RecipeStageRunRecord) -> None:
    _atomic_write_json(session.directory / RECIPE_RUN_FILENAME, record.to_dict())
    _upsert_recipe_run_history(session, record)


def cancel_recipe_stage_run(
    session: OptimizerSession,
    *,
    reason: str = "Recipe run cancelled.",
    now: datetime | None = None,
) -> RecipeStageRunRecord | None:
    """Mark the active recipe stage run cancelled and remove its pending NT command.

    Recipe mode submits work through the shared ``nt8_command.json`` bridge. A
    stop request must therefore update both the durable recipe run record and,
    when the command file still points at the same run id, remove the command so
    NinjaTrader does not pick up stale work later.
    """
    record = load_recipe_run(session)
    if record is None:
        return None

    cmd_path = Path(record.command_file) if record.command_file else DEFAULT_COMMAND_FILE
    _remove_command_file_for_run(cmd_path, run_id=record.run_id)

    terminal_states = {"completed", "cancelled", "failed"}
    if record.state not in terminal_states:
        record.state = "cancelled"
        record.finished_at = (now or datetime.now(timezone.utc)).isoformat(timespec="microseconds")
        record.last_error = reason
        save_recipe_run(session, record)
    return record


def _clear_previous_stage_artifacts(session: OptimizerSession, *, stage_id: str, dest: Path) -> None:
    session_dir = session.directory.resolve()
    targets = [
        dest,
        (session.directory / "parsed_results" / stage_id).resolve(),
    ]
    if stage_id == "final_backtest":
        handoff = session.directory / "deployment_package" / "final_backtest_handoff"
        targets.extend([
            (handoff / "nt8_backtest_results").resolve(),
            (handoff / "final_backtest_review").resolve(),
        ])
    elif stage_id == "stage_1":
        targets.extend([
            (session.directory / "recipe_selection.csv").resolve(),
            (session.directory / "recipe_selection.json").resolve(),
        ])

    for target in targets:
        if not _is_within(target, session_dir) or not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()


def _remove_command_file_for_run(path: Path, *, run_id: str) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict) or str(payload.get("runId") or "") != run_id:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _upsert_recipe_run_history(session: OptimizerSession, record: RecipeStageRunRecord) -> None:
    history = load_recipe_run_history(session)
    replaced = False
    for index, existing in enumerate(history):
        if existing.run_id == record.run_id:
            history[index] = record
            replaced = True
            break
    if not replaced:
        history.append(record)
    payload = {
        "schema_version": 1,
        "runs": [item.to_dict() for item in history],
    }
    _atomic_write_json(session.directory / RECIPE_RUN_HISTORY_FILENAME, payload)


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


def _atomic_write_json(path: Path, payload: Any) -> None:
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
