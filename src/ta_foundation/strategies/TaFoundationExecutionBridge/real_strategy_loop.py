from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ta_foundation.analysis.indicators.basic import ema
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.strategies.TaFoundationExecutionBridge.bridge_sender import (
    DEFAULT_EXPIRY_SECONDS,
    ResearchDecision,
    build_signal,
    default_template_dir,
    publish_heartbeat,
    submit_payload,
)
from ta_foundation.strategies.TaFoundationExecutionBridge.execution_runtime_client import (
    ExecutionRuntimeClient,
    RuntimeEndpoint,
)


def _read_text_shared(path: Path, *, encoding: str = "utf-8-sig", errors: str = "strict") -> str:
    if os.name != "nt":
        return path.read_text(encoding=encoding, errors=errors)

    import msvcrt

    generic_read = 0x80000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle_value = ctypes.c_void_p(-1).value

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p

    handle = create_file(
        str(path),
        generic_read,
        share_read | share_write | share_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), f"unable to open shared-read handle for {path}")

    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except Exception:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise

    try:
        with os.fdopen(fd, "r", encoding=encoding, errors=errors) as stream:
            return stream.read()
    except Exception:
        os.close(fd)
        raise


class LoopState(str, Enum):
    IDLE = "IDLE"
    SIGNAL_PENDING = "SIGNAL_PENDING"
    TRADE_ACTIVE = "TRADE_ACTIVE"
    COOLDOWN = "COOLDOWN"
    SOFT_HOLD = "SOFT_HOLD"
    HARD_HOLD = "HARD_HOLD"


@dataclass(frozen=True)
class BridgePaths:
    root: Path
    inbox: Path
    archive: Path
    rejected: Path
    outbox: Path
    logs: Path
    state: Path
    shell_state: Path
    loop_control: Path

    @classmethod
    def from_root(cls, root: Path) -> "BridgePaths":
        return cls(
            root=root,
            inbox=root / "inbox",
            archive=root / "archive",
            rejected=root / "rejected",
            outbox=root / "outbox",
            logs=root / "logs",
            state=root / "state",
            shell_state=root / "state" / "shell_state.json",
            loop_control=root / "state" / "python_strategy_loop_control.json",
        )


@dataclass
class ShellSnapshot:
    signal_intake_enabled: bool
    heartbeat_faulted: bool
    daily_lockout: bool
    shell_mode: str
    position_id: str
    last_bridge_message_utc: datetime | None
    last_health_snapshot_utc: datetime | None
    active_template: str
    last_instruction_id: str
    state_mtime_utc: datetime | None


@dataclass
class LoopControl:
    pause_new_entries: bool = False
    stop_requested: bool = False
    reason: str | None = None
    updated_utc: datetime | None = None


@dataclass
class StrategyCandidate:
    side: str
    bar_dt: datetime
    confidence: float
    thesis_id: str
    reason: str
    close_price: float
    ema_value: float
    ema_slope: float
    body_size: float
    average_body_size: float
    early_path: str = "explosive_start"


@dataclass
class OutstandingSignal:
    message_id: str
    thesis_id: str
    side: str
    bar_dt: datetime
    sent_at: datetime
    statuses_seen: set[str] = field(default_factory=set)
    archived: bool = False
    rejected: bool = False
    filled: bool = False
    entry_price: float | None = None
    exit_price: float | None = None
    exit_signal: str | None = None
    stop_price: float | None = None
    target_price: float | None = None


@dataclass
class CompletedTradeRecord:
    message_id: str
    thesis_id: str
    side: str
    entry_bar_dt: datetime
    exit_bar_dt: datetime | None
    hold_bars: int
    outcome: str
    pnl_ticks: float | None
    entry_price: float | None
    exit_price: float | None
    exit_signal: str | None


@dataclass
class LoopConfig:
    bridge_root: Path
    market_data_file: Path
    instrument: str = "NQ 06-26"
    account_name: str = "Sim101"
    runtime_host: str = "127.0.0.1"
    runtime_port: int = 8766
    timeframe: str = "1m"
    template_dir: Path = field(default_factory=default_template_dir)
    poll_interval_seconds: float = 1.0
    heartbeat_interval_seconds: float = 20.0
    heartbeat_timeout_seconds: int = 60
    max_soft_hold_cycles: int = 3
    ema_period: int = 20
    startup_warmup_bars: int = 21
    average_body_lookback_bars: int = 10
    strong_body_multiplier: float = 1.5
    cooldown_bars_after_send: int = 5
    cooldown_bars_after_reject: int = 10
    same_direction_suppression_bars: int = 5
    unresolved_signal_timeout_bars: int = 2
    max_signals_per_session: int = 4
    max_rejects_per_session: int = 2
    max_consecutive_losses: int = 2
    max_session_loss_ticks: float = 84.0
    market_data_stale_seconds: int = 150
    tick_size: float = 0.25
    dry_run: bool = True
    enable_heartbeat: bool = True
    signal_expiry_seconds: int = DEFAULT_EXPIRY_SECONDS
    max_iterations: int | None = None


class MinuteBarFileReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.parser = MinuteBarsLastTxtParser()
        self._last_signature: tuple[int, int] | None = None
        self._cached: pd.DataFrame | None = None

    def read(self) -> pd.DataFrame | None:
        if not self.path.exists():
            return None

        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._last_signature and self._cached is not None:
            return self._cached.copy()

        try:
            parsed = self.parser.parse(self.path, run_id=None)
            cached = parsed.df.copy() if parsed.df is not None else None
        except Exception:
            cached = self._fallback_read_utc_bars()

        self._last_signature = signature
        self._cached = cached
        return self._cached.copy() if self._cached is not None else None

    def _fallback_read_utc_bars(self) -> pd.DataFrame | None:
        rows: list[tuple[pd.Timestamp, float, float, float, float, int]] = []

        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                row = line.strip()
                if not row:
                    continue
                low = row.lower()
                if ("date" in low and "open" in low) or low.startswith("time") or low.startswith("datetime"):
                    continue

                if " " in row and ";" in row:
                    date_part, rest = row.split(" ", 1)
                    fields = rest.split(";")
                    if len(fields) < 6:
                        continue
                    time_part = fields[0].replace(":", "")
                    o, h, l, c, v = fields[1:6]
                else:
                    fields = row.split(";")
                    if len(fields) < 7:
                        continue
                    date_part = fields[0]
                    time_part = fields[1].replace(":", "")
                    o, h, l, c, v = fields[2:7]

                dt = pd.to_datetime(
                    f"{date_part}{time_part}",
                    format="%Y%m%d%H%M%S",
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(dt):
                    continue
                rows.append((dt, float(o), float(h), float(l), float(c), int(float(v))))

        if not rows:
            return None

        frame = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
        return frame.sort_values("dt").drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)


