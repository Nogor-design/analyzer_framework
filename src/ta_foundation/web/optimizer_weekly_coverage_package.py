from __future__ import annotations

"""Build a lane-aware weekly package from final backtest optimizer artifacts.

This is intentionally small and file-based. It does not start NinjaTrader or
rerun any optimization; it reads the final backtest review CSVs and copies the
best available named XML templates into a weekly package folder.
"""

import csv
import html
import json
import shutil
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from ta_foundation.web.optimizer_final_templates import final_renamed_index_path
from ta_foundation.web.optimizer_session import OptimizerSession


DEFAULT_START_HOURS = [0, 4, 8, 12, 16, 20]
# Widened from [100,200,300,400] — low slow-MA lanes (20/50) scored strongest in
# pooled sweep analysis but previously had no lane to fill.
DEFAULT_SLOW_MA_VALUES = [20, 50, 100, 200, 300, 400]
DEFAULT_TARGET_PER_LANE = 2

PACKAGE_DIRNAME = "weekly_coverage_package"
VALIDATED_DIRNAME = "operationally_diverse_validated_named_templates"
VALIDATED_BY_RUN_DIRNAME = "operationally_diverse_validated_named_templates_by_run"
REVIEW_DIRNAME = "review_all_top2_named_templates"
REVIEW_BY_RUN_DIRNAME = "review_all_top2_named_templates_by_run"
FALLBACK_DIRNAME = "best_effort_fallback_named_templates"
FALLBACK_BY_RUN_DIRNAME = "best_effort_fallback_named_templates_by_run"
DATA_DIRNAME = "data"
REPORTS_DIRNAME = "reports"
REPORT_FILENAME = "operationally_diverse_weekly_coverage_package_report.html"
ZIP_FILENAME = "weekly_coverage_package.zip"


class WeeklyCoveragePackageError(Exception):
    pass


DEFAULT_DURATION_HOURS = 4
# Two templates in the same lane whose numeric knobs fall in the same band are
# treated as the same operational shape (e.g. a 200 vs 210 max stop is not worth
# keeping twice). Direction flags stay a hard distinction.
DEFAULT_STOP_BAND = 50.0
DEFAULT_TP_RATIO_BAND = 0.5


@dataclass(frozen=True)
class WeeklyCoverageConfig:
    start_hours: list[int] = field(default_factory=lambda: list(DEFAULT_START_HOURS))
    slow_ma_values: list[int] = field(default_factory=lambda: list(DEFAULT_SLOW_MA_VALUES))
    target_per_lane: int = DEFAULT_TARGET_PER_LANE
    duration_hours: int = DEFAULT_DURATION_HOURS
    stop_band: float = DEFAULT_STOP_BAND
    tp_ratio_band: float = DEFAULT_TP_RATIO_BAND
    require_final_status: str = "pass"
    package_dirname: str = PACKAGE_DIRNAME

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "WeeklyCoverageConfig":
        payload = payload or {}
        return cls(
            start_hours=_int_list(payload.get("start_hours"), DEFAULT_START_HOURS),
            slow_ma_values=_int_list(payload.get("slow_ma_values"), DEFAULT_SLOW_MA_VALUES),
            target_per_lane=max(1, _safe_int(payload.get("target_per_lane"), DEFAULT_TARGET_PER_LANE)),
            duration_hours=max(1, _safe_int(payload.get("duration_hours"), DEFAULT_DURATION_HOURS)),
            stop_band=_safe_float(payload.get("stop_band"), DEFAULT_STOP_BAND),
            tp_ratio_band=_safe_float(payload.get("tp_ratio_band"), DEFAULT_TP_RATIO_BAND),
            require_final_status=str(payload.get("require_final_status") or "pass"),
            package_dirname=_safe_filename(str(payload.get("package_dirname") or PACKAGE_DIRNAME)),
        )

    @classmethod
    def from_session(
        cls,
        session: OptimizerSession,
        payload: dict[str, Any] | None = None,
    ) -> "WeeklyCoverageConfig":
        """Build config using the lane grid actually run for this session.

        The grid is read from ``recipe.json``'s stage coverage grid so the
        scored lanes can never drift from what NinjaTrader optimized. An
        explicit ``payload`` still overrides (e.g. for ad-hoc rebuilds).
        """
        payload = payload or {}
        grid_start, grid_slow = _coverage_grid_from_recipe(session)
        grid_duration = _duration_from_recipe(session)
        return cls(
            start_hours=_int_list(payload.get("start_hours"), grid_start or DEFAULT_START_HOURS),
            slow_ma_values=_int_list(payload.get("slow_ma_values"), grid_slow or DEFAULT_SLOW_MA_VALUES),
            target_per_lane=max(1, _safe_int(payload.get("target_per_lane"), DEFAULT_TARGET_PER_LANE)),
            duration_hours=max(1, _safe_int(payload.get("duration_hours"), grid_duration or DEFAULT_DURATION_HOURS)),
            stop_band=_safe_float(payload.get("stop_band"), DEFAULT_STOP_BAND),
            tp_ratio_band=_safe_float(payload.get("tp_ratio_band"), DEFAULT_TP_RATIO_BAND),
            require_final_status=str(payload.get("require_final_status") or "pass"),
            package_dirname=_safe_filename(str(payload.get("package_dirname") or PACKAGE_DIRNAME)),
        )


