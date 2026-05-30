from __future__ import annotations

"""Lineage walker for finalist candidates.

Given a finalist id like ``F_001`` from the Decision Dashboard, walk back
through every recipe stage that contributed to it: finalist → parent row in
its source stage → parent row in *that* stage's source stage → … until a
row with no ``parent_candidate_id`` is reached (a Stage 1 root).

Reads:
  * ``generated_templates/final_backtest/recipe_template_manifest.json`` to
    resolve the finalist's source stage + parent candidate id.
  * ``deployment_package/final_backtest_handoff/final_backtest_review/
    evaluated_candidates.json`` for the finalist's own KPIs + status.
  * ``parsed_results/<stage_id>/scored_rows.json`` for each prior stage's
    winning row (each row carries the ``parent_candidate_id`` of the row in
    the *previous* stage that seeded its template — that's the spine the
    walker follows).
  * ``parsed_results/<stage_id>/selected.json`` to tag each non-root row as
    selected vs evaluated.
  * The saved recipe for human-readable stage labels.

Degrades gracefully: if any artifact is missing the walker stops, returns
what it found, and records a ``notes`` entry explaining where the chain
broke. Never raises for missing optional artifacts — the page is read-only
and an incomplete tree is more useful than a hard error.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession

# NinjaTrader optimizer CSVs expose parameters under their display names
# (e.g. ``Start_Time_(HH)``), while the recipe manifest's strategy_values
# carry the canonical code-level names (``StartTimeH``). The lineage diff
# needs both sources in a single namespace — without normalization, every
# matrix axis lights up as "added" on the finalist node because the prior
# stage's row dict uses a different key. _DISPLAY_TO_PROP is the Pantheon
# parameter table that the existing settings_loader already maintains.
# Non-Pantheon strategies fall back to identity (param names pass through
# unchanged) which is no worse than the pre-normalization behavior.
try:
    from ta_foundation.analysis.strategies.pantheon_bot_v2.settings_loader import (
        _DISPLAY_TO_PROP as _PANTHEON_DISPLAY_TO_PROP,
    )
except Exception:  # pragma: no cover - safety net for import-time edge cases
    _PANTHEON_DISPLAY_TO_PROP = {}


PARSED_RESULTS_DIRNAME = "parsed_results"
GENERATED_DIRNAME = "generated_templates"
FINAL_BACKTEST_STAGE_ID = "final_backtest"
FINAL_MANIFEST_PATH = (GENERATED_DIRNAME, FINAL_BACKTEST_STAGE_ID, "recipe_template_manifest.json")
EVALUATED_PATH = (
    "deployment_package",
    "final_backtest_handoff",
    "final_backtest_review",
    "evaluated_candidates.json",
)
RECOMMENDATIONS_PATH = (
    "deployment_package",
    "final_backtest_handoff",
    "final_backtest_review",
    "recommendations.json",
)


@dataclass(frozen=True)
class LineageKpis:
    profit_factor: float | None = None
    total_net_profit: float | None = None
    max_drawdown: float | None = None
    trades: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profit_factor": self.profit_factor,
            "total_net_profit": self.total_net_profit,
            "max_drawdown": self.max_drawdown,
            "trades": self.trades,
        }


@dataclass(frozen=True)
class LineageNode:
    depth: int  # 0 = Stage 1 root, increases toward the finalist
    stage_id: str
    stage_label: str
    stage_type: str  # "optimizer" | "fixed_backtest" | "unknown"
    role: str  # "stage_root" | "stage_winner" | "finalist"
    candidate_id: str | None
    template_id: str | None
    bucket_id: str | None
    row_index: int | None
    params: dict[str, Any]
    # Per-param change marker vs the previous stage's node. Keys mirror
    # ``params``. Each value is ``{"value": Any, "previous": Any | None,
    # "kind": "unchanged" | "changed" | "added"}``. Root node entries are
    # all ``added`` (no parent to diff against). The template uses this
    # to color-code values so operators can spot what shifted between
    # stages at a glance.
    param_diff: dict[str, dict[str, Any]]
    kpis: LineageKpis
    status: str  # "selected" | "evaluated" | "recommended" | "rejected" | "warning" | "unknown"
    status_reason: str
    links: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "stage_id": self.stage_id,
            "stage_label": self.stage_label,
            "stage_type": self.stage_type,
            "role": self.role,
            "candidate_id": self.candidate_id,
            "template_id": self.template_id,
            "bucket_id": self.bucket_id,
            "row_index": self.row_index,
            "params": dict(self.params),
            "param_diff": {k: dict(v) for k, v in self.param_diff.items()},
            "kpis": self.kpis.to_dict(),
            "status": self.status,
            "status_reason": self.status_reason,
            "links": dict(self.links),
        }


@dataclass(frozen=True)
class LineageReport:
    session_id: str
    session_label: str | None
    candidate_id: str
    candidate_label: str | None
    nodes: list[LineageNode]  # ordered Stage 1 → finalist
    siblings: list[str]
    # Alphabetically-sorted union of every node's param keys. The template
    # iterates this on every node so rows line up vertically across stages,
    # rendering ``—`` placeholders for keys not present at a given node.
    union_param_keys: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_label": self.session_label,
            "candidate_id": self.candidate_id,
            "candidate_label": self.candidate_label,
            "nodes": [n.to_dict() for n in self.nodes],
            "siblings": list(self.siblings),
            "union_param_keys": list(self.union_param_keys),
            "notes": list(self.notes),
        }


class LineageError(Exception):
    pass


def build_lineage(session: OptimizerSession, candidate_id: str) -> LineageReport:
    cid = (candidate_id or "").strip()
    if not cid:
        raise LineageError("candidate_id is required.")

    notes: list[str] = []

    manifest = _load_json(session.directory.joinpath(*FINAL_MANIFEST_PATH))
    if not manifest:
        raise LineageError(
            "Final backtest manifest not found. Run the recipe through "
            "final_backtest before inspecting lineage."
        )

    templates = manifest.get("templates") if isinstance(manifest, dict) else None
    if not isinstance(templates, list):
        raise LineageError("Final backtest manifest has no templates list.")

    siblings = sorted({
        sib
        for sib in (_short_finalist_id(str(t.get("template_id") or "")) for t in templates if isinstance(t, dict))
        if sib
    })

    finalist_entry = _find_finalist(templates, cid)
    if finalist_entry is None:
        raise LineageError(
            f"Finalist {cid!r} not found in final backtest manifest. "
            f"Known finalists: {siblings or '(none)'}"
        )

    evaluated_by_run = _evaluated_by_run_id(session)
    recommendations = _recommendations_index(session)
    recipe_stages = _load_recipe_stages(session)
    selected_by_stage: dict[str, set[str]] = {}

    doc = session.load_document()

    finalist_node = _build_finalist_node(
        session=session,
        candidate_id=cid,
        manifest_entry=finalist_entry,
        evaluated_by_run=evaluated_by_run,
        recommendations=recommendations,
        recipe_stages=recipe_stages,
    )

    nodes: list[LineageNode] = [finalist_node]

    next_stage_id = _source_stage_for_finalist(finalist_entry)
    next_parent_cid = str(finalist_entry.get("parent_candidate_id") or "").strip() or None

    safety = 32
    while next_stage_id and next_parent_cid and safety > 0:
        safety -= 1
        stage_node, prev_stage_id, prev_parent_cid = _walk_one_stage(
            session=session,
            stage_id=next_stage_id,
            candidate_id=next_parent_cid,
            recipe_stages=recipe_stages,
            selected_by_stage=selected_by_stage,
            notes=notes,
        )
        if stage_node is None:
            break
        nodes.append(stage_node)
        next_stage_id = prev_stage_id
        next_parent_cid = prev_parent_cid

    if safety == 0:
        notes.append("Lineage walk hit the safety cap (32 hops). Truncated.")

    # nodes were built finalist-first; flip so Stage 1 root comes first.
    ordered = list(reversed(nodes))

    # The finalist's params come from the manifest's ``strategy_values``,
    # which is the strategy's FULL canonical parameter dict (including
    # decorative non-sweep things like BotName, FileName, IOpacity).
    # Prior stages only expose params the optimizer actually tracked, so
    # the decoratives appear nowhere except the finalist — and the naive
    # diff lights every one of them up as ``added``. Restrict the finalist's
    # params to keys that show up at *some* prior stage (or the manifest's
    # initial_bucket_values + fixed_values, which are operator-declared and
    # therefore meaningful) so the diff only shows things the operator
    # actually configured or the optimizer swept.
    if len(ordered) > 1:
        ordered[-1] = _filter_finalist_to_tracked_params(
            finalist=ordered[-1],
            prior_nodes=ordered[:-1],
            manifest_entry=finalist_entry,
        )

    # Sort every node's params identically so the operator can scan top-to-
    # bottom and visually line up the same key across stages. Alphabetical
    # is the cheapest stable order; case-insensitive so e.g. ``averageFast``
    # and ``MaxStop`` don't sort by ASCII case.
    for depth, node in enumerate(ordered):
        ordered[depth] = _replace_params(
            node, params=_sort_params_alphabetically(node.params)
        )

    previous_params: dict[str, Any] = {}
    for depth, node in enumerate(ordered):
        diff = _compute_param_diff(node.params, previous_params, is_root=(depth == 0))
        ordered[depth] = _replace_node(node, depth=depth, param_diff=diff)
        previous_params = node.params

    union_keys = sorted(
        {key for node in ordered for key in node.params.keys()},
        key=str.lower,
    )

    return LineageReport(
        session_id=session.id,
        session_label=doc.label,
        candidate_id=cid,
        candidate_label=_candidate_label(finalist_entry, evaluated_by_run.get(cid)),
        nodes=ordered,
        siblings=siblings,
        union_param_keys=union_keys,
        notes=notes,
    )


def list_finalist_ids(session: OptimizerSession) -> list[str]:
    """Return the F_NNN ids referenced by the session's final manifest, sorted."""
    manifest = _load_json(session.directory.joinpath(*FINAL_MANIFEST_PATH))
    if not isinstance(manifest, dict):
        return []
    templates = manifest.get("templates") or []
    if not isinstance(templates, list):
        return []
    return sorted({
        sib
        for sib in (
            _short_finalist_id(str(t.get("template_id") or ""))
            for t in templates if isinstance(t, dict)
        )
        if sib
    })


