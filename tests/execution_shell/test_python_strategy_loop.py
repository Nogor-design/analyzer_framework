from __future__ import annotations

import json
import os
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ta_foundation.strategies.TaFoundationExecutionBridge.real_strategy_loop as real_strategy_loop
from ta_foundation.strategies.TaFoundationExecutionBridge.bridge_sender import (
    ResearchDecision,
    build_signal,
    default_template_dir,
)
from ta_foundation.strategies.TaFoundationExecutionBridge.real_strategy_loop import (
    LoopConfig,
    LoopState,
    StrategyCandidate,
    StrategyLoop,
)


def _write_shell_state(
    state_path: Path,
    *,
    signal_intake_enabled: bool = True,
    heartbeat_faulted: bool = False,
    daily_lockout: bool = False,
    shell_mode: str = "Idle",
    position_id: str = "",
    state_timestamp: datetime | None = None,
) -> None:
    now = (state_timestamp or datetime.now(timezone.utc)).isoformat()
    payload = {
        "ActiveTemplate": "",
        "LastInstructionId": "",
        "SignalIntakeEnabled": signal_intake_enabled,
        "HeartbeatFaulted": heartbeat_faulted,
        "DailyLockout": daily_lockout,
        "CurrentTradingDay": "",
        "LastBridgeMessageUtc": now,
        "ShellMode": shell_mode,
        "PositionId": position_id,
        "PendingStopPrice": 0,
        "PendingTargetTicks": 0,
        "ProcessedIds": [],
        "LastHealthSnapshotUtc": now,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def _write_market_file(path: Path, closes: list[float], *, start: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for idx, close in enumerate(closes):
        bar_dt = start + timedelta(minutes=idx)
        open_px = closes[idx - 1] if idx else close + 1.0
        high_px = max(open_px, close) + 1.5
        low_px = min(open_px, close) - 1.5
        line = (
            f"{bar_dt:%Y%m%d %H%M%S};"
            f"{open_px:.2f};{high_px:.2f};{low_px:.2f};{close:.2f};100"
        )
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_loop_control(
    state_dir: Path,
    *,
    pause_new_entries: bool = False,
    stop_requested: bool = False,
    reason: str = "operator_test",
) -> None:
    payload = {
        "pause_new_entries": pause_new_entries,
        "stop_requested": stop_requested,
        "reason": reason,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }
    (state_dir / "python_strategy_loop_control.json").write_text(json.dumps(payload), encoding="utf-8")


def _prepare_bridge_root(root: Path) -> None:
    for name in ("inbox", "archive", "rejected", "outbox", "logs", "state"):
        (root / name).mkdir(parents=True, exist_ok=True)


def _candidate_market_closes() -> list[float]:
    return [100.0 + idx for idx in range(22)] + [145.0, 146.0]


def _always_long_candidate(self: StrategyLoop, bars, now) -> StrategyCandidate | None:
    bar_dt = self._get_completed_bar_dt(bars)
    if bar_dt is None:
        return None
    return StrategyCandidate(
        side="LONG",
        bar_dt=bar_dt,
        confidence=0.7,
        thesis_id=f"test_long_{bar_dt:%Y%m%d_%H%M}",
        reason="test candidate",
        close_price=150.0,
        ema_value=140.0,
        ema_slope=1.0,
        body_size=8.0,
        average_body_size=3.0,
    )


def _write_outbox_event(
    outbox_dir: Path,
    message_id: str,
    status: str,
    *,
    detail: str,
    idx: int,
) -> None:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "instruction_id": message_id,
        "shell_mode": "Idle",
        "position": "Flat",
        "quantity": 0,
        "detail": detail,
    }
    path = outbox_dir / f"20260418_140000{idx:03d}_{status}_{message_id}.evt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_signal_reuses_runner_route_for_explosive_start() -> None:
    payload = build_signal(
        ResearchDecision(
            instrument="NQ 06-26",
            timeframe="1m",
            early_path="explosive_start",
            confidence=0.61,
            thesis_id="explosive_start_runner_reversal_long_20260417_0830",
            side="SHORT",
        ),
        template_dir=default_template_dir(),
    )

    assert payload["template_name"] == "runner_reversal_template"
    assert payload["action"] == "ENTER_SHORT"
    assert payload["side"] == "SHORT"
    assert payload["target_mode"] == "partial_then_runner"
    assert payload["target_ticks"] == 30
    assert payload["partial_target_ticks"] == 20
    assert payload["stop_ticks"] == 42


def test_strategy_loop_uses_completed_bar_for_ema20_strong_body_signal(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=True,
            enable_heartbeat=False,
        )
    )

    bars = loop.bar_reader.read()
    candidate = loop._evaluate_candidate(bars, datetime(2026, 4, 18, 0, 0))

    assert candidate is not None
    assert candidate.side == "LONG"
    assert candidate.close_price == 145.0
    assert candidate.bar_dt == loop._normalize_bar_dt(bars.iloc[-2]["dt"])
    assert candidate.thesis_id.startswith("ema20_strong_body_long_")