@dataclass(frozen=True)
class WeeklyCoveragePackageResult:
    session_id: str
    package_dir: str
    zip_path: str
    report_path: str
    report_url: str
    zip_url: str
    validated_template_count: int
    review_template_count: int
    duplicate_drop_count: int
    lane_count: int
    full_lane_count: int
    thin_lane_count: int
    missing_lane_count: int
    fallback_template_count: int = 0
    covered_lane_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def weekly_coverage_package_dir(session: OptimizerSession, config: WeeklyCoverageConfig | None = None) -> Path:
    cfg = config or WeeklyCoverageConfig()
    return session.directory / "deployment_package" / cfg.package_dirname


def weekly_coverage_report_path(session: OptimizerSession, config: WeeklyCoverageConfig | None = None) -> Path:
    return weekly_coverage_package_dir(session, config) / REPORTS_DIRNAME / REPORT_FILENAME


def weekly_coverage_zip_path(session: OptimizerSession, config: WeeklyCoverageConfig | None = None) -> Path:
    return weekly_coverage_package_dir(session, config).parent / ZIP_FILENAME


def build_weekly_coverage_package(
    session: OptimizerSession,
    *,
    config: WeeklyCoverageConfig | None = None,
) -> WeeklyCoveragePackageResult:
    cfg = config or WeeklyCoverageConfig()
    paths = _package_paths(session, cfg)
    _reset_output_dirs(paths)

    review_dir = _review_dir(session)
    intake_rows = _load_final_rows(review_dir, cfg)
    source_templates = _source_templates_by_run_id(session)
    renamed_templates = _renamed_templates_by_run_id(session, source_templates)

    review_selection, review_reasons = _select_review_top2(intake_rows, cfg)
    validated_selection, validated_reasons, duplicate_audit = _select_operationally_diverse(
        intake_rows,
        cfg,
    )

    review_manifest = _copy_manifest_templates(
        review_selection,
        review_reasons,
        source_templates,
        renamed_templates,
        paths["review"],
        paths["review_by_run"],
    )
    validated_manifest = _copy_manifest_templates(
        validated_selection,
        validated_reasons,
        source_templates,
        renamed_templates,
        paths["validated"],
        paths["validated_by_run"],
    )

    # Best-effort fallback: for any lane the validated pass left empty, surface
    # the single best sub-threshold candidate so the team still gets a template
    # for that time frame. These are clearly labelled as not having passed
    # guardrails and are kept in a separate folder.
    fallback_selection, fallback_reasons = _select_lane_fallbacks(
        intake_rows, validated_selection, cfg,
    )
    fallback_manifest = _copy_manifest_templates(
        fallback_selection,
        fallback_reasons,
        source_templates,
        renamed_templates,
        paths["fallback"],
        paths["fallback_by_run"],
    )

    lane_rows = _lane_coverage_rows(intake_rows, validated_manifest, fallback_manifest, cfg)
    _write_csv(paths["data"] / "review_all_top2_selection.csv", review_manifest)
    _write_csv(paths["data"] / "operationally_diverse_validated_selection.csv", validated_manifest)
    _write_csv(paths["data"] / "best_effort_fallback_selection.csv", fallback_manifest)
    _write_csv(paths["data"] / "operational_diversity_duplicate_audit.csv", duplicate_audit)
    _write_csv(paths["data"] / "operationally_diverse_lane_coverage.csv", lane_rows)

    report_path = paths["reports"] / REPORT_FILENAME
    report_path.write_text(
        _render_report(validated_manifest, fallback_manifest, lane_rows, duplicate_audit, cfg),
        encoding="utf-8",
    )

    full_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) >= cfg.target_per_lane)
    thin_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) == 1)
    missing_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) == 0)
    # A lane is "covered" if it has a validated template OR a fallback template.
    covered_lanes = sum(
        1 for row in lane_rows
        if int(row["operationally_diverse_count"]) > 0 or int(row.get("fallback_count", 0)) > 0
    )

    manifest = {
        "schema_version": 1,
        "package_kind": "weekly_coverage_package",
        "session_id": session.id,
        "config": asdict(cfg),
        "validated_template_count": len(validated_manifest),
        "review_template_count": len(review_manifest),
        "fallback_template_count": len(fallback_manifest),
        "duplicate_drop_count": len(duplicate_audit),
        "lane_count": len(lane_rows),
        "full_lane_count": full_lanes,
        "thin_lane_count": thin_lanes,
        "missing_lane_count": missing_lanes,
        "covered_lane_count": covered_lanes,
        "artifacts": {
            "report": str(report_path),
            "validated_templates": str(paths["validated"]),
            "fallback_templates": str(paths["fallback"]),
            "review_templates": str(paths["review"]),
            "data": str(paths["data"]),
        },
    }
    (paths["root"] / "package_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    zip_path = weekly_coverage_zip_path(session, cfg)
    _write_zip(paths["root"], zip_path)

    return WeeklyCoveragePackageResult(
        session_id=session.id,
        package_dir=str(paths["root"].resolve()),
        zip_path=str(zip_path.resolve()),
        report_path=str(report_path.resolve()),
        report_url=f"/optimizer/sessions/{session.id}/weekly-coverage-package/report",
        zip_url=f"/optimizer/sessions/{session.id}/weekly-coverage-package.zip",
        validated_template_count=len(validated_manifest),
        review_template_count=len(review_manifest),
        duplicate_drop_count=len(duplicate_audit),
        lane_count=len(lane_rows),
        full_lane_count=full_lanes,
        thin_lane_count=thin_lanes,
        missing_lane_count=missing_lanes,
        fallback_template_count=len(fallback_manifest),
        covered_lane_count=covered_lanes,
        notes=_build_notes(
            validated_manifest, review_manifest, fallback_manifest, duplicate_audit, missing_lanes,
        ),
    )


def _package_paths(session: OptimizerSession, cfg: WeeklyCoverageConfig) -> dict[str, Path]:
    root = weekly_coverage_package_dir(session, cfg)
    return {
        "root": root,
        "validated": root / VALIDATED_DIRNAME,
        "validated_by_run": root / VALIDATED_BY_RUN_DIRNAME,
        "review": root / REVIEW_DIRNAME,
        "review_by_run": root / REVIEW_BY_RUN_DIRNAME,
        "fallback": root / FALLBACK_DIRNAME,
        "fallback_by_run": root / FALLBACK_BY_RUN_DIRNAME,
        "data": root / DATA_DIRNAME,
        "reports": root / REPORTS_DIRNAME,
    }


def _reset_output_dirs(paths: dict[str, Path]) -> None:
    root = paths["root"]
    if root.exists():
        shutil.rmtree(root)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)


