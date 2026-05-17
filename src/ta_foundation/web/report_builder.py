from __future__ import annotations

import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ta_foundation.reports.html.config import load_report_config
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.capabilities import get_capability


@dataclass(frozen=True)
class ReportBuilderResult:
    report_yaml: str
    command_preview: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class SavedReportYaml:
    path: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class LoadedReportYaml:
    path: str
    report_yaml: str
    validation: dict[str, Any]
    command_preview: str


def build_report_builder_payload(body: dict[str, Any]) -> ReportBuilderResult:
    """Build and validate report YAML for the Web UI without running ingest/report jobs."""

    config = build_report_config(body)
    report_yaml = dump_report_yaml(config)
    validation = validate_report_request(body, report_yaml)
    command_preview = build_command_preview(body)
    return ReportBuilderResult(
        report_yaml=report_yaml,
        command_preview=command_preview,
        validation=validation,
    )


def build_report_config(body: dict[str, Any]) -> dict[str, Any]:
    selected_ids = _selected_capability_ids(body)
    selected_section_ids = _selected_report_section_ids(body)
    config: dict[str, Any] = {
        "report": {
            "title": "Strategy Comparison",
            "output_filename": "comparison_report.html",
            "timezone": "America/Denver",
        },
        "sections": [],
    }

    for capability_id in selected_ids:
        capability = get_capability(capability_id)
        if capability is None:
            continue

        blocks = capability.get("config_blocks") or {}
        if isinstance(blocks, dict):
            _merge_config_blocks(config, blocks)

        for section_id in capability.get("report_sections") or []:
            _append_section(config, {"id": section_id})

    for section_id in selected_section_ids:
        if section_id in SECTION_REGISTRY:
            _append_section(config, {"id": section_id})

    if not config["sections"]:
        _append_section(config, {"id": "comparison_overview"})

    title = _clean_str(body.get("report_title"))
    output_filename = _clean_str(body.get("output_filename"))
    if title:
        config["report"]["title"] = title
    if output_filename:
        config["report"]["output_filename"] = output_filename

    return config


def dump_report_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


def validate_report_request(body: dict[str, Any], report_yaml: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    selected_ids = _selected_capability_ids(body)
    selected_section_ids = _selected_report_section_ids(body)
    selected_capabilities = []
    for capability_id in selected_ids:
        capability = get_capability(capability_id)
        if capability is None:
            errors.append(f"Unknown capability: {capability_id}")
            continue
        selected_capabilities.append(capability)

    for section_id in selected_section_ids:
        if section_id not in SECTION_REGISTRY:
            errors.append(f"Unknown report section id: {section_id}")

    input_folder = _clean_str(body.get("input_folder"))
    output_folder = _clean_str(body.get("output_folder"))
    market_data_folder = _clean_str(body.get("market_data_folder"))

    if not input_folder:
        errors.append("Input folder is required.")
    elif not Path(input_folder).exists():
        errors.append(f"Input folder does not exist: {input_folder}")

    if not output_folder:
        errors.append("Output folder is required.")

    yaml_text = report_yaml if report_yaml is not None else _clean_str(body.get("report_yaml"))

    needs_market_data = any(bool(cap.get("needs_market_data")) for cap in selected_capabilities)
    if any(_section_likely_needs_market_data(section_id) for section_id in selected_section_ids):
        needs_market_data = True
    if yaml_text and _yaml_likely_needs_market_data(yaml_text):
        needs_market_data = True
    if needs_market_data and not market_data_folder:
        errors.append("Market data folder is required by the selected capabilities.")
    elif market_data_folder and not Path(market_data_folder).exists():
        errors.append(f"Market data folder does not exist: {market_data_folder}")

    needs_tick_data = any(bool(cap.get("needs_tick_data")) for cap in selected_capabilities)
    if needs_tick_data:
        warnings.append("At least one selected capability may load tick data; runtime can be slow.")

    if yaml_text:
        errors.extend(_validate_report_yaml(yaml_text))
    else:
        errors.append("Report YAML is required.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "selected_capabilities": selected_ids,
        "selected_report_sections": selected_section_ids,
    }


def validate_report_yaml_text(report_yaml: str) -> dict[str, Any]:
    """Validate only report YAML syntax, loader behavior, and section ids."""
    errors = _validate_report_yaml(report_yaml)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [],
    }


