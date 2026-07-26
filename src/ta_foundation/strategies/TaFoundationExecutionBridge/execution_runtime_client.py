from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class RuntimeEndpoint:
    host: str = "127.0.0.1"
    port: int = 8766
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 0.25


@dataclass
class RuntimeEvent:
    payload: dict[str, Any]

    @property
    def event(self) -> str:
        return str(self.payload.get("event") or "")

    @property
    def signal_id(self) -> str:
        return str(self.payload.get("signal_id") or "")


class ExecutionRuntimeClient:
    def __init__(self, endpoint: RuntimeEndpoint | None = None) -> None:
        self.endpoint = endpoint or RuntimeEndpoint()
        self._socket: socket.socket | None = None
        self._reader = None
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._latest_snapshot: dict[str, Any] | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        with self._lock:
            if self._socket is not None:
                return
            sock = socket.create_connection(
                (self.endpoint.host, self.endpoint.port),
                timeout=self.endpoint.connect_timeout_seconds,
            )
            sock.settimeout(self.endpoint.read_timeout_seconds)
            self._socket = sock
            self._reader = sock.makefile("r", encoding="utf-8")
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True, name="ExecutionRuntimeClient")
            self._thread.start()

    def close(self) -> None:
        with self._lock:
            self._running = False
            if self._reader is not None:
                self._reader.close()
                self._reader = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def ensure_connected(self) -> None:
        if self._socket is None:
            self.connect()

    def send_command(self, payload: dict[str, Any]) -> None:
        self.ensure_connected()
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._lock:
            if self._socket is None:
                raise RuntimeError("runtime client is not connected")
            self._socket.sendall(line.encode("utf-8"))

    def drain_events(self) -> list[RuntimeEvent]:
        with self._lock:
            items = [RuntimeEvent(payload=payload) for payload in self._events]
            self._events.clear()
        return items

    def latest_state_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_snapshot is None:
                return None
            return dict(self._latest_snapshot)

    def _read_loop(self) -> None:
        while self._running:
            try:
                if self._reader is None:
                    return
                line = self._reader.readline()
                if not line:
                    return
                payload = json.loads(line)
            except TimeoutError:
                continue
            except OSError:
                return
            except json.JSONDecodeError:
                continue

            with self._lock:
                self._events.append(payload)
                if str(payload.get("event") or "") == "STATE_SNAPSHOT":
                    self._latest_snapshot = dict(payload)


def build_command(
    payload: dict[str, Any],
    *,
    command: str | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    outbound = dict(payload)
    outbound["message_type"] = "COMMAND"
    outbound["command"] = command or str(payload.get("command") or payload.get("action") or "").strip()
    outbound["signal_id"] = signal_id or str(
        payload.get("signal_id") or payload.get("message_id") or payload.get("correlation_id") or ""
    ).strip()
    outbound.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    outbound.pop("action", None)
    outbound.pop("message_id", None)
    return outbound