def test_strategy_loop_evaluates_each_completed_bar_once(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=True,
            enable_heartbeat=False,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    assert loop.last_send_bar_dt is None
    baseline_bar_dt = loop.last_evaluated_bar_dt

    closes = _candidate_market_closes() + [147.0]
    _write_market_file(market_file, closes, start=start)
    loop.step()
    first_bar_dt = loop.last_send_bar_dt

    loop.step()

    assert loop.state == LoopState.COOLDOWN
    assert loop.session_signal_count == 1
    assert loop.last_send_bar_dt == first_bar_dt
    assert baseline_bar_dt is not None
    assert not list((bridge_root / "inbox").glob("*.json"))


def test_strategy_loop_reject_cooldown_uses_completed_bars(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    assert not list((bridge_root / "inbox").glob("*.json"))

    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)
    loop.step()
    inbox_files = list((bridge_root / "inbox").glob("*.json"))
    assert len(inbox_files) == 1

    rejected_path = bridge_root / "rejected" / inbox_files[0].name
    inbox_files[0].replace(rejected_path)
    loop.step()

    assert loop.state == LoopState.COOLDOWN
    assert loop.session_reject_count == 1
    assert not list((bridge_root / "inbox").glob("*.json"))

    for idx in range(9):
        closes.append(closes[-1] + 1.0)
        _write_market_file(market_file, closes, start=start)
        loop.step()
        assert not list((bridge_root / "inbox").glob("*.json")), f"unexpected resend on cooldown bar {idx + 1}"

    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)
    loop.step()

    assert len(list((bridge_root / "inbox").glob("*.json"))) == 1
    assert loop.state == LoopState.SIGNAL_PENDING
    assert loop.session_signal_count == 2


def test_strategy_loop_unresolved_pending_escalates_after_bar_limit(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
            unresolved_signal_timeout_bars=2,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    assert loop.outstanding is None

    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)
    loop.step()
    assert loop.outstanding is not None

    for _ in range(3):
        closes.append(closes[-1] + 1.0)
        _write_market_file(market_file, closes, start=start)
        loop.step()

    assert loop.state == LoopState.HARD_HOLD
    assert loop.outstanding is not None


def test_strategy_loop_enters_hard_hold_when_intake_disabled(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(
        bridge_root / "state" / "shell_state.json",
        signal_intake_enabled=False,
        shell_mode="Disabled",
    )
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
        )
    )

    loop.step()

    assert loop.state == LoopState.HARD_HOLD
    assert not list((bridge_root / "inbox").glob("*.json"))


def test_strategy_loop_pause_control_blocks_new_entry(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    _write_loop_control(bridge_root / "state", pause_new_entries=True, reason="operator_pause")
    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)

    loop.step()

    assert loop.state == LoopState.IDLE
    assert loop.session_signal_count == 0
    assert not list((bridge_root / "inbox").glob("*.json"))
    summary = json.loads((bridge_root / "logs" / "python_strategy_loop_summary.json").read_text(encoding="utf-8"))
    assert summary["control_pause_new_entries"] is True
    assert summary["control_reason"] == "operator_pause"


