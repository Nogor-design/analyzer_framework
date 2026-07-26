from __future__ import annotations

"""
Discovery YAML builder.

Takes a stage id plus form values from the Discovery UI and produces:
- a YAML string ready to write to disk and pass to `--report-config`
- a validation result (errors + warnings)
- a CLI command preview

Design intent: the legacy discovery/*.yaml files are the source of truth for
the standard preset. The builder starts from a deep-copy of the stage's
default_yaml, applies a small fixed set of high-level transformations
(instrument substitution, family disabling, report metadata override), then
deep-merges a free-form `overrides` dict on top. Snapshot tests prove that
the builder reproduces the legacy YAMLs at default form values.
"""

import copy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Iterable

import yaml

from ta_foundation.reports.html.config import load_report_config
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.discovery_instruments import get_instrument
from ta_foundation.web.discovery_stages import (
    StageDefinition,
    get_family,
    get_stage_definition,
)


# ---------------------------------------------------------------------------
# Public result shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageBuildResult:
    stage_id: str
    config: dict[str, Any]      # parsed dict — useful for tests and combo estimation
    report_yaml: str            # serialized YAML string
    command_preview: str        # CLI command preview
    validation: dict[str, Any]  # {ok, errors, warnings}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_stage_config(
    stage_id: str,
    *,
    instrument_symbol: str = "NQ",
    overrides: dict[str, Any] | None = None,
    disabled_families: Iterable[str] | None = None,
    report_title: str | None = None,
    output_filename: str | None = None,
) -> dict[str, Any]:
    """Build the stage YAML as a Python dict. Used by tests and the public builder.

    Order of operations:
      1. Deep-copy the stage's default_yaml.
      2. Substitute tick_size and tick_value from the instrument registry,
         everywhere those keys already exist in the tree.
      3. Apply user overrides via deep-merge.
      4. Set enabled: false on every disabled family's yaml_block.
      5. Override report.title and report.output_filename if provided.
    """
    stage = _require_stage(stage_id)
    instrument = _require_instrument(instrument_symbol)

    config = copy.deepcopy(stage.default_yaml)

    # 2. Instrument substitution (in-place tree walk)
    _substitute_tick_metadata(config, tick_size=instrument.tick_size, tick_value=instrument.tick_value)

    # 3. Deep-merge user overrides
    if overrides:
        _deep_merge(config, copy.deepcopy(overrides))

    # 4. Disable families
    for family_id in (disabled_families or ()):
        family = get_family(family_id)
        if family is None:
            continue
        block = config.get(family.yaml_block)
        if isinstance(block, dict):
            block["enabled"] = False

    # 5. Report metadata overrides
    report_block = config.setdefault("report", {})
    if report_title:
        report_block["title"] = str(report_title)
    if output_filename:
        report_block["output_filename"] = str(output_filename)

    return config


def build_stage_yaml(
    stage_id: str,
    *,
    instrument_symbol: str = "NQ",
    overrides: dict[str, Any] | None = None,
    disabled_families: Iterable[str] | None = None,
    report_title: str | None = None,
    output_filename: str | None = None,
) -> str:
    """Build the stage YAML as a string. Stable key order, no aliases."""
    config = build_stage_config(
        stage_id,
        instrument_symbol=instrument_symbol,
        overrides=overrides,
        disabled_families=disabled_families,
        report_title=report_title,
        output_filename=output_filename,
    )
    return _dump_yaml(config)


