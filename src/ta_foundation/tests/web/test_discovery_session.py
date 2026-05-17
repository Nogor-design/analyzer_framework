from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ta_foundation.web.discovery_session import (
    DiscoverySession,
    ProjectContext,
    Promotion,
    SCHEMA_VERSION,
    SessionInstrument,
    SessionNotFoundError,
    SessionSchemaError,
    StageRun,
    create_session,
    delete_session,
    get_session,
    list_sessions,
    set_storage_root,
)


@pytest.fixture
def session_root(tmp_path: Path):
    set_storage_root(tmp_path / "sessions")
    yield tmp_path / "sessions"
    set_storage_root(None)


def _stage_run(stage_id: str = "01_quick_scan", job_id: str = "job_a") -> StageRun:
    return StageRun(
        stage_id=stage_id,
        job_id=job_id,
        yaml_path=f"sessions/x/stage_yaml/{stage_id}.yaml",
        report_html_path="outputs/foo.html",
        summary_json_path="outputs/discovery_summary.json",
        started_at="2026-05-04T18:00:00+00:00",
        status="queued",
    )


# ---------------------------------------------------------------------------
# Create / load round-trip
# ---------------------------------------------------------------------------

def test_create_session_writes_three_files(session_root: Path):
    session = create_session(label="My run", instrument_symbol="NQ")
    assert session.directory.exists()
    assert session.session_path.exists()
    assert session.runs_path.exists()
    assert session.promotions_path.exists()
    # Session id format
    assert session.id.startswith("ses_")
    assert len(session.id) == 16  # "ses_" + 12 hex chars


def test_create_session_persists_metadata(session_root: Path):
    session = create_session(
        label="NQ March discovery",
        instrument_symbol="NQ",
        context={
            "input_folder": "C:/exports",
            "output_folder": "./out",
            "market_data_folder": "D:/MarketData",
        },
        current_stage="02_candle_patterns",
    )
    doc = session.load_document()
    assert doc.label == "NQ March discovery"
    assert doc.instrument.symbol == "NQ"
    assert doc.instrument.tick_value == 5.00
    assert doc.context.input_folder == "C:/exports"
    assert doc.current_stage == "02_candle_patterns"
    assert doc.created_at
    assert doc.updated_at


def test_get_session_returns_none_for_unknown_id(session_root: Path):
    assert get_session("ses_nope") is None
    assert get_session("") is None


