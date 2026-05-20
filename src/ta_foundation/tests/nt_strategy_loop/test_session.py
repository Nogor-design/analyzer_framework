from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.nt_strategy_loop.session import create_session


def test_create_session_builds_durable_folder_layout(tmp_path: Path) -> None:
    session = create_session(lab_root=tmp_path, strategy_name="LoopUnit", compile_mode="fixture")
    session.ensure_dirs()

    assert session.session_dir.parent == tmp_path
    assert session.session_id.startswith("loop_") and session.session_id.endswith("loopunit")
    assert session.attempt_dir(1).is_dir()
    assert session.compile_clean_dir.is_dir()
    assert session.optimizer_output_dir.is_dir()
    assert session.decisions_dir.is_dir()


def test_write_manifest_writes_both_manifest_and_session_json(tmp_path: Path) -> None:
    session = create_session(lab_root=tmp_path, strategy_name="LoopUnit", compile_mode="fixture")
    session.ensure_dirs()

    session.write_manifest(decision="archive", artifacts={"summary": "decisions/STRATEGY_LOOP_SUMMARY.md"})

    manifest = json.loads((session.session_dir / "manifest.json").read_text(encoding="utf-8"))
    duplicate = json.loads((session.session_dir / "session.json").read_text(encoding="utf-8"))
    assert manifest == duplicate
    assert manifest["decision"] == "archive"
    assert manifest["strategy_name"] == "LoopUnit"


def test_attempt_dir_zero_pads(tmp_path: Path) -> None:
    session = create_session(lab_root=tmp_path, strategy_name="LoopUnit", compile_mode="fixture")
    assert session.attempt_dir(1).name == "attempt_001"
    assert session.attempt_dir(42).name == "attempt_042"
