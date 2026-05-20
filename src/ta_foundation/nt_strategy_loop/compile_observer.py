from __future__ import annotations

"""Client-side helpers for the NinjaTrader auto-compile observer.

Normal NinjaScript strategies are compiled inside NinjaTrader after a `.cs`
file is copied into `Documents/NinjaTrader 8/bin/Custom/Strategies`. The AddOn
observes that auto-compile cycle through the `ObserveCompile` IPC action and
exports status/errors for this module to consume.
"""

import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ta_foundation.nt_strategy_loop.installer import (
    DEFAULT_COMPILE_ROOT,
    DEFAULT_NT_DOCUMENTS,
    InstalledStrategy,
    InstallerError,
    install_strategy_source,
    sha256_file,
)


DEFAULT_COMMAND_PATH = Path(r"C:\temp\nt8_command.json")
DEFAULT_STATUS_PATH = Path(r"C:\temp\nt8_status.json")


@dataclass(frozen=True)
class CompileError:
    file: str
    line: int | None
    column: int | None
    code: str
    message: str
    raw: str
    source: str

    def formatted(self) -> str:
        location = self.file
        if self.line is not None or self.column is not None:
            location += f"({self.line or 0},{self.column or 0})"
        code = f" {self.code}:" if self.code else ""
        return f"{location}{code} {self.message}".strip()


@dataclass(frozen=True)
class CompileObservation:
    run_id: str
    state: str
    strategy_name: str
    source_file: str
    compiled: bool
    error_count: int
    errors_csv: str | None
    errors_text: str | None
    last_error: str | None
    heartbeat_utc: str | None
    output_root: str | None
    errors: tuple[CompileError, ...]
    status_path: str
    compile_block_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.compiled and self.state == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["errors"] = [asdict(error) for error in self.errors]
        return payload


class CompileObserverError(RuntimeError):
    pass


def write_observe_compile_command(
    *,
    run_id: str,
    source_file: str | Path,
    strategy_name: str,
    output_dir: str | Path,
    installed_sha256: str = "",
    command_path: str | Path = DEFAULT_COMMAND_PATH,
    timeout_seconds: int = 120,
    wait_for_quiet_seconds: int = 3,
) -> dict[str, Any]:
    command = {
        "action": "ObserveCompile",
        "runId": run_id,
        "sourceFile": str(Path(source_file)),
        "strategyName": strategy_name,
        "outputDir": str(Path(output_dir)),
        "installedSha256": installed_sha256,
        "waitForQuietSeconds": int(wait_for_quiet_seconds),
        "timeoutSeconds": int(timeout_seconds),
        "exportErrors": True,
    }
    write_json_atomic(Path(command_path), command)
    return command


def observe_compile(
    source_path: str | Path,
    *,
    strategy_name: str | None = None,
    nt_documents_dir: str | Path = DEFAULT_NT_DOCUMENTS,
    compile_root: str | Path = DEFAULT_COMPILE_ROOT,
    command_path: str | Path = DEFAULT_COMMAND_PATH,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    overwrite: bool = False,
    timeout_seconds: int = 120,
    wait_for_quiet_seconds: int = 3,
    poll_seconds: float = 1.0,
) -> CompileObservation:
    try:
        installed = install_strategy_source(
            source_path,
            nt_documents_dir=nt_documents_dir,
            compile_root=compile_root,
            overwrite=overwrite,
        )
    except InstallerError as exc:
        raise CompileObserverError(str(exc)) from exc
    name = strategy_name or Path(installed.target_path).stem
    run_id = f"compile_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{name}"
    output_dir = Path(compile_root) / "compiler_errors"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_observe_compile_command(
        run_id=run_id,
        source_file=installed.target_path,
        strategy_name=name,
        output_dir=output_dir,
        installed_sha256=installed.sha256,
        command_path=command_path,
        timeout_seconds=timeout_seconds,
        wait_for_quiet_seconds=wait_for_quiet_seconds,
    )
    return wait_for_observation(
        run_id,
        status_path=status_path,
        timeout_seconds=timeout_seconds + wait_for_quiet_seconds + 10,
        poll_seconds=poll_seconds,
    )