class StrategyLoop:
    def __init__(self, config: LoopConfig) -> None:
        bridge_template_dir = config.bridge_root / "templates"
        if bridge_template_dir.exists():
            config.template_dir = bridge_template_dir
        elif not config.template_dir.exists():
            config.template_dir = default_template_dir()

        self.config = config
        self.paths = BridgePaths.from_root(config.bridge_root)
        self.bar_reader = MinuteBarFileReader(config.market_data_file)
        self.state = LoopState.IDLE
        self.cooldown_anchor_bar_dt: datetime | None = None
        self.cooldown_bars: int = 0
        self.cooldown_reason: str | None = None
        self.outstanding: OutstandingSignal | None = None
        self.last_evaluated_bar_dt: datetime | None = None
        self.last_send_bar_dt: datetime | None = None
        self.last_direction_sent_bar_dt: dict[str, datetime] = {}
        self.last_heartbeat_sent_at: datetime | None = None
        self.last_hold_reason: str | None = None
        self.soft_hold_cycles = 0
        self.current_session_key: str | None = None
        self.session_signal_count = 0
        self.session_reject_count = 0
        self.log_file = self.paths.logs / "python_strategy_loop.log"
        self.summary_file = self.paths.logs / "python_strategy_loop_summary.json"
        self.processed_outbox_files: set[str] = set()
        self.session_completed_trades = 0
        self.session_wins = 0
        self.session_losses = 0
        self.session_unknown_outcomes = 0
        self.session_net_ticks = 0.0
        self.session_consecutive_losses = 0
        self.completed_trades: list[CompletedTradeRecord] = []
        self.run_started_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        self.last_step_utc: datetime | None = None
        self.last_control = LoopControl()
        self.process_id = os.getpid()
        self.stop_requested = False
        self.summary_stop_reason: str | None = None
        self.runtime_client = ExecutionRuntimeClient(
            endpoint=RuntimeEndpoint(host=config.runtime_host, port=config.runtime_port)
        )

    def run(self) -> None:
        iteration = 0
        try:
            self.runtime_client.ensure_connected()
            while True:
                self.step()
                if self.stop_requested:
                    return
                iteration += 1
                if self.config.max_iterations is not None and iteration >= self.config.max_iterations:
                    self.summary_stop_reason = self.summary_stop_reason or "max_iterations_reached"
                    return
                time.sleep(self.config.poll_interval_seconds)
        finally:
            self.runtime_client.close()
            self._write_summary()

    def step(self) -> None:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self.last_step_utc = now
            bars = self.bar_reader.read()
            snapshot = self._read_shell_snapshot()
            self.last_control = self._read_loop_control()
            self._maybe_emit_heartbeat(now)
            hold_state, hold_reason = self._evaluate_bridge_health(snapshot, now)
            self._refresh_outstanding(snapshot, bars, now)

            if self._maybe_graceful_stop(snapshot):
                return

            completed_bar_dt = self._get_completed_bar_dt(bars)
            new_completed_bar = completed_bar_dt is not None and (
                self.last_evaluated_bar_dt is None or completed_bar_dt > self.last_evaluated_bar_dt
            )

            if hold_state == LoopState.HARD_HOLD:
                self._enter_hold(hold_state, hold_reason)
                if new_completed_bar and completed_bar_dt is not None:
                    self._log_bar_decision(completed_bar_dt, None, f"bridge_hold:{hold_reason}")
                    self.last_evaluated_bar_dt = completed_bar_dt
                return

            if self.state == LoopState.HARD_HOLD:
                return

            if hold_state == LoopState.SOFT_HOLD:
                self._enter_hold(hold_state, hold_reason)
                if new_completed_bar and completed_bar_dt is not None:
                    self._log_bar_decision(completed_bar_dt, None, f"bridge_soft_hold:{hold_reason}")
                    self.last_evaluated_bar_dt = completed_bar_dt
                return

            if self.state == LoopState.SOFT_HOLD:
                self._log("INFO", f"fault_hold_cleared reason={self.last_hold_reason or 'unknown'}")
                self._set_state(LoopState.IDLE)
            self.soft_hold_cycles = 0
            self.last_hold_reason = None
            if bars is None or bars.empty:
                self._log("WARN", f"no_market_bars path={self.config.market_data_file}")
                return

            if completed_bar_dt is None:
                return

            if self.last_evaluated_bar_dt is None:
                self._reset_session_if_needed(completed_bar_dt)
                self.last_evaluated_bar_dt = completed_bar_dt
                self._set_state(LoopState.IDLE)
                self._log_bar_decision(completed_bar_dt, None, "startup_baseline")
                return

            if not new_completed_bar:
                return

            self._reset_session_if_needed(completed_bar_dt)

            if self.outstanding is not None:
                if snapshot and snapshot.shell_mode == "InPosition":
                    self._set_state(LoopState.TRADE_ACTIVE)
                else:
                    self._set_state(LoopState.SIGNAL_PENDING)
                self._log_bar_decision(completed_bar_dt, None, f"pending_lifecycle:{self.outstanding.message_id}")
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            cooldown_remaining = self._cooldown_bars_remaining(bars)
            if cooldown_remaining > 0:
                self._set_state(LoopState.COOLDOWN, detail=f"bars_remaining={cooldown_remaining}")
                self._log_bar_decision(
                    completed_bar_dt,
                    None,
                    f"cooldown_active reason={self.cooldown_reason or 'unknown'} bars_remaining={cooldown_remaining}",
                )
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            if snapshot is None or snapshot.shell_mode != "Idle" or snapshot.position_id:
                self._set_state(LoopState.IDLE)
                self._log_bar_decision(
                    completed_bar_dt,
                    None,
                    f"shell_not_ready mode={snapshot.shell_mode if snapshot else '<unknown>'} "
                    f"position_id={snapshot.position_id if snapshot else '<unknown>'}",
                )
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            completed_bars_count = self._completed_bars_count(bars)
            if completed_bars_count < self.config.startup_warmup_bars:
                self._set_state(LoopState.IDLE)
                self._log_bar_decision(
                    completed_bar_dt,
                    None,
                    f"warmup_active completed_bars={completed_bars_count} required={self.config.startup_warmup_bars}",
                )
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            operator_gate_reason = self._evaluate_operator_gate(snapshot)
            if operator_gate_reason is not None:
                self._set_state(LoopState.IDLE)
                self._log_bar_decision(completed_bar_dt, None, operator_gate_reason)
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            candidate = self._evaluate_candidate(bars, now)
            if candidate is None:
                self._set_state(LoopState.IDLE)
                self._log_bar_decision(completed_bar_dt, None, "no_signal")
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            gate_reason = self._evaluate_send_gate(candidate, bars, snapshot)
            if gate_reason is not None:
                self._log_bar_decision(completed_bar_dt, candidate, gate_reason)
                self.last_evaluated_bar_dt = completed_bar_dt
                return

            self._log(
                "INFO",
                "candidate_detected "
                f"side={candidate.side} thesis_id={candidate.thesis_id} "
                f"bar_dt={candidate.bar_dt.isoformat()} reason={candidate.reason}",
            )
            self.last_evaluated_bar_dt = completed_bar_dt
            self._dispatch_candidate(candidate, now)
        finally:
            self._write_summary()

    def _dispatch_candidate(self, candidate: StrategyCandidate, now: datetime) -> None:
        if self.last_send_bar_dt == candidate.bar_dt:
            self._enter_hold(
                LoopState.HARD_HOLD,
                f"duplicate_send_same_bar bar_dt={candidate.bar_dt.isoformat()}",
            )
            return

        decision = ResearchDecision(
            instrument=self.config.instrument,
            timeframe=self.config.timeframe,
            early_path=candidate.early_path,
            confidence=candidate.confidence,
            thesis_id=candidate.thesis_id,
            side=candidate.side,
            notes=candidate.reason,
            signal_expiry_seconds=self.config.signal_expiry_seconds,
        )
        payload = build_signal(decision, template_dir=self.config.template_dir)
        message_id = payload["message_id"]

        if self.config.dry_run:
            self._log(
                "INFO",
                "dry_run_signal "
                f"message_id={message_id} thesis_id={candidate.thesis_id} "
                f"side={candidate.side} template={payload['template_name']}",
            )
            self.last_send_bar_dt = candidate.bar_dt
            self.last_direction_sent_bar_dt[candidate.side] = candidate.bar_dt
            self.session_signal_count += 1
            self._start_cooldown(
                reason=f"dry_run:{message_id}",
                anchor_bar_dt=candidate.bar_dt,
                cooldown_bars=self.config.cooldown_bars_after_send,
            )
            return
        else:
            submit_payload(payload, client=self.runtime_client)
            self._log(
                "INFO",
                "signal_sent "
                f"message_id={message_id} thesis_id={candidate.thesis_id} "
                f"side={candidate.side} bar_dt={candidate.bar_dt.isoformat()} "
                f"ema20={candidate.ema_value:.2f} ema_slope={candidate.ema_slope:.4f} "
                f"body={candidate.body_size:.2f} avg_body={candidate.average_body_size:.2f} transport=socket",
            )

        self.last_send_bar_dt = candidate.bar_dt
        self.last_direction_sent_bar_dt[candidate.side] = candidate.bar_dt
        self.session_signal_count += 1
        self.outstanding = OutstandingSignal(
            message_id=message_id,
            thesis_id=candidate.thesis_id,
            side=candidate.side,
            bar_dt=candidate.bar_dt,
            sent_at=now,
        )
        self._set_state(LoopState.SIGNAL_PENDING)

    def _evaluate_candidate(self, bars: pd.DataFrame, now: datetime) -> StrategyCandidate | None:
        bars = bars.copy()
        bars = ema(bars, {"period": self.config.ema_period, "out": "ema_signal"})
        bars = bars.dropna(subset=["dt", "open", "high", "low", "close"])

        completed = bars.iloc[:-1].copy()
        if len(completed) < self.config.average_body_lookback_bars + 1:
            return None

        prev_bar = completed.iloc[-2]
        last_bar = completed.iloc[-1]
        if pd.isna(prev_bar["ema_signal"]) or pd.isna(last_bar["ema_signal"]):
            return None
        bar_dt = self._normalize_bar_dt(last_bar["dt"])

        recent_bodies = (
            completed.iloc[-(self.config.average_body_lookback_bars + 1):-1][["open", "close"]]
            .astype(float)
            .pipe(lambda frame: (frame["close"] - frame["open"]).abs())
        )
        average_body = float(recent_bodies.mean()) if not recent_bodies.empty else 0.0
        if average_body <= 0.0:
            return None

        last_open = float(last_bar["open"])
        last_close = float(last_bar["close"])
        ema_value = float(last_bar["ema_signal"])
        ema_prev = float(prev_bar["ema_signal"])
        ema_slope = ema_value - ema_prev
        body_size = abs(last_close - last_open)
        strong_body = body_size > self.config.strong_body_multiplier * average_body

        long_trigger = (
            strong_body
            and last_close > last_open
            and last_close > ema_value
            and ema_slope > 0.0
        )
        short_trigger = (
            strong_body
            and last_close < last_open
            and last_close < ema_value
            and ema_slope < 0.0
        )

        if not long_trigger and not short_trigger:
            return None

        side = "LONG" if long_trigger else "SHORT"
        body_ratio = body_size / average_body if average_body > 0 else 0.0
        confidence = min(0.8, 0.55 + max(0.0, (body_ratio - self.config.strong_body_multiplier) * 0.05))
        thesis_id = f"ema20_strong_body_{side.lower()}_{bar_dt.strftime('%Y%m%d_%H%M')}"
        reason = (
            f"strong_{side.lower()}_bar body={body_size:.2f} avg_body{self.config.average_body_lookback_bars}={average_body:.2f} "
            f"close={last_close:.2f} ema{self.config.ema_period}={ema_value:.2f} slope={ema_slope:.4f}"
        )
        return StrategyCandidate(
            side=side,
            bar_dt=bar_dt,
            confidence=min(confidence, 0.9),
            thesis_id=thesis_id,
            reason=reason,
            close_price=last_close,
            ema_value=ema_value,
            ema_slope=ema_slope,
            body_size=body_size,
            average_body_size=average_body,
        )

    def _evaluate_bridge_health(
        self,
        snapshot: ShellSnapshot | None,
        now: datetime,
    ) -> tuple[LoopState | None, str | None]:
        if snapshot is None:
            return self._soft_hold("runtime_state_unavailable")

        if self.config.market_data_stale_seconds > 0 and self.config.market_data_file.exists():
            market_file_age_seconds = (
                now - datetime.fromtimestamp(self.config.market_data_file.stat().st_mtime, timezone.utc).replace(tzinfo=None)
            ).total_seconds()
            if market_file_age_seconds > self.config.market_data_stale_seconds:
                return self._soft_hold(f"market_data_stale age_seconds={market_file_age_seconds:.1f}")

        if not snapshot.signal_intake_enabled:
            return (LoopState.HARD_HOLD, "intake_disabled_manual_reset_required")
        if snapshot.heartbeat_faulted:
            return (LoopState.HARD_HOLD, "heartbeat_faulted_manual_reset_required")
        if snapshot.daily_lockout:
            return (LoopState.HARD_HOLD, "daily_lockout_active")
        if snapshot.shell_mode in {"Disabled", "Recovery", "ExitPending"}:
            return (LoopState.HARD_HOLD, f"shell_mode={snapshot.shell_mode}")
        if snapshot.position_id and snapshot.shell_mode == "Idle":
            return (LoopState.HARD_HOLD, "idle_with_position_id_present")
        if snapshot.shell_mode in {"EntryPending", "InPosition"} and self.outstanding is None:
            return (LoopState.HARD_HOLD, f"shell_mode={snapshot.shell_mode}_without_local_pending_state")
        if self.session_reject_count >= self.config.max_rejects_per_session:
            return (LoopState.HARD_HOLD, f"max_rejects_reached count={self.session_reject_count}")
        if self.session_consecutive_losses >= self.config.max_consecutive_losses:
            return (LoopState.HARD_HOLD, f"max_consecutive_losses_reached count={self.session_consecutive_losses}")
        if -self.session_net_ticks >= self.config.max_session_loss_ticks:
            return (LoopState.HARD_HOLD, f"max_session_loss_ticks_reached ticks={-self.session_net_ticks:.2f}")

        freshness_marks = [stamp for stamp in (snapshot.last_health_snapshot_utc, snapshot.state_mtime_utc) if stamp is not None]
        if freshness_marks:
            freshest = max(freshness_marks)
            if (now - freshest).total_seconds() > self.config.heartbeat_timeout_seconds:
                return self._soft_hold(
                    f"shell_state_stale age_seconds={(now - freshest).total_seconds():.1f}"
                )

        return (None, None)

    def _refresh_outstanding(self, snapshot: ShellSnapshot | None, bars: pd.DataFrame | None, now: datetime) -> None:
        signal = self.outstanding
        if signal is None:
            return

        for event in self._read_new_outbox_events():
            if str(event.get("instruction_id") or event.get("signal_id") or "") != signal.message_id:
                if signal.filled:
                    self._capture_exit_fill(signal, event)
                continue
            if self._apply_outbox_event(signal, event):
                return

        if snapshot and snapshot.shell_mode == "InPosition":
            self._set_state(LoopState.TRADE_ACTIVE)
            return

        if signal.filled and snapshot and snapshot.shell_mode == "Idle" and snapshot.position_id == "":
            self._finalize_trade(signal, bars)
            return

        elapsed_bars = self._completed_bars_since(signal.bar_dt, bars)
        if elapsed_bars > self.config.unresolved_signal_timeout_bars:
            self._enter_hold(
                LoopState.HARD_HOLD,
                f"unresolved_signal_timeout message_id={signal.message_id} bars={elapsed_bars}",
            )

    def _maybe_emit_heartbeat(self, now: datetime) -> bool:
        if not self.config.enable_heartbeat or self.config.dry_run:
            return False

        if self.last_heartbeat_sent_at is not None:
            elapsed = (now - self.last_heartbeat_sent_at).total_seconds()
            if elapsed < self.config.heartbeat_interval_seconds:
                return False

        payload = publish_heartbeat(
            instrument=self.config.instrument,
            signal_expiry_seconds=self.config.signal_expiry_seconds,
            client=self.runtime_client,
        )
        self.last_heartbeat_sent_at = now
        self._log("INFO", f"heartbeat_sent message_id={payload['message_id']} transport=socket")
        return False

    def _read_shell_snapshot(self) -> ShellSnapshot | None:
        raw = self.runtime_client.latest_state_snapshot()
        if not raw:
            return None

        return ShellSnapshot(
            signal_intake_enabled=bool(raw.get("intake_enabled", True)),
            heartbeat_faulted=bool(raw.get("heartbeat_faulted", False)),
            daily_lockout=bool(raw.get("daily_lockout", False)),
            shell_mode=str(raw.get("runtime_state") or ""),
            position_id=str(raw.get("position_id") or ""),
            last_bridge_message_utc=self._parse_iso_datetime(raw.get("timestamp")),
            last_health_snapshot_utc=self._parse_iso_datetime(raw.get("timestamp")),
            active_template=str((raw.get("details") or {}).get("active_template") or ""),
            last_instruction_id=str(raw.get("signal_id") or ""),
            state_mtime_utc=self._parse_iso_datetime(raw.get("timestamp")),
        )

    def _read_loop_control(self) -> LoopControl:
        path = self.paths.loop_control
        if not path.exists():
            return LoopControl()
        try:
            raw = json.loads(_read_text_shared(path, encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return LoopControl(pause_new_entries=True, reason="control_file_unreadable")
        return LoopControl(
            pause_new_entries=bool(raw.get("pause_new_entries", False)),
            stop_requested=bool(raw.get("stop_requested", False)),
            reason=str(raw.get("reason") or "").strip() or None,
            updated_utc=self._parse_iso_datetime(raw.get("updated_utc")),
        )

    def _read_outbox_events(self, message_id: str) -> list[dict]:
        if not self.paths.outbox.exists():
            return []
        matched: list[dict] = []
        for path in sorted(self.paths.outbox.glob(f"*_{message_id}.evt.json")):
            try:
                matched.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except (json.JSONDecodeError, OSError):
                continue
        return matched

    def _read_new_outbox_events(self) -> list[dict]:
        matched: list[dict] = []
        for event in self.runtime_client.drain_events():
            payload = dict(event.payload)
            payload["status"] = str(payload.get("event") or "")
            payload["instruction_id"] = str(payload.get("signal_id") or "")
            detail = payload.get("details") or {}
            if isinstance(detail, dict):
                payload["detail"] = ";".join(f"{key}={value}" for key, value in detail.items() if value not in {None, ""})
            else:
                payload["detail"] = str(detail)
            matched.append(payload)
        return matched

    def _enter_hold(self, hold_state: LoopState, reason: str | None) -> None:
        if hold_state == LoopState.SOFT_HOLD:
            self.soft_hold_cycles += 1
            if self.soft_hold_cycles >= self.config.max_soft_hold_cycles:
                hold_state = LoopState.HARD_HOLD
                reason = f"soft_hold_escalated:{reason}"

        if self.state != hold_state or reason != self.last_hold_reason:
            self._set_state(hold_state)
            self.last_hold_reason = reason
            if hold_state == LoopState.HARD_HOLD:
                self.summary_stop_reason = reason
            self._log("WARN", f"fault_hold_entered state={hold_state.value} reason={reason}")

    def _soft_hold(self, reason: str) -> tuple[LoopState, str]:
        return (LoopState.SOFT_HOLD, reason)

    def _start_cooldown(self, reason: str, anchor_bar_dt: datetime, cooldown_bars: int) -> None:
        self.outstanding = None
        self.cooldown_anchor_bar_dt = anchor_bar_dt
        self.cooldown_bars = max(0, cooldown_bars)
        self.cooldown_reason = reason
        self._set_state(LoopState.COOLDOWN)
        self._log(
            "INFO",
            f"cooldown_started reason={reason} anchor_bar_dt={anchor_bar_dt.isoformat()} bars={self.cooldown_bars}",
        )

    def _set_state(self, new_state: LoopState, detail: str | None = None) -> None:
        if self.state == new_state and detail is None:
            return
        if self.state != new_state:
            self._log("INFO", f"state_change from={self.state.value} to={new_state.value}")
        elif detail:
            self._log("INFO", f"state={new_state.value} {detail}")
        self.state = new_state

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat()
        line = f"{timestamp}|{level}|loop_state={self.state.value}|{message}"
        print(line)
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _write_summary(self) -> None:
        last_trade = self.completed_trades[-1] if self.completed_trades else None
        payload = {
            "run_started_utc": self.run_started_utc.isoformat(),
            "last_updated_utc": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "last_step_utc": self.last_step_utc.isoformat() if self.last_step_utc else None,
            "process_id": self.process_id,
            "instrument": self.config.instrument,
            "account_name": self.config.account_name,
            "market_data_file": str(self.config.market_data_file),
            "bridge_root": str(self.config.bridge_root),
            "loop_state": self.state.value,
            "session": self.current_session_key,
            "signals_session": self.session_signal_count,
            "rejects_session": self.session_reject_count,
            "completed_trades_session": self.session_completed_trades,
            "wins_session": self.session_wins,
            "losses_session": self.session_losses,
            "unknown_outcomes_session": self.session_unknown_outcomes,
            "session_net_ticks": round(self.session_net_ticks, 2),
            "session_consecutive_losses": self.session_consecutive_losses,
            "average_hold_bars": round(self._average_hold_bars(), 2),
            "control_pause_new_entries": self.last_control.pause_new_entries,
            "control_stop_requested": self.last_control.stop_requested,
            "control_reason": self.last_control.reason,
            "control_updated_utc": self.last_control.updated_utc.isoformat() if self.last_control.updated_utc else None,
            "stop_reason": self.summary_stop_reason,
            "last_trade": None
            if last_trade is None
            else {
                "message_id": last_trade.message_id,
                "thesis_id": last_trade.thesis_id,
                "side": last_trade.side,
                "entry_bar_dt": last_trade.entry_bar_dt.isoformat(),
                "exit_bar_dt": last_trade.exit_bar_dt.isoformat() if last_trade.exit_bar_dt else None,
                "hold_bars": last_trade.hold_bars,
                "outcome": last_trade.outcome,
                "pnl_ticks": None if last_trade.pnl_ticks is None else round(last_trade.pnl_ticks, 2),
                "entry_price": last_trade.entry_price,
                "exit_price": last_trade.exit_price,
                "exit_signal": last_trade.exit_signal,
            },
        }
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        self.summary_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _parse_iso_datetime(value: object) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def _evaluate_send_gate(
        self,
        candidate: StrategyCandidate,
        bars: pd.DataFrame,
        snapshot: ShellSnapshot,
    ) -> str | None:
        if snapshot.shell_mode != "Idle" or snapshot.position_id:
            return f"shell_not_ready mode={snapshot.shell_mode} position_id={snapshot.position_id or '<empty>'}"
        if self.last_send_bar_dt == candidate.bar_dt:
            self._enter_hold(
                LoopState.HARD_HOLD,
                f"duplicate_send_same_bar bar_dt={candidate.bar_dt.isoformat()}",
            )
            return "duplicate_send_same_bar_bug"
        if self.session_signal_count >= self.config.max_signals_per_session:
            return f"session_signal_cap_reached count={self.session_signal_count}"

        same_direction_remaining = self._suppression_bars_remaining(candidate.side, bars)
        if same_direction_remaining > 0:
            return f"same_direction_suppression side={candidate.side} bars_remaining={same_direction_remaining}"
        return None

    def _evaluate_operator_gate(self, snapshot: ShellSnapshot | None) -> str | None:
        reason = self.last_control.reason or "operator_request"
        if self.last_control.stop_requested:
            return f"operator_stop_waiting_for_flat reason={reason}"
        if self.last_control.pause_new_entries:
            return f"operator_pause_active reason={reason}"
        return None

    def _maybe_graceful_stop(self, snapshot: ShellSnapshot | None) -> bool:
        if not self.last_control.stop_requested:
            return False
        if self.outstanding is not None:
            return False
        if snapshot is None or snapshot.shell_mode != "Idle" or bool(snapshot.position_id):
            return False
        reason = self.last_control.reason or "operator_request"
        self.summary_stop_reason = f"operator_stop_requested:{reason}"
        self.stop_requested = True
        self._set_state(LoopState.IDLE)
        self._log("INFO", f"operator_stop_acknowledged reason={reason}")
        return True

    def _reset_session_if_needed(self, bar_dt: datetime) -> None:
        session_key = bar_dt.strftime("%Y-%m-%d")
        if session_key == self.current_session_key:
            return

        self.current_session_key = session_key
        self.session_signal_count = 0
        self.session_reject_count = 0
        self.session_completed_trades = 0
        self.session_wins = 0
        self.session_losses = 0
        self.session_unknown_outcomes = 0
        self.session_net_ticks = 0.0
        self.session_consecutive_losses = 0
        self.last_direction_sent_bar_dt.clear()
        self._log("INFO", f"session_reset session={session_key}")

    def _get_completed_bar_dt(self, bars: pd.DataFrame | None) -> datetime | None:
        if bars is None or len(bars) < 2:
            return None
        return self._normalize_bar_dt(bars.iloc[-2]["dt"])

    def _completed_bars_count(self, bars: pd.DataFrame | None) -> int:
        if bars is None:
            return 0
        return max(0, len(bars) - 1)

    def _completed_bars_since(self, anchor_bar_dt: datetime, bars: pd.DataFrame | None) -> int:
        if bars is None or len(bars) < 2:
            return 0

        completed = bars.iloc[:-1]
        count = 0
        for value in completed["dt"]:
            bar_dt = self._normalize_bar_dt(value)
            if bar_dt > anchor_bar_dt:
                count += 1
        return count

    def _cooldown_bars_remaining(self, bars: pd.DataFrame | None) -> int:
        if self.cooldown_anchor_bar_dt is None or self.cooldown_bars <= 0:
            return 0

        elapsed_bars = self._completed_bars_since(self.cooldown_anchor_bar_dt, bars)
        remaining = max(0, self.cooldown_bars - elapsed_bars)
        if remaining == 0:
            self.cooldown_anchor_bar_dt = None
            self.cooldown_bars = 0
            self.cooldown_reason = None
            self._set_state(LoopState.IDLE)
        return remaining

    def _suppression_bars_remaining(self, side: str, bars: pd.DataFrame | None) -> int:
        anchor = self.last_direction_sent_bar_dt.get(side)
        if anchor is None:
            return 0
        elapsed_bars = self._completed_bars_since(anchor, bars)
        return max(0, self.config.same_direction_suppression_bars - elapsed_bars)

    def _log_bar_decision(
        self,
        bar_dt: datetime,
        candidate: StrategyCandidate | None,
        outcome: str,
    ) -> None:
        message = [
            f"bar_decision bar_dt={bar_dt.isoformat()}",
            f"session={self.current_session_key or '<unset>'}",
            f"signals_session={self.session_signal_count}",
            f"rejects_session={self.session_reject_count}",
            f"outcome={outcome}",
        ]
        if candidate is not None:
            message.extend(
                [
                    f"side={candidate.side}",
                    f"close={candidate.close_price:.2f}",
                    f"ema20={candidate.ema_value:.2f}",
                    f"ema_slope={candidate.ema_slope:.4f}",
                    f"body={candidate.body_size:.2f}",
                    f"avg_body={candidate.average_body_size:.2f}",
                ]
            )
        self._log("INFO", " ".join(message))

    def _apply_outbox_event(self, signal: OutstandingSignal, event: dict) -> bool:
        status = str(event.get("status") or "").upper()
        if not status or status in signal.statuses_seen:
            return False
        signal.statuses_seen.add(status)
        detail = str(event.get("detail") or "")
        self._log(
            "INFO",
            f"outbox_event message_id={signal.message_id} status={status} detail={detail}",
        )
        fields = self._parse_detail_fields(detail)
        if status == "FILLED":
            signal_name = str(fields.get("signal") or "")
            if signal_name.startswith("TF_ENTER_"):
                signal.filled = True
                signal.entry_price = self._maybe_float(fields.get("price"))
                self._set_state(LoopState.TRADE_ACTIVE)
            else:
                self._capture_exit_fill(signal, event)
        if status == "STOP_ATTACHED":
            signal.stop_price = self._maybe_float(fields.get("stop_price"))
            signal.target_price = self._maybe_float(fields.get("target_price"))
        if status in {"REJECTED", "ORDER_REJECTED"}:
            signal.rejected = True
            self.session_reject_count += 1
            self._start_cooldown(
                reason=f"{status}:{signal.message_id}",
                anchor_bar_dt=signal.bar_dt,
                cooldown_bars=self.config.cooldown_bars_after_reject,
            )
            return True
        if status == "ORDER_CANCELLED":
            if signal.filled:
                self._log(
                    "INFO",
                    f"order_cancel_ignored message_id={signal.message_id} detail={detail}",
                )
                return False
            signal.rejected = True
            self.session_reject_count += 1
            self._start_cooldown(
                reason=f"{status}:{signal.message_id}",
                anchor_bar_dt=signal.bar_dt,
                cooldown_bars=self.config.cooldown_bars_after_reject,
            )
            return True
        return False

    def _capture_exit_fill(self, signal: OutstandingSignal, event: dict) -> None:
        status = str(event.get("status") or "").upper()
        if status != "FILLED":
            return
        detail = str(event.get("detail") or "")
        fields = self._parse_detail_fields(detail)
        signal_name = str(fields.get("signal") or "")
        if not signal_name.startswith(("TF_TARGET_", "TF_STOP_", "TF_EXIT_")):
            return
        signal.exit_signal = signal_name
        signal.exit_price = self._maybe_float(fields.get("price"))

    def _finalize_trade(self, signal: OutstandingSignal, bars: pd.DataFrame | None) -> None:
        hold_bars = self._completed_bars_since(signal.bar_dt, bars)
        pnl_ticks = self._calculate_pnl_ticks(signal)
        outcome = self._classify_trade_outcome(signal, pnl_ticks)
        trade_record = CompletedTradeRecord(
            message_id=signal.message_id,
            thesis_id=signal.thesis_id,
            side=signal.side,
            entry_bar_dt=signal.bar_dt,
            exit_bar_dt=self._get_completed_bar_dt(bars),
            hold_bars=hold_bars,
            outcome=outcome,
            pnl_ticks=pnl_ticks,
            entry_price=signal.entry_price,
            exit_price=signal.exit_price,
            exit_signal=signal.exit_signal,
        )
        self.completed_trades.append(trade_record)
        if len(self.completed_trades) > 25:
            self.completed_trades = self.completed_trades[-25:]

        self.session_completed_trades += 1
        if pnl_ticks is not None:
            self.session_net_ticks += pnl_ticks
        if outcome == "win":
            self.session_wins += 1
            self.session_consecutive_losses = 0
        elif outcome == "loss":
            self.session_losses += 1
            self.session_consecutive_losses += 1
        else:
            self.session_unknown_outcomes += 1

        self._log(
            "INFO",
            "trade_completed "
            f"message_id={signal.message_id} thesis_id={signal.thesis_id} "
            f"outcome={outcome} pnl_ticks={pnl_ticks if pnl_ticks is not None else '<unknown>'} "
            f"hold_bars={hold_bars} exit_signal={signal.exit_signal or '<unknown>'}",
        )
        self._start_cooldown(
            reason=f"trade_flat:{signal.message_id}",
            anchor_bar_dt=signal.bar_dt,
            cooldown_bars=self.config.cooldown_bars_after_send,
        )

    def _calculate_pnl_ticks(self, signal: OutstandingSignal) -> float | None:
        if signal.entry_price is None or signal.exit_price is None:
            return None
        raw_delta = signal.exit_price - signal.entry_price
        if signal.side == "SHORT":
            raw_delta = -raw_delta
        return raw_delta / self.config.tick_size

    def _classify_trade_outcome(self, signal: OutstandingSignal, pnl_ticks: float | None) -> str:
        if pnl_ticks is not None:
            if pnl_ticks > 0:
                return "win"
            if pnl_ticks < 0:
                return "loss"
        if signal.exit_signal and signal.exit_signal.startswith("TF_TARGET_"):
            return "win"
        if signal.exit_signal and signal.exit_signal.startswith("TF_STOP_"):
            return "loss"
        return "unknown"

    def _average_hold_bars(self) -> float:
        if not self.completed_trades:
            return 0.0
        return sum(trade.hold_bars for trade in self.completed_trades) / len(self.completed_trades)

    @staticmethod
    def _parse_detail_fields(detail: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for part in detail.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
        return fields

    @staticmethod
    def _maybe_float(value: object) -> float | None:
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_bar_dt(value: object) -> datetime:
        bar_dt = pd.Timestamp(value).to_pydatetime()
        if bar_dt.tzinfo is not None:
            return bar_dt.astimezone(timezone.utc).replace(tzinfo=None)
        return bar_dt


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safe polling strategy loop that feeds TaFoundationExecutionShell using the existing file bridge contract.",
    )
    parser.add_argument("--bridge-root", default="C:/ta_foundation/bridge")
    parser.add_argument("--market-data-file", required=True, help="Path to the NT-exported minute-bar file, e.g. NQ 06-26.Last.txt")
    parser.add_argument("--instrument", default="NQ 06-26")
    parser.add_argument("--account", default="Sim101")
    parser.add_argument("--runtime-host", default="127.0.0.1")
    parser.add_argument("--runtime-port", type=int, default=8766)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--template-dir", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=20.0)
    parser.add_argument("--heartbeat-timeout-seconds", type=int, default=60)
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--warmup-bars", type=int, default=21)
    parser.add_argument("--avg-body-lookback-bars", type=int, default=10)
    parser.add_argument("--strong-body-multiplier", type=float, default=1.5)
    parser.add_argument("--cooldown-bars-after-send", type=int, default=5)
    parser.add_argument("--cooldown-bars-after-reject", type=int, default=10)
    parser.add_argument("--same-direction-bars", type=int, default=5)
    parser.add_argument("--unresolved-pending-bars", type=int, default=2)
    parser.add_argument("--max-signals-per-session", type=int, default=4)
    parser.add_argument("--max-rejects-per-session", type=int, default=2)
    parser.add_argument("--max-consecutive-losses", type=int, default=2)
    parser.add_argument("--max-session-loss-ticks", type=float, default=84.0)
    parser.add_argument("--market-data-stale-seconds", type=int, default=150)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--signal-expiry-seconds", type=int, default=DEFAULT_EXPIRY_SECONDS)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--enable-heartbeat", action="store_true", default=False)
    parser.add_argument("--disable-heartbeat", action="store_true", default=False)
    parser.add_argument("--live", action="store_true", help="Write heartbeat/signal files into the real bridge inbox.")
    return parser


def loop_config_from_args(args: argparse.Namespace) -> LoopConfig:
    enable_heartbeat = True
    if args.disable_heartbeat:
        enable_heartbeat = False
    elif args.enable_heartbeat:
        enable_heartbeat = True

    return LoopConfig(
        bridge_root=Path(args.bridge_root),
        market_data_file=Path(args.market_data_file),
        instrument=args.instrument,
        account_name=args.account,
        runtime_host=args.runtime_host,
        runtime_port=args.runtime_port,
        timeframe=args.timeframe,
        template_dir=Path(args.template_dir) if args.template_dir else default_template_dir(),
        poll_interval_seconds=args.poll_seconds,
        heartbeat_interval_seconds=args.heartbeat_seconds,
        heartbeat_timeout_seconds=args.heartbeat_timeout_seconds,
        ema_period=args.ema_period,
        startup_warmup_bars=args.warmup_bars,
        average_body_lookback_bars=args.avg_body_lookback_bars,
        strong_body_multiplier=args.strong_body_multiplier,
        cooldown_bars_after_send=args.cooldown_bars_after_send,
        cooldown_bars_after_reject=args.cooldown_bars_after_reject,
        same_direction_suppression_bars=args.same_direction_bars,
        unresolved_signal_timeout_bars=args.unresolved_pending_bars,
        max_signals_per_session=args.max_signals_per_session,
        max_rejects_per_session=args.max_rejects_per_session,
        max_consecutive_losses=args.max_consecutive_losses,
        max_session_loss_ticks=args.max_session_loss_ticks,
        market_data_stale_seconds=args.market_data_stale_seconds,
        tick_size=args.tick_size,
        dry_run=not args.live,
        enable_heartbeat=enable_heartbeat,
        signal_expiry_seconds=args.signal_expiry_seconds,
        max_iterations=args.max_iterations,
    )


def parse_args() -> LoopConfig:
    return loop_config_from_args(build_arg_parser().parse_args())


def run_loop(config: LoopConfig) -> None:
    loop = StrategyLoop(config)
    loop._log(
        "INFO",
        "loop_start "
        f"instrument={config.instrument} account={config.account_name} "
        f"route=ema{config.ema_period}_strong_body->runner_reversal_template "
        f"dry_run={config.dry_run}",
    )
    loop.run()


def main() -> None:
    run_loop(parse_args())


if __name__ == "__main__":
    main()
