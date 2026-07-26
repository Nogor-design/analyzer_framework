from __future__ import annotations

"""Author → install → observe → repair orchestrator.

Implements Slice 3 of `docs/designs/autonomous_ninjatrader_strategy_loop.md`:
take a `StrategySpec`, render an initial `.cs`, install it into NinjaTrader,
observe the auto-compile, and feed any errors to the repair pipeline until
either the strategy compiles clean or `policy.evaluate_stop` halts the loop.

The `compile_mode` parameter controls how compile observations are produced:

- ``"live"``    – uses `compile_observer.observe_compile` (real NinjaTrader)
- ``"fixture"`` – uses the supplied `observation_provider` callable, which
  receives the per-attempt source path and attempt number and returns a
  `CompileObservation`. Used by tests and dry-runs.
"""

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ta_foundation.nt_strategy_loop.authoring import (
    StrategySpec,
    render_source,
    render_source_request,
)
from ta_foundation.nt_strategy_loop.compile_observer import (
    CompileObservation,
    compiler_error_signature,
    observe_compile,
)
from ta_foundation.nt_strategy_loop.policy import RepairPolicy, StopReason, evaluate_stop
from ta_foundation.nt_strategy_loop.repair import (
    RepairCallback,
    RepairResult,
    build_repair_prompt,
    context_from_attempt,
    repair,
)
from ta_foundation.nt_strategy_loop.session import (
    DEFAULT_LAB_ROOT,
    StrategyLoopSession,
    create_session,
)


