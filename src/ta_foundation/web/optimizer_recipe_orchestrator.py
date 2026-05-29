from __future__ import annotations

"""Durable orchestrator shell for Recipe/Matrix optimizer runs.

This module intentionally implements the first narrow orchestration slice:
plan the recipe, generate the first runnable stage, submit that stage to the
existing NinjaTrader AddOn command path, and persist resumable state/events.
``advance_once`` performs one resumable stage-boundary step: detect completed
stage output, ingest results, select candidates, and park before child-stage
generation.
"""

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_recipe import load_recipe
from ta_foundation.optimization.evaluator import EvaluationConfig
from ta_foundation.optimization.review import write_result_review
from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan, load_recipe_plan
from ta_foundation.web.optimizer_recipe_results import load_recipe_stage_results
from ta_foundation.web.optimizer_recipe_runner import (
    NT_OUTPUT_DIRNAME,
    RecipeStageRunRecord,
    cancel_recipe_stage_run,
    load_recipe_run_history,
    save_recipe_run,
    start_recipe_stage_run,
)
from ta_foundation.web.optimizer_recipe_selection import select_recipe_stage_candidates
from ta_foundation.web.optimizer_recipe_state import (
    RecipeRunState,
    append_recipe_event,
    load_recipe_events,
    load_recipe_state,
    save_recipe_state,
)
from ta_foundation.web.optimizer_recipe_templates import MANIFEST_FILENAME, generate_recipe_stage_templates
from ta_foundation.web.optimizer_session import OptimizerSession


class RecipeOrchestratorError(Exception):
    pass


