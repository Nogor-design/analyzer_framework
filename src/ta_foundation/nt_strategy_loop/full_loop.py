from __future__ import annotations

"""Top-level autonomous strategy loop.

Chains the repair loop (slice 3) and the optimizer bridge (slice 4) into a
single driver. A successful run produces:

- a compile-clean `.cs` under ``<session>/compile_clean/``
- generated Strategy Analyzer XMLs under the linked optimizer session
- an ingested ``*_Optimization.csv`` copied into the loop session's
  ``optimizer/nt_output/``
- ``optimizer/optimizer_analysis.json`` with the guardrail verdict
- ``decisions/STRATEGY_LOOP_SUMMARY.md`` and ``decisions/NEXT_ACTION.md``

If the repair loop halts before compile-clean, the optimizer phase is
skipped entirely and the session is marked ``halted``.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.nt_strategy_loop.analyzer import Guardrails
from ta_foundation.nt_strategy_loop.authoring import StrategySpec
from ta_foundation.nt_strategy_loop.optimizer_bridge import (
    OptimizerBridgeResult,
    OptimizerRunner,
    run_optimizer_for_strategy,
)
from ta_foundation.nt_strategy_loop.policy import RepairPolicy
from ta_foundation.nt_strategy_loop.repair import RepairCallback
from ta_foundation.nt_strategy_loop.repair_loop import (
    ObservationProvider,
    RepairLoopResult,
    run_repair_loop,
)
from ta_foundation.nt_strategy_loop.session import (
    DEFAULT_LAB_ROOT,
    StrategyLoopSession,
)


@dataclass(frozen=True)
class FullLoopResult:
    session_id: str
    session_dir: str
    strategy_name: str
    decision: str  # "candidate" | "archive" | "incomplete" | "halted"
    repair: dict[str, Any]
    optimizer: dict[str, Any] | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        return payload


def run_full_loop(
    spec: StrategySpec,
    *,
    lab_root: str | Path = DEFAULT_LAB_ROOT,
    guardrails: Guardrails = Guardrails(),
    instrument: str = "NQ 06-26",
    market_suffix: str = "NQ",
    keep_best_results: int = 500,
    max_combinations_per_chunk: int = 5000,
    optimizer_storage_root: str | Path | None = None,
    # Repair-side knobs ------------------------------------------------------
    compile_mode: str = "live",
    policy: RepairPolicy = RepairPolicy(),
    repair_callback: RepairCallback | None = None,
    observation_provider: ObservationProvider | None = None,
    overwrite: bool = False,
    nt_documents_dir: str | Path | None = None,
    compile_root: str | Path | None = None,
    command_path: str | Path | None = None,
    status_path: str | Path | None = None,
    compile_timeout_seconds: int = 120,
    wait_for_quiet_seconds: int = 3,
    # Optimizer-side knobs ---------------------------------------------------
    runner: OptimizerRunner | None = None,
    optimizer_poll_seconds: float = 5.0,
    optimizer_timeout_seconds: int = 3600,
) -> FullLoopResult:
    repair_result = run_repair_loop(
        spec,
        lab_root=lab_root,
        compile_mode=compile_mode,
        policy=policy,
        repair_callback=repair_callback,
        observation_provider=observation_provider,
        overwrite=overwrite,
        nt_documents_dir=nt_documents_dir,
        compile_root=compile_root,
        command_path=command_path,
        status_path=status_path,
        timeout_seconds=compile_timeout_seconds,
        wait_for_quiet_seconds=wait_for_quiet_seconds,
    )

    if repair_result.decision != "compile_clean":
        _write_summary_markdown(
            session_dir=Path(repair_result.session_dir),
            spec=spec,
            repair_result=repair_result,
            bridge_result=None,
            decision="halted",
        )
        return FullLoopResult(
            session_id=repair_result.session_id,
            session_dir=repair_result.session_dir,
            strategy_name=spec.strategy_name,
            decision="halted",
            repair=repair_result.to_dict(),
            optimizer=None,
            notes=(f"Repair loop halted: {repair_result.stop_reason.get('message', '')}",),
        )

    session_dir = Path(repair_result.session_dir)
    compile_clean_source = session_dir / "compile_clean" / f"{spec.strategy_name}.cs"
    loop_session = StrategyLoopSession(
        session_id=repair_result.session_id,
        session_dir=session_dir,
        strategy_name=spec.strategy_name,
        compile_mode=compile_mode,
    )
    loop_session.ensure_dirs()

    bridge_result: OptimizerBridgeResult = run_optimizer_for_strategy(
        loop_session=loop_session,
        compile_clean_source=compile_clean_source,
        guardrails=guardrails,
        instrument=instrument,
        market_suffix=market_suffix,
        keep_best_results=keep_best_results,
        max_combinations_per_chunk=max_combinations_per_chunk,
        optimizer_storage_root=optimizer_storage_root,
        runner=runner,
        poll_seconds=optimizer_poll_seconds,
        timeout_seconds=optimizer_timeout_seconds,
    )

    decision = bridge_result.decision
    _augment_manifest(loop_session, decision=decision, bridge_result=bridge_result)
    _write_summary_markdown(
        session_dir=session_dir,
        spec=spec,
        repair_result=repair_result,
        bridge_result=bridge_result,
        decision=decision,
    )

    return FullLoopResult(
        session_id=repair_result.session_id,
        session_dir=repair_result.session_dir,
        strategy_name=spec.strategy_name,
        decision=decision,
        repair=repair_result.to_dict(),
        optimizer=bridge_result.to_dict(),
        notes=bridge_result.warnings,
    )


def _augment_manifest(
    session: StrategyLoopSession,
    *,
    decision: str,
    bridge_result: OptimizerBridgeResult,
) -> None:
    manifest_path = session.session_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["decision"] = decision
    artifacts = dict(data.get("artifacts") or {})
    artifacts["optimizer_session_id"] = bridge_result.optimizer_session_id
    artifacts["optimizer_session_dir"] = bridge_result.optimizer_session_dir
    artifacts["optimizer_csv"] = bridge_result.optimizer_csv
    artifacts["optimizer_analysis"] = "optimizer/optimizer_analysis.json"
    artifacts["seed_template"] = bridge_result.seed_template_path
    data["artifacts"] = artifacts
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (session.session_dir / "session.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_summary_markdown(
    *,
    session_dir: Path,
    spec: StrategySpec,
    repair_result: RepairLoopResult,
    bridge_result: OptimizerBridgeResult | None,
    decision: str,
) -> None:
    decisions_dir = session_dir / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Strategy Loop Summary: {spec.strategy_name}",
        "",
        f"Session: `{repair_result.session_id}`",
        f"Decision: `{decision}`",
        "",
        "## Repair",
        "",
        f"- Repair decision: `{repair_result.decision}`",
        f"- Stop reason: `{repair_result.stop_reason.get('code')}` — {repair_result.stop_reason.get('message')}",
        f"- Attempts: {len(repair_result.attempts)}",
        "",
    ]
    if bridge_result is None:
        lines += [
            "## Optimizer",
            "",
            "Not run (repair did not reach compile-clean).",
            "",
        ]
    else:
        analyzer = bridge_result.analyzer_result or {}
        lines += [
            "## Optimizer",
            "",
            f"- Optimizer session: `{bridge_result.optimizer_session_id}`",
            f"- Run state: `{bridge_result.run_state}`",
            f"- Generated templates: {bridge_result.generated_template_count}",
            f"- CSV: `{bridge_result.optimizer_csv}`",
            f"- Rows: {analyzer.get('row_count', 0)}; passing: {analyzer.get('passing_rows', 0)}",
            "",
        ]
        if bridge_result.warnings:
            lines += ["## Warnings", ""] + [f"- {w}" for w in bridge_result.warnings] + [""]
    (decisions_dir / "STRATEGY_LOOP_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")

    next_action = _next_action(decision, bridge_result)
    (decisions_dir / "NEXT_ACTION.md").write_text(next_action, encoding="utf-8")


def _next_action(decision: str, bridge_result: OptimizerBridgeResult | None) -> str:
    if decision == "halted":
        return (
            "# Next Action\n\n"
            "Repair loop halted before compile-clean. Inspect the latest "
            "`attempts/attempt_*/compile_status.json` and either extend the "
            "repair heuristics or supply a stronger `repair_callback` before "
            "re-running.\n"
        )
    if decision == "incomplete":
        return (
            "# Next Action\n\n"
            "RunBatch did not reach a terminal state. Verify NinjaTrader is "
            "running and the AddOn is authorized, then re-run `optimizer-bridge` "
            "against the existing session before regenerating the strategy.\n"
        )
    if decision == "candidate":
        best = (bridge_result.analyzer_result.get("best_row") if bridge_result else None) or {}
        return (
            "# Next Action\n\n"
            "At least one optimizer row cleared the guardrails. Promote this "
            "strategy to the candidate review queue and run robustness/walk-forward "
            "before any live exposure.\n\n"
            f"Best row: `{json.dumps(best, default=str)}`\n"
        )
    return (
        "# Next Action\n\n"
        "No optimizer row cleared the guardrails. Archive this strategy or "
        "narrow the parameter neighborhood with an explicit refinement spec "
        "before retrying.\n"
    )
