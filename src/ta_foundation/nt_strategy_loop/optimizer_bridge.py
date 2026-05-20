from __future__ import annotations

"""Bridge a compile-clean strategy into the existing optimizer control plane.

The repair loop produces a verified `.cs` under `<session>/compile_clean/`.
This module takes that source plus a strategy loop session, drives the
existing `ta_foundation.web.optimizer_*` modules through their seed →
session → plan → templates → RunBatch → results sequence, and writes the
resulting optimizer CSV + analyzer verdict back into the strategy loop
session under `optimizer/`.

Two seams keep this testable without NinjaTrader:

- ``runner``: an `OptimizerRunner` protocol object. The default
  `LiveOptimizerRunner` calls ``optimizer_runner.start_run`` and
  ``optimizer_runner.get_status`` directly. Tests pass a stub that simulates
  Strategy Analyzer completing by depositing a Summary CSV.
- ``poll_seconds`` / ``timeout_seconds``: explicit so tests can configure a
  tight loop instead of waiting on real-world cadence.
"""

import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ta_foundation.nt_strategy_loop.analyzer import Guardrails, analyze_optimization_csv
from ta_foundation.nt_strategy_loop.seed_template import (
    extract_strategy_parameters,
    generate_seed_template_from_source,
)
from ta_foundation.nt_strategy_loop.session import StrategyLoopSession
from ta_foundation.web import optimizer_runner
from ta_foundation.web.optimizer_plan import build_plan_preview
from ta_foundation.web.optimizer_runner import RunRecord, RunStatus
from ta_foundation.web.optimizer_session import (
    OptimizerSession,
    ParameterConfig,
    create_session as create_optimizer_session,
    set_storage_root,
)
from ta_foundation.web.optimizer_template_writer import generate_session_templates


_TERMINAL_RUN_STATES = {"finished", "stale", "cancelled"}


class OptimizerBridgeError(RuntimeError):
    pass


class OptimizerRunner(Protocol):
    def start(self, session: OptimizerSession) -> RunRecord: ...

    def get_status(self, session: OptimizerSession) -> RunStatus | None: ...


class LiveOptimizerRunner:
    """Default runner — drives the real NinjaTrader AddOn via IPC."""

    def start(self, session: OptimizerSession) -> RunRecord:
        return optimizer_runner.start_run(session)

    def get_status(self, session: OptimizerSession) -> RunStatus | None:
        return optimizer_runner.get_status(session)


@dataclass(frozen=True)
class OptimizerBridgeResult:
    optimizer_session_id: str
    optimizer_session_dir: str
    seed_template_path: str
    generated_template_count: int
    run_state: str  # "finished" | "stale" | "cancelled" | "timeout"
    optimizer_csv: str | None
    analyzer_result: dict[str, Any]
    decision: str  # "candidate" | "archive" | "incomplete"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


def run_optimizer_for_strategy(
    *,
    loop_session: StrategyLoopSession,
    compile_clean_source: str | Path,
    guardrails: Guardrails,
    instrument: str = "NQ 06-26",
    market_suffix: str = "NQ",
    keep_best_results: int = 500,
    max_combinations_per_chunk: int = 5000,
    optimizer_storage_root: str | Path | None = None,
    runner: OptimizerRunner | None = None,
    poll_seconds: float = 5.0,
    timeout_seconds: int = 3600,
) -> OptimizerBridgeResult:
    source = Path(compile_clean_source)
    if not source.is_file():
        raise OptimizerBridgeError(f"compile-clean source not found: {source}")

    if optimizer_storage_root is not None:
        set_storage_root(Path(optimizer_storage_root))

    seed_path = _generate_seed(loop_session, source, instrument=instrument, keep_best_results=keep_best_results)

    optimizer_session = create_optimizer_session(
        label=f"loop:{loop_session.session_id}",
        strategy_id=loop_session.strategy_name,
        seed_template_path=str(seed_path),
        instrument=instrument,
        market_suffix=market_suffix,
    )

    parameters = _parameter_configs(source)
    if not parameters:
        raise OptimizerBridgeError(f"No NinjaScriptProperty parameters found in {source}")

    optimizer_session.update(
        parameters=[p.to_dict() for p in parameters],
        chunking={
            "max_combinations_per_chunk": max_combinations_per_chunk,
            "keep_best_results": keep_best_results,
        },
    )

    plan = build_plan_preview(optimizer_session.load_document())
    optimizer_session.save_plan(plan.to_dict())

    generated = generate_session_templates(optimizer_session)
    if not generated:
        raise OptimizerBridgeError("Plan produced no generated templates; refusing to start RunBatch.")

    active_runner = runner or LiveOptimizerRunner()
    active_runner.start(optimizer_session)

    final_status, timed_out = _wait_for_run(
        active_runner,
        optimizer_session,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
    )
    run_state = "timeout" if timed_out else (final_status.state if final_status else "stale")

    optimizer_csv = _copy_results_csv(optimizer_session, loop_session)
    analyzer_result: dict[str, Any] = {
        "row_count": 0,
        "passing_rows": 0,
        "best_row": None,
        "reject_reasons": ["no optimizer CSV produced"],
        "warnings": [],
    }
    if optimizer_csv is not None:
        analyzer_result = analyze_optimization_csv(optimizer_csv, guardrails)

    _write_analyzer_artifact(loop_session, analyzer_result)

    decision = _decision(run_state, analyzer_result)
    warnings: list[str] = []
    if timed_out:
        warnings.append(f"RunBatch did not reach a terminal state within {timeout_seconds}s.")
    if optimizer_csv is None:
        warnings.append("No Summary CSV was found under the optimizer session nt_output folder.")

    return OptimizerBridgeResult(
        optimizer_session_id=optimizer_session.id,
        optimizer_session_dir=str(optimizer_session.directory.resolve()),
        seed_template_path=str(seed_path.resolve()),
        generated_template_count=len(generated),
        run_state=run_state,
        optimizer_csv=str(optimizer_csv.resolve()) if optimizer_csv else None,
        analyzer_result=analyzer_result,
        decision=decision,
        warnings=tuple(warnings),
    )


