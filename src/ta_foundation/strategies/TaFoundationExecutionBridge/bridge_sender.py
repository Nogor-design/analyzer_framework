from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from ta_foundation.strategies.TaFoundationExecutionBridge.execution_runtime_client import (
    ExecutionRuntimeClient,
    RuntimeEndpoint,
    build_command,
)

def _resolve_bridge_timezone():
    try:
        return ZoneInfo("America/Denver")
    except Exception:
        return timezone.utc


DENVER_TZ = _resolve_bridge_timezone()
DEFAULT_EXPIRY_SECONDS = 60

EARLY_PATH_TEMPLATE_MAP = {
    "explosive_start": "runner_reversal_template",
    "orderly_start": "expansion_reversal_template",
    "weak_start": "scalp_reversal_template",
}

# Existing sender defaults remain the source of truth for fields that are not
# explicitly defined as template defaults in the JSON templates.
LEGACY_TEMPLATE_DEFAULTS = {
    "runner_reversal_template": {
        "stop_mode": "signal_extreme_capped",
        "stop_ticks": 42,
        "target_mode": "partial_then_runner",
        "target_ticks": 72,
        "partial_target_ticks": 18,
        "runner_mode": "trail_structure",
        "max_hold_bars": 20,
    },
    "expansion_reversal_template": {
        "stop_mode": "signal_extreme_capped",
        "stop_ticks": 30,
        "target_mode": "fixed_ticks",
        "target_ticks": 24,
        "partial_target_ticks": 18,
        "runner_mode": "trail_structure",
        "max_hold_bars": 20,
    },
    "scalp_reversal_template": {
        "stop_mode": "tight_signal_extreme_capped",
        "stop_ticks": 28,
        "target_mode": "fixed_ticks",
        "target_ticks": 14,
        "partial_target_ticks": None,
        "runner_mode": "none",
        "max_hold_bars": 6,
    },
    "hybrid_reversal_template": {
        "stop_mode": "signal_extreme_capped",
        "stop_ticks": 42,
        "target_mode": "partial_then_runner",
        "target_ticks": 48,
        "partial_target_ticks": 16,
        "runner_mode": "trail_structure",
        "max_hold_bars": 20,
    },
}


@dataclass(frozen=True)
class ResearchDecision:
    instrument: str
    timeframe: str
    early_path: str
    confidence: float
    thesis_id: str
    side: Literal["LONG", "SHORT"] = "LONG"
    quantity: int = 1
    entry_mode: str = "market"
    notes: str = ""
    signal_expiry_seconds: int = DEFAULT_EXPIRY_SECONDS
    stop_ticks: int | None = None
    target_ticks: int | None = None
    partial_target_ticks: int | None = None
    max_hold_bars: int | None = None


def default_template_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def choose_template(decision: ResearchDecision) -> str:
    return EARLY_PATH_TEMPLATE_MAP.get(decision.early_path, "hybrid_reversal_template")


def resolve_template_defaults(template_name: str, template_dir: Path | None = None) -> dict:
    defaults = dict(LEGACY_TEMPLATE_DEFAULTS.get(template_name) or {})
    candidate_dir = Path(template_dir) if template_dir else default_template_dir()
    template_path = candidate_dir / f"{template_name}.json"

    if not template_path.exists():
        return defaults

    data = json.loads(template_path.read_text(encoding="utf-8"))
    partial_rules = data.get("partial_rules") or {}
    runner_rules = data.get("runner_rules") or {}

    defaults["stop_mode"] = data.get("stop_mode", defaults.get("stop_mode"))
    defaults["target_mode"] = data.get("initial_target_mode", defaults.get("target_mode"))
    defaults["target_ticks"] = data.get("initial_target_ticks", defaults.get("target_ticks"))
    defaults["partial_target_ticks"] = partial_rules.get(
        "partial_target_ticks",
        defaults.get("partial_target_ticks"),
    )
    defaults["runner_mode"] = runner_rules.get("trail_mode", defaults.get("runner_mode"))
    defaults["max_hold_bars"] = data.get("max_hold_bars", defaults.get("max_hold_bars"))

    hard_stop_cap = data.get("hard_stop_ticks_cap")
    if hard_stop_cap is not None:
        try:
            defaults["stop_ticks"] = min(int(defaults.get("stop_ticks") or hard_stop_cap), int(hard_stop_cap))
        except (TypeError, ValueError):
            defaults["stop_ticks"] = defaults.get("stop_ticks")

    return defaults


