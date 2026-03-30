from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json
from hashlib import sha256

import pandas as pd


DENVER_TZ = "America/Denver"


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize(DENVER_TZ)
        else:
            value = value.tz_convert(DENVER_TZ)
        return value.isoformat()
    return value


def build_storage_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _to_jsonable(payload)
    raw = json.dumps(normalized, sort_keys=True, default=str)
    digest = sha256(raw.encode("utf-8")).hexdigest()[:20]
    return {
        "record_id": digest,
        "captured_at": pd.Timestamp.now(tz=DENVER_TZ).isoformat(),
        "payload": normalized,
    }


def persist_record_jsonl(payload: Dict[str, Any], jsonl_path: str) -> Dict[str, Any]:
    record = build_storage_record(payload)
    path = Path(jsonl_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return {
        "record_id": record["record_id"],
        "path": str(path),
        "captured_at": record["captured_at"],
    }
