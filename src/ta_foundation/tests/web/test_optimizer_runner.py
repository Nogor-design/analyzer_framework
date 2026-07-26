from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_runner import (
    GENERATED_DIRNAME,
    NT_OUTPUT_DIRNAME,
    RUN_FILENAME,
    STALE_AFTER_SECONDS,
    OptimizerRunnerError,
    cancel_run,
    get_status,
    load_run,
    start_run,
)
from ta_foundation.web.optimizer_session import create_session


def _valid_chunk_xml(instrument: str = "NQ 06-26") -> str:
    return (
        "<StrategyTemplate>"
        "<BacktestType>Optimize</BacktestType>"
        "<Strategy><FakeStrategy>"
        "<Category>Optimize</Category>"
        f"<InstrumentOrInstrumentList>{instrument}</InstrumentOrInstrumentList>"
        "<From>2026-04-14T00:00:00</From>"
        "<To>2026-05-14T00:00:00</To>"
        "</FakeStrategy></Strategy>"
        "</StrategyTemplate>"
    )


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session_with_templates(*, count: int = 3):
    session = create_session(label="run-test", instrument="NQ 06-26")
    gen_dir = session.directory / GENERATED_DIRNAME
    gen_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (gen_dir / f"chunk_{i:03d}.xml").write_text(_valid_chunk_xml(), encoding="utf-8")
    return session


def test_start_run_blocks_generic_chunk_instrument(tmp_path: Path):
    session = create_session(label="generic-test", instrument="NQ 06-26")
    gen_dir = session.directory / GENERATED_DIRNAME
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "chunk_001.xml").write_text(
        "<StrategyTemplate><Strategy><FakeStrategy>"
        "<InstrumentOrInstrumentList>NQ</InstrumentOrInstrumentList>"
        "</FakeStrategy></Strategy></StrategyTemplate>",
        encoding="utf-8",
    )

    with pytest.raises(OptimizerRunnerError, match="preflight failed"):
        start_run(session, command_file=tmp_path / "nt8_command.json")