def _coverage_grid_from_recipe(
    session: OptimizerSession,
) -> tuple[list[int] | None, list[int] | None]:
    """Read StartTimeH / averageSlow lane values from the session recipe.

    Returns (start_hours, slow_ma_values), each None if unavailable, so the
    package lanes match the grid that was actually optimized.
    """
    recipe_path = session.directory / "recipe.json"
    if not recipe_path.exists():
        return None, None
    try:
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None

    def _clean(values: Any) -> list[int] | None:
        if not isinstance(values, list):
            return None
        out = sorted({_safe_int(v, -1) for v in values if _safe_int(v, -1) >= 0})
        return out or None

    # 1) Explicit coverage grid (weekly-coverage sessions).
    for stage in data.get("stages") or []:
        grid = ((stage.get("selection") or {}).get("coverage_grid")) or {}
        if grid:
            return _clean(grid.get("StartTimeH")), _clean(grid.get("averageSlow"))

    # 2) Fall back to the base matrix axes (generic recipe sessions), so the
    #    package lanes still match the values actually swept.
    starts = slows = None
    for entry in data.get("base_matrix") or []:
        if str(entry.get("role")) != "matrix_axis":
            continue
        if entry.get("param") == "StartTimeH":
            starts = _clean(entry.get("values"))
        elif entry.get("param") == "averageSlow":
            slows = _clean(entry.get("values"))
    return starts, slows


def _duration_from_recipe(session: OptimizerSession) -> int | None:
    """Read the fixed DurationTimeH from the recipe base matrix, if present."""
    recipe_path = session.directory / "recipe.json"
    if not recipe_path.exists():
        return None
    try:
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for entry in data.get("base_matrix") or []:
        if entry.get("param") == "DurationTimeH":
            value = _safe_int(entry.get("value"), -1)
            return value if value > 0 else None
    return None


def _review_dir(session: OptimizerSession) -> Path:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    if not review_dir.exists():
        raise WeeklyCoveragePackageError(f"Final backtest review not found: {review_dir}")
    return review_dir


def _load_final_rows(review_dir: Path, cfg: WeeklyCoverageConfig) -> list[dict[str, Any]]:
    intake_path = review_dir / "result_intake.csv"
    evaluated_path = review_dir / "evaluated_candidates.csv"
    recommendations_path = review_dir / "recommendations.csv"
    if not intake_path.exists():
        raise WeeklyCoveragePackageError(f"Missing final backtest intake CSV: {intake_path}")
    if not evaluated_path.exists():
        raise WeeklyCoveragePackageError(f"Missing evaluated candidates CSV: {evaluated_path}")

    intake = _read_csv(intake_path)
    evaluated = {row.get("run_id", ""): row for row in _read_csv(evaluated_path)}
    recommended = {
        row.get("run_id", "")
        for row in _read_csv(recommendations_path)
    } if recommendations_path.exists() else set()

    rows: list[dict[str, Any]] = []
    for row in intake:
        merged = dict(row)
        eval_row = evaluated.get(str(row.get("run_id") or ""), {})
        for key in ("status", "score", "mode", "session_bucket", "risk_shape", "direction", "reasons"):
            merged[key] = eval_row.get(key, "")
        merged["recommended"] = "yes" if merged.get("run_id") in recommended else ""
        start = _safe_int(merged.get("start_hour"), -1)
        rev = _bool(merged.get("reverse"))
        slow = _safe_int(merged.get("average_slow"), -1)
        merged["bucket"] = f"{start:02d}-{start + cfg.duration_hours:02d}" if start >= 0 else ""
        merged["side"] = _side(rev)
        merged["naming_family"] = _family_name(slow, rev)
        merged["direction_shape"] = _direction_shape(merged)
        merged["trade_band"] = _trade_band(merged.get("trades"))
        rows.append(merged)
    return rows


