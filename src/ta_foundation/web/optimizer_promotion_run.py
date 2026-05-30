from __future__ import annotations

"""NinjaTrader RunBatch dispatch + completion pipeline for promoted shortlist rows.

The recipe runner already knows how to talk to the AddOn at
``C:\\temp\\nt8_command.json`` — we mirror that contract here for the
promoted batch, but keep separate persistence (``promoted_run.json``) so
we don't trample the active recipe run state.

Lifecycle::

    start_promoted_run(session)
        → writes RunBatch command pointing at generated_templates/promoted/
        → state = "requested"
    advance_promoted_run(session)            (called by the UI poll)
        → reads nt8_status.json + counts Summary.csv files
        → state stays "running" until expected templates land
        → on complete: runs load_promoted_results + build_all_candidate_reports
        → state = "complete"
    cancel_promoted_run(session)
        → removes the command file if it still points at our run
        → state = "cancelled"

No subprocess is spawned; the AddOn must already be loaded (i.e., NT
running with the BatchStrategyOptimizerAddOn authorized). See
``NINJATRADER_INTEGRATION_RUNBOOK.md`` for the prerequisite checklist.
"""

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_promotion import (
    PROMOTED_DIRNAME,
    PROMOTED_MANIFEST_FILENAME,
)
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    _command_instrument_for,
    _timeout_seconds_for,
)
from ta_foundation.web.optimizer_session import OptimizerSession


PROMOTED_RUN_FILENAME = "promoted_run.json"
TERMINAL_STATES = frozenset({"complete", "failed", "cancelled"})


class PromotionRunError(Exception):
    pass


@dataclass
class PromotedRunRecord:
    run_id: str
    state: str  # "requested" | "running" | "complete" | "failed" | "cancelled"
    started_at: str
    finished_at: str | None = None
    source_folder: str = ""
    dest_folder: str = ""
    command_file: str = ""
    status_file: str = ""
    total_templates: int = 0
    completed_templates: int = 0
    last_error: str | None = None
    review_dir: str | None = None
    evaluated_candidates_path: str | None = None
    report_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromotedRunRecord":
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
            completed_templates=int(data.get("completed_templates") or 0),
            last_error=data.get("last_error"),
            review_dir=data.get("review_dir"),
            evaluated_candidates_path=data.get("evaluated_candidates_path"),
            report_count=int(data.get("report_count") or 0),
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def start_promoted_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
    status_file: Path | None = None,
    now: datetime | None = None,
) -> PromotedRunRecord:
    """Write the RunBatch command for ``generated_templates/promoted/``.

    Wipes ``nt_output/promoted/`` first so a re-run produces a clean
    surface for the parse step. The AddOn picks the command up by
    polling its watch directory.
    """
    _ensure_no_conflicting_run(session)

    source = (session.directory / "generated_templates" / PROMOTED_DIRNAME).resolve()
    xmls = sorted(source.glob("*.xml"))
    if not xmls:
        raise PromotionRunError(
            f"No promoted templates to run. Stamp some via promote_pending first ({source})."
        )

    dest = (session.directory / "nt_output" / PROMOTED_DIRNAME).resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    cmd_path = Path(command_file) if command_file else DEFAULT_COMMAND_FILE
    status_path = Path(status_file) if status_file else DEFAULT_STATUS_FILE
    moment = now or datetime.now(timezone.utc)
    run_id = "promoted_" + moment.strftime("%Y%m%d_%H%M%S")

    # Clear any stale status so polling sees the next AddOn heartbeat.
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
        # Promoted batches can be dozens of templates wide; without this
        # the Strategy Analyzer accumulates per-template tabs and OOMs.
        "closeTempTabs": True,
    }
    timeout_seconds = _timeout_seconds_for(session)
    if timeout_seconds:
        payload["timeoutSeconds"] = timeout_seconds
    instrument = _command_instrument_for(session)
    if instrument:
        payload["instrument"] = instrument

    _write_command_file_atomic(cmd_path, payload)
    record = PromotedRunRecord(
        run_id=run_id,
        state="requested",
        started_at=moment.isoformat(timespec="microseconds"),
        source_folder=str(source),
        dest_folder=str(dest),
        command_file=str(cmd_path),
        status_file=str(status_path),
        total_templates=len(xmls),
    )
    save_promoted_run(session, record)
    return record


def _ensure_no_conflicting_run(session: OptimizerSession) -> None:
    """Refuse to dispatch while a recipe stage run is still live.

    Both pipelines write the same NT command file (``C:\\temp\\nt8_command.json``),
    so starting a promoted run mid-recipe would clobber the recipe's command and
    point NinjaTrader at the wrong templates. This guard is scoped to the
    session's own run records; cross-session collisions on the shared bridge
    still need a global lock (tracked separately).
    """
    try:
        from ta_foundation.web.optimizer_recipe_runner import load_recipe_run
    except ImportError:  # pragma: no cover - recipe runner always present today
        return
    record = load_recipe_run(session)
    if record is None:
        return
    if record.state not in {"completed", "cancelled", "failed"}:
        raise PromotionRunError(
            f"A recipe stage run is active (stage={record.stage_id!r}, "
            f"state={record.state!r}). Wait for it to finish or cancel it "
            "before promoting — both share the NinjaTrader command file."
        )


# ---------------------------------------------------------------------------
# Poll + complete
# ---------------------------------------------------------------------------