def _generate_seed(
    loop_session: StrategyLoopSession,
    source: Path,
    *,
    instrument: str,
    keep_best_results: int,
) -> Path:
    seeds_dir = loop_session.optimizer_dir / "seed_templates"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    seed_path = seeds_dir / f"{loop_session.strategy_name}_seed.xml"
    generate_seed_template_from_source(
        source,
        seed_path,
        strategy_name=loop_session.strategy_name,
        instrument=instrument,
        keep_best_results=keep_best_results,
    )
    return seed_path


def _parameter_configs(source: Path) -> list[ParameterConfig]:
    text = source.read_text(encoding="utf-8-sig", errors="replace")
    parameters = extract_strategy_parameters(text)
    configs: list[ParameterConfig] = []
    for parameter in parameters:
        if parameter.minimum == parameter.maximum:
            configs.append(
                ParameterConfig(
                    name=parameter.name,
                    type_name=parameter.type_name,
                    mode="fixed",
                    fixed_value=parameter.default,
                )
            )
            continue
        configs.append(
            ParameterConfig(
                name=parameter.name,
                type_name=parameter.type_name,
                mode="optimize",
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                increment=parameter.increment,
            )
        )
    return configs


def _wait_for_run(
    runner: OptimizerRunner,
    optimizer_session: OptimizerSession,
    *,
    poll_seconds: float,
    timeout_seconds: int,
) -> tuple[RunStatus | None, bool]:
    deadline = time.monotonic() + timeout_seconds
    last: RunStatus | None = None
    while time.monotonic() < deadline:
        status = runner.get_status(optimizer_session)
        if status is not None:
            last = status
            if status.state in _TERMINAL_RUN_STATES:
                return status, False
        time.sleep(poll_seconds)
    return last, True


def _copy_results_csv(
    optimizer_session: OptimizerSession,
    loop_session: StrategyLoopSession,
) -> Path | None:
    nt_output = optimizer_session.directory / "nt_output"
    if not nt_output.exists():
        return None
    candidates = sorted(nt_output.rglob("*_Optimization.csv"))
    if not candidates:
        candidates = sorted(nt_output.rglob("Summary.csv"))
    if not candidates:
        return None
    chosen = candidates[-1]
    destination_name = f"{loop_session.strategy_name}_Optimization.csv"
    destination = loop_session.optimizer_output_dir / destination_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(chosen, destination)
    return destination


def _write_analyzer_artifact(loop_session: StrategyLoopSession, analyzer_result: dict[str, Any]) -> None:
    import json

    loop_session.optimizer_dir.mkdir(parents=True, exist_ok=True)
    (loop_session.optimizer_dir / "optimizer_analysis.json").write_text(
        json.dumps(analyzer_result, indent=2) + "\n",
        encoding="utf-8",
    )


def _decision(run_state: str, analyzer_result: dict[str, Any]) -> str:
    if run_state in {"timeout", "cancelled", "stale"}:
        return "incomplete"
    if int(analyzer_result.get("passing_rows", 0)) > 0:
        return "candidate"
    return "archive"