def save_report_yaml(body: dict[str, Any]) -> SavedReportYaml:
    """Validate generated report YAML and save it to the user-specified path."""
    report_yaml = str(body.get("report_yaml") or "")
    save_path = _clean_str(body.get("save_path") or body.get("report_config_path"))
    if not save_path:
        return SavedReportYaml(
            path="",
            validation={
                "ok": False,
                "errors": ["Save path is required."],
                "warnings": [],
            },
        )

    path_error = _yaml_file_path_error(save_path)
    if path_error:
        return SavedReportYaml(
            path=save_path,
            validation={
                "ok": False,
                "errors": [path_error],
                "warnings": [],
            },
        )

    validation = validate_report_yaml_text(report_yaml)
    if not validation["ok"]:
        return SavedReportYaml(path=save_path, validation=validation)

    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_yaml, encoding="utf-8")
    return SavedReportYaml(path=str(path), validation=validation)


def load_report_yaml(body: dict[str, Any]) -> LoadedReportYaml:
    """Load a reusable report YAML file from the user-specified local path."""
    load_path = _clean_str(body.get("load_path") or body.get("report_config_path") or body.get("save_path"))
    if not load_path:
        validation = {"ok": False, "errors": ["YAML path is required."], "warnings": []}
        return LoadedReportYaml(path="", report_yaml="", validation=validation, command_preview=build_command_preview(body))

    path_error = _yaml_file_path_error(load_path)
    if path_error:
        validation = {"ok": False, "errors": [path_error], "warnings": []}
        return LoadedReportYaml(path=load_path, report_yaml="", validation=validation, command_preview=build_command_preview(body))

    path = Path(load_path).expanduser()
    if not path.exists():
        validation = {"ok": False, "errors": [f"Report YAML path does not exist: {load_path}"], "warnings": []}
        return LoadedReportYaml(path=str(path), report_yaml="", validation=validation, command_preview=build_command_preview(body))
    if not path.is_file():
        validation = {"ok": False, "errors": [f"Report YAML path is not a file: {load_path}"], "warnings": []}
        return LoadedReportYaml(path=str(path), report_yaml="", validation=validation, command_preview=build_command_preview(body))

    try:
        report_yaml = path.read_text(encoding="utf-8")
    except OSError as exc:
        validation = {"ok": False, "errors": [f"Could not read report YAML: {exc}"], "warnings": []}
        return LoadedReportYaml(path=str(path), report_yaml="", validation=validation, command_preview=build_command_preview(body))

    validation = validate_report_request(body, report_yaml=report_yaml)
    command_preview = build_command_preview({**body, "report_config_path": str(path)})
    return LoadedReportYaml(
        path=str(path),
        report_yaml=report_yaml,
        validation=validation,
        command_preview=command_preview,
    )


def validate_report_run_request(body: dict[str, Any]) -> dict[str, Any]:
    report_config_path = _clean_str(body.get("report_config_path") or body.get("save_path"))
    if not report_config_path:
        return {
            "ok": False,
            "errors": ["Report config path is required."],
            "warnings": [],
        }
    path = Path(report_config_path)
    if not path.exists():
        return {
            "ok": False,
            "errors": [f"Report config path does not exist: {report_config_path}"],
            "warnings": [],
        }
    try:
        report_yaml = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "errors": [f"Could not read report config path: {exc}"],
            "warnings": [],
        }
    return validate_report_request(body, report_yaml=report_yaml)


def build_command_preview(body: dict[str, Any]) -> str:
    report_config_path = _clean_str(body.get("report_config_path")) or "<generated-report.yaml>"
    return " ".join(_quote_arg(arg) for arg in build_report_command_args(body, report_config_path))


def build_report_command_args(
    body: dict[str, Any],
    report_config_path: str | Path | None = None,
) -> list[str]:
    config_path = str(report_config_path or _clean_str(body.get("report_config_path")) or "<generated-report.yaml>")
    args = [
        sys.executable,
        "-m",
        "ta_foundation.cli.main",
        "--input",
        _clean_str(body.get("input_folder")) or "<input-folder>",
        "--output",
        _clean_str(body.get("output_folder")) or "<output-folder>",
        "--report-config",
        config_path,
    ]

    market_data_folder = _clean_str(body.get("market_data_folder"))
    if market_data_folder:
        args.extend(["--market-data", market_data_folder])

    if bool(body.get("recursive")):
        args.append("--recursive")
    if bool(body.get("include_run_images")):
        args.append("--include-run-images")
    if bool(body.get("export_exec_cards_png")):
        args.append("--export-exec-cards-png")
    exec_cards_dir = _clean_str(body.get("exec_cards_dir"))
    if exec_cards_dir:
        args.extend(["--exec-cards-dir", exec_cards_dir])
    if bool(body.get("no_tick_data")):
        args.append("--no-tick-data")
    db_path = _clean_str(body.get("db_path"))
    if db_path:
        args.extend(["--db-path", db_path])
    run_id_regex = _clean_str(body.get("run_id_regex"))
    if run_id_regex:
        args.extend(["--run-id-regex", run_id_regex])

    return args


