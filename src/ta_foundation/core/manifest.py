from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ManifestFileEntry:
    path: str
    sha256: str
    size_bytes: int
    run_id: str
    parser_kind: Optional[str] = None
    parser_name: Optional[str] = None


def write_manifest(
    output_path: Path,
    *,
    input_folder: Path,
    files_parsed: list[ManifestFileEntry],
    files_unparsed: list[Path],
    packages_warnings: dict[str, list[dict[str, Any]]],
    extra: Optional[dict[str, Any]] = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")

    manifest: dict[str, Any] = {
        "generated_at": now,
        "input_folder": str(input_folder),
        "timestamp_policy": {
            "timezone": "America/Denver",
            "timestamp_source": "ninjatrader_local_pc_time",
            "datetime_policy": "localized_on_ingest",
        },
        "parsed_files": [fe.__dict__ for fe in files_parsed],
        "unparsed_files": [str(p) for p in files_unparsed],
        "warnings_by_run_id": packages_warnings,
    }

    if extra:
        manifest["extra"] = extra

    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