def _walk_one_stage(
    *,
    session: OptimizerSession,
    stage_id: str,
    candidate_id: str,
    recipe_stages: dict[str, dict[str, Any]],
    selected_by_stage: dict[str, set[str]],
    notes: list[str],
) -> tuple[LineageNode | None, str | None, str | None]:
    stage_dir = session.directory / PARSED_RESULTS_DIRNAME / stage_id
    scored_path = stage_dir / "scored_rows.json"
    scored = _load_json(scored_path)
    if not isinstance(scored, list):
        notes.append(
            f"No scored_rows.json for {stage_id}: lineage stops here."
        )
        return None, None, None

    row = next(
        (r for r in scored if isinstance(r, dict) and str(r.get("candidate_id") or "") == candidate_id),
        None,
    )
    if row is None:
        notes.append(
            f"Row {candidate_id!r} not found in {scored_path.name} for {stage_id}: lineage stops here."
        )
        return None, None, None

    if stage_id not in selected_by_stage:
        selected_by_stage[stage_id] = _selected_candidate_ids(stage_dir / "selected.json")

    is_selected = candidate_id in selected_by_stage[stage_id]
    status = "selected" if is_selected else "evaluated"
    status_reason = (
        "Picked by the stage's selection rules."
        if is_selected
        else "Scored but not selected (sibling row to the winner)."
    )

    stage_meta = recipe_stages.get(stage_id) or {}
    role = "stage_root" if not row.get("parent_candidate_id") else "stage_winner"

    node = LineageNode(
        depth=-1,  # filled in by caller after the list is reversed
        stage_id=stage_id,
        stage_label=str(stage_meta.get("description") or stage_id),
        stage_type=str(stage_meta.get("stage_type") or "optimizer"),
        role=role,
        candidate_id=candidate_id,
        template_id=_str_or_none(row.get("template_id")),
        bucket_id=_str_or_none(row.get("bucket_id")),
        row_index=_int_or_none(row.get("optimizer_row_id")),
        params=_extract_params(row),
        param_diff={},
        kpis=_kpis_from_row(row),
        status=status,
        status_reason=status_reason,
        links=_links_for_stage_row(session, stage_id=stage_id),
    )

    prev_parent_cid = _str_or_none(row.get("parent_candidate_id"))
    prev_stage_id = _stage_from_candidate_id(prev_parent_cid) if prev_parent_cid else None
    return node, prev_stage_id, prev_parent_cid


