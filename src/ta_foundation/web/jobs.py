from __future__ import annotations

import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PopenFactory = Callable[..., subprocess.Popen]


@dataclass(frozen=True)
class GeneratedArtifact:
    path: str
    kind: str
    label: str
    is_directory: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "label": self.label,
            "is_directory": self.is_directory,
        }


@dataclass
class JobRecord:
    id: str
    kind: str
    command: list[str]
    status: str = "queued"
    returncode: int | None = None
    output: str = ""
    error: str | None = None
    artifacts: list[GeneratedArtifact] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _now_iso())
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "command": list(self.command),
            "status": self.status,
            "returncode": self.returncode,
            "output": self.output,
            "error": self.error,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    """Small in-process job registry for local web-triggered CLI runs.

    Captures stdout (with stderr merged in) line-by-line so the UI can poll
    /api/jobs/<id>/log and tail output while the job is still running.
    Falls back to .communicate() when the Popen-like has no .stdout attribute,
    so test fakes that only implement communicate() keep working.

    cancel(job_id) terminates the running subprocess; the job ends in status
    'cancelled' rather than 'failed'.
    """

    def __init__(
        self,
        *,
        popen_factory: PopenFactory | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._popen_factory = popen_factory or subprocess.Popen
        self._cwd = str(cwd) if cwd is not None else None
        self._active_procs: dict[str, Any] = {}
        self._cancelled: set[str] = set()

    def start(self, *, kind: str, command: list[str]) -> JobRecord:
        job = JobRecord(id=uuid.uuid4().hex, kind=kind, command=list(command))
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._run, args=(job.id,), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running job. Returns True if the request
        was accepted (proc was terminated), False if there's no live proc."""
        with self._lock:
            proc = self._active_procs.get(job_id)
            job = self._jobs.get(job_id)
            if not proc or not job:
                return False
            if job.status in ("succeeded", "failed", "cancelled"):
                return False
            self._cancelled.add(job_id)
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                return False
        return True

    def is_allowed_artifact(self, path: str | Path) -> bool:
        requested = _resolve_path(path)
        if requested is None:
            return False
        with self._lock:
            artifacts = [artifact for job in self._jobs.values() for artifact in job.artifacts]
        for artifact in artifacts:
            allowed = _resolve_path(artifact.path)
            if allowed is None:
                continue
            if artifact.is_directory:
                try:
                    requested.relative_to(allowed)
                    return True
                except ValueError:
                    continue
            if requested == allowed:
                return True
        return False

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now_iso()
        try:
            proc = self._popen_factory(
                job.command,
                cwd=self._cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with self._lock:
                self._active_procs[job_id] = proc

            output = self._capture_output(job, proc)

            artifacts = discover_generated_artifacts(output or "", command=job.command)
            returncode = getattr(proc, "returncode", None)
            with self._lock:
                self._active_procs.pop(job_id, None)
                cancelled = job_id in self._cancelled
                self._cancelled.discard(job_id)
                job.output = output or ""
                job.artifacts = artifacts
                job.returncode = returncode
                if cancelled:
                    job.status = "cancelled"
                else:
                    job.status = "succeeded" if returncode == 0 else "failed"
                job.finished_at = _now_iso()
        except Exception as exc:  # noqa: BLE001 - surface local runner failures to UI
            with self._lock:
                self._active_procs.pop(job_id, None)
                self._cancelled.discard(job_id)
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
                job.finished_at = _now_iso()

    def _capture_output(self, job: JobRecord, proc: Any) -> str:
        """Capture stdout incrementally if possible, else fall back to communicate()."""
        stream = getattr(proc, "stdout", None)
        if stream is not None and hasattr(stream, "readline"):
            buf: list[str] = []
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    buf.append(line)
                    with self._lock:
                        job.output = "".join(buf)
            finally:
                # Make sure the proc has fully exited and returncode is set.
                try:
                    proc.wait()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            return "".join(buf)
        out, _ = proc.communicate()
        return out or ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_FILE_PATTERNS = (
    re.compile(r"Wrote:\s+(.+\.(?:html|json|txt|xml|md|png|ya?ml))\s*$", re.IGNORECASE),
    re.compile(r"Wrote NT template:\s+(.+\.xml)\s*$", re.IGNORECASE),
    re.compile(r"Wrote PantheonBotV2 template:\s+(.+\.xml)\s*$", re.IGNORECASE),
    re.compile(r"Wrote PantheonMaster template:\s+(.+\.xml)\s*$", re.IGNORECASE),
    re.compile(r"Wrote per-rule template:.*?->\s+(.+\.xml)\s*$", re.IGNORECASE),
)
_DIR_PATTERNS = (
    re.compile(r"Exported\s+\d+\s+exec cards to:\s+(.+)\s*$", re.IGNORECASE),
)
_SAFE_ARTIFACT_EXTENSIONS = {".html", ".json", ".txt", ".xml", ".md", ".png", ".yaml", ".yml"}


def discover_generated_artifacts(
    output: str,
    *,
    command: list[str] | tuple[str, ...] | None = None,
) -> list[GeneratedArtifact]:
    """Parse CLI output into generated artifacts that the web app may serve."""

    by_path: dict[str, GeneratedArtifact] = {}
    for line in str(output or "").splitlines():
        for pattern in _FILE_PATTERNS:
            match = pattern.search(line)
            if match:
                _add_artifact(by_path, match.group(1), is_directory=False)
        for pattern in _DIR_PATTERNS:
            match = pattern.search(line)
            if match:
                _add_artifact(by_path, match.group(1), is_directory=True)
                _add_directory_children(by_path, match.group(1))
    _add_command_artifacts(by_path, command)
    return sorted(by_path.values(), key=_artifact_sort_key)


def _add_command_artifacts(
    by_path: dict[str, GeneratedArtifact],
    command: list[str] | tuple[str, ...] | None,
) -> None:
    if not command:
        return
    output_dir = _option_value(command, "--output")
    if output_dir:
        _add_artifact(by_path, str(Path(output_dir) / "manifest.json"), is_directory=False)

    config_path = _option_value(command, "--report-config")
    if output_dir and config_path:
        _add_artifact(by_path, config_path, is_directory=False)
        for output_filename in _report_output_filenames(config_path):
            _add_artifact(by_path, str(Path(output_dir) / output_filename), is_directory=False)

    cards_dir = _option_value(command, "--exec-cards-dir")
    if not cards_dir and "--export-exec-cards-png" in command and output_dir:
        cards_dir = str(Path(output_dir) / "cards")
    if cards_dir:
        _add_artifact(by_path, cards_dir, is_directory=True)
        _add_directory_children(by_path, cards_dir)


def _option_value(command: list[str] | tuple[str, ...], option: str) -> str | None:
    try:
        idx = list(command).index(option)
    except ValueError:
        return None
    next_idx = idx + 1
    if next_idx >= len(command):
        return None
    value = str(command[next_idx]).strip()
    return value or None


def _report_output_filenames(config_path: str | Path) -> list[str]:
    path = _resolve_path(config_path)
    if path is None or not path.is_file():
        return []
    try:
        from ta_foundation.reports.html.config import load_report_configs

        configs = load_report_configs(path)
    except Exception:
        return []
    filenames: list[str] = []
    for cfg in configs:
        filename = str(getattr(cfg, "output_filename", "") or "").strip()
        if filename and filename not in filenames:
            filenames.append(filename)
    return filenames


def _add_directory_children(by_path: dict[str, GeneratedArtifact], raw_path: str) -> None:
    path = _resolve_path(raw_path)
    if path is None or not path.is_dir():
        return
    for child in sorted(path.iterdir()):
        if child.is_file() and child.suffix.lower() in _SAFE_ARTIFACT_EXTENSIONS:
            _add_artifact(by_path, str(child), is_directory=False)


def _add_artifact(by_path: dict[str, GeneratedArtifact], raw_path: str, *, is_directory: bool) -> None:
    path = _resolve_path(raw_path)
    if path is None:
        return
    if is_directory:
        if path.exists() and not path.is_dir():
            return
        kind = "folder"
    else:
        if path.suffix.lower() not in _SAFE_ARTIFACT_EXTENSIONS:
            return
        kind = path.suffix.lower().lstrip(".")
    key = str(path)
    by_path[key] = GeneratedArtifact(
        path=key,
        kind=kind,
        label=_artifact_label(path, kind=kind, is_directory=is_directory),
        is_directory=is_directory,
    )


def _artifact_label(path: Path, *, kind: str, is_directory: bool) -> str:
    if is_directory:
        return f"Folder {path.name}"
    if path.name.lower() == "manifest.json":
        return "Manifest"
    if path.suffix.lower() == ".html":
        return f"HTML {path.name}"
    if path.suffix.lower() in {".txt", ".md"}:
        return f"Summary {path.name}"
    if path.suffix.lower() in {".yaml", ".yml"}:
        return f"YAML {path.name}"
    if path.suffix.lower() == ".xml":
        return f"Template {path.name}"
    if path.suffix.lower() == ".png":
        return f"Card {path.name}"
    return f"{kind.upper()} {path.name}"


def _artifact_sort_key(artifact: GeneratedArtifact) -> tuple[int, str]:
    if artifact.kind == "html":
        rank = 0
    elif Path(artifact.path).name.lower() == "manifest.json":
        rank = 1
    elif artifact.kind in {"txt", "md"}:
        rank = 2
    elif artifact.kind in {"yaml", "yml"}:
        rank = 3
    elif artifact.kind == "xml":
        rank = 4
    elif artifact.kind == "png":
        rank = 5
    elif artifact.is_directory:
        rank = 6
    else:
        rank = 9
    return rank, artifact.path.lower()


def _resolve_path(path: str | Path) -> Path | None:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