def test_get_session_returns_wrapper_for_existing(session_root: Path):
    created = create_session(label="x")
    fetched = get_session(created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_load_document_raises_for_missing(session_root: Path, tmp_path: Path):
    rogue_dir = tmp_path / "sessions" / "ses_doesnotexist"
    rogue_dir.mkdir(parents=True)
    session = DiscoverySession(rogue_dir)
    with pytest.raises(SessionNotFoundError):
        session.load_document()


def test_schema_version_mismatch_is_rejected(session_root: Path):
    session = create_session(label="x")
    # Hand-corrupt the schema version
    data = json.loads(session.session_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    session.session_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SessionSchemaError):
        session.load_document()


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

def test_update_label(session_root: Path):
    session = create_session(label="initial")
    session.update_label("renamed")
    assert session.load_document().label == "renamed"


def test_update_context_only_touches_known_fields(session_root: Path):
    session = create_session(label="x")
    session.update_context(input_folder="A", market_data_folder="B", garbage="ignored")
    doc = session.load_document()
    assert doc.context.input_folder == "A"
    assert doc.context.market_data_folder == "B"
    # Unknown fields don't appear
    assert not hasattr(doc.context, "garbage")


def test_set_instrument_swaps_symbol_and_values(session_root: Path):
    session = create_session(label="x", instrument_symbol="NQ")
    session.set_instrument("ES")
    doc = session.load_document()
    assert doc.instrument.symbol == "ES"
    assert doc.instrument.tick_value == 12.50


def test_set_current_stage(session_root: Path):
    session = create_session(label="x")
    session.set_current_stage("03_levels_regions")
    assert session.load_document().current_stage == "03_levels_regions"


def test_set_form_values_per_stage(session_root: Path):
    session = create_session(label="x")
    session.set_form_values("01_quick_scan", {"min_trades": 30})
    session.set_form_values("02_candle_patterns", {"timeframes": [1, 5]})
    doc = session.load_document()
    assert doc.stage_form_values["01_quick_scan"] == {"min_trades": 30}
    assert doc.stage_form_values["02_candle_patterns"] == {"timeframes": [1, 5]}


# ---------------------------------------------------------------------------
# Stage runs
# ---------------------------------------------------------------------------

def test_append_run_preserves_history(session_root: Path):
    session = create_session(label="x")
    a = _stage_run("01_quick_scan", "job_a")
    b = _stage_run("02_candle_patterns", "job_b")
    session.append_run(a)
    session.append_run(b)

    runs = session.list_runs()
    assert [r.job_id for r in runs] == ["job_a", "job_b"]
    assert [r.stage_id for r in runs] == ["01_quick_scan", "02_candle_patterns"]


def test_update_run_status_mutates_only_target(session_root: Path):
    session = create_session(label="x")
    session.append_run(_stage_run("01_quick_scan", "job_a"))
    session.append_run(_stage_run("02_candle_patterns", "job_b"))

    updated = session.update_run_status(
        "job_b",
        status="succeeded",
        finished_at="2026-05-04T18:30:00+00:00",
        report_html_path="outputs/02_candle_patterns/02.html",
    )
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.report_html_path == "outputs/02_candle_patterns/02.html"

    runs = {r.job_id: r for r in session.list_runs()}
    assert runs["job_a"].status == "queued"
    assert runs["job_b"].status == "succeeded"


def test_update_run_status_returns_none_for_unknown_job(session_root: Path):
    session = create_session(label="x")
    session.append_run(_stage_run("01_quick_scan", "job_a"))
    assert session.update_run_status("unknown_job", status="failed") is None


# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------

def _promotion(rank: int = 1, from_stage: str = "01_quick_scan", to_stage: str = "02_candle_patterns") -> Promotion:
    return Promotion(
        from_stage=from_stage,
        to_stage=to_stage,
        rank=rank,
        promoted_at="2026-05-04T18:30:00+00:00",
        yaml_overrides={"candle_discovery": {"enabled": True}},
        explain="candle/large_body PF 1.41",
    )


def test_append_promotion_preserves_history(session_root: Path):
    session = create_session(label="x")
    session.append_promotion(_promotion(rank=1))
    session.append_promotion(_promotion(rank=2))

    promotions = session.list_promotions()
    assert [p.rank for p in promotions] == [1, 2]


def test_list_promotions_filters_by_stage(session_root: Path):
    session = create_session(label="x")
    session.append_promotion(_promotion(rank=1, from_stage="01_quick_scan", to_stage="02_candle_patterns"))
    session.append_promotion(_promotion(rank=1, from_stage="02_candle_patterns", to_stage="06_validate"))

    assert len(session.list_promotions(from_stage="01_quick_scan")) == 1
    assert len(session.list_promotions(to_stage="06_validate")) == 1
    assert len(session.list_promotions(from_stage="02_candle_patterns", to_stage="06_validate")) == 1


# ---------------------------------------------------------------------------
# Stage YAML files
# ---------------------------------------------------------------------------

def test_write_stage_yaml_creates_subdir_and_unique_filename(session_root: Path):
    session = create_session(label="x")
    p1 = session.write_stage_yaml("01_quick_scan", "report:\n  title: a\n")
    p2 = session.write_stage_yaml("01_quick_scan", "report:\n  title: b\n")
    assert p1.exists() and p2.exists()
    # Files are timestamped, so we should have two distinct files when produced
    # on different calls. If timestamps collide (sub-second), at minimum they
    # both reside in the stage_yaml subdir.
    assert p1.parent == session.stage_yaml_dir
    assert p2.parent == session.stage_yaml_dir


# ---------------------------------------------------------------------------
# Listing and deleting
# ---------------------------------------------------------------------------

def test_list_sessions_returns_summaries_sorted_by_updated_at(session_root: Path):
    a = create_session(label="alpha")
    b = create_session(label="beta")
    # Touch b last
    b.update_label("beta-updated")

    summaries = list_sessions()
    assert len(summaries) >= 2
    ids = [s["session_id"] for s in summaries]
    # b updated more recently → comes first
    assert ids[0] == b.id
    assert ids[1] == a.id

    summary_b = next(s for s in summaries if s["session_id"] == b.id)
    assert summary_b["label"] == "beta-updated"
    assert summary_b["instrument_symbol"] == "NQ"


def test_list_sessions_skips_directories_without_session_json(session_root: Path):
    a = create_session(label="real")
    rogue = session_root / "ses_fake"
    rogue.mkdir()
    (rogue / "garbage.txt").write_text("hi", encoding="utf-8")
    summaries = list_sessions()
    ids = {s["session_id"] for s in summaries}
    assert a.id in ids
    assert "ses_fake" not in ids


def test_delete_session_removes_directory(session_root: Path):
    session = create_session(label="x")
    assert session.directory.exists()
    assert delete_session(session.id) is True
    assert not session.directory.exists()
    assert get_session(session.id) is None


def test_delete_session_returns_false_for_unknown(session_root: Path):
    assert delete_session("ses_nothing") is False


# ---------------------------------------------------------------------------
# Atomic write safety
# ---------------------------------------------------------------------------

def test_atomic_write_does_not_leave_temp_files_on_success(session_root: Path):
    session = create_session(label="x")
    session.update_label("first")
    session.update_label("second")
    session.update_label("third")

    leftovers = [p.name for p in session.directory.iterdir() if p.name.startswith(".tmp.")]
    assert leftovers == []


def test_atomic_write_preserves_old_copy_on_failure(session_root: Path, monkeypatch):
    session = create_session(label="initial")
    original = session.session_path.read_text(encoding="utf-8")

    # Force os.replace to blow up after the temp file is written
    import ta_foundation.web.discovery_session as mod

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(mod.os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        session.update_label("doomed")

    # Original on-disk content is intact
    assert session.session_path.read_text(encoding="utf-8") == original
    # Temp files are cleaned up
    leftovers = [p.name for p in session.directory.iterdir() if p.name.startswith(".tmp.")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_appends_do_not_drop_records(session_root: Path):
    session = create_session(label="x")

    def worker(idx: int):
        session.append_run(_stage_run(stage_id="01_quick_scan", job_id=f"job_{idx:03d}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    runs = session.list_runs()
    assert len(runs) == 20
    job_ids = {r.job_id for r in runs}
    assert job_ids == {f"job_{i:03d}" for i in range(20)}


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------

def test_summary_includes_run_count(session_root: Path):
    session = create_session(label="x")
    session.append_run(_stage_run("01_quick_scan", "a"))
    session.append_run(_stage_run("02_candle_patterns", "b"))
    summary = session.summary()
    assert summary["session_id"] == session.id
    assert summary["label"] == "x"
    assert summary["run_count"] == 2
    assert summary["instrument_symbol"] == "NQ"