class RecipeRunOrchestrator:
    def __init__(self, session: OptimizerSession) -> None:
        self.session = session

    def start(
        self,
        *,
        command_file: Path | None = None,
        status_file: Path | None = None,
    ) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        plan = load_recipe_plan(self.session) or build_and_save_recipe_plan(self.session).to_dict()
        stage_id = _first_runnable_stage_id(plan)
        if not stage_id:
            raise RecipeOrchestratorError("Recipe plan has no runnable optimizer stage.")

        state = RecipeRunState(
            recipe_id=recipe.recipe_id,
            state="ready_to_generate_stage",
            current_stage_id=stage_id,
        )
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="recipe_started",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Recipe {recipe.recipe_id} started; first stage is {stage_id}.",
        )

        state.state = "generating_stage_templates"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="generating_stage_templates",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generating templates for {stage_id}.",
        )
        templates = generate_recipe_stage_templates(self.session, stage_id=stage_id)

        state.state = "ready_to_run_stage"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="stage_templates_generated",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generated {len(templates)} templates for {stage_id}.",
            details={"template_count": len(templates)},
        )

        run = start_recipe_stage_run(
            self.session,
            stage_id=stage_id,
            command_file=command_file,
            status_file=status_file,
        )
        state.state = "running_stage"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="stage_run_requested",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Submitted {stage_id} to NinjaTrader.",
            details=run.to_dict(),
        )
        return self.status()

    def advance_once(
        self,
        *,
        command_file: Path | None = None,
        status_file: Path | None = None,
    ) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        state = load_recipe_state(self.session)
        if state is None:
            state = RecipeRunState(recipe_id=recipe.recipe_id, state="planned")
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_noop",
                recipe_id=recipe.recipe_id,
                message="No recipe run is active yet.",
            )
            return self.status()

        if state.stop_requested or state.state == "stopped":
            state.state = "stopped"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=state.current_stage_id,
                message="Advance skipped because stop is requested.",
            )
            return self.status()

        if state.pause_requested or state.state == "paused":
            state.state = "paused"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=state.current_stage_id,
                message="Advance paused at a safe boundary.",
            )
            return self.status()

        if state.state == "generating_child_stage":
            return self._generate_and_start_child_stage(state, command_file=command_file, status_file=status_file)

        if state.state == "ready_for_final_backtest":
            return self._generate_and_start_final_backtest(state, command_file=command_file, status_file=status_file)

        if state.state == "running_final_backtest":
            return self._advance_final_backtest(state)

        if state.state not in {"running_stage", "waiting_for_results"}:
            append_recipe_event(
                self.session,
                event_type="advance_noop",
                recipe_id=recipe.recipe_id,
                stage_id=state.current_stage_id,
                message=f"No advance action for state {state.state}.",
            )
            return self.status()

        stage_id = state.current_stage_id
        if not stage_id:
            state.state = "failed"
            state.last_error = "Current stage is missing."
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_failed",
                recipe_id=recipe.recipe_id,
                message=state.last_error,
            )
            return self.status()

        if state.state == "running_stage":
            state.state = "waiting_for_results"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="waiting_for_results",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message=f"Waiting for NinjaTrader output for {stage_id}.",
            )

        completion = _stage_output_completion(self.session, stage_id=stage_id)
        if not completion["complete"]:
            return self.status()

        _mark_run_record_completed(self.session, stage_id=stage_id)
        append_recipe_event(
            self.session,
            event_type="stage_output_complete",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Detected complete output for {stage_id}.",
            details=completion,
        )

        state.state = "ingesting_results"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="ingesting_results",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Ingesting optimizer results for {stage_id}.",
        )
        results = load_recipe_stage_results(self.session, stage_id=stage_id)

        state.state = "selecting_candidates"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="selecting_candidates",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Selecting recipe candidates for {stage_id}.",
            details={"row_count": results.row_count, "batch_count": results.batch_count},
        )
        selection = select_recipe_stage_candidates(self.session, stage_id=stage_id, results=results)
        if selection.selected_count <= 0:
            state.state = "failed"
            state.last_error = (
                f"No candidates passed guardrails for {stage_id}. "
                "Review rejected rows before running refinement or final Backtest."
            )
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="no_candidates_passed_guardrails",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message=state.last_error,
                details=selection.to_dict(),
            )
            return self.status()

        next_stage_id = _next_stage_id(load_recipe_plan(self.session) or {}, stage_id)
        if state.stop_requested:
            state.state = "stopped"
            message = "Recipe stopped after candidate selection."
            event_type = "stage_selection_stopped"
        elif state.pause_requested:
            state.state = "paused"
            message = "Recipe paused after candidate selection."
            event_type = "stage_selection_paused"
        elif next_stage_id:
            state.current_stage_id = next_stage_id
            next_stage = _stage_by_id(load_recipe_plan(self.session) or {}, next_stage_id)
            if next_stage and next_stage.get("stage_type") == "optimizer":
                state.state = "generating_child_stage"
                save_recipe_state(self.session, state)
                append_recipe_event(
                    self.session,
                    event_type="child_stage_pending",
                    recipe_id=recipe.recipe_id,
                    stage_id=stage_id,
                    message=f"Selected {selection.selected_count} candidates for {stage_id}; generating {next_stage_id}.",
                    details=selection.to_dict(),
                )
                return self._generate_and_start_child_stage(state, command_file=command_file, status_file=status_file)
            state.state = "ready_for_final_backtest"
            message = (
                f"Selected {selection.selected_count} candidates for {stage_id}; "
                f"next stage {next_stage_id} is ready for final backtest generation."
            )
            event_type = "final_backtest_pending"
        else:
            state.state = "complete"
            message = f"Selected {selection.selected_count} candidates for {stage_id}; recipe has no next stage."
            event_type = "recipe_complete"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type=event_type,
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=message,
            details=selection.to_dict(),
        )
        return self.status()

    def _generate_and_start_child_stage(
        self,
        state: RecipeRunState,
        *,
        command_file: Path | None,
        status_file: Path | None,
    ) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        stage_id = state.current_stage_id
        if not stage_id:
            state.state = "failed"
            state.last_error = "Child stage is missing."
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_failed",
                recipe_id=recipe.recipe_id,
                message=state.last_error,
            )
            return self.status()

        append_recipe_event(
            self.session,
            event_type="generating_child_stage",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generating child-stage templates for {stage_id}.",
        )
        templates = generate_recipe_stage_templates(self.session, stage_id=stage_id)
        state.state = "ready_to_run_stage"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="child_stage_templates_generated",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generated {len(templates)} child templates for {stage_id}.",
            details={"template_count": len(templates)},
        )

        if state.stop_requested:
            state.state = "stopped"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message="Child stage generated but not submitted because stop is requested.",
            )
            return self.status()
        if state.pause_requested:
            state.state = "paused"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message="Child stage generated but not submitted because pause is requested.",
            )
            return self.status()

        run = start_recipe_stage_run(
            self.session,
            stage_id=stage_id,
            command_file=command_file,
            status_file=status_file,
        )
        state.state = "running_stage"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="stage_run_requested",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Submitted {stage_id} to NinjaTrader.",
            details=run.to_dict(),
        )
        return self.status()

    def _generate_and_start_final_backtest(
        self,
        state: RecipeRunState,
        *,
        command_file: Path | None,
        status_file: Path | None,
    ) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        stage_id = state.current_stage_id or "final_backtest"
        state.current_stage_id = stage_id
        append_recipe_event(
            self.session,
            event_type="generating_final_backtest",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generating final fixed Backtest templates for {stage_id}.",
        )
        templates = generate_recipe_stage_templates(self.session, stage_id=stage_id)
        state.state = "ready_to_run_stage"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="final_backtest_templates_generated",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Generated {len(templates)} final fixed Backtest templates.",
            details={"template_count": len(templates)},
        )

        if state.stop_requested:
            state.state = "stopped"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message="Final Backtest templates generated but not submitted because stop is requested.",
            )
            return self.status()
        if state.pause_requested:
            state.state = "paused"
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="advance_blocked",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message="Final Backtest templates generated but not submitted because pause is requested.",
            )
            return self.status()

        run = start_recipe_stage_run(
            self.session,
            stage_id=stage_id,
            command_file=command_file,
            status_file=status_file,
        )
        state.state = "running_final_backtest"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="final_backtest_run_requested",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=f"Submitted {stage_id} to NinjaTrader.",
            details=run.to_dict(),
        )
        return self.status()

    def _advance_final_backtest(self, state: RecipeRunState) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        stage_id = state.current_stage_id or "final_backtest"
        completion = _stage_output_completion(self.session, stage_id=stage_id)
        if not completion["complete"]:
            append_recipe_event(
                self.session,
                event_type="waiting_for_final_backtest",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message=f"Waiting for final Backtest output for {stage_id}.",
                details=completion,
            )
            return self.status()

        _mark_run_record_completed(self.session, stage_id=stage_id)
        mirrored = _mirror_final_backtest_results(self.session, stage_id=stage_id)
        review = _write_final_backtest_review(self.session)
        finalize = _auto_finalize_artifacts(self.session) if review.get("review_dir") else {}
        state.state = "complete" if review.get("review_dir") else "reviewing_final_backtest"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="final_backtest_review_complete" if review.get("review_dir") else "final_backtest_results_ready",
            recipe_id=recipe.recipe_id,
            stage_id=stage_id,
            message=(
                f"Final Backtest review complete ({len(mirrored)} folders mirrored)."
                if review.get("review_dir")
                else f"Final Backtest results are ready for review ({len(mirrored)} folders mirrored)."
            ),
            details={
                "mirrored_result_dirs": mirrored,
                "review": review,
                "finalize": finalize,
                **completion,
            },
        )
        return self.status()

    def pause(self) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        state = load_recipe_state(self.session) or RecipeRunState(
            recipe_id=recipe.recipe_id,
            state="paused",
        )
        state.pause_requested = True
        if state.state not in {"running_stage", "waiting_for_results", "running_final_backtest"}:
            state.state = "paused"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="pause_requested",
            recipe_id=recipe.recipe_id,
            stage_id=state.current_stage_id,
            message="Pause requested; it will take effect at the next safe boundary.",
        )
        return self.status()

    def resume(
        self,
        *,
        command_file: Path | None = None,
        status_file: Path | None = None,
    ) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        state = load_recipe_state(self.session) or RecipeRunState(
            recipe_id=recipe.recipe_id,
            state="planned",
        )
        previous_state = state.state
        state.pause_requested = False
        state.stop_requested = False

        if previous_state == "paused":
            state.state = "planned"
            state.last_error = None
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="resume_requested",
                recipe_id=recipe.recipe_id,
                stage_id=state.current_stage_id,
                message="Resume requested.",
            )
            return self.status()

        if previous_state == "stopped":
            # Recover without destroying the plan or losing the current stage.
            # If a stage was in flight, re-arm it (regenerate templates and
            # resubmit). If we hadn't picked a stage yet, fall back to the
            # first runnable stage in the plan.
            stage_id = state.current_stage_id or _first_runnable_stage_id(
                load_recipe_plan(self.session) or {}
            )
            state.current_stage_id = stage_id
            state.current_template_id = None
            state.last_error = None
            save_recipe_state(self.session, state)
            append_recipe_event(
                self.session,
                event_type="resume_requested",
                recipe_id=recipe.recipe_id,
                stage_id=stage_id,
                message=(
                    f"Resume requested after stop; re-arming {stage_id}."
                    if stage_id
                    else "Resume requested after stop; no stage to resume."
                ),
            )
            if not stage_id:
                state.state = "planned"
                save_recipe_state(self.session, state)
                return self.status()

            plan = load_recipe_plan(self.session) or {}
            stage = _stage_by_id(plan, stage_id) or {}
            if stage.get("stage_type") == "fixed_backtest":
                state.state = "ready_for_final_backtest"
                save_recipe_state(self.session, state)
                return self._generate_and_start_final_backtest(
                    state, command_file=command_file, status_file=status_file
                )
            state.state = "generating_stage_templates"
            save_recipe_state(self.session, state)
            return self._generate_and_start_child_stage(
                state, command_file=command_file, status_file=status_file
            )

        # Any other state: just clear the flags so the run can proceed.
        state.last_error = None
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="resume_requested",
            recipe_id=recipe.recipe_id,
            stage_id=state.current_stage_id,
            message="Resume requested.",
        )
        return self.status()

    def stop(self) -> dict[str, Any]:
        recipe = load_recipe(self.session)
        state = load_recipe_state(self.session) or RecipeRunState(
            recipe_id=recipe.recipe_id,
            state="stopped",
        )
        cancelled_run = cancel_recipe_stage_run(
            self.session,
            reason="Recipe stopped by user.",
        )
        state.stop_requested = True
        state.state = "stopped"
        save_recipe_state(self.session, state)
        append_recipe_event(
            self.session,
            event_type="stop_requested",
            recipe_id=recipe.recipe_id,
            stage_id=state.current_stage_id,
            message="Recipe stopped by user.",
            details={"cancelled_run": cancelled_run.to_dict() if cancelled_run else None},
        )
        return self.status()

    def status(self) -> dict[str, Any]:
        state = load_recipe_state(self.session)
        run = _load_run(self.session)
        current_stage_id = state.current_stage_id if state else None
        progress = _recipe_progress(self.session, stage_id=current_stage_id, run=run)
        state = _sync_state_from_progress(self.session, state=state, progress=progress)
        run = _load_run(self.session)
        events = [_compact_event_for_status(event) for event in load_recipe_events(self.session, limit=20)]
        run_history = load_recipe_run_history(self.session)
        return {
            "session_id": self.session.id,
            "state": state.to_dict() if state else None,
            "run": run.to_dict() if run else None,
            "run_history": [item.to_dict() for item in run_history],
            "timeline": _recipe_stage_timeline(self.session, runs=run_history, events=events, current_progress=progress),
            "events": events,
            "progress": progress,
            "stage_results": _latest_stage_results(self.session, stage_id=current_stage_id),
            "final_review": _final_review_status(self.session),
            "artifacts": _recipe_artifact_links(self.session),
        }