def _select_review_top2(
    rows: list[dict[str, Any]],
    cfg: WeeklyCoverageConfig,
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    selected: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    for start in cfg.start_hours:
        for reverse in (False, True):
            for slow in cfg.slow_ma_values:
                pool = [row for row in rows if _lane_key(row) == (start, reverse, slow)]
                for rank, row in enumerate(sorted(pool, key=_sort_pf, reverse=True)[:cfg.target_per_lane], 1):
                    selected[str(row["run_id"])] = row
                    reasons[str(row["run_id"])].add(f"top{rank}_pf")
                for rank, row in enumerate(sorted(pool, key=_sort_net, reverse=True)[:cfg.target_per_lane], 1):
                    selected[str(row["run_id"])] = row
                    reasons[str(row["run_id"])].add(f"top{rank}_net")
    return selected, reasons


def _select_operationally_diverse(
    rows: list[dict[str, Any]],
    cfg: WeeklyCoverageConfig,
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], list[dict[str, Any]]]:
    selected: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    audit: list[dict[str, Any]] = []
    status_filter = cfg.require_final_status.strip().lower()

    for start in cfg.start_hours:
        for reverse in (False, True):
            for slow in cfg.slow_ma_values:
                pool = [
                    row for row in rows
                    if _lane_key(row) == (start, reverse, slow)
                    and (not status_filter or str(row.get("status", "")).lower() == status_filter)
                ]
                ordered: list[tuple[str, int, dict[str, Any]]] = []
                for label, ordered_rows in (
                    ("top_pf", sorted(pool, key=_sort_pf, reverse=True)),
                    ("top_net", sorted(pool, key=_sort_net, reverse=True)),
                    ("fallback_score", sorted(pool, key=_sort_score, reverse=True)),
                ):
                    ordered.extend((label, rank, row) for rank, row in enumerate(ordered_rows, 1))

                seen_runs: set[str] = set()
                seen_exact: set[tuple[str, ...]] = set()
                seen_operational: set[tuple[str, ...]] = set()
                picked: list[tuple[str, int, dict[str, Any]]] = []
                for label, rank, row in ordered:
                    run_id = str(row.get("run_id") or "")
                    if not run_id or run_id in seen_runs:
                        continue
                    seen_runs.add(run_id)
                    exact_sig = _exact_parameter_signature(row)
                    op_sig = _operational_signature(row, cfg)
                    drop_reason = ""
                    if exact_sig in seen_exact:
                        drop_reason = "exact_parameter_duplicate"
                    elif op_sig in seen_operational:
                        drop_reason = "same_operational_shape"
                    if drop_reason:
                        audit.append(_duplicate_audit_row(row, drop_reason, label, op_sig))
                        continue
                    seen_exact.add(exact_sig)
                    seen_operational.add(op_sig)
                    picked.append((label, rank, row))
                    if len(picked) >= cfg.target_per_lane:
                        break
                for label, rank, row in picked:
                    run_id = str(row["run_id"])
                    selected[run_id] = row
                    reasons[run_id].add(f"operational_{label}_{rank}")
    return selected, reasons, audit


