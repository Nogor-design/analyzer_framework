from __future__ import annotations

"""Create end-user deployment decision packages for optimizer sessions.

The builder auto-advances through whatever phases have returned NinjaTrader
results under ``<session>/deployment_package/``:

    phase 1 results  -> phase 2 optimizer templates
    phase 2 results  -> phase 3 optimizer templates
    phase 3 results  -> final fixed Backtest templates
    final results    -> bucket-diverse recommendations + END_USER_DECISION.md

Returned NT outputs (``nt_output*/`` and ``nt8_backtest_results/``) are
preserved across rebuilds; only generated/derived folders are regenerated.
"""

import csv
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.optimization.evaluator import EvaluationConfig
from ta_foundation.optimization.grid_workflow import (
    OptimizationGridConfig,
    create_final_backtest_templates_from_phase3_csv,
    create_next_phase_from_optimization_csv,
)
from ta_foundation.optimization.handoff import write_handoff_package
from ta_foundation.optimization.review import write_result_review
from ta_foundation.web.optimizer_preflight import run_preflight
from ta_foundation.web.optimizer_results import OptimizerResultsError, load_optimizer_results
from ta_foundation.web.optimizer_runner import load_run
from ta_foundation.web.optimizer_session import OptimizerSession


PACKAGE_DIRNAME = "deployment_package"
PHASE2_DIRNAME = "phase2_refinement_handoff"
PHASE3_DIRNAME = "phase3_risk_handoff"
FINAL_DIRNAME = "final_backtest_handoff"
FINAL_RESULTS_DIRNAME = "nt8_backtest_results"
FINAL_REVIEW_DIRNAME = "final_backtest_review"


@dataclass(frozen=True)
class OptimizerDeploymentPackage:
    session_id: str
    package_dir: str
    decision_state: str
    summary_path: str
    end_user_decision_path: str
    manifest_path: str
    template_count: int
    phase2_refinement_template_count: int
    phase3_risk_template_count: int
    final_backtest_template_count: int
    final_review_dir: str | None
    final_validation_status: str | None
    final_recommendation_count: int
    result_rows: int
    guardrail_candidate_count: int
    top_candidate_count: int
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OptimizerDeploymentPackageError(Exception):
    pass