def _compact_event_for_status(event: dict[str, Any]) -> dict[str, Any]:
    compact = dict(event)
    details = compact.get("details")
    if not isinstance(details, dict):
        return compact
    compact_details: dict[str, Any] = {}
    for key, value in details.items():
        if key.endswith("_rows") and isinstance(value, list):
            compact_details[f"{key}_count"] = len(value)
        elif key in {"selected_rows", "rejected_rows"} and isinstance(value, list):
            compact_details[f"{key}_count"] = len(value)
        else:
            compact_details[key] = value
    compact["details"] = compact_details
    return compact


def _first_runnable_stage_id(plan: dict[str, Any]) -> str | None:
    for stage in plan.get("stages") or []:
        if stage.get("stage_type") == "optimizer" and not stage.get("deferred"):
            return str(stage.get("stage_id") or "")
    return None


def _next_stage_id(plan: dict[str, Any], current_stage_id: str) -> str | None:
    stages = list(plan.get("stages") or [])
    for index, stage in enumerate(stages):
        if str(stage.get("stage_id") or "") != current_stage_id:
            continue
        for next_stage in stages[index + 1:]:
            stage_id = str(next_stage.get("stage_id") or "")
            if stage_id:
                return stage_id
        return None
    return None


def _stage_by_id(plan: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
    for stage in plan.get("stages") or []:
        if str(stage.get("stage_id") or "") == stage_id:
            return stage
    return None


def _load_run(session: OptimizerSession) -> RecipeStageRunRecord | None:
    from ta_foundation.web.optimizer_recipe_runner import load_recipe_run

    return load_recipe_run(session)


def _mark_run_record_completed(session: OptimizerSession, *, stage_id: str) -> None:
    run = _load_run(session)
    if run is None or run.stage_id != stage_id:
        return
    if run.state == "completed" and run.finished_at:
        return
    run.state = "completed"
    run.finished_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    run.last_error = None
    save_recipe_run(session, run)


def _sync_state_from_progress(
    session: OptimizerSession,
    *,
    state: RecipeRunState | None,
    progress: dict[str, Any],
) -> RecipeRunState | None:
    if state is None:
        return None
    run_state = str(progress.get("run_state") or "").strip().lower()
    if progress.get("complete") and state.state == "running_stage":
        if state.current_stage_id:
            _mark_run_record_completed(session, stage_id=state.current_stage_id)
        state.state = "waiting_for_results"
        save_recipe_state(session, state)
        append_recipe_event(
            session,
            event_type="stage_output_ready",
            recipe_id=state.recipe_id,
            stage_id=state.current_stage_id,
            message="NinjaTrader output is complete; stage is ready for ingestion and selection.",
            details={"progress": progress},
        )
        return state
    if run_state not in {"failed", "error", "timed_out", "timedout", "cancelled"}:
        return state
    error = str(progress.get("last_error") or run_state or "Recipe run failed.").strip()
    next_state = "stopped" if run_state == "cancelled" else "failed"
    if state.state == next_state and state.last_error == error:
        return state
    state.state = next_state
    state.last_error = error
    save_recipe_state(session, state)
    append_recipe_event(
        session,
        event_type="stage_run_failed",
        recipe_id=state.recipe_id,
        stage_id=state.current_stage_id,
        message=error,
        details={"run_state": run_state, "progress": progress},
    )
    return state


def _recipe_progress(
    session: OptimizerSession,
    *,
    stage_id: str | None,
    run: RecipeStageRunRecord | None,
) -> dict[str, Any]:
    if not stage_id:
        return {
            "stage_id": None,
            "completed_templates": 0,
            "total_templates": run.total_templates if run else 0,
            "percent": 0,
            "complete": False,
        }
    heartbeat = _load_recipe_heartbeat(run) if run and run.stage_id == stage_id else None
    live_run_active = bool(
        run
        and run.stage_id == stage_id
        and str(run.state or "").strip().lower() in {"requested", "running", "starting"}
    )
    completion = _stage_output_completion(
        session,
        stage_id=stage_id,
        include_batch_summary=not live_run_active,
    )
    completed = max(
        int(completion.get("summary_count") or 0),
        int(completion.get("optimization_csv_count") or 0),
        int(completion.get("batch_summary_completed") or 0),
    )
    total = int(completion.get("expected_count") or 0)
    if total <= 0 and run and run.stage_id == stage_id:
        total = int(run.total_templates or 0)

    live_state = run.state if run and run.stage_id == stage_id else None
    current_template = None
    last_error = None
    heartbeat_age_seconds = None
    source = "folder"
    if heartbeat:
        hb_completed = _safe_int(heartbeat.get("completed"))
        hb_total = _safe_int(heartbeat.get("total"))
        completed = max(completed, hb_completed)
        total = max(total, hb_total, 1)
        current_template = heartbeat.get("currentTemplate")
        last_error = heartbeat.get("lastError")
        heartbeat_age_seconds = heartbeat.get("_age_seconds")
        source = "heartbeat"
        hb_state = str(heartbeat.get("state") or "running").strip().lower()
        if hb_state == "finished" or (total > 0 and completed >= total and hb_state not in {"failed", "error", "timed_out", "timedout", "cancelled"}):
            live_state = "completed"
        elif hb_state == "cancelled":
            live_state = "cancelled"
        elif hb_state in {"failed", "error"}:
            live_state = "failed"
        elif hb_state in {"timed_out", "timedout", "timeout"}:
            live_state = "timed_out"
        elif hb_state in {"starting", "running", "requested"}:
            live_state = "running"
        elif hb_state:
            live_state = hb_state
        if run and run.stage_id == stage_id and (run.state != live_state or run.last_error != last_error):
            run.state = live_state or run.state
            run.last_error = last_error
            if run.state in {"completed", "failed", "timed_out", "cancelled"} and not run.finished_at:
                run.finished_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            save_recipe_run(session, run)

    percent = int(round((completed / total) * 100)) if total else 0
    return {
        "stage_id": stage_id,
        "run_id": run.run_id if run and run.stage_id == stage_id else None,
        "completed_templates": completed,
        "total_templates": total,
        "percent": max(0, min(100, percent)),
        "complete": bool(completion.get("complete")) or bool(total and completed >= total),
        "reason": completion.get("reason"),
        "output_dir": completion.get("output_dir"),
        "source": source,
        "run_state": live_state,
        "current_template": current_template,
        "last_error": last_error,
        "heartbeat_age_seconds": heartbeat_age_seconds,
    }


def _load_recipe_heartbeat(run: RecipeStageRunRecord) -> dict[str, Any] | None:
    path = Path(run.status_file or "")
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("runId") or "") != run.run_id:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    payload["_age_seconds"] = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    return payload


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _recipe_stage_timeline(
    session: OptimizerSession,
    *,
    runs: list[RecipeStageRunRecord],
    events: list[dict[str, Any]],
    current_progress: dict[str, Any],
) -> list[dict[str, Any]]:
    by_stage: dict[str, dict[str, Any]] = {}
    for run in runs:
        stage_id = run.stage_id
        live_run_active = str(run.state or "").strip().lower() in {"requested", "running", "starting"}
        completion = _stage_output_completion(
            session,
            stage_id=stage_id,
            include_batch_summary=not live_run_active,
        )
        completed = max(
            int(completion.get("summary_count") or 0),
            int(completion.get("optimization_csv_count") or 0),
            int(completion.get("batch_summary_completed") or 0),
        )
        total = int(completion.get("expected_count") or 0) or int(run.total_templates or 0)
        if current_progress.get("stage_id") == stage_id and run.run_id == str(current_progress.get("run_id") or run.run_id):
            completed = max(completed, int(current_progress.get("completed_templates") or 0))
            total = max(total, int(current_progress.get("total_templates") or 0))
        by_stage[stage_id] = {
            "stage_id": stage_id,
            "run_id": run.run_id,
            "state": run.state,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "completed_templates": completed,
            "total_templates": total,
            "last_error": run.last_error,
            "last_event": None,
        }

    for event in events:
        stage_id = str(event.get("stage_id") or "")
        if not stage_id:
            continue
        row = by_stage.setdefault(stage_id, {
            "stage_id": stage_id,
            "run_id": "",
            "state": "",
            "started_at": "",
            "finished_at": None,
            "completed_templates": 0,
            "total_templates": 0,
            "last_error": None,
            "last_event": None,
        })
        row["last_event"] = {
            "event_type": event.get("event_type"),
            "message": event.get("message"),
            "timestamp": event.get("timestamp"),
        }
    return sorted(by_stage.values(), key=lambda item: str(item.get("started_at") or item.get("stage_id") or ""))


