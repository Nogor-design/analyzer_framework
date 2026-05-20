from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ta_foundation.nt_strategy_loop.compile_observer import (
    DEFAULT_COMMAND_PATH,
    DEFAULT_COMPILE_ROOT,
    DEFAULT_NT_DOCUMENTS,
    DEFAULT_STATUS_PATH,
    observe_compile,
)
from ta_foundation.nt_strategy_loop.nt_startup import (
    DEFAULT_NT_EXE,
    DEFAULT_PASSWORD_FILE,
    DEFAULT_USERNAME,
    ensure_ninjatrader_ready,
)
from ta_foundation.nt_strategy_loop.analyzer import Guardrails
from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.full_loop import run_full_loop
from ta_foundation.nt_strategy_loop.optimizer_bridge import run_optimizer_for_strategy
from ta_foundation.nt_strategy_loop.policy import RepairPolicy
from ta_foundation.nt_strategy_loop.repair import RepairCallback
from ta_foundation.nt_strategy_loop.repair_llm import make_ollama_repair_callback
from ta_foundation.nt_strategy_loop.repair_loop import run_repair_loop
from ta_foundation.nt_strategy_loop.seed_template import generate_seed_template_from_source
from ta_foundation.nt_strategy_loop.session import StrategyLoopSession
from ta_foundation.nt_strategy_loop.smoke_loop import SmokeGuardrails, run_smoke_loop


def _cmd_observe_compile(args: argparse.Namespace) -> int:
    result = observe_compile(
        args.source,
        strategy_name=args.strategy_name or None,
        nt_documents_dir=args.nt_documents_dir,
        compile_root=args.compile_root,
        command_path=args.command_path,
        status_path=args.status_path,
        overwrite=args.overwrite,
        timeout_seconds=args.timeout_seconds,
        wait_for_quiet_seconds=args.wait_for_quiet_seconds,
        poll_seconds=args.poll_seconds,
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 2


def _cmd_ensure_nt_ready(args: argparse.Namespace) -> int:
    result = ensure_ninjatrader_ready(
        nt_exe=args.nt_exe,
        username=args.username,
        password_file=args.password_file,
        restart=args.restart,
        startup_wait_seconds=args.startup_wait_seconds,
    )
    print(
        json.dumps(
            {
                "state": result.state,
                "message": result.message,
                "prompt_clicked": result.prompt_clicked,
                "login_submitted": result.login_submitted,
            },
            indent=2,
        )
    )
    return 0 if result.ok else 2


def _cmd_smoke_loop(args: argparse.Namespace) -> int:
    result = run_smoke_loop(
        lab_root=args.lab_root,
        strategy_name=args.strategy_name,
        compile_mode=args.compile_mode,
        guardrails=SmokeGuardrails(
            max_drawdown=args.max_drawdown,
            min_trades=args.min_trades,
            min_profit_factor=args.min_profit_factor,
        ),
        nt_documents_dir=args.nt_documents_dir or None,
        compile_root=args.compile_root or None,
        command_path=args.command_path or None,
        status_path=args.status_path or None,
        overwrite=args.overwrite,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.compile_state == "succeeded" else 2


def _repair_callback_from_args(args: argparse.Namespace) -> RepairCallback | None:
    """Build the optional LLM repair callback when --repair-llm is requested."""
    if not getattr(args, "repair_llm", False):
        return None
    return make_ollama_repair_callback(
        model=args.repair_llm_model,
        base_url=args.repair_llm_url,
    )


def _cmd_repair_loop(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec)
    spec = StrategySpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))
    policy = RepairPolicy(max_repair_attempts=args.max_repair_attempts)
    result = run_repair_loop(
        spec,
        lab_root=args.lab_root,
        compile_mode=args.compile_mode,
        policy=policy,
        repair_callback=_repair_callback_from_args(args),
        overwrite=args.overwrite,
        nt_documents_dir=args.nt_documents_dir or None,
        compile_root=args.compile_root or None,
        command_path=args.command_path or None,
        status_path=args.status_path or None,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.decision == "compile_clean" else 2


def _cmd_full_loop(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec)
    spec = StrategySpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))
    policy = RepairPolicy(max_repair_attempts=args.max_repair_attempts)
    guardrails = Guardrails(
        max_drawdown=args.max_drawdown,
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
    )
    result = run_full_loop(
        spec,
        lab_root=args.lab_root,
        guardrails=guardrails,
        instrument=args.instrument,
        market_suffix=args.market_suffix,
        keep_best_results=args.keep_best_results,
        max_combinations_per_chunk=args.max_combinations_per_chunk,
        compile_mode=args.compile_mode,
        policy=policy,
        repair_callback=_repair_callback_from_args(args),
        overwrite=args.overwrite,
        nt_documents_dir=args.nt_documents_dir or None,
        compile_root=args.compile_root or None,
        command_path=args.command_path or None,
        status_path=args.status_path or None,
        optimizer_poll_seconds=args.optimizer_poll_seconds,
        optimizer_timeout_seconds=args.optimizer_timeout_seconds,
    )
    print(json.dumps(result.to_dict(), indent=2))
    if result.decision == "candidate":
        return 0
    if result.decision in {"archive", "incomplete"}:
        return 2
    return 3  # halted


