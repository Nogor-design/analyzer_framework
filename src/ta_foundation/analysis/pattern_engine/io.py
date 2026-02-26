from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import json
import pandas as pd


ARTIFACT_ROOT_DIRNAME = ".ta_artifacts"
ARTIFACT_SUBDIR = "pattern_engine"


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p




# ----------------------------
# Pathing (Windows-safe)
# ----------------------------

_INVALID_WIN_CHARS = '<>:"/\\|?*'

def safe_run_id_for_path(run_id: str) -> str:
    """
    Convert run_id to a filesystem-safe folder name.

    IMPORTANT:
    - Does NOT change the logical run_id (used as package key, report selection, display).
    - Only used for artifact folder names.

    Windows disallows ':' among other characters; replace with '__'.
    """
    s = str(run_id)
    for ch in _INVALID_WIN_CHARS:
        s = s.replace(ch, "__")
    # also strip trailing dots/spaces which are illegal on Windows
    s = s.strip(" .")
    # avoid empty folder name
    return s or "__run__"

# ----------------------------
# Artifact refs
# ----------------------------

def attach_artifact_ref(*, artifacts: Dict[str, Any], key: str, path: Path) -> None:
    """
    Store JSON-safe artifact reference under metadata["derived"]["pattern_engine"]["artifacts"][key]
    """
    artifacts[key] = {"path": str(path)}


def df_to_parquet(*, df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)



def json_canonical(obj: Any) -> str:
    """
    Canonical JSON encoding for stable IDs / params_json.
    NOTE: This must never be used to encode DataFrames.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def pattern_engine_run_dir(run_id: str) -> Path:
    """
    Returns:
      <repo_root>/.ta_artifacts/pattern_engine/<safe_run_id>/

    Uses a Windows-safe folder name.
    """
    # If your project already has a "repo root" resolver, use it.
    # This assumes cwd is repo root (as your error path indicates).
    base = Path(".ta_artifacts") / "pattern_engine"
    safe = safe_run_id_for_path(run_id)
    return base / safe

# ----------------------------
# JSON-safe helpers
# ----------------------------

def deep_copy_json_safe(obj: Any) -> Any:
    """
    Defensive snapshot for metadata: JSON-serializable only.
    """
    try:
        return json.loads(json.dumps(obj))
    except Exception:
        # best-effort fallback
        return str(obj)

    return _coerce(d)  # type: ignore[return-value]