def _build_finalist_node(
    *,
    session: OptimizerSession,
    candidate_id: str,
    manifest_entry: dict[str, Any],
    evaluated_by_run: dict[str, dict[str, Any]],
    recommendations: dict[str, dict[str, Any]],
    recipe_stages: dict[str, dict[str, Any]],
) -> LineageNode:
    eval_row = evaluated_by_run.get(candidate_id) or {}
    rec_entry = recommendations.get(candidate_id) or {}
    rec_status = str(rec_entry.get("status") or "").lower()

    if rec_status == "recommended":
        status = "recommended"
        status_reason = str(rec_entry.get("reason") or "Passed all final validation checks.")
    elif rec_status == "rejected":
        status = "rejected"
        status_reason = str(rec_entry.get("reason") or "Did not pass final validation.")
    elif eval_row:
        status = "evaluated"
        status_reason = str(eval_row.get("reasons") or "Evaluated; not promoted to recommendations.")
    else:
        status = "unknown"
        status_reason = "Final-backtest review not on disk yet."

    stage_meta = recipe_stages.get(FINAL_BACKTEST_STAGE_ID) or {}
    template_id = str(manifest_entry.get("template_id") or "") or None

    params = _merge_finalist_params(manifest_entry, eval_row)
    links = {
        "report_url": f"/optimizer/sessions/{session.id}/candidates/{candidate_id}/report",
        "decision_url": f"/optimizer/sessions/{session.id}/decision",
    }

    return LineageNode(
        depth=-1,
        stage_id=FINAL_BACKTEST_STAGE_ID,
        stage_label=str(stage_meta.get("description") or "Final Backtest Validation"),
        stage_type=str(stage_meta.get("stage_type") or "fixed_backtest"),
        role="finalist",
        candidate_id=candidate_id,
        template_id=template_id,
        bucket_id=_str_or_none(manifest_entry.get("initial_bucket_key")),
        row_index=None,
        params=params,
        param_diff={},
        kpis=_kpis_from_evaluated(eval_row),
        status=status,
        status_reason=status_reason,
        links=links,
    )