def advance_promoted_run(session: OptimizerSession) -> PromotedRunRecord | None:
    """Polled by the UI. Drives the run from ``requested`` → ``complete``.

    Checks the AddOn's status file first (authoritative when present and
    fresh), falls back to counting completed Summary.csv files under the
    dest folder. On terminal completion, runs the parse + reports
    pipeline and writes the final state into ``promoted_run.json``.
    """
    record = load_promoted_run(session)
    if record is None:
        return None
    if record.state in TERMINAL_STATES:
        return record

    expected_total = max(record.total_templates, 0)
    completed = _count_completed_templates(Path(record.dest_folder))
    record.completed_templates = completed

    addon_state, addon_error = _read_addon_state(Path(record.status_file), record.run_id)

    declared_complete = addon_state in {"complete", "completed", "success"}
    output_complete = expected_total > 0 and completed >= expected_total

    if addon_state in {"failed", "error"}:
        record.state = "failed"
        record.last_error = addon_error or "AddOn reported failure"
        record.finished_at = _now_iso()
        save_promoted_run(session, record)
        return record

    if addon_state in {"cancelled", "canceled"}:
        record.state = "cancelled"
        record.finished_at = _now_iso()
        save_promoted_run(session, record)
        return record

    if declared_complete or output_complete:
        try:
            _run_post_completion_pipeline(session, record)
            record.state = "complete"
            record.finished_at = _now_iso()
        except Exception as exc:  # noqa: BLE001 — never poison the UI
            record.state = "failed"
            record.last_error = f"post-run pipeline failed: {exc}"
            record.finished_at = _now_iso()
        save_promoted_run(session, record)
        return record

    # Still running — promote "requested" → "running" once we see any
    # heartbeat or first Summary.csv land.
    if record.state == "requested" and (addon_state == "running" or completed > 0):
        record.state = "running"
    save_promoted_run(session, record)
    return record


def cancel_promoted_run(
    session: OptimizerSession,
    *,
    reason: str = "Promoted run cancelled by operator.",
    now: datetime | None = None,
) -> PromotedRunRecord | None:
    """Remove the IPC command (if it still points at our run) and mark
    the persisted record cancelled. Safe to call when no run is active."""
    record = load_promoted_run(session)
    if record is None:
        return None
    cmd_path = Path(record.command_file) if record.command_file else DEFAULT_COMMAND_FILE
    _remove_command_file_for_run(cmd_path, run_id=record.run_id)
    if record.state not in TERMINAL_STATES:
        record.state = "cancelled"
        record.finished_at = (now or datetime.now(timezone.utc)).isoformat(timespec="microseconds")
        record.last_error = reason
        save_promoted_run(session, record)
    return record


# ---------------------------------------------------------------------------
# Post-completion pipeline
# ---------------------------------------------------------------------------

def _run_post_completion_pipeline(
    session: OptimizerSession, record: PromotedRunRecord
) -> None:
    """Mirror NT output, write the promoted review, render per-candidate HTML.

    Imports are local so a session that never promoted anything doesn't
    pay the cost. Failures bubble up to the caller, which writes them
    into ``record.last_error`` and marks the run failed.
    """
    from ta_foundation.web.optimizer_promotion_results import load_promoted_results
    from ta_foundation.web.optimizer_candidate_report import build_all_candidate_reports

    results = load_promoted_results(session)
    record.review_dir = results.review_dir
    record.evaluated_candidates_path = results.evaluated_candidates_path

    batch = build_all_candidate_reports(session)
    # Count only HTMLs that came from this batch's run_ids; the batch
    # output also includes finalists that share the per_candidate_reports
    # folder, so we filter for P_NNN entries.
    record.report_count = sum(
        1 for item in batch.per_candidate
        if (item.html_path or "") and str(getattr(item, "run_id", "")).startswith("P_")
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_promoted_run(session: OptimizerSession) -> PromotedRunRecord | None:
    path = session.directory / PROMOTED_RUN_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return PromotedRunRecord.from_dict(payload)


def save_promoted_run(session: OptimizerSession, record: PromotedRunRecord) -> None:
    _atomic_write_json(session.directory / PROMOTED_RUN_FILENAME, record.to_dict())


# ---------------------------------------------------------------------------
# IPC helpers (mirror optimizer_recipe_runner internals)
# ---------------------------------------------------------------------------

def _read_addon_state(
    path: Path, run_id: str
) -> tuple[str, str | None]:
    """Return ``(state, error)`` from the AddOn status file, scoped to our
    run_id. ``("unknown", None)`` when the file is missing, malformed,
    or belongs to a different run."""
    if not path.exists():
        return "unknown", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown", None
    if not isinstance(payload, dict):
        return "unknown", None
    if str(payload.get("runId") or "") != run_id:
        return "unknown", None
    state = str(payload.get("state") or "").strip().lower()
    error = payload.get("error") or payload.get("message")
    return state or "unknown", str(error) if error else None


def _count_completed_templates(dest: Path) -> int:
    if not dest.exists():
        return 0
    return sum(
        1 for path in dest.rglob("Summary.csv") if path.is_file()
    )


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


def _write_command_file_atomic(path: Path, payload: dict[str, Any]) -> None:
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
        # Bump mtime so the AddOn's filesystem watcher sees the change
        # even on platforms where atomic replace preserves timestamps.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# Manifest sanity check (exposed for tests + UI)
# ---------------------------------------------------------------------------

def expected_promoted_template_ids(session: OptimizerSession) -> list[str]:
    manifest_path = (
        session.directory / "generated_templates" / PROMOTED_DIRNAME / PROMOTED_MANIFEST_FILENAME
    )
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    templates = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(templates, list):
        return []
    return sorted(
        str(item.get("template_id") or "")
        for item in templates
        if isinstance(item, dict) and item.get("template_id")
    )
