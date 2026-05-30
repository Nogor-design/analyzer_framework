from __future__ import annotations

"""Promote pending shortlist rows to NinjaTrader fixed-backtest templates.

The shortlist (:mod:`ta_foundation.web.optimizer_shortlist`) lets the
operator save interesting rows from any stage. Rows from refinement
stages (anything other than ``final_backtest``) come back as
``status="pending"`` because they don't yet have a stamped fixed-backtest
XML, so they can't be backtested OOS. This module is the promotion path
that turns those pending rows into runnable ``P_NNN.xml`` templates and
mirrors the existing ``final_backtest`` directory layout under a parallel
``promoted/`` tree.

Directory layout written by :func:`promote_pending`::

    <session>/generated_templates/promoted/
        P_001.xml
        P_002.xml
        recipe_template_manifest.json
    <session>/deployment_package/promoted_handoff/named_backtest_templates/recipe/
        P_001.xml  (mirror)

The operator runs NinjaTrader against ``generated_templates/promoted/``;
the parse + dashboard merge are handled by
:mod:`ta_foundation.web.optimizer_promotion_results`.

Promotion is idempotent: re-calling :func:`promote_pending` after the
shortlist has already been promoted is a no-op (each ``ShortlistItem``
records its ``promoted_run_id``).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.optimizer_lineage import extract_params_from_row
from ta_foundation.web.optimizer_recipe import (
    OptimizerRecipeDocument,
    OptimizerRecipeError,
    RecipeStage,
    load_recipe,
)
from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_shortlist import (
    ITEM_STATUS_PENDING,
    Shortlist,
    ShortlistItem,
    load_shortlist,
    mark_promoted,
    resolve_shortlist,
)


PROMOTED_DIRNAME = "promoted"
PROMOTED_MANIFEST_FILENAME = "recipe_template_manifest.json"
PROMOTED_HANDOFF_DIRNAME = "promoted_handoff"
PROMOTED_NAMED_TEMPLATES_SUBPATH = ("named_backtest_templates", "recipe")
PROMOTED_STAGE_TYPE = "promoted_backtest"
PARSED_RESULTS_DIRNAME = "parsed_results"


class PromotionError(Exception):
    """Raised only when the session is so misconfigured that nothing can run
    (e.g. recipe missing, seed template missing). Per-row failures land in
    :attr:`PromotionResult.errors` instead."""


@dataclass(frozen=True)
class PromotedTemplateEntry:
    template_id: str                       # "P_001"
    source_stage_id: str                   # parent stage of the source row
    source_candidate_id: str               # parent candidate id of the source row
    path: str                              # absolute path to stamped xml
    deployment_package_path: str | None    # mirror in deployment_package, if written
    strategy_values: dict[str, Any]
    promoted_at: str                       # ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "source_stage_id": self.source_stage_id,
            "source_candidate_id": self.source_candidate_id,
            "path": self.path,
            "deployment_package_path": self.deployment_package_path,
            "strategy_values": dict(self.strategy_values),
            "promoted_at": self.promoted_at,
        }


@dataclass(frozen=True)
class PromotionResult:
    promoted: list[PromotedTemplateEntry] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    manifest_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": [e.to_dict() for e in self.promoted],
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "manifest_path": self.manifest_path,
            "promoted_count": len(self.promoted),
            "skipped_count": len(self.skipped),
            "error_count": len(self.errors),
        }


def promote_pending(session: OptimizerSession) -> PromotionResult:
    """Stamp a NinjaTrader fixed-backtest XML for every pending shortlist row.

    Already-promoted items (``ShortlistItem.promoted_run_id is not None``)
    go to :attr:`PromotionResult.skipped`. Items whose source row can no
    longer be resolved go to :attr:`PromotionResult.errors`.
    """
    try:
        recipe = load_recipe(session)
    except OptimizerRecipeError as exc:
        raise PromotionError(f"Cannot promote without a saved recipe: {exc}") from exc

    doc = session.load_document()
    seed_path = Path(doc.seed_template_path)
    if not seed_path.exists():
        raise PromotionError(f"Seed template not found: {seed_path}")

    raw = load_shortlist(session)
    resolved = resolve_shortlist(session).items
    by_key: dict[tuple[str, str], ShortlistItem] = {item.key(): item for item in raw.items}

    output_dir = session.directory / "generated_templates" / PROMOTED_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    deployment_dir = (
        session.directory
        / "deployment_package"
        / PROMOTED_HANDOFF_DIRNAME
        / PROMOTED_NAMED_TEMPLATES_SUBPATH[0]
        / PROMOTED_NAMED_TEMPLATES_SUBPATH[1]
    )
    deployment_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_existing_manifest(output_dir / PROMOTED_MANIFEST_FILENAME)
    existing_template_ids = {
        str(entry.get("template_id") or "")
        for entry in manifest.get("templates") or []
        if isinstance(entry, dict)
    }
    next_index = _next_p_nnn_index(existing_template_ids)

    from_date = doc.oos_from_date or None
    to_date = doc.oos_to_date or None
    fixed_overrides = {
        entry.param: entry.value
        for entry in recipe.base_matrix
        if entry.role == "fixed"
    }
    pin_names_by_stage = {
        stage.stage_id: tuple(stage.pin) for stage in recipe.stages
    }
    stage_row_cache: dict[str, dict[str, dict[str, Any]]] = {}

    result_promoted: list[PromotedTemplateEntry] = []
    result_skipped: list[dict[str, Any]] = []
    result_errors: list[dict[str, Any]] = []

    for resolved_item in resolved:
        if resolved_item.is_finalist:
            continue
        source_item = by_key.get((resolved_item.stage_id, resolved_item.candidate_id))
        if source_item is None:
            # Out of sync with raw shortlist — should not happen, but skip safely.
            continue
        if source_item.promoted_run_id:
            result_skipped.append({
                "stage_id": source_item.stage_id,
                "candidate_id": source_item.candidate_id,
                "promoted_run_id": source_item.promoted_run_id,
                "promoted_at": source_item.promoted_at,
                "reason": "already_promoted",
            })
            continue
        # Only act on rows resolvable to a stage row. Stale rows or items
        # whose status isn't pending (e.g. a finalist sneaking in) are
        # skipped silently to keep promotion focused.
        if resolved_item.status != ITEM_STATUS_PENDING:
            result_errors.append({
                "stage_id": source_item.stage_id,
                "candidate_id": source_item.candidate_id,
                "reason": f"shortlist status is {resolved_item.status!r}; only pending rows can be promoted",
            })
            continue

        rows_by_cid = stage_row_cache.get(source_item.stage_id)
        if rows_by_cid is None:
            rows_by_cid = _load_stage_rows(session, source_item.stage_id)
            stage_row_cache[source_item.stage_id] = rows_by_cid
        row = rows_by_cid.get(source_item.candidate_id)
        if row is None:
            result_errors.append({
                "stage_id": source_item.stage_id,
                "candidate_id": source_item.candidate_id,
                "reason": "no scored_rows.json entry for this candidate",
            })
            continue

        stage = _find_stage(recipe, source_item.stage_id)
        pin_names = pin_names_by_stage.get(source_item.stage_id, ())

        values = _resolve_promoted_params(
            row=row,
            stage=stage,
            pin_names=pin_names,
            base_matrix_fixed=fixed_overrides,
        )

        template_id = f"P_{next_index:03d}"
        next_index += 1
        promoted_at = _now_iso()
        target = output_dir / f"{template_id}.xml"
        try:
            generated = generate_fixed_backtest_template(
                seed_path,
                target,
                values,
                from_date=from_date,
                to_date=to_date,
                strict_params=True,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a per-row error
            result_errors.append({
                "stage_id": source_item.stage_id,
                "candidate_id": source_item.candidate_id,
                "reason": f"generate_fixed_backtest_template failed: {exc}",
            })
            continue

        mirror_path: Path | None = None
        try:
            import shutil as _shutil

            mirror_path = deployment_dir / f"{template_id}.xml"
            _shutil.copy2(generated, mirror_path)
        except OSError as exc:
            # Mirror is a convenience copy; the template is still on disk.
            mirror_path = None
            result_errors.append({
                "stage_id": source_item.stage_id,
                "candidate_id": source_item.candidate_id,
                "reason": f"mirror to deployment_package failed: {exc}",
            })

        entry = PromotedTemplateEntry(
            template_id=template_id,
            source_stage_id=source_item.stage_id,
            source_candidate_id=source_item.candidate_id,
            path=str(generated),
            deployment_package_path=str(mirror_path) if mirror_path else None,
            strategy_values=values,
            promoted_at=promoted_at,
        )
        result_promoted.append(entry)
        mark_promoted(
            session,
            stage_id=source_item.stage_id,
            candidate_id=source_item.candidate_id,
            promoted_run_id=template_id,
            promoted_at=promoted_at,
        )

    manifest_path = output_dir / PROMOTED_MANIFEST_FILENAME
    _write_merged_manifest(
        manifest_path,
        existing=manifest,
        new_entries=result_promoted,
        recipe=recipe,
    )

    return PromotionResult(
        promoted=result_promoted,
        skipped=result_skipped,
        errors=result_errors,
        manifest_path=str(manifest_path),
    )


# ---------------------------------------------------------------------------
# Param resolution
# ---------------------------------------------------------------------------

def _resolve_promoted_params(
    *,
    row: dict[str, Any],
    stage: RecipeStage | None,
    pin_names: tuple[str, ...],
    base_matrix_fixed: dict[str, Any],
) -> dict[str, Any]:
    """Build the ``strategy_values`` dict for one promoted template.

    Merge order (lowest → highest priority):
      1. ``base_matrix`` ``role=fixed`` entries — recipe contract.
      2. Parent-stage pin values — read defensively from the row by name
         (the row already carries these as ``param_*`` columns in the
         normal case; this loop just makes the dependency explicit).
      3. Row's own ``param_*`` columns, normalized display→code via
         :func:`extract_params_from_row` — these win because they describe
         the actual point in the parameter space the operator picked.

    Seed defaults aren't materialized here; they're carried by the seed
    XML and ``generate_fixed_backtest_template`` patches in only what we
    pass.
    """
    values: dict[str, Any] = {}
    # 1. base_matrix fixed
    for name, value in base_matrix_fixed.items():
        if value is not None:
            values[name] = value
    # 2. parent pin values (defensive read)
    for pin in pin_names:
        pin_value = _row_value(row, pin)
        if pin_value is not None:
            values[pin] = pin_value
    # 3. row's own param_* (normalized) — last write wins
    for name, value in extract_params_from_row(row).items():
        if value is not None:
            values[name] = value
    # stage is currently informational; reserved for future per-stage
    # overrides (e.g. forcing add_optimize defaults). Keep the parameter
    # so callers don't have to rewire when that wiring lands.
    _ = stage
    return values


def _row_value(row: dict[str, Any], name: str) -> Any:
    """Look up ``name`` in a scored row, accepting bare and ``param_`` forms."""
    if name in row and row[name] is not None:
        return row[name]
    prefixed = f"param_{name}"
    if prefixed in row and row[prefixed] is not None:
        return row[prefixed]
    return None


# ---------------------------------------------------------------------------
# Manifest read / write
# ---------------------------------------------------------------------------

def _load_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_merged_manifest(
    path: Path,
    *,
    existing: dict[str, Any],
    new_entries: list[PromotedTemplateEntry],
    recipe: OptimizerRecipeDocument,
) -> None:
    """Append new promoted entries to the existing manifest.

    Existing templates are preserved so the operator's prior NT runs and
    the parse path keep their manifest linkage. Re-promotions after an
    on-disk edit reconcile by ``template_id`` (last write wins).
    """
    by_id: dict[str, dict[str, Any]] = {}
    for entry in existing.get("templates") or []:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("template_id") or "")
        if tid:
            by_id[tid] = dict(entry)
    for new in new_entries:
        by_id[new.template_id] = {
            "stage_id": PROMOTED_DIRNAME,
            "stage_type": PROMOTED_STAGE_TYPE,
            "template_id": new.template_id,
            "bucket_id": new.template_id,  # one bucket per promoted row
            "path": new.path,
            "deployment_package_path": new.deployment_package_path,
            "combination_count": 1,
            "parent_candidate_id": new.source_candidate_id,
            "parent_stage_id": new.source_stage_id,
            "source_stage_id": new.source_stage_id,
            "source_candidate_id": new.source_candidate_id,
            "promoted_at": new.promoted_at,
            "strategy_values": dict(new.strategy_values),
        }
    templates = sorted(by_id.values(), key=lambda e: str(e.get("template_id") or ""))
    manifest = {
        "schema_version": 1,
        "stage_id": PROMOTED_DIRNAME,
        "stage_type": PROMOTED_STAGE_TYPE,
        "recipe_id": recipe.recipe_id,
        "template_count": len(templates),
        "templates": templates,
    }
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# P_NNN assignment
# ---------------------------------------------------------------------------

def _next_p_nnn_index(existing_template_ids: set[str]) -> int:
    """Return the next ``P_NNN`` rank, starting at 1 and stepping past any
    gap left by removed templates so re-running promotion never collides."""
    highest = 0
    for tid in existing_template_ids:
        if not tid.startswith("P_"):
            continue
        tail = tid[2:]
        try:
            value = int(tail)
        except ValueError:
            continue
        if value > highest:
            highest = value
    return highest + 1


# ---------------------------------------------------------------------------
# Stage row loader
# ---------------------------------------------------------------------------

def _load_stage_rows(
    session: OptimizerSession, stage_id: str
) -> dict[str, dict[str, Any]]:
    path = session.directory / PARSED_RESULTS_DIRNAME / stage_id / "scored_rows.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in payload:
        if isinstance(row, dict) and row.get("candidate_id"):
            out[str(row["candidate_id"])] = row
    return out


def _find_stage(
    recipe: OptimizerRecipeDocument, stage_id: str
) -> RecipeStage | None:
    for stage in recipe.stages:
        if stage.stage_id == stage_id:
            return stage
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