def _candidate_label(manifest_entry: dict[str, Any], eval_row: dict[str, Any] | None) -> str | None:
    eval_row = eval_row or {}
    label = str(eval_row.get("label") or manifest_entry.get("label") or "").strip()
    return label or None


def _find_finalist(templates: list[Any], candidate_id: str) -> dict[str, Any] | None:
    short = candidate_id.strip()
    upper = short.upper()
    for item in templates:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("template_id") or "")
        if not tid:
            continue
        if tid == short or tid == upper:
            return item
        s = _short_finalist_id(tid)
        if s == short or s == upper:
            return item
    return None


def _short_finalist_id(template_id: str) -> str:
    tail = template_id.rsplit("__", 1)[-1]
    return tail if tail.startswith("F_") else ""


def _source_stage_for_finalist(entry: dict[str, Any]) -> str | None:
    explicit = str(entry.get("final_selection_source_stage") or "").strip()
    if explicit:
        return explicit
    parent_cid = str(entry.get("parent_candidate_id") or "")
    head, sep, _ = parent_cid.partition("__")
    if sep and head.startswith("stage_"):
        return head
    nominal = str(entry.get("parent_stage_id") or "").strip()
    return nominal or None


def _stage_from_candidate_id(candidate_id: str | None) -> str | None:
    if not candidate_id:
        return None
    head, sep, _ = candidate_id.partition("__")
    return head if sep and head.startswith("stage_") else None


def _evaluated_by_run_id(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    payload = _load_json(session.directory.joinpath(*EVALUATED_PATH))
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("run_id") or "").strip()
        if rid:
            out[rid] = row
    return out