def _merge_config_blocks(config: dict[str, Any], blocks: dict[str, Any]) -> None:
    for key, value in blocks.items():
        if key == "report" and isinstance(value, dict):
            report = dict(config.get("report") or {})
            report.update(value)
            config["report"] = report
        elif key == "sections" and isinstance(value, list):
            for section in value:
                if isinstance(section, dict):
                    _append_section(config, section)
        else:
            config[key] = value


def _append_section(config: dict[str, Any], section: dict[str, Any]) -> None:
    section_id = _clean_str(section.get("id"))
    if not section_id:
        return
    sections = config.setdefault("sections", [])
    if any(isinstance(item, dict) and item.get("id") == section_id for item in sections):
        return
    sections.append(dict(section))


def _validate_report_yaml(report_yaml: str) -> list[str]:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(report_yaml)
    except yaml.YAMLError as exc:
        return [f"Report YAML is not valid YAML: {exc}"]

    if not isinstance(raw, dict):
        return ["Report YAML must be a mapping."]

    sections = raw.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("Report YAML must include at least one section.")
    else:
        for idx, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                errors.append(f"Section #{idx} must be a mapping.")
                continue
            section_id = _clean_str(section.get("id"))
            if not section_id:
                errors.append(f"Section #{idx} is missing an id.")
            elif section_id not in SECTION_REGISTRY:
                errors.append(f"Unknown report section id: {section_id}")

    try:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
            handle.write(report_yaml)
            temp_path = Path(handle.name)
        try:
            load_report_config(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f"Report config loader rejected the YAML: {type(exc).__name__}: {exc}")

    return errors


def _yaml_likely_needs_market_data(report_yaml: str) -> bool:
    try:
        raw = yaml.safe_load(report_yaml)
    except yaml.YAMLError:
        return False
    if not isinstance(raw, dict):
        return False
    market_blocks = (
        "anchor_interaction",
        "pattern_engine",
        "strategy_discovery",
        "regime_recommender",
        "candle_discovery",
        "ma_discovery",
        "orb_discovery",
        "bb_discovery",
        "breakout_discovery",
        "pullback_discovery",
        "level_discovery",
        "lcr_discovery",
        "premarket_discovery",
        "large_candle_excursion",
    )
    for key in market_blocks:
        block = raw.get(key)
        if isinstance(block, dict) and block.get("enabled"):
            return True
    sections = raw.get("sections") or []
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict) and _section_likely_needs_market_data(_clean_str(section.get("id"))):
                return True
    return False


def _selected_capability_ids(body: dict[str, Any]) -> list[str]:
    raw = body.get("capability_ids") or body.get("capabilities") or []
    if not isinstance(raw, list):
        return []
    return [_clean_str(item) for item in raw if _clean_str(item)]


def _selected_report_section_ids(body: dict[str, Any]) -> list[str]:
    raw = body.get("report_section_ids") or body.get("sections") or []
    if not isinstance(raw, list):
        return []
    section_ids: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            section_id = _clean_str(item.get("id"))
        else:
            section_id = _clean_str(item)
        if section_id and section_id not in section_ids:
            section_ids.append(section_id)
    return section_ids


def _section_likely_needs_market_data(section_id: str) -> bool:
    market_prefixes = (
        "anchor_",
        "pattern_",
        "strategy_discovery",
        "candle_discovery",
        "ma_discovery",
        "orb_discovery",
        "premarket_discovery",
        "bb_discovery",
        "breakout_discovery",
        "pullback_discovery",
        "level_discovery",
        "lcr_discovery",
        "regime_",
        "large_candle_excursion",
        "trade_candle",
        "tick_data",
        "horizon_",
        "market_regime",
    )
    return any(section_id.startswith(prefix) for prefix in market_prefixes)


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _yaml_file_path_error(path_value: str) -> str | None:
    path = Path(path_value).expanduser()
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return "Report config path must end in .yaml or .yml."
    return None


def _quote_arg(value: str) -> str:
    if not value:
        return value
    if any(ch.isspace() for ch in value):
        return f'"{value}"'
    return value
