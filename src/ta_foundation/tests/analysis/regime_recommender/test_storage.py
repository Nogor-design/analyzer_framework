from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.analysis.regime_recommender.storage import build_storage_record, persist_record_jsonl


def test_build_storage_record_has_id_and_payload():
    record = build_storage_record({"a": 1, "b": {"c": 2}})
    assert record["record_id"]
    assert record["payload"]["b"]["c"] == 2
    assert "captured_at" in record


def test_persist_record_jsonl_writes_line(tmp_path: Path):
    out_path = tmp_path / "rr_store.jsonl"
    info = persist_record_jsonl(payload={"x": 1}, jsonl_path=str(out_path))

    assert out_path.exists()
    assert info["record_id"]

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["record_id"] == info["record_id"]