def _select_lane_fallbacks(
    rows: list[dict[str, Any]],
    validated_selection: dict[str, dict[str, Any]],
    cfg: WeeklyCoverageConfig,
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """For every lane the validated pass left empty, pick the single best
    candidate regardless of final status. Returns (selection, reasons)."""
    selected: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    validated_lanes = {_lane_key(row) for row in validated_selection.values()}

    for start in cfg.start_hours:
        for reverse in (False, True):
            for slow in cfg.slow_ma_values:
                lane = (start, reverse, slow)
                if lane in validated_lanes:
                    continue
                pool = [row for row in rows if _lane_key(row) == lane]
                if not pool:
                    continue  # truly nothing ran for this lane — cannot fall back
                best = sorted(pool, key=_sort_score, reverse=True)[0]
                run_id = str(best.get("run_id") or "")
                if not run_id:
                    continue
                selected[run_id] = best
                status = str(best.get("status") or "").strip().lower()
                why = "fallback_no_validated_template"
                if status and status != cfg.require_final_status.strip().lower():
                    why = f"fallback_did_not_pass_guardrails_{status or 'unknown'}"
                reasons[run_id].add(why)
    return selected, reasons


def _copy_manifest_templates(
    rows_by_run_id: dict[str, dict[str, Any]],
    reasons_by_run_id: dict[str, set[str]],
    source_templates: dict[str, Path],
    renamed_templates: dict[str, Path],
    named_dest: Path,
    by_run_dest: Path,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for run_id in sorted(rows_by_run_id, key=_run_sort_key):
        row = rows_by_run_id[run_id]
        template = renamed_templates.get(run_id) or source_templates.get(run_id)
        template_name = ""
        if template and template.exists():
            template_name = template.name
            shutil.copy2(template, named_dest / template.name)
            shutil.copy2(template, by_run_dest / f"{run_id}__{template.name}")
        manifest.append(_manifest_row(row, template_name, reasons_by_run_id.get(run_id, set()), template))
    return manifest


def _lane_coverage_rows(
    rows: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
    fallback_manifest: list[dict[str, Any]],
    cfg: WeeklyCoverageConfig,
) -> list[dict[str, Any]]:
    def _in_lane(row: dict[str, Any], start: int, reverse: bool, slow: int) -> bool:
        return (
            _safe_int(row.get("start_hour"), -1) == start
            and _bool(row.get("reverse")) == reverse
            and _safe_int(row.get("slowMA"), -1) == slow
        )

    output: list[dict[str, Any]] = []
    for start in cfg.start_hours:
        for reverse in (False, True):
            for slow in cfg.slow_ma_values:
                lane_rows = [row for row in rows if _lane_key(row) == (start, reverse, slow)]
                passed = [row for row in lane_rows if str(row.get("status", "")).lower() == cfg.require_final_status.lower()]
                picked = [row for row in manifest if _in_lane(row, start, reverse, slow)]
                fallbacks = [row for row in fallback_manifest if _in_lane(row, start, reverse, slow)]
                output.append({
                    "bucket": f"{start:02d}-{start + cfg.duration_hours:02d}",
                    "side": _side(reverse),
                    "reverse": str(reverse),
                    "slowMA": slow,
                    "naming_family": _family_name(slow, reverse),
                    "final_backtest_count": len(lane_rows),
                    "passed_count": len(passed),
                    "unique_operational_signatures": len({_operational_signature(row, cfg) for row in passed}),
                    "operationally_diverse_count": len(picked),
                    "operationally_diverse_run_ids": "; ".join(str(row["run_id"]) for row in picked),
                    "fallback_count": len(fallbacks),
                    "fallback_run_ids": "; ".join(str(row["run_id"]) for row in fallbacks),
                })
    return output


def _source_templates_by_run_id(session: OptimizerSession) -> dict[str, Path]:
    source_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "named_backtest_templates"
    )
    templates: dict[str, Path] = {}
    if not source_dir.exists():
        return templates
    for path in sorted(source_dir.rglob("F_*.xml")):
        run_id = path.name.split("_StartTimeH_")[0]
        if run_id.startswith("F_"):
            templates[run_id] = path
    return templates


def _renamed_templates_by_run_id(
    session: OptimizerSession,
    source_templates: dict[str, Path],
) -> dict[str, Path]:
    index_path = final_renamed_index_path(session)
    indexed: dict[str, Path] = {}
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            for run_id, record in (data.get("templates") or {}).items():
                renamed = Path(str(record.get("renamed_path") or ""))
                if renamed.exists():
                    indexed[str(run_id)] = renamed
        except (OSError, json.JSONDecodeError, TypeError):
            indexed = {}
    if indexed:
        return indexed

    renamed_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "renamed_backtest_templates"
    )
    if not renamed_dir.exists():
        return {}
    by_signature: dict[tuple[tuple[str, str], ...], list[Path]] = defaultdict(list)
    for path in sorted(renamed_dir.rglob("*.xml")):
        by_signature[_xml_signature(path)].append(path)

    mapped: dict[str, Path] = {}
    used: set[Path] = set()
    for run_id, source in sorted(source_templates.items(), key=lambda item: _run_sort_key(item[0])):
        for candidate in by_signature.get(_xml_signature(source), []):
            if candidate not in used:
                mapped[run_id] = candidate
                used.add(candidate)
                break
    return mapped


def _xml_signature(path: Path) -> tuple[tuple[str, str], ...]:
    tags = (
        "StartTimeH", "StartTimeM", "DurationTimeH", "DurationTimeM",
        "averageFast", "averageSlow", "averageTrend", "UseTrend", "UseTrendReverse",
        "ProfitStop", "LossStop", "MaxTrades", "Contracts", "MaxStop", "UseMaxStop",
        "MaxTPRatio", "UseMaxTP", "Long", "Short", "Reverse", "UseKill",
        "KillProfitStop", "KillLossStop",
    )
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return tuple((tag, "") for tag in tags)
    values: list[tuple[str, str]] = []
    for tag in tags:
        node = tree.find(f".//{tag}")
        values.append((tag, (node.text or "").strip() if node is not None else ""))
    return tuple(values)


