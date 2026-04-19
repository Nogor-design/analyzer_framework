from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.strategies.TaFoundationExecutionBridge import real_strategy_loop


DEFAULT_BRIDGE_ROOT = Path("C:/ta_foundation/bridge")


def control_file_path(bridge_root: Path) -> Path:
    return bridge_root / "state" / "python_strategy_loop_control.json"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(real_strategy_loop._read_text_shared(path, encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def read_loop_control_file(bridge_root: Path) -> dict[str, Any] | None:
    return _read_json_file(control_file_path(bridge_root))


def write_loop_control_file(
    bridge_root: Path,
    *,
    pause_new_entries: bool,
    stop_requested: bool,
    reason: str | None,
) -> Path:
    payload = {
        "pause_new_entries": pause_new_entries,
        "stop_requested": stop_requested,
        "reason": reason or "",
        "updated_utc": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    path = control_file_path(bridge_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def clear_loop_control_file(bridge_root: Path) -> Path:
    path = control_file_path(bridge_root)
    if path.exists():
        path.unlink()
    return path


def read_status_snapshot(bridge_root: Path) -> dict[str, Any]:
    return {
        "bridge_root": str(bridge_root),
        "shell": _read_json_file(bridge_root / "state" / "shell_state.json"),
        "summary": _read_json_file(bridge_root / "logs" / "python_strategy_loop_summary.json"),
        "control": read_loop_control_file(bridge_root),
    }


def format_status_lines(snapshot: dict[str, Any]) -> list[str]:
    shell = snapshot.get("shell") or {}
    summary = snapshot.get("summary") or {}
    control = snapshot.get("control") or {}

    lines = [
        "CURRENT STATUS",
        f"  shell_mode={shell.get('ShellMode', '<missing>')}",
        f"  intake_enabled={shell.get('SignalIntakeEnabled', '<missing>')}",
        f"  heartbeat_faulted={shell.get('HeartbeatFaulted', '<missing>')}",
        f"  position_id={shell.get('PositionId', '<missing>') or '<empty>'}",
        f"  loop_state={summary.get('loop_state', '<missing>')}",
        f"  session={summary.get('session', '<missing>')}",
        f"  signals_session={summary.get('signals_session', '<missing>')}",
        f"  completed_trades_session={summary.get('completed_trades_session', '<missing>')}",
        f"  session_net_ticks={summary.get('session_net_ticks', '<missing>')}",
        f"  stop_reason={summary.get('stop_reason', '<none>') or '<none>'}",
        "OPERATOR CONTROL",
        f"  pause_new_entries={control.get('pause_new_entries', False)}",
        f"  stop_requested={control.get('stop_requested', False)}",
        f"  reason={control.get('reason', '') or '<none>'}",
        f"  updated_utc={control.get('updated_utc', '') or '<none>'}",
    ]
    return lines


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small operator CLI for the NT8 bridge strategy loop.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show current shell, loop, and operator control status.")
    status_parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))

    summary_parser = subparsers.add_parser("summary", help="Print the raw loop summary JSON.")
    summary_parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))

    pause_parser = subparsers.add_parser("pause", help="Pause new entries while keeping the loop alive.")
    pause_parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))
    pause_parser.add_argument("--reason", default="operator_pause")

    resume_parser = subparsers.add_parser("resume", help="Clear pause/stop control flags.")
    resume_parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))

    stop_parser = subparsers.add_parser("stop", help="Request a graceful loop stop once the shell is flat.")
    stop_parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))
    stop_parser.add_argument("--reason", default="operator_stop")

    run_parser = subparsers.add_parser("run-loop", help="Run the existing strategy loop through this operator entrypoint.")
    run_parser.add_argument("loop_args", nargs=argparse.REMAINDER)

    return parser


def _run_loop(loop_args: list[str]) -> None:
    args = loop_args[1:] if loop_args and loop_args[0] == "--" else loop_args
    parser = real_strategy_loop.build_arg_parser()
    parsed = parser.parse_args(args)
    config = real_strategy_loop.loop_config_from_args(parsed)
    real_strategy_loop.run_loop(config)


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.command == "run-loop":
        _run_loop(args.loop_args)
        return

    bridge_root = Path(args.bridge_root)

    if args.command == "status":
        print("\n".join(format_status_lines(read_status_snapshot(bridge_root))))
        return

    if args.command == "summary":
        summary = read_status_snapshot(bridge_root).get("summary")
        print(json.dumps(summary or {}, indent=2))
        return

    if args.command == "pause":
        path = write_loop_control_file(
            bridge_root,
            pause_new_entries=True,
            stop_requested=False,
            reason=args.reason,
        )
        print(f"pause_requested path={path} reason={args.reason}")
        return

    if args.command == "resume":
        path = clear_loop_control_file(bridge_root)
        print(f"control_cleared path={path}")
        return

    if args.command == "stop":
        path = write_loop_control_file(
            bridge_root,
            pause_new_entries=True,
            stop_requested=True,
            reason=args.reason,
        )
        print(f"stop_requested path={path} reason={args.reason}")
        return

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
