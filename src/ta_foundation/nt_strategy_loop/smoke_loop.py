from __future__ import annotations

"""A tiny autonomous strategy-loop smoke runner.

This module exercises the loop contract without requiring a promising
strategy: author a NinjaScript source artifact, record a compile observation,
ingest optimizer-style output, analyze guardrails, and write a decision
packet. The full repair/refinement loop lives in `repair_loop.py`; this one
deliberately bypasses the repair branch by emitting a known-good `.cs` and
using fixture compile output.
"""

import csv
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.nt_strategy_loop.analyzer import (
    Guardrails,
    SmokeGuardrails,
    analyze_optimization_csv,
)
from ta_foundation.nt_strategy_loop.authoring import StrategySpec, render_source, render_source_request
from ta_foundation.nt_strategy_loop.compile_observer import (
    CompileObservation,
    observation_from_status,
    observe_compile,
)
from ta_foundation.nt_strategy_loop.seed_template import generate_seed_template_from_source
from ta_foundation.nt_strategy_loop.session import DEFAULT_LAB_ROOT, create_session


__all__ = [
    "SmokeGuardrails",
    "Guardrails",
    "SmokeLoopResult",
    "run_smoke_loop",
    "DEFAULT_LAB_ROOT",
    "latest_session",
]


@dataclass(frozen=True)
class SmokeLoopResult:
    session_id: str
    session_dir: str
    strategy_name: str
    compile_state: str
    optimizer_rows: int
    passing_rows: int
    decision: str
    summary_path: str
    next_action_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_smoke_loop(
    *,
    lab_root: str | Path = DEFAULT_LAB_ROOT,
    strategy_name: str = "AutonomousLoopSmoke",
    compile_mode: str = "fixture",
    guardrails: Guardrails = Guardrails(),
    nt_documents_dir: str | Path | None = None,
    compile_root: str | Path | None = None,
    command_path: str | Path | None = None,
    status_path: str | Path | None = None,
    overwrite: bool = False,
) -> SmokeLoopResult:
    if compile_mode not in {"fixture", "live"}:
        raise ValueError("compile_mode must be 'fixture' or 'live'")

    session = create_session(lab_root=lab_root, strategy_name=strategy_name, compile_mode=compile_mode)
    session.ensure_dirs()
    attempt_dir = session.attempt_dir(1)

    spec = _smoke_spec(strategy_name)
    session.write_spec(spec.to_dict())
    session.write_source_request(render_source_request(spec))

    source_path = attempt_dir / f"{strategy_name}.cs"
    source_path.write_text(render_source(spec), encoding="utf-8")
    template_path = attempt_dir / f"{strategy_name}_SmokeTemplate.xml"
    seed_result = generate_seed_template_from_source(
        source_path,
        template_path,
        strategy_name=strategy_name,
        instrument="NQ 06-26",
    )
    (attempt_dir / "seed_template_manifest.json").write_text(
        json.dumps(seed_result.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )

    observation = _compile_observation(
        compile_mode=compile_mode,
        strategy_name=strategy_name,
        source_path=source_path,
        attempt_dir=attempt_dir,
        nt_documents_dir=nt_documents_dir,
        compile_root=compile_root,
        command_path=command_path,
        status_path=status_path,
        overwrite=overwrite,
    )
    (attempt_dir / "compile_status.json").write_text(
        json.dumps(observation.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )

    if observation.ok:
        shutil.copy2(source_path, session.compile_clean_dir / source_path.name)
        shutil.copy2(template_path, session.compile_clean_dir / template_path.name)
        (session.compile_clean_dir / "compile_status.json").write_text(
            json.dumps(observation.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    opt_csv = session.optimizer_output_dir / f"{strategy_name}_Optimization.csv"
    _write_synthetic_optimization_csv(opt_csv)
    analyzed = analyze_optimization_csv(opt_csv, guardrails)
    analysis_path = session.optimizer_dir / "optimizer_analysis.json"
    analysis_path.write_text(json.dumps(analyzed, indent=2) + "\n", encoding="utf-8")

    decision = "archive" if analyzed["passing_rows"] == 0 else "continue_refinement"
    summary_path = session.decisions_dir / "STRATEGY_LOOP_SUMMARY.md"
    next_action_path = session.decisions_dir / "NEXT_ACTION.md"
    summary_path.write_text(
        _summary_markdown(
            strategy_name=strategy_name,
            session_id=session.session_id,
            observation=observation,
            analyzed=analyzed,
            decision=decision,
            guardrails=guardrails,
        ),
        encoding="utf-8",
    )
    next_action_path.write_text(_next_action_markdown(strategy_name, decision, analyzed), encoding="utf-8")

    session.write_manifest(
        decision=decision,
        artifacts={
            "strategy_spec": "strategy_spec.json",
            "source_request": "source_request.md",
            "attempt_source": f"attempts/attempt_001/{source_path.name}",
            "seed_template_manifest": "attempts/attempt_001/seed_template_manifest.json",
            "compile_status": "attempts/attempt_001/compile_status.json",
            "compile_clean_source": f"compile_clean/{source_path.name}" if observation.ok else None,
            "optimizer_csv": f"optimizer/nt_output/{opt_csv.name}",
            "optimizer_analysis": "optimizer/optimizer_analysis.json",
            "summary": "decisions/STRATEGY_LOOP_SUMMARY.md",
            "next_action": "decisions/NEXT_ACTION.md",
        },
    )

    return SmokeLoopResult(
        session_id=session.session_id,
        session_dir=str(session.session_dir.resolve()),
        strategy_name=strategy_name,
        compile_state=observation.state,
        optimizer_rows=int(analyzed["row_count"]),
        passing_rows=int(analyzed["passing_rows"]),
        decision=decision,
        summary_path=str(summary_path.resolve()),
        next_action_path=str(next_action_path.resolve()),
    )


def _compile_observation(
    *,
    compile_mode: str,
    strategy_name: str,
    source_path: Path,
    attempt_dir: Path,
    nt_documents_dir: str | Path | None,
    compile_root: str | Path | None,
    command_path: str | Path | None,
    status_path: str | Path | None,
    overwrite: bool,
) -> CompileObservation:
    if compile_mode == "live":
        kwargs: dict[str, Any] = {
            "strategy_name": strategy_name,
            "overwrite": overwrite,
            "timeout_seconds": 120,
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

    status_path_fixture = attempt_dir / "fixture_nt8_status.json"
    payload = {
        "runId": f"compile_fixture_{strategy_name}",
        "workerKind": "compile_observer",
        "state": "succeeded",
        "strategyName": strategy_name,
        "sourceFile": str(source_path.resolve()),
        "compiled": True,
        "errorCount": 0,
        "errorsCsv": None,
        "errorsText": None,
        "lastError": None,
        "heartbeatUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outputRoot": str(attempt_dir.resolve()),
    }
    status_path_fixture.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return observation_from_status(payload, status_path_fixture)


def _write_synthetic_optimization_csv(path: Path) -> None:
    fieldnames = [
        "Instrument",
        "Performance",
        "Parameters",
        "Total net profit",
        "Gross profit",
        "Gross loss",
        "Profit factor",
        "Max. drawdown",
        "Total # of trades",
        "Percent profitable",
        "",
    ]
    rows = [
        ["NQ 06-26", "1.12", "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )", "180", "1290", "-1110", "1.16", "-1200", "14", "42.86%", ""],
        ["", "0.88", "12/30/30/20/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )", "-240", "960", "-1200", "0.80", "-1550", "18", "38.89%", ""],
        ["", "1.42", "6/18/20/12/true (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )", "95", "760", "-665", "1.14", "-840", "7", "57.14%", ""],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def _smoke_spec(strategy_name: str) -> StrategySpec:
    return StrategySpec(
        strategy_name=strategy_name,
        family="sma_cross_smoke",
        intent="Minimal autonomous-loop test strategy; not an edge candidate.",
        parameters={
            "FastPeriod": 9,
            "SlowPeriod": 21,
            "ProfitTargetTicks": 24,
            "StopLossTicks": 16,
            "Reverse": False,
        },
        risk_note="Generated only to test loop mechanics.",
    )


def _summary_markdown(
    *,
    strategy_name: str,
    session_id: str,
    observation: CompileObservation,
    analyzed: dict[str, Any],
    decision: str,
    guardrails: Guardrails,
) -> str:
    best = analyzed.get("best_row") or {}
    reasons = "; ".join(analyzed.get("reject_reasons") or [])
    return "\n".join(
        [
            f"# Strategy Loop Summary: {strategy_name}",
            "",
            f"Session: `{session_id}`",
            "",
            "## Compile",
            "",
            f"- State: `{observation.state}`",
            f"- Compiled: `{observation.compiled}`",
            f"- Error count: `{observation.error_count}`",
            "",
            "## Optimization Smoke",
            "",
            f"- Rows parsed: {analyzed['row_count']}",
            f"- Passing rows: {analyzed['passing_rows']}",
            f"- Guardrails: PF >= {guardrails.min_profit_factor:g}, trades >= {guardrails.min_trades}, drawdown <= {guardrails.max_drawdown:g}",
            f"- Best PF: {_fmt(best.get('profit_factor'))}",
            f"- Best net profit: {_fmt(best.get('total_net_profit'))}",
            f"- Best max drawdown: {_fmt(best.get('max_drawdown'))}",
            f"- Best trades: {_fmt(best.get('total_trades'))}",
            f"- Best-row verdict: {reasons}",
            "",
            "## Decision",
            "",
            f"`{decision}`",
            "",
            "The generated strategy exercised the loop mechanics but did not clear the configured smoke guardrails. It should remain a lab artifact, not a candidate.",
            "",
        ]
    )


def _next_action_markdown(strategy_name: str, decision: str, analyzed: dict[str, Any]) -> str:
    if decision == "archive":
        return "\n".join(
            [
                f"# Next Action: {strategy_name}",
                "",
                "Archive this smoke strategy.",
                "",
                "Recommended continuation:",
                "",
                "1. Run a live `observe-compile` pass against this generated source once NinjaTrader is open and the AddOn is authorized.",
                "2. Create or export a real Strategy Analyzer seed template for this strategy family.",
                "3. Replace the synthetic optimizer CSV with a live `RunBatch` result folder.",
                "4. Let the next loop modify the hypothesis only if live results show a specific failure mode worth testing.",
                "",
            ]
        )
    return "\n".join(
        [
            f"# Next Action: {strategy_name}",
            "",
            "Continue refinement with a narrowed parameter neighborhood around the best passing row.",
            "",
            f"Best row: `{json.dumps(analyzed.get('best_row'), default=str)}`",
            "",
        ]
    )


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def latest_session(root: str | Path = DEFAULT_LAB_ROOT) -> Path | None:
    from ta_foundation.nt_strategy_loop.session import latest_session as _latest

    return _latest(root)