def wait_for_observation(
    run_id: str,
    *,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    timeout_seconds: int = 150,
    poll_seconds: float = 1.0,
) -> CompileObservation:
    deadline = time.monotonic() + timeout_seconds
    status_file = Path(status_path)
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        payload = read_status(status_file)
        if payload and payload.get("runId") == run_id:
            last_payload = payload
            if payload.get("workerKind") == "compile_observer" and payload.get("state") in {
                "succeeded",
                "failed",
                "timed_out",
                "worker_error",
            }:
                return observation_from_status(payload, status_file)
        time.sleep(poll_seconds)
    if last_payload is not None:
        return observation_from_status(last_payload, status_file)
    raise CompileObserverError(f"Timed out waiting for ObserveCompile status for {run_id}")


def read_status(path: str | Path) -> dict[str, Any] | None:
    status_file = Path(path)
    if not status_file.exists():
        return None
    try:
        return json.loads(status_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def observation_from_status(payload: dict[str, Any], status_path: str | Path) -> CompileObservation:
    errors_csv = _optional_str(payload.get("errorsCsv"))
    errors_text = _optional_str(payload.get("errorsText"))
    errors = load_compile_errors(errors_csv, errors_text)
    return CompileObservation(
        run_id=str(payload.get("runId") or ""),
        state=str(payload.get("state") or ""),
        strategy_name=str(payload.get("strategyName") or ""),
        source_file=str(payload.get("sourceFile") or ""),
        compiled=bool(payload.get("compiled")),
        error_count=int(payload.get("errorCount") or len(errors)),
        errors_csv=errors_csv,
        errors_text=errors_text,
        last_error=_optional_str(payload.get("lastError")),
        heartbeat_utc=_optional_str(payload.get("heartbeatUtc")),
        output_root=_optional_str(payload.get("outputRoot")),
        errors=tuple(errors),
        status_path=str(Path(status_path)),
        compile_block_reason=_optional_str(payload.get("compileBlockReason")),
    )


def load_compile_errors(errors_csv: str | None, errors_text: str | None = None) -> list[CompileError]:
    if errors_csv and Path(errors_csv).is_file():
        return parse_compile_errors_csv(errors_csv)
    if errors_text and Path(errors_text).is_file():
        return parse_compile_errors_text(errors_text)
    return []


def parse_compile_errors_csv(path: str | Path) -> list[CompileError]:
    source = Path(path)
    rows: list[CompileError] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            rows.append(
                CompileError(
                    file=_first(row, "NinjaScript File", "File", "Filename", "Source", "Path"),
                    line=_int_or_none(_first(row, "Line", "Line Number")),
                    column=_int_or_none(_first(row, "Column", "Column Number")),
                    code=_first(row, "Code", "Error Code"),
                    message=_first(row, "Error", "Message", "Description"),
                    raw=_first(row, "Raw") or " | ".join(f"{k}={v}" for k, v in row.items() if str(v or "").strip()),
                    source=_first(row, "Source") or str(source),
                )
            )
    return rows


def parse_compile_errors_text(path: str | Path) -> list[CompileError]:
    source = Path(path)
    rows: list[CompileError] = []
    import re

    pattern = re.compile(
        r"^(?P<file>.*?)(?:\((?P<line>\d+)\s*,\s*(?P<column>\d+)\))?\s*"
        r"(?P<code>CS\d{4})?\s*:?\s*(?P<message>.+)$"
    )
    for raw in source.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        text = raw.strip()
        if not text:
            continue
        match = pattern.match(text)
        if match:
            rows.append(
                CompileError(
                    file=(match.group("file") or "").strip(),
                    line=_int_or_none(match.group("line")),
                    column=_int_or_none(match.group("column")),
                    code=(match.group("code") or "").strip(),
                    message=(match.group("message") or text).strip(),
                    raw=text,
                    source=str(source),
                )
            )
        else:
            rows.append(CompileError("", None, None, "", text, text, str(source)))
    return rows


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _first(row: dict[str, Any], *keys: str) -> str:
    lower = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        value = row.get(key)
        if value is None:
            value = lower.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def compiler_error_signature(errors: Iterable[CompileError]) -> str:
    normalized = "\n".join(
        "|".join(
            [
                error.file.lower(),
                "" if error.line is None else str(error.line),
                "" if error.column is None else str(error.column),
                error.code,
                error.message.strip(),
            ]
        )
        for error in errors
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