def _latest_stage_results(session: OptimizerSession, *, stage_id: str | None) -> dict[str, Any]:
    candidates = [stage_id] if stage_id else []
    parsed_root = session.directory / "parsed_results"
    if parsed_root.exists():
        candidates.extend(
            path.name for path in sorted(parsed_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
            if path.is_dir() and path.name not in candidates
        )
    for candidate in candidates:
        if not candidate:
            continue
        stage_dir = parsed_root / candidate
        if not stage_dir.exists():
            continue
        scored = _load_json_list(stage_dir / "scored_rows.json")
        selected = _load_json_list(stage_dir / "selected.json")
        rejected = _load_json_list(stage_dir / "rejected.json")
        if scored or selected or rejected:
            return {
                "stage_id": candidate,
                "row_count": len(scored),
                "selected_count": len(selected),
                "rejected_count": len(rejected),
                "selected_csv": str(stage_dir / "selected.csv") if (stage_dir / "selected.csv").exists() else None,
                "selected_json": str(stage_dir / "selected.json") if (stage_dir / "selected.json").exists() else None,
            }
    return {"stage_id": stage_id, "row_count": 0, "selected_count": 0, "rejected_count": 0}


def _final_review_status(session: OptimizerSession) -> dict[str, Any]:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    summary_path = review_dir / "review_summary.json"
    recommendations_path = review_dir / "recommendations.json"
    payload = _load_json_dict(summary_path)
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "exists": review_dir.exists(),
        "review_dir": str(review_dir) if review_dir.exists() else None,
        "validation_status": payload.get("validation_status"),
        "recommendation_count": int(counts.get("recommendations") or 0) if counts else 0,
        "summary_url": f"/optimizer/sessions/{session.id}/recipe/artifacts/final_review_summary"
        if summary_path.exists() else None,
        "recommendations_url": f"/optimizer/sessions/{session.id}/recipe/artifacts/final_recommendations"
        if recommendations_path.exists() else None,
        "decision_url": f"/optimizer/sessions/{session.id}/decision" if review_dir.exists() else None,
    }


