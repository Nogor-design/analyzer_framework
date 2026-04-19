"""Example Python bridge sender for TaFoundationExecutionShell (NT8).

Usage:
    python python_sender_example.py --inbox "C:/ta_foundation/bridge/inbox" --instrument "NQ 06-26"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ta_foundation.strategies.TaFoundationExecutionBridge.bridge_sender import (
    ResearchDecision,
    build_signal,
    default_template_dir,
    send_message,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send normalized ta_foundation signal to NT8 file bridge")
    parser.add_argument("--inbox", required=True, help="Bridge inbox directory polled by NT8 strategy")
    parser.add_argument("--instrument", required=True, help="Instrument, e.g. NQ 06-26")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--early-path", default="explosive_start", choices=["explosive_start", "orderly_start", "weak_start"])
    parser.add_argument("--confidence", type=float, default=0.87)
    parser.add_argument("--thesis-id", default="explosive_start_extended_vwap")
    parser.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    parser.add_argument("--template-dir", default=None)

    args = parser.parse_args()

    decision = ResearchDecision(
        instrument=args.instrument,
        timeframe=args.timeframe,
        early_path=args.early_path,
        confidence=args.confidence,
        thesis_id=args.thesis_id,
        side=args.side,
    )

    template_dir = Path(args.template_dir) if args.template_dir else (Path(args.inbox).resolve().parent / "templates")
    if not template_dir.exists():
        template_dir = default_template_dir()

    payload = build_signal(decision, template_dir=template_dir)
    path = send_message(Path(args.inbox), payload)
    print(f"Wrote message: {path}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
