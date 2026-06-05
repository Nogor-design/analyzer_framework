from __future__ import annotations

"""Saved report profiles for optimizer final-review artifacts."""

import json
import shutil
from pathlib import Path
from typing import Any

from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.optimizer_candidate_report import (
    DEFAULT_SESSION_CANDIDATE_SECTIONS,
    WEEKLY_PROP_SECTION,
)
from ta_foundation.web.optimizer_session import OptimizerSession


FINAL_REPORT_CONFIG_FILENAME = "final_report_config.json"
DEPLOYMENT_REPORT_CONFIG_DIRNAME = "report_configs"


class OptimizerReportConfigError(Exception):
    pass


def default_final_report_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_kind": "final_session_candidate_report",
        "preset_id": "operator_final_review",
        "output_filename": "session_candidate_report.html",
        "sections": [{"id": section_id} for section_id in DEFAULT_SESSION_CANDIDATE_SECTIONS],
        "auto_build_on_recipe_complete": True,
    }


def final_report_presets() -> list[dict[str, Any]]:
    """Curated final-report profiles shown by the report builder UI."""
    return [
        {
            "id": "operator_final_review",
            "name": "Operator Final Review",
            "description": (
                "Balanced final report for weekly review: comparison, bundle basket, "
                "equity, KPIs, daily behavior, and settings."
            ),
            "sections": [{"id": section_id} for section_id in DEFAULT_SESSION_CANDIDATE_SECTIONS],
        },
        {
            "id": "compact_comparison",
            "name": "Compact Comparison",
            "description": (
                "Fast, low-noise report for quickly comparing final candidates by "
                "headline metrics and equity shape."
            ),
            "sections": [
                {"id": "comparison_overview"},
                {"id": "equity_curve_comparison"},
                {"id": "run_kpi_cards"},
                {"id": "run_snapshot_clipboard"},
            ],
        },
        {
            "id": "weekly_package_review",
            "name": "Weekly Package Review",
            "description": (
                "Focuses on deployable package decisions: bucket bundles, daily "
                "scoreboard, weekly leaderboard, run cards, and copyable summary."
            ),
            "sections": [
                {"id": "comparison_overview"},
                {"id": "final_template_bundle_basket"},
                {"id": "daily_scoreboard"},
                WEEKLY_PROP_SECTION,
                {"id": "run_card_catalog"},
                {"id": "run_snapshot_clipboard"},
                {"id": "run_settings_table"},
            ],
        },
        {
            "id": "deep_diagnostics",
            "name": "Deep Diagnostics",
            "description": (
                "Fuller troubleshooting view with metadata, settings, daily boards, "
                "leaderboards, equity, and analysis chart replicas."
            ),
            "sections": [
                {"id": "comparison_overview"},
                {"id": "final_template_bundle_basket"},
                {"id": "equity_curve_comparison"},
                {"id": "run_kpi_cards"},
                {"id": "run_metadata_cards"},
                {"id": "daily_scoreboard"},
                {"id": "daily_winner_spotlight"},
                {"id": "daily_leaderboard_cards"},
                WEEKLY_PROP_SECTION,
                {"id": "analysis_chart_replica"},
                {"id": "run_settings_table"},
            ],
        },
    ]


def final_report_preset_map() -> dict[str, dict[str, Any]]:
    return {str(preset["id"]): preset for preset in final_report_presets()}


def sections_from_preset(preset_id: str) -> list[dict[str, Any]]:
    preset = final_report_preset_map().get(str(preset_id or ""))
    if not preset:
        raise OptimizerReportConfigError(f"Unknown final report preset: {preset_id}")
    return _normalize_sections(preset.get("sections"))


def final_report_config_path(session: OptimizerSession) -> Path:
    return session.directory / FINAL_REPORT_CONFIG_FILENAME


def deployment_final_report_config_path(session: OptimizerSession) -> Path:
    return (
        session.directory
        / "deployment_package"
        / DEPLOYMENT_REPORT_CONFIG_DIRNAME
        / FINAL_REPORT_CONFIG_FILENAME
    )


def load_final_report_config(session: OptimizerSession) -> dict[str, Any]:
    path = final_report_config_path(session)
    if not path.exists():
        return default_final_report_config()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OptimizerReportConfigError(f"Could not read final report config: {exc}") from exc
    return normalize_final_report_config(payload)


def save_final_report_config(
    session: OptimizerSession,
    payload: dict[str, Any],
    *,
    mirror_to_deployment_package: bool = True,
) -> dict[str, Any]:
    normalized = normalize_final_report_config(payload)
    path = final_report_config_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    if mirror_to_deployment_package:
        package_path = deployment_final_report_config_path(session)
        package_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, package_path)
    return normalized


def sections_from_final_report_config(config: dict[str, Any]) -> list[str | dict[str, Any]]:
    normalized = normalize_final_report_config(config)
    return list(normalized["sections"])


def normalize_final_report_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    default = default_final_report_config()
    preset_id = str(raw.get("preset_id") or default.get("preset_id") or "").strip()
    raw_sections = raw.get("sections")
    if raw_sections is None and preset_id:
        raw_sections = final_report_preset_map().get(preset_id, {}).get("sections")
    sections = _normalize_sections(raw_sections if raw_sections is not None else default["sections"])
    if not sections:
        raise OptimizerReportConfigError("Final report config must include at least one valid section.")
    return {
        "schema_version": 1,
        "report_kind": "final_session_candidate_report",
        "preset_id": preset_id,
        "output_filename": str(raw.get("output_filename") or default["output_filename"]),
        "sections": sections,
        "auto_build_on_recipe_complete": bool(
            raw.get(
                "auto_build_on_recipe_complete",
                default["auto_build_on_recipe_complete"],
            )
        ),
    }


def _normalize_sections(raw_sections: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_sections, list):
        raise OptimizerReportConfigError("sections must be a list.")
    sections: list[dict[str, Any]] = []
    unknown: list[str] = []
    for entry in raw_sections:
        if isinstance(entry, str):
            sec_id = entry
            title = None
            options: dict[str, Any] = {}
        elif isinstance(entry, dict):
            sec_id = str(entry.get("id") or "")
            title = entry.get("title")
            options = dict(entry.get("options") or {})
        else:
            sec_id = str(entry or "")
            title = None
            options = {}
        sec_id = sec_id.strip()
        if not sec_id:
            continue
        if sec_id not in SECTION_REGISTRY:
            unknown.append(sec_id)
            continue
        normalized: dict[str, Any] = {"id": sec_id}
        if title:
            normalized["title"] = str(title)
        if options:
            normalized["options"] = options
        sections.append(normalized)
    if unknown:
        raise OptimizerReportConfigError(
            "Unknown report section id(s): " + ", ".join(sorted(unknown))
        )
    return sections