def _recipe_artifact_links(session: OptimizerSession) -> dict[str, Any]:
    links: dict[str, Any] = {}
    if (session.directory / "recipe_selection.json").exists():
        links["recipe_selection_json"] = f"/optimizer/sessions/{session.id}/recipe/artifacts/recipe_selection_json"
    if (session.directory / "recipe_selection.csv").exists():
        links["recipe_selection_csv"] = f"/optimizer/sessions/{session.id}/recipe/artifacts/recipe_selection_csv"
    final_templates_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "named_backtest_templates"
    )
    generated_final_dir = session.directory / "generated_templates" / "final_backtest"
    if final_templates_dir.exists() or generated_final_dir.exists():
        links["final_templates"] = f"/optimizer/sessions/{session.id}/templates/final"
    if generated_final_dir.exists():
        links["generated_final_dir"] = str(generated_final_dir)
    handoff_dir = session.directory / "deployment_package" / "final_backtest_handoff"
    if handoff_dir.exists():
        links["final_handoff_dir"] = str(handoff_dir)
    return links


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = _load_json_any(path)
    return payload if isinstance(payload, list) else []


def _load_json_dict(path: Path) -> dict[str, Any]:
    payload = _load_json_any(path)
    return payload if isinstance(payload, dict) else {}


def _load_json_any(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stage_output_completion(
    session: OptimizerSession,
    *,
    stage_id: str,
    include_batch_summary: bool = True,
) -> dict[str, Any]:
    output_dir = session.directory / NT_OUTPUT_DIRNAME / stage_id
    expected_ids = _expected_template_ids(session, stage_id)
    expected_count = len(expected_ids)
    if expected_count == 0:
        run = _load_run(session)
        if run and run.stage_id == stage_id:
            expected_count = run.total_templates

    summary_completed = _summary_completed_templates(output_dir)
    optimization_completed = _optimization_completed_templates(output_dir)
    batch_summary = (
        _batch_summary_completion(output_dir)
        if include_batch_summary
        else {"complete": False, "completed": 0, "total": 0}
    )

    complete_by_summary = bool(expected_ids) and expected_ids.issubset(summary_completed)
    complete_by_batch = bool(batch_summary["complete"])
    complete_by_output_evidence = expected_count > 0 and len(summary_completed | optimization_completed) >= expected_count
    complete = complete_by_summary or complete_by_batch or complete_by_output_evidence

    return {
        "complete": complete,
        "stage_id": stage_id,
        "output_dir": str(output_dir),
        "expected_count": expected_count,
        "summary_count": len(summary_completed),
        "optimization_csv_count": len(optimization_completed),
        "batch_summary_completed": batch_summary["completed"],
        "batch_summary_total": batch_summary["total"],
        "reason": (
            "summary_files"
            if complete_by_summary
            else "batch_run_summary"
            if complete_by_batch
            else "output_evidence"
            if complete_by_output_evidence
            else "incomplete"
        ),
    }


def _expected_template_ids(session: OptimizerSession, stage_id: str) -> set[str]:
    manifest = session.directory / "generated_templates" / stage_id / MANIFEST_FILENAME
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        templates = payload.get("templates") if isinstance(payload, dict) else None
        if isinstance(templates, list):
            return {
                str(item.get("template_id") or "")
                for item in templates
                if isinstance(item, dict) and item.get("template_id")
            }

    plan = load_recipe_plan(session) or {}
    for stage in plan.get("stages") or []:
        if str(stage.get("stage_id") or "") != stage_id:
            continue
        jobs = stage.get("jobs") or []
        return {
            str(job.get("template_id") or "")
            for job in jobs
            if isinstance(job, dict) and job.get("template_id")
        }
    return set()


def _summary_completed_templates(output_dir: Path) -> set[str]:
    if not output_dir.exists():
        return set()
    return {path.parent.name for path in output_dir.rglob("Summary.csv") if path.is_file()}


def _optimization_completed_templates(output_dir: Path) -> set[str]:
    if not output_dir.exists():
        return set()
    completed: set[str] = set()
    for path in output_dir.rglob("*_Optimization.csv"):
        if path.is_file():
            completed.add(path.parent.name)
    return completed


def _batch_summary_completion(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "BatchRunSummary.csv"
    if not path.exists():
        return {"complete": False, "completed": 0, "total": 0}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {"complete": False, "completed": 0, "total": 0}
    if not rows:
        return {"complete": False, "completed": 0, "total": 0}
    completed = [
        row for row in rows
        if str(row.get("Status") or "").strip().lower() == "completed"
    ]
    return {
        "complete": len(completed) == len(rows),
        "completed": len(completed),
        "total": len(rows),
    }


def _mirror_final_backtest_results(session: OptimizerSession, *, stage_id: str) -> list[str]:
    output_dir = session.directory / NT_OUTPUT_DIRNAME / stage_id
    manifest_path = session.directory / "generated_templates" / stage_id / MANIFEST_FILENAME
    if not output_dir.exists() or not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    templates = manifest.get("templates") if isinstance(manifest, dict) else None
    if not isinstance(templates, list):
        return []

    target_root = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "nt8_backtest_results"
    )
    target_root.mkdir(parents=True, exist_ok=True)
    mirrored: list[str] = []
    for item in templates:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("template_id") or "")
        if not template_id:
            continue
        source = output_dir / template_id
        if not source.exists():
            continue
        run_id = _final_run_id_from_template_id(template_id)
        target = target_root / run_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        mirrored.append(str(target))
    return mirrored