def test_strategy_loop_stop_control_requests_graceful_exit_when_flat(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
        )
    )

    _write_loop_control(bridge_root / "state", pause_new_entries=True, stop_requested=True, reason="operator_stop")
    loop.step()

    assert loop.stop_requested is True
    assert loop.summary_stop_reason == "operator_stop_requested:operator_stop"
    summary = json.loads((bridge_root / "logs" / "python_strategy_loop_summary.json").read_text(encoding="utf-8"))
    assert summary["control_stop_requested"] is True
    assert summary["stop_reason"] == "operator_stop_requested:operator_stop"


def test_strategy_loop_allows_playback_bars_older_than_wall_clock(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=True,
            enable_heartbeat=False,
        )
    )

    bars = loop.bar_reader.read()
    candidate = loop._evaluate_candidate(bars, datetime(2026, 4, 18, 0, 0))

    assert candidate is not None
    assert candidate.bar_dt.date().isoformat() == "2026-04-10"


def test_strategy_loop_sends_heartbeat_before_stale_state_hold(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    _write_shell_state(
        bridge_root / "state" / "shell_state.json",
        state_timestamp=stale,
    )
    os.utime(bridge_root / "state" / "shell_state.json", (stale.timestamp(), stale.timestamp()))
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=True,
            heartbeat_interval_seconds=20,
            heartbeat_timeout_seconds=60,
        )
    )

    loop.step()

    inbox_files = list((bridge_root / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["action"] == "HEARTBEAT"
    assert loop.state == LoopState.SOFT_HOLD


def test_strategy_loop_ignores_stop_cancel_after_filled_trade(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)
    loop.step()

    assert loop.outstanding is not None
    message_id = loop.outstanding.message_id

    events = [
        {"status": "ACCEPTED", "detail": "action=ENTER_LONG"},
        {"status": "FILLED", "detail": "signal=TF_ENTER_LONG;price=25349.25"},
        {"status": "STOP_ATTACHED", "detail": "signal=TF_STOP_LONG;stop_price=25338.75"},
        {"status": "ENTRY_SUBMITTED", "detail": "signal=TF_ENTER_LONG"},
        {"status": "ORDER_CANCELLED", "detail": "signal=TF_STOP_LONG;order_id=abc123"},
    ]
    for idx, event in enumerate(events):
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": event["status"],
            "instruction_id": message_id,
            "shell_mode": "Idle",
            "position": "Flat",
            "quantity": 0,
            "detail": event["detail"],
        }
        path = bridge_root / "outbox" / f"20260418_140000{idx:03d}_{event['status']}_{message_id}.evt.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

    _write_shell_state(bridge_root / "state" / "shell_state.json", shell_mode="Idle", position_id="")
    loop.step()

    assert loop.state == LoopState.COOLDOWN
    assert loop.cooldown_reason == f"trade_flat:{message_id}"
    assert loop.session_reject_count == 0


def test_strategy_loop_enters_soft_hold_when_market_data_export_stalls(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, _candidate_market_closes(), start=start)
    stale = datetime.now(timezone.utc) - timedelta(seconds=180)
    os.utime(market_file, (stale.timestamp(), stale.timestamp()))

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=True,
            enable_heartbeat=False,
            market_data_stale_seconds=60,
        )
    )

    loop.step()

    assert loop.state == LoopState.SOFT_HOLD