def build_signal(decision: ResearchDecision, template_dir: Path | None = None) -> dict:
    template_name = choose_template(decision)
    template_defaults = resolve_template_defaults(template_name, template_dir=template_dir)
    side = decision.side.upper()
    action = "ENTER_LONG" if side == "LONG" else "ENTER_SHORT"

    notes = decision.notes or f"auto-generated from early_path={decision.early_path}"

    return {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(DENVER_TZ).isoformat(),
        "instrument": decision.instrument,
        "timeframe": decision.timeframe,
        "action": action,
        "side": side,
        "template_name": template_name,
        "confidence": round(decision.confidence, 3),
        "entry_mode": decision.entry_mode,
        "quantity": max(1, int(decision.quantity)),
        "stop_mode": template_defaults["stop_mode"],
        "stop_ticks": int(decision.stop_ticks or template_defaults["stop_ticks"]),
        "stop_price": None,
        "target_mode": template_defaults["target_mode"],
        "target_ticks": int(decision.target_ticks or template_defaults["target_ticks"]),
        "partial_target_ticks": (
            decision.partial_target_ticks
            if decision.partial_target_ticks is not None
            else template_defaults["partial_target_ticks"]
        ),
        "runner_mode": template_defaults["runner_mode"],
        "max_hold_bars": int(decision.max_hold_bars or template_defaults["max_hold_bars"]),
        "thesis_id": decision.thesis_id,
        "notes": notes,
        "signal_expiry_seconds": int(decision.signal_expiry_seconds),
    }


def build_heartbeat(
    instrument: str,
    signal_expiry_seconds: int = DEFAULT_EXPIRY_SECONDS,
) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(DENVER_TZ).isoformat(),
        "instrument": instrument,
        "action": "HEARTBEAT",
        "signal_expiry_seconds": int(signal_expiry_seconds),
    }


def send_message(inbox: Path, payload: dict) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    msg_id = payload["message_id"]

    tmp_path = inbox / f"{msg_id}.tmp"
    final_path = inbox / f"{msg_id}.json"

    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    tmp_path.replace(final_path)
    return final_path


def submit_payload(
    payload: dict,
    *,
    endpoint: RuntimeEndpoint | None = None,
    client: ExecutionRuntimeClient | None = None,
) -> dict:
    managed_client = client or ExecutionRuntimeClient(endpoint=endpoint)
    owns_client = client is None
    try:
        managed_client.send_command(build_command(payload))
    finally:
        if owns_client:
            managed_client.close()
    return build_command(payload)


def submit_signal(
    decision: ResearchDecision,
    *,
    template_dir: Path | None = None,
    endpoint: RuntimeEndpoint | None = None,
    client: ExecutionRuntimeClient | None = None,
) -> dict:
    payload = build_signal(decision, template_dir=template_dir)
    submit_payload(payload, endpoint=endpoint, client=client)
    return payload


def publish_heartbeat(
    instrument: str,
    *,
    signal_expiry_seconds: int = DEFAULT_EXPIRY_SECONDS,
    endpoint: RuntimeEndpoint | None = None,
    client: ExecutionRuntimeClient | None = None,
) -> dict:
    payload = build_heartbeat(instrument, signal_expiry_seconds=signal_expiry_seconds)
    submit_payload(payload, endpoint=endpoint, client=client)
    return payload
