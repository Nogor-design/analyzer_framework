"""Example Python bridge sender for TaFoundationExecutionShell (NT8).

Usage:
    python python_sender_example.py --inbox "C:/ta_foundation/bridge/inbox" --instrument "NQ 06-26"
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DENVER_TZ = ZoneInfo("America/Denver")
DEFAULT_EXPIRY_SECONDS = 60

TEMPLATE_DEFAULTS = {
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


@dataclass
class ResearchDecision:
    instrument: str
    timeframe: str
    early_path: str
    confidence: float
    thesis_id: str


def choose_template(decision: ResearchDecision) -> str:
    if decision.early_path == "explosive_start":
        return "runner_reversal_template"
    if decision.early_path == "orderly_start":
        return "expansion_reversal_template"
    if decision.early_path == "weak_start":
        return "scalp_reversal_template"
    return "hybrid_reversal_template"


def build_signal(decision: ResearchDecision) -> dict:
    template_name = choose_template(decision)
    template_defaults = TEMPLATE_DEFAULTS[template_name]

    return {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(DENVER_TZ).isoformat(),
        "instrument": decision.instrument,
        "timeframe": decision.timeframe,
        "action": "ENTER_LONG",
        "side": "LONG",
        "template_name": template_name,
        "confidence": round(decision.confidence, 3),
        "entry_mode": "market",
        "quantity": 1,
        "stop_mode": template_defaults["stop_mode"],
        "stop_ticks": template_defaults["stop_ticks"],
        "stop_price": None,
        "target_mode": template_defaults["target_mode"],
        "target_ticks": template_defaults["target_ticks"],
        "partial_target_ticks": template_defaults["partial_target_ticks"],
        "runner_mode": template_defaults["runner_mode"],
        "max_hold_bars": template_defaults["max_hold_bars"],
        "thesis_id": decision.thesis_id,
        "notes": f"auto-generated from early_path={decision.early_path}",
        "signal_expiry_seconds": DEFAULT_EXPIRY_SECONDS,
    }


def send_message(inbox: Path, payload: dict) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    msg_id = payload["message_id"]

    tmp_path = inbox / f"{msg_id}.tmp"
    final_path = inbox / f"{msg_id}.json"

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp_path.replace(final_path)
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Send normalized ta_foundation signal to NT8 file bridge")
    parser.add_argument("--inbox", required=True, help="Bridge inbox directory polled by NT8 strategy")
    parser.add_argument("--instrument", required=True, help="Instrument, e.g. NQ 06-26")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--early-path", default="explosive_start", choices=["explosive_start", "orderly_start", "weak_start"])
    parser.add_argument("--confidence", type=float, default=0.87)
    parser.add_argument("--thesis-id", default="explosive_start_extended_vwap")

    args = parser.parse_args()

    decision = ResearchDecision(
        instrument=args.instrument,
        timeframe=args.timeframe,
        early_path=args.early_path,
        confidence=args.confidence,
        thesis_id=args.thesis_id,
    )

    payload = build_signal(decision)
    path = send_message(Path(args.inbox), payload)
    print(f"Wrote message: {path}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
