from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.cli import bridge_operator


def test_write_pause_control_file(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"

    path = bridge_operator.write_loop_control_file(
        bridge_root,
        pause_new_entries=True,
        stop_requested=False,
        reason="operator_pause",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pause_new_entries"] is True
    assert payload["stop_requested"] is False
    assert payload["reason"] == "operator_pause"


def test_clear_control_file(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_operator.write_loop_control_file(
        bridge_root,
        pause_new_entries=True,
        stop_requested=True,
        reason="operator_stop",
    )

    path = bridge_operator.clear_loop_control_file(bridge_root)

    assert path == bridge_operator.control_file_path(bridge_root)
    assert not path.exists()


def test_format_status_lines_uses_shell_summary_and_control_fields(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    (bridge_root / "state").mkdir(parents=True, exist_ok=True)
    (bridge_root / "logs").mkdir(parents=True, exist_ok=True)

    (bridge_root / "state" / "shell_state.json").write_text(
        json.dumps(
            {
                "ShellMode": "Idle",
                "SignalIntakeEnabled": True,
                "HeartbeatFaulted": False,
                "PositionId": "",
            }
        ),
        encoding="utf-8",
    )
    (bridge_root / "logs" / "python_strategy_loop_summary.json").write_text(
        json.dumps(
            {
                "loop_state": "IDLE",
                "session": "2026-04-18",
                "signals_session": 1,
                "completed_trades_session": 1,
                "session_net_ticks": 30.0,
                "stop_reason": None,
            }
        ),
        encoding="utf-8",
    )
    bridge_operator.write_loop_control_file(
        bridge_root,
        pause_new_entries=True,
        stop_requested=False,
        reason="operator_pause",
    )

    lines = bridge_operator.format_status_lines(bridge_operator.read_status_snapshot(bridge_root))
    text = "\n".join(lines)

    assert "shell_mode=Idle" in text
    assert "loop_state=IDLE" in text
    assert "pause_new_entries=True" in text
    assert "reason=operator_pause" in text