def build_stage_payload(
    stage_id: str,
    *,
    instrument_symbol: str = "NQ",
    overrides: dict[str, Any] | None = None,
    disabled_families: Iterable[str] | None = None,
    report_title: str | None = None,
    output_filename: str | None = None,
    input_folder: str | None = None,
    output_folder: str | None = None,
    market_data_folder: str | None = None,
    recursive: bool = True,
    no_tick_data: bool = True,
    report_config_path: str | None = None,
) -> StageBuildResult:
    """Full build: config + yaml + validation + command preview."""
    config = build_stage_config(
        stage_id,
        instrument_symbol=instrument_symbol,
        overrides=overrides,
        disabled_families=disabled_families,
        report_title=report_title,
        output_filename=output_filename,
    )
    report_yaml = _dump_yaml(config)
    validation = validate_stage_request(
        stage_id=stage_id,
        report_yaml=report_yaml,
        instrument_symbol=instrument_symbol,
        input_folder=input_folder,
        output_folder=output_folder,
        market_data_folder=market_data_folder,
    )
    command_preview = _build_command_preview(
        input_folder=input_folder,
        output_folder=output_folder,
        market_data_folder=market_data_folder,
        report_config_path=report_config_path,
        recursive=recursive,
        no_tick_data=no_tick_data,
    )
    return StageBuildResult(
        stage_id=stage_id,
        config=config,
        report_yaml=report_yaml,
        command_preview=command_preview,
        validation=validation,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_stage_request(
    *,
    stage_id: str,
    report_yaml: str,
    instrument_symbol: str = "NQ",
    input_folder: str | None = None,
    output_folder: str | None = None,
    market_data_folder: str | None = None,
) -> dict[str, Any]:
    """Validate everything the UI cares about before running the stage."""
    errors: list[str] = []
    warnings: list[str] = []

    if get_stage_definition(stage_id) is None:
        errors.append(f"Unknown stage id: {stage_id}")

    if get_instrument(instrument_symbol) is None:
        errors.append(f"Unknown instrument: {instrument_symbol}")

    if not _clean(input_folder):
        errors.append("Input folder is required.")
    elif not Path(_clean(input_folder)).exists():
        errors.append(f"Input folder does not exist: {input_folder}")

    if not _clean(output_folder):
        errors.append("Output folder is required.")

    # Discovery always needs market data — every funnel stage and the LCE
    # event study sweeps minute bars.
    if not _clean(market_data_folder):
        errors.append("Market data folder is required for discovery runs.")
    elif not Path(_clean(market_data_folder)).exists():
        errors.append(f"Market data folder does not exist: {market_data_folder}")

    # Validate the YAML itself (syntax, sections registered, loader accepts it)
    errors.extend(_validate_report_yaml(report_yaml))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_report_yaml(report_yaml: str) -> list[str]:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(report_yaml)
    except yaml.YAMLError as exc:
        return [f"Stage YAML is not valid YAML: {exc}"]

    if not isinstance(raw, dict):
        return ["Stage YAML must be a mapping."]

    sections = raw.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("Stage YAML must include at least one section.")
    else:
        for idx, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                errors.append(f"Section #{idx} must be a mapping.")
                continue
            section_id = _clean(section.get("id"))
            if not section_id:
                errors.append(f"Section #{idx} is missing an id.")
            elif section_id not in SECTION_REGISTRY:
                errors.append(f"Unknown report section id: {section_id}")

    # Verify the existing report-config loader accepts it. This catches
    # malformed top-level blocks that the registry alone wouldn't notice.
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", encoding="utf-8", delete=False
        ) as handle:
            handle.write(report_yaml)
            tmp_path = Path(handle.name)
        try:
            load_report_config(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Report config loader rejected the YAML: {type(exc).__name__}: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _require_stage(stage_id: str) -> StageDefinition:
    stage = get_stage_definition(stage_id)
    if stage is None:
        raise ValueError(f"Unknown stage id: {stage_id}")
    return stage


def _require_instrument(symbol: str):
    inst = get_instrument(symbol)
    if inst is None:
        raise ValueError(f"Unknown instrument: {symbol}")
    return inst


def _substitute_tick_metadata(
    node: Any,
    *,
    tick_size: float,
    tick_value: float,
) -> None:
    """Walk the tree and replace any tick_size or tick_value scalar with the
    instrument's value. Only replaces existing keys — never adds new ones.

    This handles the various places these keys appear:
      orb_discovery.tick_size, orb_discovery.tick_value
      lcr_discovery.tick_size, lcr_discovery.tick_value
      candle_discovery.candle_features.tick_size
      large_candle_excursion.candle_size.tick_size
    """
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "tick_size" and isinstance(value, (int, float)) and not isinstance(value, bool):
                node[key] = float(tick_size)
            elif key == "tick_value" and isinstance(value, (int, float)) and not isinstance(value, bool):
                node[key] = float(tick_value)
            else:
                _substitute_tick_metadata(value, tick_size=tick_size, tick_value=tick_value)
    elif isinstance(node, list):
        for item in node:
            _substitute_tick_metadata(item, tick_size=tick_size, tick_value=tick_value)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Mutate `base` in place, merging `overlay` on top.

    Rules:
      - Both values are dicts → recurse.
      - Otherwise → overlay wins outright (lists, scalars, None all replace).
    """
    for key, val in overlay.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(val, dict)
        ):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def _dump_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    )


def _build_command_preview(
    *,
    input_folder: str | None,
    output_folder: str | None,
    market_data_folder: str | None,
    report_config_path: str | None,
    recursive: bool,
    no_tick_data: bool,
) -> str:
    args = [
        sys.executable,
        "-m",
        "ta_foundation.cli.main",
        "--input",
        _clean(input_folder) or "<input-folder>",
        "--output",
        _clean(output_folder) or "<output-folder>",
        "--report-config",
        _clean(report_config_path) or "<generated-stage.yaml>",
    ]
    if _clean(market_data_folder):
        args.extend(["--market-data", _clean(market_data_folder)])
    if recursive:
        args.append("--recursive")
    if no_tick_data:
        args.append("--no-tick-data")
    return " ".join(_quote_arg(a) for a in args)


def _quote_arg(value: str) -> str:
    if not value:
        return value
    if any(ch.isspace() for ch in value):
        return f'"{value}"'
    return value


def _clean(value: Any) -> str:
    return str(value or "").strip()
