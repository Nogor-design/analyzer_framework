from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ta_foundation.cli.soak_monitor as soak_monitor
from ta_foundation.cli.soak_monitor import build_snapshot, read_state_file, render_snapshot, resolve_bridge_paths


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload))


def test_snapshot_reuses_existing_bridge_outputs(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    paths = resolve_bridge_paths(root)
    for directory in (paths.inbox, paths.archive, paths.rejected, paths.outbox, paths.logs, paths.state):
        directory.mkdir(parents=True, exist_ok=True)

    _write_json(
        paths.state_file,
        {
            "ActiveTemplate": "runner_reversal_template",
            "LastInstructionId": "msg-1",
            "SignalIntakeEnabled": True,
            "HeartbeatFaulted": False,
            "DailyLockout": False,
            "LastBridgeMessageUtc": "2026-04-17T12:00:20Z",
            "ShellMode": "Idle",
            "PositionId": "pos-1",
        },
    )
    _write(
        paths.log_file,
        "\n".join(
            [
                "2026-04-17T12:00:00Z|HEALTH|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=reason=STARTUP shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T12:00:00Z",
                "2026-04-17T12:00:05Z|ACCEPT|instr=msg-1|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=id=msg-1 action=ENTER_LONG template=runner_reversal_template archived=C:/ta_foundation/bridge/archive/msg-1.json",
                "2026-04-17T12:00:10Z|ORDER|instr=msg-1|template=runner_reversal_template|mode=EntryPending|pos=Flat|qty=0|msg=id=msg-1 side=Long qty=1 stop_ticks=50 target_ticks=200 template=runner_reversal_template pending_stop=0",
                "2026-04-17T12:00:15Z|FILL|instr=msg-1|template=runner_reversal_template|mode=InPosition|pos=Long|qty=1|msg=order=nt-1 signal=TF_ENTER_LONG side=Long qty=1 price=21000 state=Filled",
                "2026-04-17T12:00:17Z|STOP_INIT|instr=msg-1|template=runner_reversal_template|mode=InPosition|pos=Long|qty=1|msg=side=Long fill=21000 stop=20950 target=21200 qty=1",
                "2026-04-17T12:00:20Z|HEALTH|instr=msg-1|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=reason=PERIODIC shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False active_position_id=pos-1 pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=200 queue_depth=0 last_msg_utc=2026-04-17T12:00:20Z",
            ]
        ),
    )
    _write_json(
        paths.outbox / "20260417_120005000_ACCEPTED_msg-1.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:05Z",
            "status": "ACCEPTED",
            "instruction_id": "msg-1",
            "shell_mode": "Idle",
            "position": "Flat",
            "quantity": 0,
            "detail": "id=msg-1;action=ENTER_LONG;template=runner_reversal_template",
        },
    )
    _write_json(
        paths.outbox / "20260417_120010000_ENTRY_SUBMITTED_msg-1.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:10Z",
            "status": "ENTRY_SUBMITTED",
            "instruction_id": "msg-1",
            "shell_mode": "EntryPending",
            "position": "Flat",
            "quantity": 0,
            "detail": "id=msg-1;side=Long;qty=1;stop=0;target_ticks=200",
        },
    )
    _write_json(
        paths.outbox / "20260417_120015000_FILLED_msg-1.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:15Z",
            "status": "FILLED",
            "instruction_id": "msg-1",
            "shell_mode": "InPosition",
            "position": "Long",
            "quantity": 1,
            "detail": "order_id=nt-1;signal=TF_ENTER_LONG;side=Long;qty=1;price=21000;remaining_qty=1;pos_side=Long",
        },
    )
    _write_json(
        paths.outbox / "20260417_120017000_STOP_ATTACHED_msg-1.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:17Z",
            "status": "STOP_ATTACHED",
            "instruction_id": "msg-1",
            "shell_mode": "InPosition",
            "position": "Long",
            "quantity": 1,
            "detail": "side=Long;fill_price=21000;stop_price=20950;target_price=21200;qty=1",
        },
    )
    _write_json(
        paths.outbox / "20260417_120000000_SHELL_READY_boot.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:00Z",
            "status": "SHELL_READY",
            "instruction_id": "boot",
            "shell_mode": "Idle",
            "position": "Flat",
            "quantity": 0,
            "detail": "mode=Idle;intake=True;hb_faulted=False;daily_lockout=False;recovered_mode=Flat;inbox_depth=0",
        },
    )
    _write_json(
        paths.archive / "msg-1.json",
        {
            "message_id": "msg-1",
            "timestamp": "2026-04-17T12:00:04-06:00",
            "instrument": "NQ 06-26",
            "action": "ENTER_LONG",
            "thesis_id": "explosive_start_extended_vwap",
        },
    )

    snapshot = build_snapshot(
        paths,
        recent_minutes=60,
        tail_lines=10,
        stuck_seconds=60,
        heartbeat_timeout_seconds=60,
        pending_seconds=90,
        ready_grace_seconds=30,
        reject_threshold=2,
    )

    assert snapshot.current_mode == "Idle"
    assert snapshot.current_position == "Flat"
    assert snapshot.current_qty == 0
    assert snapshot.current_message_id == "msg-1"
    assert snapshot.current_thesis_id == "explosive_start_extended_vwap"
    assert snapshot.trade_state_guess == "idle"
    assert snapshot.last_by_status["FILLED"] is not None
    assert snapshot.last_ready_at is not None
    assert snapshot.last_ready_at.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-04-17T12:00:00Z"
    assert snapshot.last_ready_reason == "STARTUP"
    assert snapshot.inbox_count == 0
    assert snapshot.archive_recent_count == 1
    assert snapshot.alerts == []