def _cmd_optimizer_bridge(args: argparse.Namespace) -> int:
    source = Path(args.compile_clean_source)
    if not source.is_file():
        raise SystemExit(f"compile-clean source not found: {source}")
    session_dir = Path(args.session_dir).resolve()
    strategy_name = args.strategy_name or source.stem
    loop_session = StrategyLoopSession(
        session_id=session_dir.name,
        session_dir=session_dir,
        strategy_name=strategy_name,
        compile_mode="live",
    )
    loop_session.ensure_dirs()
    guardrails = Guardrails(
        max_drawdown=args.max_drawdown,
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
    )
    result = run_optimizer_for_strategy(
        loop_session=loop_session,
        compile_clean_source=source,
        guardrails=guardrails,
        instrument=args.instrument,
        market_suffix=args.market_suffix,
        keep_best_results=args.keep_best_results,
        max_combinations_per_chunk=args.max_combinations_per_chunk,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.decision != "incomplete" else 2


def _cmd_generate_seed_template(args: argparse.Namespace) -> int:
    result = generate_seed_template_from_source(
        args.source,
        args.output,
        strategy_name=args.strategy_name or None,
        instrument=args.instrument,
        optimizer_type=args.optimizer_type,
        optimization_fitness=args.optimization_fitness,
        keep_best_results=args.keep_best_results,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _add_repair_llm_args(p: argparse.ArgumentParser) -> None:
    """Attach the opt-in LLM repair-callback flags shared by repair-loop/full-loop."""
    p.add_argument(
        "--repair-llm",
        action="store_true",
        help="Use a local Ollama model to repair compile errors the heuristics cannot fix.",
    )
    p.add_argument(
        "--repair-llm-model",
        default="qwen3-coder:30b",
        help="Ollama model name for --repair-llm (default: qwen3-coder:30b).",
    )
    p.add_argument(
        "--repair-llm-url",
        default="http://localhost:11434",
        help="Ollama server base URL for --repair-llm.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous NinjaTrader strategy-loop helpers.")
    sub = parser.add_subparsers(dest="command", required=True)

    observe = sub.add_parser(
        "observe-compile",
        help="Install a strategy source file, let NinjaTrader auto-compile it, and observe exported compile status/errors.",
    )
    observe.add_argument("--source", required=True, help="Generated or repaired NinjaScript .cs file.")
    observe.add_argument("--strategy-name", default="", help="Strategy class name. Defaults to source stem.")
    observe.add_argument("--nt-documents-dir", default=str(DEFAULT_NT_DOCUMENTS), help="NinjaTrader Documents folder.")
    observe.add_argument("--compile-root", default=str(DEFAULT_COMPILE_ROOT), help="Compile-loop root folder.")
    observe.add_argument("--command-path", default=str(DEFAULT_COMMAND_PATH), help="NT AddOn IPC command path.")
    observe.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH), help="NT AddOn status path.")
    observe.add_argument("--overwrite", action="store_true", help="Overwrite existing strategy file in NT Strategies folder.")
    observe.add_argument("--timeout-seconds", type=int, default=120)
    observe.add_argument("--wait-for-quiet-seconds", type=int, default=3)
    observe.add_argument("--poll-seconds", type=float, default=1.0)
    observe.add_argument("--out", default="", help="Optional JSON output path for the observation result.")
    observe.set_defaults(func=_cmd_observe_compile)

    ready = sub.add_parser(
        "ensure-nt-ready",
        help="Start/login NinjaTrader, authorize the AddOn prompt, and wait for Control Center.",
    )
    ready.add_argument("--nt-exe", default=str(DEFAULT_NT_EXE), help="Path to NinjaTrader.exe.")
    ready.add_argument("--username", default=DEFAULT_USERNAME, help="NinjaTrader username.")
    ready.add_argument(
        "--password-file",
        default=str(DEFAULT_PASSWORD_FILE),
        help="Local password file. Contents are read in memory and never printed.",
    )
    ready.add_argument("--restart", action="store_true", help="Close and restart NinjaTrader before waiting.")
    ready.add_argument(
        "--startup-wait-seconds",
        type=int,
        default=150,
        help="Seconds to wait after clicking the AddOn authorization prompt.",
    )
    ready.set_defaults(func=_cmd_ensure_nt_ready)

    smoke = sub.add_parser(
        "smoke-loop",
        help="Run a tiny author/compile/analyze loop and write durable nt_strategy_lab artifacts.",
    )
    smoke.add_argument("--strategy-name", default="AutonomousLoopSmoke")
    smoke.add_argument("--lab-root", default=str(Path(".ta_artifacts") / "nt_strategy_lab" / "sessions"))
    smoke.add_argument(
        "--compile-mode",
        choices=("fixture", "live"),
        default="fixture",
        help="fixture records a compile-clean observation; live uses NinjaTrader ObserveCompile.",
    )
    smoke.add_argument("--max-drawdown", type=float, default=2500.0)
    smoke.add_argument("--min-trades", type=int, default=10)
    smoke.add_argument("--min-profit-factor", type=float, default=1.5)
    smoke.add_argument("--nt-documents-dir", default="")
    smoke.add_argument("--compile-root", default="")
    smoke.add_argument("--command-path", default="")
    smoke.add_argument("--status-path", default="")
    smoke.add_argument("--overwrite", action="store_true")
    smoke.set_defaults(func=_cmd_smoke_loop)

    repair = sub.add_parser(
        "repair-loop",
        help="Author a NinjaScript strategy from a spec, install/observe/repair until compile-clean or policy stop.",
    )
    repair.add_argument("--spec", required=True, help="Path to a JSON StrategySpec file.")
    repair.add_argument("--lab-root", default=str(Path(".ta_artifacts") / "nt_strategy_lab" / "sessions"))
    repair.add_argument("--compile-mode", choices=("live", "fixture"), default="live")
    repair.add_argument("--max-repair-attempts", type=int, default=5)
    repair.add_argument("--overwrite", action="store_true")
    repair.add_argument("--nt-documents-dir", default="")
    repair.add_argument("--compile-root", default="")
    repair.add_argument("--command-path", default="")
    repair.add_argument("--status-path", default="")
    _add_repair_llm_args(repair)
    repair.set_defaults(func=_cmd_repair_loop)

    bridge = sub.add_parser(
        "optimizer-bridge",
        help="Run the optimizer control plane against a compile-clean NinjaScript and ingest results.",
    )
    bridge.add_argument("--session-dir", required=True, help="Existing strategy loop session directory.")
    bridge.add_argument("--compile-clean-source", required=True, help="Path to the compile-clean .cs file.")
    bridge.add_argument("--strategy-name", default="", help="Defaults to the source file stem.")
    bridge.add_argument("--instrument", default="NQ 06-26")
    bridge.add_argument("--market-suffix", default="NQ")
    bridge.add_argument("--keep-best-results", type=int, default=500)
    bridge.add_argument("--max-combinations-per-chunk", type=int, default=5000)
    bridge.add_argument("--max-drawdown", type=float, default=2500.0)
    bridge.add_argument("--min-trades", type=int, default=10)
    bridge.add_argument("--min-profit-factor", type=float, default=1.5)
    bridge.add_argument("--poll-seconds", type=float, default=5.0)
    bridge.add_argument("--timeout-seconds", type=int, default=3600)
    bridge.set_defaults(func=_cmd_optimizer_bridge)

    full = sub.add_parser(
        "full-loop",
        help="Author → repair → compile-clean → optimize → analyze end-to-end.",
    )
    full.add_argument("--spec", required=True, help="JSON StrategySpec file.")
    full.add_argument("--lab-root", default=str(Path(".ta_artifacts") / "nt_strategy_lab" / "sessions"))
    full.add_argument("--compile-mode", choices=("live", "fixture"), default="live")
    full.add_argument("--max-repair-attempts", type=int, default=5)
    full.add_argument("--overwrite", action="store_true")
    full.add_argument("--nt-documents-dir", default="")
    full.add_argument("--compile-root", default="")
    full.add_argument("--command-path", default="")
    full.add_argument("--status-path", default="")
    full.add_argument("--instrument", default="NQ 06-26")
    full.add_argument("--market-suffix", default="NQ")
    full.add_argument("--keep-best-results", type=int, default=500)
    full.add_argument("--max-combinations-per-chunk", type=int, default=5000)
    full.add_argument("--max-drawdown", type=float, default=2500.0)
    full.add_argument("--min-trades", type=int, default=10)
    full.add_argument("--min-profit-factor", type=float, default=1.5)
    full.add_argument("--optimizer-poll-seconds", type=float, default=5.0)
    full.add_argument("--optimizer-timeout-seconds", type=int, default=3600)
    _add_repair_llm_args(full)
    full.set_defaults(func=_cmd_full_loop)

    seed = sub.add_parser(
        "generate-seed-template",
        help="Generate a provisional Strategy Analyzer seed XML from a NinjaScript source file.",
    )
    seed.add_argument("--source", required=True, help="Compile-clean NinjaScript .cs file.")
    seed.add_argument("--output", required=True, help="Output XML path.")
    seed.add_argument("--strategy-name", default="", help="Strategy class name. Defaults to source/class detection.")
    seed.add_argument("--instrument", default="NQ 06-26")
    seed.add_argument("--optimizer-type", default="NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer")
    seed.add_argument("--optimization-fitness", default="NinjaTrader.NinjaScript.OptimizationFitnesses.MaxNetProfit")
    seed.add_argument("--keep-best-results", type=int, default=500)
    seed.set_defaults(func=_cmd_generate_seed_template)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
