from __future__ import annotations

"""Parse NinjaTrader output for promoted shortlist templates.

After :func:`ta_foundation.web.optimizer_promotion.promote_pending` stamps
``P_NNN.xml`` templates and the operator runs them in NinjaTrader, this
module:

1. Reuses :func:`load_recipe_stage_results` with ``stage_id="promoted"``
   to produce ``<session>/parsed_results/promoted/scored_rows.{csv,json}``.
2. Runs :func:`write_result_review` over ``<session>/nt_output/promoted/``
   to write a sibling review under
   ``<session>/deployment_package/promoted_handoff/promoted_review/``
   (mirrors the ``final_backtest_review`` layout, so the Decision
   Dashboard can load it with the same code path).

Both steps are best-effort: when ``nt_output/promoted/`` is missing or
empty, this module returns a result that records the reason rather than
raising. The deployment package builder calls into here unconditionally
and treats a no-op return as "no promoted rows to surface yet".
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.optimization.evaluator import EvaluationConfig
from ta_foundation.optimization.review import write_result_review
from ta_foundation.web.optimizer_promotion import (
    PROMOTED_DIRNAME,
    PROMOTED_HANDOFF_DIRNAME,
    PROMOTED_MANIFEST_FILENAME,
)
from ta_foundation.web.optimizer_recipe_results import (
    RecipeResultsError,
    load_recipe_stage_results,
)
from ta_foundation.web.optimizer_session import OptimizerSession


PROMOTED_REVIEW_DIRNAME = "promoted_review"
PROMOTED_RESULTS_DIRNAME = "nt8_backtest_results"


@dataclass(frozen=True)
class PromotedResults:
    parsed_rows_json: str | None = None
    parsed_rows_csv: str | None = None
    review_dir: str | None = None
    evaluated_candidates_path: str | None = None
    mirrored_results_dir: str | None = None
    mirrored_run_ids: list[str] = field(default_factory=list)
    row_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed_rows_json": self.parsed_rows_json,
            "parsed_rows_csv": self.parsed_rows_csv,
            "review_dir": self.review_dir,
            "evaluated_candidates_path": self.evaluated_candidates_path,
            "mirrored_results_dir": self.mirrored_results_dir,
            "mirrored_run_ids": list(self.mirrored_run_ids),
            "row_count": self.row_count,
            "notes": list(self.notes),
        }


def load_promoted_results(
    session: OptimizerSession,
    *,
    eval_config: EvaluationConfig | None = None,
) -> PromotedResults:
    """Parse promoted NT output and write the promoted review artifacts.

    Returns a snapshot describing what landed on disk (or what was
    missing). Never raises for empty/missing inputs — the caller is the
    deployment-package builder and a partial promoted layout shouldn't
    block the final_backtest path.
    """
    notes: list[str] = []
    nt_output_dir = session.directory / "nt_output" / PROMOTED_DIRNAME
    manifest_path = (
        session.directory
        / "generated_templates"
        / PROMOTED_DIRNAME
        / PROMOTED_MANIFEST_FILENAME
    )

    if not manifest_path.exists():
        notes.append("No promoted templates have been stamped yet.")
        return PromotedResults(notes=notes)
    if not nt_output_dir.exists() or not _has_files(nt_output_dir):
        notes.append(
            "No NinjaTrader output under nt_output/promoted/ — operator has "
            "not run the promoted templates yet."
        )
        return PromotedResults(notes=notes)

    parsed_rows_json: str | None = None
    parsed_rows_csv: str | None = None
    row_count = 0
    try:
        stage_results = load_recipe_stage_results(
            session, stage_id=PROMOTED_DIRNAME, persist=True
        )
        parsed_rows_json = stage_results.parsed_rows_json
        parsed_rows_csv = stage_results.parsed_rows_csv
        row_count = stage_results.row_count
        notes.extend(stage_results.notes)
    except RecipeResultsError as exc:
        notes.append(f"Parsing promoted NT output failed: {exc}")

    # Mirror nt_output/promoted/<P_NNN>/ → deployment_package/promoted_handoff/
    # nt8_backtest_results/<P_NNN>/ so build_candidate_report (and the existing
    # write_result_review path) see the same layout as the final-backtest
    # handoff. No-op when there are no promoted output folders yet.
    mirrored_root = (
        session.directory
        / "deployment_package"
        / PROMOTED_HANDOFF_DIRNAME
        / PROMOTED_RESULTS_DIRNAME
    )
    mirrored_ids = _mirror_promoted_results(
        nt_output_dir=nt_output_dir,
        manifest_path=manifest_path,
        target_root=mirrored_root,
    )
    review_input_dir = mirrored_root if mirrored_ids else nt_output_dir

    review_dir = (
        session.directory
        / "deployment_package"
        / PROMOTED_HANDOFF_DIRNAME
        / PROMOTED_REVIEW_DIRNAME
    )
    evaluated_path: Path | None = None
    try:
        review_dir.mkdir(parents=True, exist_ok=True)
        write_result_review(
            review_input_dir,
            review_dir,
            count=8,
            config=eval_config or EvaluationConfig(),
            # ``promoted-backtest`` skips the final-only settings-contract
            # check; promoted rows aren't required to match the trend
            # settings the final backtest contract enforces.
            review_kind="promoted-backtest",
        )
        evaluated_path = review_dir / "evaluated_candidates.json"
        if evaluated_path.exists():
            _decorate_evaluated_with_source_pointers(
                evaluated_path=evaluated_path,
                manifest_path=manifest_path,
            )
        else:
            notes.append("write_result_review did not produce evaluated_candidates.json.")
    except Exception as exc:  # noqa: BLE001 — surface as a note, never block
        notes.append(f"write_result_review failed for promoted: {exc}")

    return PromotedResults(
        parsed_rows_json=parsed_rows_json,
        parsed_rows_csv=parsed_rows_csv,
        review_dir=str(review_dir),
        evaluated_candidates_path=str(evaluated_path) if evaluated_path and evaluated_path.exists() else None,
        mirrored_results_dir=str(mirrored_root) if mirrored_ids else None,
        mirrored_run_ids=mirrored_ids,
        row_count=row_count,
        notes=notes,
    )


def _mirror_promoted_results(
    *,
    nt_output_dir: Path,
    manifest_path: Path,
    target_root: Path,
) -> list[str]:
    """Copy ``nt_output/promoted/<P_NNN>/`` trees into the handoff results
    directory. Returns the list of P_NNN ids that landed on disk.

    Mirrors the final-backtest pattern (``_mirror_final_backtest_results``
    in ``optimizer_recipe_orchestrator.py``) so the rest of the report
    pipeline doesn't need to know that promoted exists. Missing pieces
    (no manifest, no per-template subfolder) are skipped silently.
    """
    if not manifest_path.exists() or not nt_output_dir.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    templates = manifest.get("templates") if isinstance(manifest, dict) else None
    if not isinstance(templates, list):
        return []
    target_root.mkdir(parents=True, exist_ok=True)
    mirrored: list[str] = []
    for item in templates:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("template_id") or "")
        if not template_id:
            continue
        source = nt_output_dir / template_id
        if not source.exists():
            continue
        target = target_root / template_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        mirrored.append(template_id)
    return mirrored


def _decorate_evaluated_with_source_pointers(
    *,
    evaluated_path: Path,
    manifest_path: Path,
) -> None:
    """Join the promoted manifest into ``evaluated_candidates.json``.

    The Decision Dashboard wants ``kind`` and a ``source`` pointer on
    every promoted row so the operator can walk back to the original
    shortlist row. ``write_result_review`` is shared with the
    final-backtest path and can't carry those fields, so we layer them
    onto its output here.

    Idempotent: re-running the decorate overwrites in place.
    """
    try:
        payload = json.loads(evaluated_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return

    by_template = _index_promoted_manifest(manifest_path)
    for row in rows:
        if not isinstance(row, dict):
            continue
        template_id = str(row.get("run_id") or "")
        meta = by_template.get(template_id)
        row["kind"] = "promoted"
        if meta is not None:
            row["source_stage_id"] = meta.get("source_stage_id") or meta.get("parent_stage_id")
            row["source_candidate_id"] = meta.get("source_candidate_id") or meta.get("parent_candidate_id")
            row["template_id"] = template_id
            row["promoted_at"] = meta.get("promoted_at")

    evaluated_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _index_promoted_manifest(manifest_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    templates = payload.get("templates")
    if not isinstance(templates, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in templates:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("template_id") or "")
        if tid:
            out[tid] = entry
    return out


def _has_files(path: Path) -> bool:
    if not path.exists():
        return False
    return any(p.is_file() for p in path.rglob("*"))