def test_snapshot_raises_operator_alerts_for_faults(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    paths = resolve_bridge_paths(root)
    for directory in (paths.inbox, paths.archive, paths.rejected, paths.outbox, paths.logs, paths.state):
        directory.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    reject_1 = (now - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reject_2 = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_json(
        paths.state_file,
        {
            "LastInstructionId": "msg-2",
            "SignalIntakeEnabled": False,
            "HeartbeatFaulted": True,
            "DailyLockout": False,
            "ShellMode": "Disabled",
        },
    )
    _write(
        paths.log_file,
        "2026-04-17T12:00:00Z|HEALTH|instr=msg-2|template=runner_reversal_template|mode=Disabled|pos=Flat|qty=0|msg=reason=PERIODIC shell_mode=Disabled intake=False heartbeat_faulted=True daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=1 last_msg_utc=2026-04-17T11:58:00Z",
    )
    _write_json(
        paths.outbox / "20260417_115900000_REJECTED_msg-2.evt.json",
        {
            "timestamp_utc": reject_1,
            "status": "REJECTED",
            "instruction_id": "msg-2",
            "shell_mode": "Disabled",
            "position": "Flat",
            "quantity": 0,
            "detail": "id=msg-2;action=ENTER_LONG;reason=intake_disabled",
        },
    )
    _write_json(
        paths.outbox / "20260417_115910000_REJECTED_msg-3.evt.json",
        {
            "timestamp_utc": reject_2,
            "status": "REJECTED",
            "instruction_id": "msg-3",
            "shell_mode": "Disabled",
            "position": "Flat",
            "quantity": 0,
            "detail": "id=msg-3;action=ENTER_LONG;reason=intake_disabled",
        },
    )
    _write_json(paths.inbox / "stuck-msg.json", {"message_id": "stuck-msg"})

    snapshot = build_snapshot(
        paths,
        recent_minutes=60,
        tail_lines=10,
        stuck_seconds=0,
        heartbeat_timeout_seconds=60,
        pending_seconds=90,
        ready_grace_seconds=30,
        reject_threshold=2,
    )

    messages = [alert.message for alert in snapshot.alerts]
    assert any("Heartbeat faulted" in message for message in messages)
    assert any("Intake disabled while flat" in message for message in messages)
    assert any("Inbox file stuck" in message for message in messages)
    assert any("Repeated rejects" in message for message in messages)


def test_render_snapshot_contains_operator_sections(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    paths = resolve_bridge_paths(root)
    for directory in (paths.inbox, paths.archive, paths.rejected, paths.outbox, paths.logs, paths.state):
        directory.mkdir(parents=True, exist_ok=True)
    _write_json(paths.state_file, {"ShellMode": "Idle", "SignalIntakeEnabled": True, "HeartbeatFaulted": False})

    snapshot = build_snapshot(
        paths,
        recent_minutes=15,
        tail_lines=5,
        stuck_seconds=60,
        heartbeat_timeout_seconds=60,
        pending_seconds=90,
        ready_grace_seconds=30,
        reject_threshold=2,
    )
    rendered = render_snapshot(snapshot, recent_minutes=15, stuck_seconds=60)

    assert "CURRENT STATUS" in rendered
    assert "LAST SIGNAL / TRADE" in rendered
    assert "BRIDGE HEALTH" in rendered
    assert "ALERTS" in rendered
    assert "RECENT EVENT FEED" in rendered


def test_recent_feed_prefers_current_session_and_dedupes_repeated_health(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    paths = resolve_bridge_paths(root)
    for directory in (paths.inbox, paths.archive, paths.rejected, paths.outbox, paths.logs, paths.state):
        directory.mkdir(parents=True, exist_ok=True)

    _write_json(
        paths.state_file,
        {
            "ShellMode": "Idle",
            "SignalIntakeEnabled": True,
            "HeartbeatFaulted": False,
            "DailyLockout": False,
        },
    )
    _write(
        paths.log_file,
        "\n".join(
            [
                "2026-04-17T11:50:00Z|HEALTH|instr=old-1|template=runner_reversal_template|mode=Recovery|pos=Flat|qty=0|msg=reason=RECOVERY_TIMEOUT shell_mode=Recovery intake=False heartbeat_faulted=True daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T11:49:00Z",
                "2026-04-17T11:50:30Z|HEALTH|instr=old-1|template=runner_reversal_template|mode=Recovery|pos=Flat|qty=0|msg=reason=RECOVERY_TIMEOUT shell_mode=Recovery intake=False heartbeat_faulted=True daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T11:49:30Z",
                "2026-04-17T12:00:00Z|STARTUP_STATE|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False pos=Flat qty=0 template=runner_reversal_template pending_stop=0",
                "2026-04-17T12:00:01Z|STARTUP_INBOX|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=inbox_depth=0 inbox_dir=C:/ta_foundation/bridge/inbox",
                "2026-04-17T12:00:02Z|HEALTH|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=reason=STARTUP shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T12:00:02Z",
                "2026-04-17T12:00:10Z|HEALTH|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=reason=PERIODIC shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T12:00:10Z",
                "2026-04-17T12:00:20Z|HEALTH|instr=|template=runner_reversal_template|mode=Idle|pos=Flat|qty=0|msg=reason=PERIODIC shell_mode=Idle intake=True heartbeat_faulted=False daily_lockout=False active_position_id= pos_side=Flat pos_qty=0 avg=0 entry_order=<null> stop_order=<null> target_order=<null> exit_order=<null> pending_stop=0 pending_target_ticks=0 queue_depth=0 last_msg_utc=2026-04-17T12:00:20Z",
            ]
        ),
    )
    _write_json(
        paths.outbox / "20260417_120002000_SHELL_READY_boot.evt.json",
        {
            "timestamp_utc": "2026-04-17T12:00:02Z",
            "status": "SHELL_READY",
            "instruction_id": "boot",
            "shell_mode": "Idle",
            "position": "Flat",
            "quantity": 0,
            "detail": "mode=Idle;intake=True;hb_faulted=False;daily_lockout=False;recovered_mode=Flat;inbox_depth=0",
        },
    )

    snapshot = build_snapshot(
        paths,
        recent_minutes=60,
        tail_lines=20,
        stuck_seconds=60,
        heartbeat_timeout_seconds=60,
        pending_seconds=90,
        ready_grace_seconds=30,
        reject_threshold=2,
    )

    feed_text = "\n".join(snapshot.recent_feed)
    assert "RECOVERY_TIMEOUT" not in feed_text
    assert "STARTUP_STATE" in feed_text
    assert "STARTUP_INBOX" in feed_text
    assert feed_text.count("LOG HEALTH | reason=PERIODIC mode=Idle intake=True hb_faulted=False queue=0") == 1
    assert snapshot.last_ready_at is not None
    assert snapshot.last_ready_at.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-04-17T12:00:02Z"


def test_read_state_file_uses_shared_reader(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "shell_state.json"
    state_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        soak_monitor,
        "_read_text_shared",
        lambda path, **kwargs: json.dumps({"ShellMode": "Idle", "SignalIntakeEnabled": True}),
    )

    state = read_state_file(state_file)

    assert state["ShellMode"] == "Idle"
    assert state["SignalIntakeEnabled"] is True