def _manifest_row(
    row: dict[str, Any],
    template_name: str,
    selection_reasons: set[str],
    template_path: Path | None,
) -> dict[str, Any]:
    return {
        "run_id": str(row.get("run_id") or ""),
        "template_name": template_name,
        "selection_reasons": ";".join(sorted(selection_reasons)),
        "bucket": str(row.get("bucket") or ""),
        "start_hour": row.get("start_hour", ""),
        "duration_hours": row.get("duration_hours", ""),
        "side": str(row.get("side") or ""),
        "reverse": row.get("reverse", ""),
        "slowMA": row.get("average_slow", ""),
        "naming_family": str(row.get("naming_family") or ""),
        "status": row.get("status", ""),
        "score": row.get("score", ""),
        "net_profit": row.get("total_net_profit", ""),
        "profit_factor": row.get("profit_factor", ""),
        "max_drawdown": row.get("max_drawdown", ""),
        "trades": row.get("trades", ""),
        "direction_shape": row.get("direction_shape", ""),
        "trade_band": row.get("trade_band", ""),
        "percent_days_traded": row.get("percent_days_traded", ""),
        "average_fast": row.get("average_fast", ""),
        "max_stop": row.get("max_stop", ""),
        "max_tp_ratio": row.get("max_tp_ratio", ""),
        "profit_stop": row.get("profit_stop", ""),
        "loss_stop": row.get("loss_stop", ""),
        "max_trades": row.get("max_trades", ""),
        "long_enabled": row.get("long_enabled", ""),
        "short_enabled": row.get("short_enabled", ""),
        "recommended": row.get("recommended", ""),
        "reasons": row.get("reasons", ""),
        "template_path": str(template_path) if template_path else "",
    }


def _duplicate_audit_row(
    row: dict[str, Any],
    drop_reason: str,
    source_rank_mode: str,
    signature: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "bucket": str(row.get("bucket") or ""),
        "side": str(row.get("side") or ""),
        "reverse": row.get("reverse", ""),
        "slowMA": row.get("average_slow", ""),
        "naming_family": str(row.get("naming_family") or ""),
        "dropped_run_id": str(row.get("run_id") or ""),
        "drop_reason": drop_reason,
        "source_rank_mode": source_rank_mode,
        "net_profit": row.get("total_net_profit", ""),
        "profit_factor": row.get("profit_factor", ""),
        "trades": row.get("trades", ""),
        "direction_shape": row.get("direction_shape", ""),
        "trade_band": row.get("trade_band", ""),
        "operational_signature": " | ".join(signature),
    }