def build_deployment_package(
    session: OptimizerSession,
    *,
    top_n: int = 25,
    oos_from_date: str | None = None,
    oos_to_date: str | None = None,
    backtest_seed_template_path: str | None = None,
) -> OptimizerDeploymentPackage:
    doc = session.load_document()
    package_dir = session.directory / PACKAGE_DIRNAME
    generated_dir = session.directory / "generated_templates"
    if not generated_dir.exists():
        raise OptimizerDeploymentPackageError(
            f"No generated templates found: {generated_dir}"
        )

    package_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = package_dir / "templates_to_run"
    analysis_dir = package_dir / "analysis"
    _clear_dir(templates_dir)
    _clear_dir(analysis_dir)

    handoff_rows = write_handoff_package(generated_dir, templates_dir, recursive=False)
    preflight = run_preflight(session)
    run = load_run(session)

    try:
        results = load_optimizer_results(session, top_n=top_n)
    except OptimizerResultsError:
        results = None

    top_rows = results.top_rows if results is not None else []
    guardrail_rows = results.guardrail_rows if results is not None else []
    batches = results.batches if results is not None else []
    row_count = results.row_count if results is not None else 0

    _write_dict_rows(analysis_dir / "batch_summary.csv", batches)
    _write_dict_rows(analysis_dir / "top_optimizer_rows.csv", top_rows)
    _write_dict_rows(analysis_dir / "guardrail_candidates.csv", guardrail_rows)

    grid_config = OptimizationGridConfig(
        max_drawdown=float(doc.guardrails.max_drawdown_dollars) if doc.guardrails.max_drawdown_dollars is not None else 2500.0,
        min_trades=int(doc.guardrails.min_trades) if doc.guardrails.min_trades is not None else 10,
        min_profit_factor=float(doc.guardrails.min_profit_factor) if doc.guardrails.min_profit_factor is not None else 1.5,
        count=8,
    )
    eval_config = EvaluationConfig(
        max_drawdown=grid_config.max_drawdown,
        min_trades=grid_config.min_trades,
        min_profit_factor=grid_config.min_profit_factor,
        min_percent_days_traded=float(doc.guardrails.min_percent_days_traded) if doc.guardrails.min_percent_days_traded is not None else 20.0,
    )

    phase2_templates = _advance_phase(
        package_dir,
        target_phase="phase2",
        seed_template_path=doc.seed_template_path,
        optimization_csv_dir=session.directory / "nt_output",
        config=grid_config,
    )

    phase3_templates: list[Path] = []
    phase2_results_dir = _find_results_dir(package_dir / PHASE2_DIRNAME)
    if phase2_results_dir is not None:
        phase3_templates = _advance_phase(
            package_dir,
            target_phase="phase3",
            seed_template_path=doc.seed_template_path,
            optimization_csv_dir=phase2_results_dir,
            config=grid_config,
        )

    final_templates: list[Path] = []
    phase3_results_dir = _find_results_dir(package_dir / PHASE3_DIRNAME) if phase3_templates else None
    resolved_from = (oos_from_date or doc.oos_from_date or "").strip() or None
    resolved_to = (oos_to_date or doc.oos_to_date or "").strip() or None
    resolved_backtest_seed = (
        (backtest_seed_template_path or doc.backtest_seed_template_path or "").strip()
        or doc.seed_template_path
    )
    if phase3_results_dir is not None and resolved_backtest_seed:
        final_templates = _generate_final_templates(
            package_dir,
            seed_backtest_template=resolved_backtest_seed,
            optimization_csv_dir=phase3_results_dir,
            config=grid_config,
            from_date=resolved_from,
            to_date=resolved_to,
        )

    # Promoted handoff (shortlist→template path). Runs unconditionally so a
    # second build after the operator finishes NT against P_NNN templates
    # surfaces them in the dashboard. No-op when no promoted templates have
    # been stamped or no NT output exists yet.
    promoted_review_dir: Path | None = None
    promoted_template_count = 0
    promoted_row_count = 0
    promoted_notes: list[str] = []
    try:
        from ta_foundation.web.optimizer_promotion_results import (
            PROMOTED_REVIEW_DIRNAME, load_promoted_results,
        )
        from ta_foundation.web.optimizer_promotion import (
            PROMOTED_DIRNAME, PROMOTED_HANDOFF_DIRNAME, PROMOTED_MANIFEST_FILENAME,
        )
        promoted_manifest_path = (
            session.directory / "generated_templates" / PROMOTED_DIRNAME / PROMOTED_MANIFEST_FILENAME
        )
        if promoted_manifest_path.exists():
            try:
                pm = json.loads(promoted_manifest_path.read_text(encoding="utf-8"))
                promoted_template_count = int(pm.get("template_count") or 0)
            except (OSError, ValueError, TypeError):
                promoted_template_count = 0
            promoted_results = load_promoted_results(session, eval_config=eval_config)
            promoted_row_count = promoted_results.row_count
            promoted_notes = list(promoted_results.notes)
            candidate_review_dir = (
                package_dir / PROMOTED_HANDOFF_DIRNAME / PROMOTED_REVIEW_DIRNAME
            )
            if candidate_review_dir.exists():
                promoted_review_dir = candidate_review_dir
    except Exception as exc:  # noqa: BLE001 — never block the package build
        promoted_notes.append(f"promoted handoff build failed: {exc}")

    final_review_dir: Path | None = None
    final_validation_status: str | None = None
    final_recommendation_count = 0
    final_results_dir = package_dir / FINAL_DIRNAME / FINAL_RESULTS_DIRNAME
    if final_templates and _dir_has_files(final_results_dir):
        final_review_dir = package_dir / FINAL_DIRNAME / FINAL_REVIEW_DIRNAME
        _clear_dir(final_review_dir)
        write_result_review(
            final_results_dir,
            final_review_dir,
            count=8,
            config=eval_config,
            review_kind="final-backtest",
        )
        summary_json = final_review_dir / "review_summary.json"
        if summary_json.exists():
            try:
                payload = json.loads(summary_json.read_text(encoding="utf-8"))
                final_validation_status = str(payload.get("validation_status") or "")
                counts = payload.get("counts") or {}
                final_recommendation_count = int(counts.get("recommendations") or 0)
            except (OSError, ValueError, TypeError):
                pass

    decision_state, notes = _decision_state(
        preflight_ok=preflight.ok,
        row_count=row_count,
        guardrail_rows=guardrail_rows,
        phase2_template_count=len(phase2_templates),
        phase3_template_count=len(phase3_templates),
        final_template_count=len(final_templates),
        final_review_dir=final_review_dir,
        final_validation_status=final_validation_status,
        oos_from_date=resolved_from,
        oos_to_date=resolved_to,
    )
    summary_path = package_dir / "DECISION_SUMMARY.md"
    end_user_path = package_dir / "END_USER_DECISION.md"
    manifest_path = package_dir / "manifest.json"

    if promoted_notes:
        notes.extend(promoted_notes)

    manifest = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": doc.to_dict(),
        "run": run.to_dict() if run is not None else None,
        "decision_state": decision_state,
        "notes": notes,
        "preflight": preflight.to_dict(),
        "results": results.to_dict() if results is not None else None,
        "handoff_template_count": len(handoff_rows),
        "phase2_refinement_template_count": len(phase2_templates),
        "phase3_risk_template_count": len(phase3_templates),
        "final_backtest_template_count": len(final_templates),
        "final_validation_status": final_validation_status,
        "final_recommendation_count": final_recommendation_count,
        "final_review_dir": str(final_review_dir) if final_review_dir else None,
        "promoted_template_count": promoted_template_count,
        "promoted_row_count": promoted_row_count,
        "promoted_review_dir": str(promoted_review_dir) if promoted_review_dir else None,
        "resolved_oos_from_date": resolved_from,
        "resolved_oos_to_date": resolved_to,
        "resolved_backtest_seed_template_path": resolved_backtest_seed,
        "files": {
            "decision_summary": str(summary_path),
            "end_user_decision": str(end_user_path),
            "manifest": str(manifest_path),
            "templates_to_run": str(templates_dir),
            "phase2_refinement_handoff": str(package_dir / PHASE2_DIRNAME),
            "phase3_risk_handoff": str(package_dir / PHASE3_DIRNAME),
            "final_backtest_handoff": str(package_dir / FINAL_DIRNAME),
            "final_backtest_review": str(final_review_dir) if final_review_dir else None,
            "promoted_review": str(promoted_review_dir) if promoted_review_dir else None,
            "batch_summary_csv": str(analysis_dir / "batch_summary.csv"),
            "top_optimizer_rows_csv": str(analysis_dir / "top_optimizer_rows.csv"),
            "guardrail_candidates_csv": str(analysis_dir / "guardrail_candidates.csv"),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_decision_summary(
        summary_path,
        session=session,
        decision_state=decision_state,
        notes=notes,
        preflight=preflight.to_dict(),
        results=results.to_dict() if results is not None else None,
        top_rows=top_rows,
        guardrail_rows=guardrail_rows,
        phase2_template_count=len(phase2_templates),
        phase3_template_count=len(phase3_templates),
        final_template_count=len(final_templates),
    )
    _write_end_user_decision(
        end_user_path,
        session=session,
        decision_state=decision_state,
        notes=notes,
        preflight=preflight.to_dict(),
        results=results.to_dict() if results is not None else None,
        phase2_template_count=len(phase2_templates),
        phase3_template_count=len(phase3_templates),
        final_template_count=len(final_templates),
        final_review_dir=final_review_dir,
        final_validation_status=final_validation_status,
        final_recommendation_count=final_recommendation_count,
        oos_from_date=resolved_from,
        oos_to_date=resolved_to,
    )

    # Auto-generate per-candidate HTML reports whenever final-Backtest
    # results are on disk. Safe to call when nothing's there (returns
    # an empty batch with a note); guarded with broad try so a report
    # render error never breaks the package rebuild itself.
    if final_review_dir is not None and _dir_has_files(final_results_dir):
        try:
            from ta_foundation.web.optimizer_candidate_report import build_all_candidate_reports
            build_all_candidate_reports(
                session,
                images_dir=doc.god_images_dir or None,
            )
        except Exception as exc:
            notes.append(f"per-candidate report build failed: {exc}")

    return OptimizerDeploymentPackage(
        session_id=session.id,
        package_dir=str(package_dir.resolve()),
        decision_state=decision_state,
        summary_path=str(summary_path.resolve()),
        end_user_decision_path=str(end_user_path.resolve()),
        manifest_path=str(manifest_path.resolve()),
        template_count=len(handoff_rows),
        phase2_refinement_template_count=len(phase2_templates),
        phase3_risk_template_count=len(phase3_templates),
        final_backtest_template_count=len(final_templates),
        final_review_dir=str(final_review_dir.resolve()) if final_review_dir else None,
        final_validation_status=final_validation_status,
        final_recommendation_count=final_recommendation_count,
        result_rows=row_count,
        guardrail_candidate_count=len(guardrail_rows),
        top_candidate_count=len(top_rows),
        notes=notes,
    )


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _dir_has_files(path: Path) -> bool:
    if not path.exists():
        return False
    return any(p.is_file() for p in path.rglob("*"))


def _find_results_dir(phase_dir: Path) -> Path | None:
    """Return the most-recent subfolder under ``phase_dir`` that contains
    NinjaTrader optimization CSV exports. Looks for any ``nt_output*`` child
    folder; the reference session has e.g. ``nt_output_short_path_success``.
    Returns None if no such folder has *_Optimization.csv files.
    """
    if not phase_dir.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for child in phase_dir.iterdir():
        if not child.is_dir():
            continue
        if not child.name.lower().startswith("nt_output"):
            continue
        has_csv = any(p.suffix.lower() == ".csv" and "Optimization" in p.name for p in child.rglob("*"))
        if has_csv:
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0
            candidates.append((mtime, child))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _advance_phase(
    package_dir: Path,
    *,
    target_phase: str,
    seed_template_path: str,
    optimization_csv_dir: Path,
    config: OptimizationGridConfig,
) -> list[Path]:
    if not seed_template_path or not Path(seed_template_path).exists():
        return []
    if not optimization_csv_dir.exists():
        return []
    sub = PHASE2_DIRNAME if target_phase == "phase2" else PHASE3_DIRNAME
    phase_dir = package_dir / sub
    # Wipe only the generated + team_handoff outputs; preserve nt_output*/.
    for stale in (phase_dir / f"generated_{target_phase}_templates", phase_dir / "team_handoff"):
        if stale.exists():
            shutil.rmtree(stale)
    try:
        return create_next_phase_from_optimization_csv(
            seed_template_path,
            optimization_csv_dir,
            phase_dir,
            target_phase=target_phase,
            config=config,
        )
    except Exception:
        return []


def _generate_final_templates(
    package_dir: Path,
    *,
    seed_backtest_template: str,
    optimization_csv_dir: Path,
    config: OptimizationGridConfig,
    from_date: str | None,
    to_date: str | None,
) -> list[Path]:
    if not seed_backtest_template or not Path(seed_backtest_template).exists():
        return []
    if not optimization_csv_dir.exists():
        return []
    phase_dir = package_dir / FINAL_DIRNAME
    # Preserve any returned nt8_backtest_results; wipe only what we regenerate.
    for stale in (phase_dir / "named_backtest_templates",):
        if stale.exists():
            shutil.rmtree(stale)
    try:
        return create_final_backtest_templates_from_phase3_csv(
            seed_backtest_template,
            optimization_csv_dir,
            phase_dir,
            config=config,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception:
        return []


def _decision_state(
    *,
    preflight_ok: bool,
    row_count: int,
    guardrail_rows: list[dict[str, Any]],
    phase2_template_count: int,
    phase3_template_count: int,
    final_template_count: int,
    final_review_dir: Path | None,
    final_validation_status: str | None,
    oos_from_date: str | None,
    oos_to_date: str | None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not preflight_ok:
        notes.append("Preflight failed; fix template contract issues before running or deploying.")
        return "blocked", notes
    if row_count <= 0:
        notes.append("No phase-1 optimizer rows parsed; there is no candidate to evaluate yet.")
        return "no_results", notes
    if not guardrail_rows:
        notes.append("No phase-1 rows passed the configured guardrails.")
        return "no_guardrail_candidates", notes
    if final_review_dir is not None:
        if final_validation_status == "valid":
            notes.append(
                "Final fixed Backtests passed validation. Review named templates and final_backtest_review/recommendations.md before deploying."
            )
            return "candidate_ready_for_operator_review", notes
        if final_validation_status == "settings_warning":
            notes.append(
                "Final fixed Backtests returned settings_contract_violations. Inspect settings_contract_violations.csv before deploying."
            )
            return "settings_contract_warning", notes
        notes.append(
            f"Final review returned status {final_validation_status!r}; inspect REVIEW_SUMMARY.md before deploying."
        )
        return "final_review_inconclusive", notes
    if final_template_count > 0:
        notes.append("Final fixed Backtest templates generated; run them in NinjaTrader and return results to advance.")
        return "needs_final_backtest_run", notes
    if phase3_template_count > 0:
        if not oos_from_date or not oos_to_date:
            notes.append("Phase 3 templates generated. Set --from-date / --to-date (or session oos_from_date / oos_to_date) before advancing to final Backtests.")
            return "needs_phase3_run_then_oos_dates", notes
        notes.append("Phase 3 templates generated; run them in NinjaTrader and return results to advance.")
        return "needs_phase3_run", notes
    if phase2_template_count > 0:
        notes.append("Phase 2 templates generated; run them in NinjaTrader and return results to advance.")
        return "needs_phase2_run", notes

    best = guardrail_rows[0]
    trades = _as_float(best.get("total_trades"))
    profit_factor = _as_float(best.get("profit_factor"))
    if trades is not None and trades < 10:
        notes.append(
            "Top guardrail candidate has fewer than 10 trades; treat this as a discovery signal, not a deployment decision."
        )
    if profit_factor is not None and profit_factor >= 5:
        notes.append(
            "Very high profit factor can be a small-sample or overfit signal; require fixed-template validation."
        )
    notes.append(
        "Optimizer rows are retained combinations, not final deployment proof. Run fixed-template backtests on selected candidates before live use."
    )
    return "needs_fixed_backtest_validation", notes


def _write_decision_summary(
    path: Path,
    *,
    session: OptimizerSession,
    decision_state: str,
    notes: list[str],
    preflight: dict[str, Any],
    results: dict[str, Any] | None,
    top_rows: list[dict[str, Any]],
    guardrail_rows: list[dict[str, Any]],
    phase2_template_count: int,
    phase3_template_count: int,
    final_template_count: int,
) -> None:
    row_count = int((results or {}).get("row_count") or 0)
    batch_count = int((results or {}).get("batch_count") or 0)
    lines = [
        "# Optimizer Deployment Decision Package",
        "",
        f"Session: `{session.id}`",
        f"Decision state: `{decision_state}`",
        f"Resolved contract: `{preflight.get('resolved_instrument') or ''}`",
        f"Templates: {preflight.get('template_count') or 0}",
        f"Parsed optimizer rows: {row_count}",
        f"Parsed batches: {batch_count}",
        f"Phase 2 refinement templates: {phase2_template_count}",
        f"Phase 3 daily-risk templates: {phase3_template_count}",
        f"Final fixed Backtest templates: {final_template_count}",
        "",
        "## Decision Notes",
        "",
    ]
    lines.extend(f"- {note}" for note in notes)
    lines.extend([
        "",
        "## Top Guardrail Candidates",
        "",
        _candidate_table(guardrail_rows[:10]),
        "",
        "## Top Optimizer Rows",
        "",
        _candidate_table(top_rows[:10]),
        "",
        "## Files For The End User",
        "",
        "- `END_USER_DECISION.md`: operator-facing summary keyed to current decision state.",
        "- `templates_to_run/`: generated NinjaTrader XML templates plus run-plan files.",
        "- `analysis/guardrail_candidates.csv`: top rows after configured guardrails.",
        "- `analysis/top_optimizer_rows.csv`: top retained optimizer rows.",
        "- `analysis/batch_summary.csv`: per-chunk parse and best-metric summary.",
        f"- `{PHASE2_DIRNAME}/`: {phase2_template_count} refinement templates.",
        f"- `{PHASE3_DIRNAME}/`: {phase3_template_count} daily-risk templates.",
        f"- `{FINAL_DIRNAME}/`: {final_template_count} fixed Backtest templates + final review when results are returned.",
        "- `manifest.json`: machine-readable package metadata.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_end_user_decision(
    path: Path,
    *,
    session: OptimizerSession,
    decision_state: str,
    notes: list[str],
    preflight: dict[str, Any],
    results: dict[str, Any] | None,
    phase2_template_count: int,
    phase3_template_count: int,
    final_template_count: int,
    final_review_dir: Path | None,
    final_validation_status: str | None,
    final_recommendation_count: int,
    oos_from_date: str | None,
    oos_to_date: str | None,
) -> None:
    lines = [
        "# End User Strategy Deployment Decision",
        "",
        f"Decision state: `{decision_state}`",
        "",
        "This package contains the templates and analysis needed for a human operator to decide whether to run the strategy. It is not an automatic live-trading approval.",
        "",
        "## What Was Run",
        "",
        f"- Contract: `{preflight.get('resolved_instrument') or ''}` from the selected NinjaTrader seed template.",
        f"- Phase 1 optimizer rows parsed: {int((results or {}).get('row_count') or 0)}",
        f"- Phase 2 refinement templates generated: {phase2_template_count}",
        f"- Phase 3 daily-risk templates generated: {phase3_template_count}",
        f"- Final fixed Backtest templates generated: {final_template_count}",
        f"- Final review status: `{final_validation_status or 'not yet run'}`",
        f"- Final recommendations: {final_recommendation_count}",
    ]
    if oos_from_date or oos_to_date:
        lines.append(f"- OOS validation window: {oos_from_date or '(seed default)'} to {oos_to_date or '(seed default)'}")
    lines.extend(["", "## Decision Notes", ""])
    lines.extend(f"- {note}" for note in notes)

    if final_review_dir is not None:
        recommendations_md = final_review_dir / "recommendations.md"
        lines.extend([
            "",
            "## Final Recommendations",
            "",
            f"See `{recommendations_md.name}` in `{final_review_dir.name}/` for the bucket-diverse ranked candidate list.",
        ])
        if recommendations_md.exists():
            text = recommendations_md.read_text(encoding="utf-8")
            lines.extend(["", text.strip()])

    lines.extend([
        "",
        "## Operator Decision Guidance",
        "",
        "The package supports operator review. Inspect trade distribution, recent trade sequence, and live-market suitability before running. Several non-lead candidates are intentionally retained to cover different session buckets and risk shapes — they give the operator alternatives if the top candidate is too concentrated for the intended deployment window.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candidate_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No candidates._"
    headers = [
        "batch_id",
        "profit_factor",
        "total_net_profit",
        "drawdown_abs",
        "total_trades",
        "param_Start_Time_(HH)",
        "param_Duration_Time_(HH)",
        "param_averageSlow",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row.get(key)) for key in headers) + " |")
    return "\n".join(lines)


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value).replace("|", "\\|")


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