def _final_run_id_from_template_id(template_id: str) -> str:
    tail = template_id.rsplit("__", 1)[-1]
    if tail.startswith("F_"):
        return tail
    return template_id


def _write_final_backtest_review(session: OptimizerSession) -> dict[str, Any]:
    results_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "nt8_backtest_results"
    )
    if not results_dir.exists() or not any(path.is_file() for path in results_dir.rglob("*.csv")):
        return {"review_dir": None, "validation_status": None, "recommendation_count": 0}

    doc = session.load_document()
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    if review_dir.exists():
        shutil.rmtree(review_dir)
    config = EvaluationConfig(
        max_drawdown=float(doc.guardrails.max_drawdown_dollars) if doc.guardrails.max_drawdown_dollars is not None else 2500.0,
        min_trades=int(doc.guardrails.min_trades) if doc.guardrails.min_trades is not None else 10,
        min_profit_factor=float(doc.guardrails.min_profit_factor) if doc.guardrails.min_profit_factor is not None else 1.5,
        min_percent_days_traded=float(doc.guardrails.min_percent_days_traded) if doc.guardrails.min_percent_days_traded is not None else 20.0,
    )
    write_result_review(
        results_dir,
        review_dir,
        count=8,
        config=config,
        review_kind="final-backtest",
    )
    validation_status: str | None = None
    recommendation_count = 0
    summary_json = review_dir / "review_summary.json"
    if summary_json.exists():
        try:
            payload = json.loads(summary_json.read_text(encoding="utf-8"))
            validation_status = str(payload.get("validation_status") or "")
            counts = payload.get("counts") or {}
            recommendation_count = int(counts.get("recommendations") or 0)
        except (OSError, ValueError, TypeError):
            pass
    return {
        "review_dir": str(review_dir),
        "validation_status": validation_status,
        "recommendation_count": recommendation_count,
    }