def _render_report(
    manifest: list[dict[str, Any]],
    fallback_manifest: list[dict[str, Any]],
    lane_rows: list[dict[str, Any]],
    duplicate_audit: list[dict[str, Any]],
    cfg: WeeklyCoverageConfig,
) -> str:
    full_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) >= cfg.target_per_lane)
    thin_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) == 1)
    missing_lanes = sum(1 for row in lane_rows if int(row["operationally_diverse_count"]) == 0)
    covered_lanes = sum(
        1 for row in lane_rows
        if int(row["operationally_diverse_count"]) > 0 or int(row.get("fallback_count", 0)) > 0
    )
    uncovered_lanes = len(lane_rows) - covered_lanes
    css = """
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2937;background:#fff}
h1{font-size:24px;margin:0 0 8px}h2{font-size:18px;margin-top:24px;border-bottom:1px solid #ddd;padding-bottom:5px}
.meta{color:#6b7280}.cards{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;margin:14px 0}
.card{border:1px solid #d1d5db;border-radius:6px;padding:10px;background:#f9fafb}.big{font-size:22px;font-weight:700}
table{border-collapse:collapse;width:100%;font-size:12px;margin:10px 0 18px}th,td{border:1px solid #e5e7eb;padding:5px 7px;vertical-align:top}
th{background:#f3f4f6;text-align:left}.ok{background:#ecfdf5}.thin{background:#fff7ed}.miss{background:#fee2e2}.mono{font-family:Consolas,monospace}
</style>
"""
    parts = [f"<html><head><meta charset=\"utf-8\"><title>Weekly Coverage Package</title>{css}</head><body>"]
    parts.append("<h1>Operationally Diverse Weekly Coverage Package</h1>")
    parts.append(
        "<div class=\"meta\">Diversity rule: Long/Short direction flags and realized trade-count "
        "profile are first-class distinctions. A one-trade style is not treated as the same shape "
        "as a multi-trade style.</div>"
    )
    cards = [
        ("Validated templates", len(manifest), "operationally diverse and validated"),
        ("Best-effort fallbacks", len(fallback_manifest), "filled empty lanes (below guardrails)"),
        (f"Lanes covered of {len(lane_rows)}", covered_lanes, f"{uncovered_lanes} still have nothing to run"),
        ("Full lanes", full_lanes, f"{cfg.target_per_lane} distinct validated shapes"),
    ]
    parts.append("<div class=\"cards\">")
    for title, value, note in cards:
        parts.append(f"<div class=\"card\"><div class=\"big\">{value}</div><div>{title}</div><div class=\"meta\">{note}</div></div>")
    parts.append("</div>")
    if len(fallback_manifest):
        parts.append(
            "<div class=\"meta\" style=\"background:#fff7ed;border:1px solid #fdba74;border-radius:6px;"
            "padding:8px 12px;margin:6px 0\">"
            f"⚠ {len(fallback_manifest)} lane(s) had no validated template, so the single best "
            "available candidate was included as a <b>best-effort fallback</b>. These did not pass the "
            "configured guardrails — review before handing to the team."
            "</div>"
        )
    parts.append("<h2>Lane Coverage</h2>")
    parts.append("<table><tr><th>Bucket</th><th>Side</th><th>slowMA</th><th>Name</th><th>Passed</th><th>Unique Shapes</th><th>Validated</th><th>Fallback</th><th>Run IDs</th></tr>")
    for row in lane_rows:
        count = int(row["operationally_diverse_count"])
        fb = int(row.get("fallback_count", 0))
        if count >= cfg.target_per_lane:
            cls = "ok"
        elif count >= 1:
            cls = "thin"
        elif fb >= 1:
            cls = "thin"  # covered only by fallback
        else:
            cls = "miss"
        run_ids = row["operationally_diverse_run_ids"] or row.get("fallback_run_ids", "")
        run_label = run_ids if count else (f"fallback: {row.get('fallback_run_ids', '')}" if fb else "")
        parts.append(
            f"<tr class=\"{cls}\"><td>{row['bucket']}</td><td>{row['side']}</td>"
            f"<td>{row['slowMA']}</td><td>{html.escape(str(row['naming_family']))}</td>"
            f"<td>{row['passed_count']}</td><td>{row['unique_operational_signatures']}</td>"
            f"<td>{row['operationally_diverse_count']}</td>"
            f"<td>{fb}</td>"
            f"<td class=\"mono\">{html.escape(str(run_label))}</td></tr>"
        )
    parts.append("</table>")
    if len(fallback_manifest):
        parts.append("<h2>Best-Effort Fallbacks (did not pass guardrails)</h2>")
        parts.append("<table><tr><th>Run</th><th>Template</th><th>Lane</th><th>Status</th><th>Trades</th><th>% Days</th><th>Net</th><th>PF</th><th>Why</th></tr>")
        for row in fallback_manifest:
            parts.append(
                f"<tr class=\"thin\"><td class=\"mono\">{html.escape(str(row['run_id']))}</td>"
                f"<td>{html.escape(str(row['template_name']))}</td>"
                f"<td>{html.escape(str(row['bucket']))} {html.escape(str(row['side']))} "
                f"{html.escape(str(row['naming_family']))} {html.escape(str(row['slowMA']))}</td>"
                f"<td>{html.escape(str(row['status']))}</td><td>{html.escape(str(row['trades']))}</td>"
                f"<td>{html.escape(str(row['percent_days_traded']))}</td>"
                f"<td>{html.escape(str(row['net_profit']))}</td><td>{html.escape(str(row['profit_factor']))}</td>"
                f"<td>{html.escape(str(row['selection_reasons']))}</td></tr>"
            )
        parts.append("</table>")
    parts.append("<h2>Template Manifest</h2>")
    parts.append("<table><tr><th>Run</th><th>Template</th><th>Lane</th><th>Direction</th><th>Trade Band</th><th>Trades</th><th>Net</th><th>PF</th><th>Params</th></tr>")
    for row in manifest:
        params = (
            f"fast={row['average_fast']}, maxTrades={row['max_trades']}, "
            f"stop={row['max_stop']}, rr={row['max_tp_ratio']}"
        )
        parts.append(
            f"<tr><td class=\"mono\">{html.escape(str(row['run_id']))}</td>"
            f"<td>{html.escape(str(row['template_name']))}</td>"
            f"<td>{html.escape(str(row['bucket']))} {html.escape(str(row['side']))} "
            f"{html.escape(str(row['naming_family']))} {html.escape(str(row['slowMA']))}</td>"
            f"<td>{html.escape(str(row['direction_shape']))}</td>"
            f"<td>{html.escape(str(row['trade_band']))}</td><td>{html.escape(str(row['trades']))}</td>"
            f"<td>{html.escape(str(row['net_profit']))}</td><td>{html.escape(str(row['profit_factor']))}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    parts.append("</table></body></html>")
    return "\n".join(parts)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_zip(package_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir.parent))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _lane_key(row: dict[str, Any]) -> tuple[int, bool, int]:
    return (
        _safe_int(row.get("start_hour"), -1),
        _bool(row.get("reverse")),
        _safe_int(row.get("average_slow"), -1),
    )