def _recommendations_index(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    payload = _load_json(session.directory.joinpath(*RECOMMENDATIONS_PATH))
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("recommendations") or []:
        if isinstance(row, dict) and row.get("run_id"):
            entry = dict(row)
            entry["status"] = "recommended"
            out[str(row["run_id"])] = entry
    for row in payload.get("rejected") or []:
        if isinstance(row, dict) and row.get("run_id"):
            entry = dict(row)
            entry["status"] = "rejected"
            out[str(row["run_id"])] = entry
    return out


def _load_recipe_stages(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    path = session.directory / "recipe.json"
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for stage in payload.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage_id"):
            out[str(stage["stage_id"])] = stage
    return out


def _selected_candidate_ids(selected_path: Path) -> set[str]:
    rows = _load_json(selected_path)
    if not isinstance(rows, list):
        return set()
    return {
        str(r.get("candidate_id") or "")
        for r in rows
        if isinstance(r, dict) and r.get("candidate_id")
    }


def _extract_params(row: dict[str, Any]) -> dict[str, Any]:
    """Pull ``param_*`` columns out of a scored row and normalize the names
    to the strategy's canonical key namespace.

    NinjaTrader's optimizer CSV exposes each parameter under its DISPLAY
    name (e.g. ``Start_Time_(HH)``), but the manifest's strategy_values dict
    and the operator's mental model both use the CODE name (``StartTimeH``).
    Without normalization, the lineage diff treats them as different params
    and lights up every matrix axis as a spurious ``added`` badge on the
    finalist. We translate display→code here via the existing Pantheon
    mapping; non-Pantheon strategies fall back to identity, which is no
    worse than the pre-fix behavior.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if not key.startswith("param_") or value is None:
            continue
        bare = key[len("param_"):]
        canonical = _PANTHEON_DISPLAY_TO_PROP.get(bare, bare)
        if canonical not in out:
            out[canonical] = value
    return out


def _merge_finalist_params(
    manifest_entry: dict[str, Any],
    eval_row: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the finalist's authoritative parameter set.

    Prefer ``strategy_values`` from the manifest — it's the canonical
    full-param dict the recipe builder writes when stamping the fixed
    backtest template and already uses code-level names that line up with
    the normalized scored_rows ``param_*`` keys.

    Fall back to the bucket+fixed+eval-row merge when ``strategy_values``
    is absent (older sessions); that path is what we had before but is
    namespace-mismatched against scored_rows, so the diff against the
    parent stage will under-report changes there.
    """
    strategy = manifest_entry.get("strategy_values")
    if isinstance(strategy, dict) and strategy:
        return {k: v for k, v in strategy.items() if v is not None}

    merged: dict[str, Any] = {}
    merged.update({
        k: v
        for k, v in (manifest_entry.get("initial_bucket_values") or {}).items()
        if v is not None
    })
    merged.update({
        k: v
        for k, v in (manifest_entry.get("fixed_values") or {}).items()
        if v is not None
    })
    merged.update(_extract_params(eval_row))
    return merged


def _kpis_from_row(row: dict[str, Any]) -> LineageKpis:
    return LineageKpis(
        profit_factor=_safe_float(row.get("profit_factor")),
        total_net_profit=_safe_float(row.get("total_net_profit") or row.get("net_profit")),
        max_drawdown=_safe_float(row.get("max_drawdown") or row.get("drawdown_abs")),
        trades=_safe_int(row.get("trades") or row.get("total_trades")),
    )


def _kpis_from_evaluated(eval_row: dict[str, Any]) -> LineageKpis:
    return LineageKpis(
        profit_factor=_safe_float(eval_row.get("profit_factor")),
        total_net_profit=_safe_float(eval_row.get("total_net_profit")),
        max_drawdown=_safe_float(eval_row.get("max_drawdown")),
        trades=_safe_int(eval_row.get("trades")),
    )


def _links_for_stage_row(session: OptimizerSession, *, stage_id: str) -> dict[str, str]:
    # Route through ``/resume`` so the ``_optimizer_session`` cookie gets
    # pinned to this session before the recipe editor reads it, and pass
    # ``focus_tab=results`` so the page lands on the Results tab with the
    # stage pre-selected instead of the default Matrix & Roles tab.
    return {
        "stage_results_url": (
            f"/optimizer/sessions/{session.id}/resume?"
            f"focus_stage={stage_id}&focus_tab=results"
        ),
    }


def _filter_finalist_to_tracked_params(
    *,
    finalist: LineageNode,
    prior_nodes: list[LineageNode],
    manifest_entry: dict[str, Any],
) -> LineageNode:
    """Strip strategy-decorative keys (BotName, FileName, IOpacity, ...) that
    only appear at the finalist because ``strategy_values`` is the complete
    strategy state. We keep keys that either showed up at a prior stage or
    were explicitly declared on the manifest entry (matrix axes, fixed
    values) so genuinely operator-tracked params survive."""
    tracked: set[str] = set()
    for node in prior_nodes:
        tracked.update(node.params.keys())
    tracked.update(_dict_keys(manifest_entry.get("initial_bucket_values")))
    tracked.update(_dict_keys(manifest_entry.get("fixed_values")))
    if not tracked:
        # Nothing to filter against — keep finalist params as-is rather
        # than blanking them out and losing all signal.
        return finalist
    filtered = {k: v for k, v in finalist.params.items() if k in tracked}
    return _replace_node(finalist, depth=finalist.depth, param_diff=finalist.param_diff) \
        if filtered == finalist.params else LineageNode(
            depth=finalist.depth,
            stage_id=finalist.stage_id,
            stage_label=finalist.stage_label,
            stage_type=finalist.stage_type,
            role=finalist.role,
            candidate_id=finalist.candidate_id,
            template_id=finalist.template_id,
            bucket_id=finalist.bucket_id,
            row_index=finalist.row_index,
            params=filtered,
            param_diff=finalist.param_diff,
            kpis=finalist.kpis,
            status=finalist.status,
            status_reason=finalist.status_reason,
            links=finalist.links,
        )


def _dict_keys(value: Any) -> list[str]:
    return list(value.keys()) if isinstance(value, dict) else []


def _sort_params_alphabetically(params: dict[str, Any]) -> dict[str, Any]:
    return {k: params[k] for k in sorted(params.keys(), key=str.lower)}


def _replace_params(node: LineageNode, *, params: dict[str, Any]) -> LineageNode:
    return LineageNode(
        depth=node.depth,
        stage_id=node.stage_id,
        stage_label=node.stage_label,
        stage_type=node.stage_type,
        role=node.role,
        candidate_id=node.candidate_id,
        template_id=node.template_id,
        bucket_id=node.bucket_id,
        row_index=node.row_index,
        params=params,
        param_diff=node.param_diff,
        kpis=node.kpis,
        status=node.status,
        status_reason=node.status_reason,
        links=node.links,
    )


def _replace_node(
    node: LineageNode,
    *,
    depth: int,
    param_diff: dict[str, dict[str, Any]],
) -> LineageNode:
    return LineageNode(
        depth=depth,
        stage_id=node.stage_id,
        stage_label=node.stage_label,
        stage_type=node.stage_type,
        role=node.role,
        candidate_id=node.candidate_id,
        template_id=node.template_id,
        bucket_id=node.bucket_id,
        row_index=node.row_index,
        params=node.params,
        param_diff=param_diff,
        kpis=node.kpis,
        status=node.status,
        status_reason=node.status_reason,
        links=node.links,
    )


def _compute_param_diff(
    current: dict[str, Any],
    previous: dict[str, Any],
    *,
    is_root: bool,
) -> dict[str, dict[str, Any]]:
    """Per-param change marker keyed by param name.

    - ``is_root`` (depth 0): every entry is ``"unchanged"`` because there is
      no parent stage to diff against. We do not tag root params as
      ``"added"`` — that would visually highlight the entire baseline.
    - Param value differs from parent: ``"changed"`` with ``previous``.
    - Param present here but not in the parent: ``"added"`` (e.g. a tuning
      parameter that only the child stage sweeps).
    - Param present and equal to parent: ``"unchanged"``.

    Numeric comparisons coerce via ``float`` so ``5`` and ``5.0`` match;
    booleans compare directly; everything else falls back to string equality
    so casing-trivial differences don't show up as changes.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, value in current.items():
        if is_root:
            out[key] = {"value": value, "previous": None, "kind": "unchanged"}
            continue
        if key not in previous:
            out[key] = {"value": value, "previous": None, "kind": "added"}
            continue
        if _params_equal(value, previous[key]):
            out[key] = {"value": value, "previous": previous[key], "kind": "unchanged"}
        else:
            out[key] = {"value": value, "previous": previous[key], "kind": "changed"}
    return out


def _params_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