def _auto_finalize_artifacts(session: OptimizerSession) -> dict[str, Any]:
    """Run the post-final-review steps that the operator otherwise has to
    click one-at-a-time on the Decision dashboard: rename templates, build
    the all-finalist HTML report, build per-candidate HTML reports.

    Each step is independent and reports its own success/error so a failure
    in one does not abort the others.
    """
    result: dict[str, Any] = {}

    try:
        from ta_foundation.web.optimizer_final_templates import rename_final_templates

        rename = rename_final_templates(session)
        result["rename_templates"] = {
            "ok": True,
            "count": rename.template_count,
            "output_dir": rename.output_dir,
        }
    except Exception as exc:
        result["rename_templates"] = {"ok": False, "error": str(exc)}

    try:
        from ta_foundation.web.optimizer_candidate_report import (
            build_session_candidate_report,
        )

        report = build_session_candidate_report(session)
        result["session_candidate_report"] = {
            "ok": bool(report.html_path),
            "html_path": report.html_path,
            "package_count": report.package_count,
        }
    except Exception as exc:
        result["session_candidate_report"] = {"ok": False, "error": str(exc)}

    try:
        from ta_foundation.web.optimizer_candidate_report import (
            build_all_candidate_reports,
        )

        batch = build_all_candidate_reports(session)
        result["per_candidate_reports"] = {
            "ok": True,
            "output_dir": batch.output_dir,
            "count": len(batch.per_candidate),
        }
    except Exception as exc:
        result["per_candidate_reports"] = {"ok": False, "error": str(exc)}

    return result
