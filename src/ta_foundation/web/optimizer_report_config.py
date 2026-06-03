from __future__ import annotations

"""Saved report profiles for optimizer final-review artifacts."""

import json
import shutil
from pathlib import Path
from typing import Any

from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.optimizer_candidate_report import DEFAULT_SESSION_CANDIDATE_SECTIONS
from ta_foundation.web.optimizer_session import OptimizerSession


FINAL_REPORT_CONFIG_FILENAME = "final_report_config.json"
DEPLOYMENT_REPORT_CONFIG_DIRNAME = "report_configs"


class OptimizerReportConfigError(Exception):
    pass


def default_final_report_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_kind": "final_session_candidate_report",
        "output_filename": "session_candidate_report.html",
        "sections": [{"id": section_id} for section_id in DEFAULT_SESSION_CANDIDATE_SECTIONS],
        "auto_build_on_recipe_complete": True,
    }


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
    sections = _normalize_sections(raw.get("sections", default["sections"]))
    if not sections:
        raise OptimizerReportConfigError("Final report config must include at least one valid section.")
    return {
        "schema_version": 1,
        "report_kind": "final_session_candidate_report",
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