def _sort_pf(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        _safe_float(row.get("profit_factor"), -999.0),
        _safe_float(row.get("total_net_profit"), -10**12),
        _safe_float(row.get("score"), -999.0),
        -abs(_safe_float(row.get("max_drawdown"), 10**12)),
    )


def _sort_net(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        _safe_float(row.get("total_net_profit"), -10**12),
        _safe_float(row.get("profit_factor"), -999.0),
        _safe_float(row.get("score"), -999.0),
        -abs(_safe_float(row.get("max_drawdown"), 10**12)),
    )


def _sort_score(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        _safe_float(row.get("score"), -999.0),
        _safe_float(row.get("total_net_profit"), -10**12),
        _safe_float(row.get("profit_factor"), -999.0),
        -abs(_safe_float(row.get("max_drawdown"), 10**12)),
    )


def _exact_parameter_signature(row: dict[str, Any]) -> tuple[str, ...]:
    keys = (
        "duration_hours", "average_fast", "max_stop", "max_tp_ratio",
        "profit_stop", "loss_stop", "max_trades", "long_enabled", "short_enabled",
    )
    return tuple(str(row.get(key, "")).strip().lower() for key in keys)


def _operational_signature(row: dict[str, Any], cfg: WeeklyCoverageConfig) -> tuple[str, ...]:
    # Numeric knobs are banded so near-identical values collapse (a 200 vs 210
    # max stop is the same shape). Direction flags stay a hard distinction.
    return (
        str(row.get("duration_hours", "")).strip(),
        str(row.get("average_fast", "")).strip(),
        _band(row.get("max_stop"), cfg.stop_band),
        _band(row.get("max_tp_ratio"), cfg.tp_ratio_band),
        str(row.get("profit_stop", "")).strip(),
        str(row.get("loss_stop", "")).strip(),
        str(row.get("max_trades", "")).strip(),
        _direction_shape(row),
        _trade_band(row.get("trades")),
    )


def _band(value: Any, band: float) -> str:
    """Snap a numeric value to the nearest band so close values share a key.

    Non-numeric or non-positive bands fall back to the raw string so we never
    over-collapse unexpectedly.
    """
    raw = _safe_float(value, None) if value not in (None, "") else None
    if raw is None or band is None or band <= 0:
        return str(value).strip()
    return str(int(round(raw / band)))


def _direction_shape(row: dict[str, Any]) -> str:
    long_enabled = _bool(row.get("long_enabled"))
    short_enabled = _bool(row.get("short_enabled"))
    if long_enabled and short_enabled:
        return "both"
    if long_enabled:
        return "long_only"
    if short_enabled:
        return "short_only"
    return "disabled"


def _trade_band(value: Any) -> str:
    trades = _safe_int(value, 0)
    if trades <= 1:
        return "single_trade"
    if trades <= 5:
        return "few_trades_2_5"
    if trades <= 15:
        return "moderate_6_15"
    if trades <= 40:
        return "active_16_40"
    return "high_activity_41_plus"


def _family_name(slow: int, reverse: bool) -> str:
    ranges = (
        (2, 75, "Hermes", "Harpy"),
        (76, 125, "Artemis", "Griffin"),
        (126, 175, "Poseidon", "Medusa"),
        (176, 225, "Apollo", "Hydra"),
        (226, 275, "Zeus", "Chimera"),
        (276, 325, "Ares", "Cerberus"),
        (326, 375, "Athena", "Sphinx"),
        (376, 425, "Aphrodite", "Siren"),
        (426, 999999, "Dionysus", "Typhon"),
    )
    for low, high, god, monster in ranges:
        if low <= slow <= high:
            return monster if reverse else god
    return "Unknown"


def _side(reverse: bool) -> str:
    return "Monster" if reverse else "God"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _int_list(value: Any, default: list[int]) -> list[int]:
    if not isinstance(value, list):
        return list(default)
    output = [_safe_int(item, -1) for item in value]
    return [item for item in output if item >= 0] or list(default)


def _safe_filename(value: str) -> str:
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"_", "-", "."})
    return safe or PACKAGE_DIRNAME


def _run_sort_key(run_id: str) -> tuple[int, str]:
    return (_safe_int(str(run_id).replace("F_", ""), 10**9), str(run_id))


def _build_notes(
    validated_manifest: list[dict[str, Any]],
    review_manifest: list[dict[str, Any]],
    fallback_manifest: list[dict[str, Any]],
    duplicate_audit: list[dict[str, Any]],
    missing_lanes: int,
) -> list[str]:
    notes = [
        f"Built {len(validated_manifest)} operationally diverse validated templates.",
        f"Included {len(review_manifest)} review-only top-two templates before final validation filtering.",
    ]
    if duplicate_audit:
        notes.append(f"Dropped {len(duplicate_audit)} candidates with duplicate operational shape.")
    if fallback_manifest:
        notes.append(
            f"Added {len(fallback_manifest)} best-effort fallback templates for lanes with no "
            "validated candidate (these did not pass guardrails)."
        )
    if missing_lanes:
        notes.append(f"{missing_lanes} lanes have no validated deployable template.")
    return notes