def _drop_summary(session, template_name: str) -> Path:
    dest = session.directory / NT_OUTPUT_DIRNAME / template_name
    dest.mkdir(parents=True, exist_ok=True)
    summary = dest / "Summary.csv"
    summary.write_text("Performance,All trades,Long trades,Short trades,\n", encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------

def test_start_run_writes_command_file_and_persists_record(tmp_path: Path):
    session = _make_session_with_templates(count=2)
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    record = start_run(session, command_file=cmd_path)

    assert cmd_path.exists()
    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["sourceFolder"] == str(session.directory / GENERATED_DIRNAME)
    assert payload["destFolder"] == str(session.directory / NT_OUTPUT_DIRNAME)

    assert record.total_templates == 2
    assert record.state == "requested"
    assert record.run_id.startswith("run_")

    # run.json round-trips
    persisted = load_run(session)
    assert persisted is not None
    assert persisted.run_id == record.run_id
    assert persisted.command_file == str(cmd_path)


def test_start_run_requires_generated_templates(tmp_path: Path):
    session = create_session(label="empty")
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    with pytest.raises(OptimizerRunnerError):
        start_run(session, command_file=cmd_path)
    assert not cmd_path.exists()


def test_start_run_creates_nt_output_dir(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    start_run(session, command_file=cmd_path)
    assert (session.directory / NT_OUTPUT_DIRNAME).is_dir()


def test_start_run_prefers_seed_contract_when_session_has_generic_root(tmp_path: Path):
    seed = tmp_path / "Pass1.xml"
    seed.write_text(
        """<StrategyTemplate>
  <Strategy>
    <FakeStrategy>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )
    session = _make_session_with_templates(count=1)
    session.update(instrument="NQ", seed_template_path=str(seed))
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    start_run(session, command_file=cmd_path)

    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["instrument"] == "NQ 06-26"


def test_start_run_keeps_explicit_full_contract_over_seed(tmp_path: Path):
    seed = tmp_path / "Pass1.xml"
    seed.write_text(
        """<StrategyTemplate>
  <Strategy>
    <FakeStrategy>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )
    session = _make_session_with_templates(count=1)
    session.update(instrument="ES 06-26", seed_template_path=str(seed))
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    start_run(session, command_file=cmd_path)

    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["instrument"] == "ES 06-26"


def test_start_run_passes_chunk_runtime_timeout_seconds(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    session.update(chunking={"max_runtime_minutes_per_chunk": 12})
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"

    record = start_run(session, command_file=cmd_path)

    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["timeoutSeconds"] == 720
    assert record.timeout_seconds == 720


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

def test_get_status_returns_none_when_no_run(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    assert get_status(session) is None


def test_get_status_requested_when_nothing_written_yet(tmp_path: Path):
    session = _make_session_with_templates(count=3)
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"
    start_run(session, command_file=cmd_path)

    status = get_status(session)
    assert status is not None
    assert status.state == "requested"
    assert status.total == 3
    assert status.completed == 0
    assert status.current_template is None


def test_get_status_running_with_recent_write(tmp_path: Path):
    session = _make_session_with_templates(count=3)
    start_run(session, command_file=tmp_path / "nt8_command.json")

    # One template finished, one in progress
    _drop_summary(session, "chunk_000")
    (session.directory / NT_OUTPUT_DIRNAME / "chunk_001").mkdir(parents=True, exist_ok=True)
    (session.directory / NT_OUTPUT_DIRNAME / "chunk_001" / "partial.txt").write_text("x", encoding="utf-8")

    status = get_status(session)
    assert status.state == "running"
    assert status.completed == 1
    assert status.finished_templates == ["chunk_000"]
    assert status.current_template == "chunk_001"


def test_get_status_finished_when_all_have_summary(tmp_path: Path):
    session = _make_session_with_templates(count=2)
    start_run(session, command_file=tmp_path / "nt8_command.json")

    _drop_summary(session, "chunk_000")
    _drop_summary(session, "chunk_001")

    status = get_status(session)
    assert status.state == "finished"
    assert status.completed == 2
    assert status.current_template is None
    # persisted finish time
    record = load_run(session)
    assert record.finished_at is not None


def test_get_status_surfaces_batch_summary_timeout(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    start_run(session, command_file=tmp_path / "nt8_command.json")
    _drop_summary(session, "chunk_000")
    output = session.directory / NT_OUTPUT_DIRNAME
    (output / "BatchRunSummary.csv").write_text(
        "Template,Status,Strategy,Instrument,Backtest start,Backtest end,"
        "Total net profit,Trades,Profit factor,Max drawdown,Run start time,Run end time,Output folder,Error\n"
        "chunk_000,TimedOut,,,2025-03-08,2025-03-14,,,,,2026-05-20 23:10:46,"
        "2026-05-20 23:20:48,C:\\out,timed out waiting for results\n",
        encoding="utf-8",
    )

    status = get_status(session)

    assert status.state == "timed_out"
    assert status.batch_run_statuses[0]["status"] == "TimedOut"
    assert "TimedOut" in status.last_error
    assert load_run(session).finished_at is not None


def test_get_status_stale_when_recent_write_is_old(tmp_path: Path):
    session = _make_session_with_templates(count=2)
    start_run(session, command_file=tmp_path / "nt8_command.json")

    _drop_summary(session, "chunk_000")
    # Backdate every file under nt_output past the stale threshold
    nt_out = session.directory / NT_OUTPUT_DIRNAME
    old = time.time() - (STALE_AFTER_SECONDS + 60)
    for path in nt_out.rglob("*"):
        os.utime(path, (old, old))

    now = datetime.now(timezone.utc)
    status = get_status(session, now=now)
    assert status.state == "stale"
    assert status.completed == 1


# ---------------------------------------------------------------------------
# cancel_run
# ---------------------------------------------------------------------------

def test_cancel_run_removes_command_file_and_marks_cancelled(tmp_path: Path):
    session = _make_session_with_templates(count=2)
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"
    start_run(session, command_file=cmd_path)
    assert cmd_path.exists()

    cancelled = cancel_run(session, command_file=cmd_path)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.cancelled_at
    assert not cmd_path.exists()

    # status still readable, stays cancelled
    status = get_status(session)
    assert status.state == "cancelled"


def test_cancel_run_returns_none_when_no_run(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    assert cancel_run(session, command_file=tmp_path / "nt8_command.json") is None


def test_cancel_tolerates_missing_command_file(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    cmd_path = tmp_path / "fake_temp" / "nt8_command.json"
    start_run(session, command_file=cmd_path)
    cmd_path.unlink()

    cancelled = cancel_run(session, command_file=cmd_path)
    assert cancelled.state == "cancelled"


# ---------------------------------------------------------------------------
# Heartbeat (nt8_status.json) integration
# ---------------------------------------------------------------------------

def _write_heartbeat(path: Path, *, run_id: str, state: str, completed: int, total: int,
                     current: str | None = None, last_error: str | None = None) -> None:
    payload = {
        "runId": run_id,
        "state": state,
        "currentTemplate": current,
        "completed": completed,
        "total": total,
        "lastError": last_error,
        "heartbeatUtc": datetime.now(timezone.utc).isoformat(),
        "outputRoot": "irrelevant",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_start_run_passes_run_id_in_command_and_clears_stale_status(tmp_path: Path):
    session = _make_session_with_templates(count=1)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    # Pre-existing stale heartbeat from a prior run
    status_path.write_text('{"runId":"old","state":"finished"}', encoding="utf-8")

    record = start_run(session, command_file=cmd_path, status_file=status_path)

    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["runId"] == record.run_id
    assert not status_path.exists()  # stale heartbeat purged
    assert record.status_file == str(status_path)


def test_get_status_prefers_heartbeat_when_present(tmp_path: Path):
    session = _make_session_with_templates(count=4)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    record = start_run(session, command_file=cmd_path, status_file=status_path)

    # AddOn reports template 2 currently running, 1 done. Folder has nothing yet.
    _write_heartbeat(
        status_path,
        run_id=record.run_id,
        state="running",
        completed=1,
        total=4,
        current="chunk_001",
    )
    status = get_status(session)
    assert status.source == "heartbeat"
    assert status.state == "running"
    assert status.completed == 1
    assert status.total == 4
    assert status.current_template == "chunk_001"


def test_get_status_heartbeat_terminal_state_is_authoritative(tmp_path: Path):
    session = _make_session_with_templates(count=2)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    record = start_run(session, command_file=cmd_path, status_file=status_path)

    # AddOn says finished even though we wrote no Summary.csv (synthetic edge
    # case: AddOn ran but exports failed). The heartbeat is still trusted.
    _write_heartbeat(
        status_path,
        run_id=record.run_id,
        state="finished",
        completed=2,
        total=2,
    )
    status = get_status(session)
    assert status.state == "finished"
    assert status.completed == 2


def test_get_status_ignores_heartbeat_with_wrong_run_id(tmp_path: Path):
    session = _make_session_with_templates(count=3)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    start_run(session, command_file=cmd_path, status_file=status_path)

    # Heartbeat from an unrelated run — must be ignored, fall back to folder.
    _write_heartbeat(
        status_path,
        run_id="someone_elses_run",
        state="running",
        completed=99,
        total=99,
    )
    status = get_status(session)
    assert status.source == "folder"
    assert status.completed == 0


def test_get_status_falls_back_when_heartbeat_old_but_files_still_landing(tmp_path: Path):
    session = _make_session_with_templates(count=3)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    record = start_run(session, command_file=cmd_path, status_file=status_path)

    _write_heartbeat(
        status_path,
        run_id=record.run_id,
        state="running",
        completed=1,
        total=3,
        current="chunk_001",
    )
    # Backdate heartbeat past the stale threshold.
    old = time.time() - 120
    os.utime(status_path, (old, old))
    # But Summary.csv is still being written (fresh).
    _drop_summary(session, "chunk_000")

    status = get_status(session, now=datetime.now(timezone.utc))
    # Heartbeat is stale, but folder is fresh — UI should still say running.
    assert status.state == "running"


def test_heartbeat_completed_max_with_folder(tmp_path: Path):
    """When heartbeat says completed=1 but folder shows 2 Summary.csv files,
    the larger count wins (export-write order can race the AddOn's counter)."""
    session = _make_session_with_templates(count=3)
    cmd_path = tmp_path / "nt8_command.json"
    status_path = tmp_path / "nt8_status.json"
    record = start_run(session, command_file=cmd_path, status_file=status_path)

    _write_heartbeat(
        status_path,
        run_id=record.run_id,
        state="running",
        completed=1,
        total=3,
        current="chunk_002",
    )
    _drop_summary(session, "chunk_000")
    _drop_summary(session, "chunk_001")

    status = get_status(session)
    assert status.completed == 2