def test_strategy_loop_hard_holds_after_consecutive_losses(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
            cooldown_bars_after_send=0,
            same_direction_suppression_bars=0,
            max_consecutive_losses=2,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    for loss_idx in range(2):
        closes.append(closes[-1] + 1.0)
        _write_market_file(market_file, closes, start=start)
        loop.step()

        assert loop.outstanding is not None
        message_id = loop.outstanding.message_id
        base_idx = loss_idx * 10
        _write_outbox_event(
            bridge_root / "outbox",
            message_id,
            "ACCEPTED",
            detail="action=ENTER_LONG",
            idx=base_idx,
        )
        _write_outbox_event(
            bridge_root / "outbox",
            message_id,
            "FILLED",
            detail="signal=TF_ENTER_LONG;price=25349.25",
            idx=base_idx + 1,
        )
        _write_outbox_event(
            bridge_root / "outbox",
            message_id,
            "STOP_ATTACHED",
            detail="signal=TF_STOP_LONG;stop_price=25338.75;target_price=25356.75",
            idx=base_idx + 2,
        )
        heartbeat_id = f"hb_{loss_idx}"
        _write_outbox_event(
            bridge_root / "outbox",
            heartbeat_id,
            "FILLED",
            detail="signal=TF_STOP_LONG;price=25338.75",
            idx=base_idx + 3,
        )
        _write_shell_state(bridge_root / "state" / "shell_state.json", shell_mode="Idle", position_id="")
        loop.step()
        loop.step()

    assert loop.session_losses == 2
    assert loop.session_consecutive_losses == 2
    assert loop.state == LoopState.HARD_HOLD


def test_strategy_loop_writes_summary_report_for_completed_trade(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    closes = _candidate_market_closes()
    _prepare_bridge_root(bridge_root)
    _write_shell_state(bridge_root / "state" / "shell_state.json")
    _write_market_file(market_file, closes, start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=False,
            enable_heartbeat=False,
            cooldown_bars_after_send=0,
        )
    )
    loop._evaluate_candidate = types.MethodType(_always_long_candidate, loop)

    loop.step()
    closes.append(closes[-1] + 1.0)
    _write_market_file(market_file, closes, start=start)
    loop.step()

    assert loop.outstanding is not None
    message_id = loop.outstanding.message_id
    _write_outbox_event(
        bridge_root / "outbox",
        message_id,
        "ACCEPTED",
        detail="action=ENTER_LONG",
        idx=0,
    )
    _write_outbox_event(
        bridge_root / "outbox",
        message_id,
        "FILLED",
        detail="signal=TF_ENTER_LONG;price=25349.25",
        idx=1,
    )
    _write_outbox_event(
        bridge_root / "outbox",
        message_id,
        "STOP_ATTACHED",
        detail="signal=TF_STOP_LONG;stop_price=25338.75;target_price=25356.75",
        idx=2,
    )
    _write_outbox_event(
        bridge_root / "outbox",
        "heartbeat_exit",
        "FILLED",
        detail="signal=TF_TARGET_LONG;price=25356.75",
        idx=3,
    )
    _write_shell_state(bridge_root / "state" / "shell_state.json", shell_mode="Idle", position_id="")

    loop.step()

    summary = json.loads((bridge_root / "logs" / "python_strategy_loop_summary.json").read_text(encoding="utf-8"))
    assert summary["completed_trades_session"] == 1
    assert summary["wins_session"] == 1
    assert summary["losses_session"] == 0
    assert summary["session_net_ticks"] == 30.0
    assert summary["last_trade"]["outcome"] == "win"
    assert summary["last_trade"]["exit_signal"] == "TF_TARGET_LONG"


def test_read_shell_snapshot_uses_shared_reader(tmp_path: Path, monkeypatch) -> None:
    bridge_root = tmp_path / "bridge"
    market_file = tmp_path / "market" / "NQ 06-26.Last.txt"
    start = datetime(2026, 4, 18, 14, 0, tzinfo=timezone.utc)
    _prepare_bridge_root(bridge_root)
    _write_market_file(market_file, _candidate_market_closes(), start=start)

    loop = StrategyLoop(
        LoopConfig(
            bridge_root=bridge_root,
            market_data_file=market_file,
            dry_run=True,
        )
    )
    loop.paths.shell_state.write_text("{}", encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        real_strategy_loop,
        "_read_text_shared",
        lambda path, **kwargs: json.dumps(
            {
                "SignalIntakeEnabled": True,
                "HeartbeatFaulted": False,
                "DailyLockout": False,
                "ShellMode": "Idle",
                "PositionId": "",
                "LastBridgeMessageUtc": now,
                "LastHealthSnapshotUtc": now,
                "ActiveTemplate": "runner_reversal_template",
                "LastInstructionId": "shared-read-msg",
            }
        ),
    )

    snapshot = loop._read_shell_snapshot()

    assert snapshot is not None
    assert snapshot.last_instruction_id == "shared-read-msg"
    assert snapshot.active_template == "runner_reversal_template"