ObservationProvider = Callable[[Path, int], CompileObservation]


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    source_path: str
    compile_status_path: str
    repair_summary_path: str | None
    state: str
    error_count: int
    repair_applied: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairLoopResult:
    session_id: str
    session_dir: str
    strategy_name: str
    decision: str
    stop_reason: dict[str, str]
    attempts: tuple[AttemptRecord, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["attempts"] = [record.to_dict() for record in self.attempts]
        return payload


def run_repair_loop(
    spec: StrategySpec,
    *,
    lab_root: str | Path = DEFAULT_LAB_ROOT,
    compile_mode: str = "live",
    policy: RepairPolicy = RepairPolicy(),
    repair_callback: RepairCallback | None = None,
    observation_provider: ObservationProvider | None = None,
    overwrite: bool = False,
    nt_documents_dir: str | Path | None = None,
    compile_root: str | Path | None = None,
    command_path: str | Path | None = None,
    status_path: str | Path | None = None,
    timeout_seconds: int = 120,
    wait_for_quiet_seconds: int = 3,
) -> RepairLoopResult:
    if compile_mode not in {"live", "fixture"}:
        raise ValueError("compile_mode must be 'live' or 'fixture'")
    if compile_mode == "fixture" and observation_provider is None:
        raise ValueError("compile_mode='fixture' requires an observation_provider")

    session = create_session(lab_root=lab_root, strategy_name=spec.strategy_name, compile_mode=compile_mode)
    session.ensure_dirs()
    session.write_spec(spec.to_dict())
    session.write_source_request(render_source_request(spec))

    current_source = render_source(spec)
    records: list[AttemptRecord] = []
    prior_signatures: list[str] = []
    prior_summaries: list[str] = []
    final_stop: StopReason | None = None

    for attempt in range(1, policy.max_repair_attempts + 1):
        attempt_dir = session.attempt_dir(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        source_path = attempt_dir / f"{spec.strategy_name}.cs"
        source_path.write_text(current_source, encoding="utf-8")

        observation = _observe(
            source_path=source_path,
            attempt=attempt,
            compile_mode=compile_mode,
            observation_provider=observation_provider,
            overwrite=overwrite,
            nt_documents_dir=nt_documents_dir,
            compile_root=compile_root,
            command_path=command_path,
            status_path=status_path,
            timeout_seconds=timeout_seconds,
            wait_for_quiet_seconds=wait_for_quiet_seconds,
        )
        compile_status_path = attempt_dir / "compile_status.json"
        compile_status_path.write_text(
            json.dumps(observation.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

        stop = evaluate_stop(
            attempt=attempt,
            observation=observation,
            prior_signatures=prior_signatures,
            policy=policy,
        )

        repair_summary_path: Path | None = None
        repair_applied: tuple[str, ...] = ()

        if stop is None:
            context = context_from_attempt(
                spec=spec,
                source_path=source_path,
                errors=observation.errors,
                attempt=attempt,
                prior_attempt_summaries=prior_summaries,
            )
            (attempt_dir / "repair_prompt.md").write_text(
                build_repair_prompt(context),
                encoding="utf-8",
            )
            repair_result = repair(context, callback=repair_callback)
            if repair_result is None:
                stop = StopReason(
                    "repair_declined",
                    "No heuristic fix applied and no repair callback produced a new source.",
                )
            else:
                repair_summary_path = attempt_dir / "repair_summary.md"
                repair_summary_path.write_text(
                    _repair_summary_markdown(attempt, repair_result),
                    encoding="utf-8",
                )
                current_source = repair_result.source
                repair_applied = repair_result.applied
                prior_summaries.append(
                    f"attempt {attempt}: {repair_result.channel} – {'; '.join(repair_result.applied)}"
                )

        if observation.errors:
            signature = compiler_error_signature(observation.errors)
            prior_signatures.append(signature)

        records.append(
            AttemptRecord(
                attempt=attempt,
                source_path=str(source_path.resolve()),
                compile_status_path=str(compile_status_path.resolve()),
                repair_summary_path=str(repair_summary_path.resolve()) if repair_summary_path else None,
                state=observation.state,
                error_count=observation.error_count,
                repair_applied=repair_applied,
            )
        )

        if stop is not None:
            final_stop = stop
            break
    else:
        final_stop = StopReason(
            "max_attempts",
            f"Exhausted max_repair_attempts={policy.max_repair_attempts} without compile-clean.",
        )

    decision = "compile_clean" if final_stop.code == "compile_clean" else "halted"
    if decision == "compile_clean":
        last_source = Path(records[-1].source_path)
        shutil.copy2(last_source, session.compile_clean_dir / last_source.name)
        shutil.copy2(
            records[-1].compile_status_path,
            session.compile_clean_dir / "compile_status.json",
        )

    artifacts = {
        "strategy_spec": "strategy_spec.json",
        "source_request": "source_request.md",
        "attempts": [
            {
                "attempt": record.attempt,
                "source": _relative(record.source_path, session.session_dir),
                "compile_status": _relative(record.compile_status_path, session.session_dir),
                "repair_summary": _relative(record.repair_summary_path, session.session_dir)
                if record.repair_summary_path
                else None,
            }
            for record in records
        ],
        "compile_clean_source": (
            f"compile_clean/{spec.strategy_name}.cs" if decision == "compile_clean" else None
        ),
        "stop_reason": final_stop.to_dict(),
    }
    session.write_manifest(decision=decision, artifacts=artifacts)

    return RepairLoopResult(
        session_id=session.session_id,
        session_dir=str(session.session_dir.resolve()),
        strategy_name=spec.strategy_name,
        decision=decision,
        stop_reason=final_stop.to_dict(),
        attempts=tuple(records),
    )


def _observe(
    *,
    source_path: Path,
    attempt: int,
    compile_mode: str,
    observation_provider: ObservationProvider | None,
    overwrite: bool,
    nt_documents_dir: str | Path | None,
    compile_root: str | Path | None,
    command_path: str | Path | None,
    status_path: str | Path | None,
    timeout_seconds: int,
    wait_for_quiet_seconds: int,
) -> CompileObservation:
    if compile_mode == "fixture":
        assert observation_provider is not None  # validated upstream
        return observation_provider(source_path, attempt)

    kwargs: dict[str, Any] = {
        "overwrite": overwrite,
        "timeout_seconds": timeout_seconds,
        "wait_for_quiet_seconds": wait_for_quiet_seconds,
    }
    if nt_documents_dir is not None:
        kwargs["nt_documents_dir"] = nt_documents_dir
    if compile_root is not None:
        kwargs["compile_root"] = compile_root
    if command_path is not None:
        kwargs["command_path"] = command_path
    if status_path is not None:
        kwargs["status_path"] = status_path
    return observe_compile(source_path, **kwargs)


def _repair_summary_markdown(attempt: int, repair_result: RepairResult) -> str:
    bullets = "\n".join(f"- {item}" for item in repair_result.applied) or "- (no change)"
    return (
        f"# Repair Summary: Attempt {attempt}\n\n"
        f"Channel: `{repair_result.channel}`\n\n"
        f"Applied:\n{bullets}\n"
    )


def _relative(path: str | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return path
