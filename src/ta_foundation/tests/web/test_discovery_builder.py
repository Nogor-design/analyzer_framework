from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ta_foundation.web.discovery_builder import (
    build_stage_config,
    build_stage_payload,
    build_stage_yaml,
    validate_stage_request,
)
from ta_foundation.web.discovery_stages import (
    get_stage_definition,
    list_stages,
    reload_stages,
)


@pytest.fixture(autouse=True)
def _ensure_real_stages_loaded():
    reload_stages()


def _legacy_yaml_dict(stage_id: str) -> dict:
    sdef = get_stage_definition(stage_id)
    assert sdef is not None
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / "discovery" / sdef.source_yaml_filename
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# ---------------------------------------------------------------------------
# Snapshot parity — every legacy YAML must reproduce at NQ defaults
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stage_id",
    [
        "01_quick_scan",
        "02_candle_patterns",
        "03_levels_regions",
        "04_ny_open",
        "05_orb_momentum",
        "06_validate",
        "large_candle_excursion",
    ],
)
def test_builder_matches_legacy_yaml_at_nq_defaults(stage_id: str):
    legacy = _legacy_yaml_dict(stage_id)
    built = build_stage_config(stage_id, instrument_symbol="NQ")
    assert built == legacy, (
        f"{stage_id}: builder output diverges from legacy YAML at NQ defaults"
    )


def test_builder_emits_yaml_string_that_parses_back_to_config():
    text = build_stage_yaml("01_quick_scan", instrument_symbol="NQ")
    parsed = yaml.safe_load(text)
    expected = build_stage_config("01_quick_scan", instrument_symbol="NQ")
    assert parsed == expected


# ---------------------------------------------------------------------------
# Instrument propagation
# ---------------------------------------------------------------------------

def test_es_instrument_substitutes_tick_value_everywhere():
    config = build_stage_config("01_quick_scan", instrument_symbol="ES")
    assert config["orb_discovery"]["tick_size"] == 0.25
    assert config["orb_discovery"]["tick_value"] == 12.50
    assert config["lcr_discovery"]["tick_size"] == 0.25
    assert config["lcr_discovery"]["tick_value"] == 12.50
    assert config["candle_discovery"]["candle_features"]["tick_size"] == 0.25


def test_cl_instrument_substitutes_tick_size_and_value():
    config = build_stage_config("03_levels_regions", instrument_symbol="CL")
    assert config["lcr_discovery"]["tick_size"] == 0.01
    assert config["lcr_discovery"]["tick_value"] == 10.00


def test_unknown_instrument_raises():
    with pytest.raises(ValueError, match="Unknown instrument"):
        build_stage_config("01_quick_scan", instrument_symbol="ZZZ")


def test_lce_substitutes_tick_size_in_candle_size_block():
    config = build_stage_config("large_candle_excursion", instrument_symbol="ES")
    assert config["large_candle_excursion"]["candle_size"]["tick_size"] == 0.25


# ---------------------------------------------------------------------------
# disabled_families
# ---------------------------------------------------------------------------

def test_disable_families_sets_enabled_false_on_yaml_block():
    config = build_stage_config(
        "01_quick_scan",
        instrument_symbol="NQ",
        disabled_families=["ma", "bb"],
    )
    assert config["ma_discovery"]["enabled"] is False
    assert config["bb_discovery"]["enabled"] is False
    # Other families stay enabled
    assert config["candle_discovery"]["enabled"] is True
    assert config["orb_discovery"]["enabled"] is True


def test_disable_unknown_family_is_ignored():
    config = build_stage_config(
        "01_quick_scan",
        instrument_symbol="NQ",
        disabled_families=["bogus", "candle"],
    )
    assert config["candle_discovery"]["enabled"] is False


# ---------------------------------------------------------------------------
# Deep-merge overrides
# ---------------------------------------------------------------------------

def test_overrides_deep_merge_preserves_other_keys():
    config = build_stage_config(
        "01_quick_scan",
        instrument_symbol="NQ",
        overrides={
            "candle_discovery": {
                "min_trades": 50,
                "patterns": {
                    "large_body": {"body_multiplier": [1.5, 2.5]}
                }
            }
        },
    )
    assert config["candle_discovery"]["min_trades"] == 50
    # The override touched min_trades and one pattern; everything else preserved
    assert config["candle_discovery"]["timeframes"] == [1]
    assert config["candle_discovery"]["patterns"]["large_body"]["body_multiplier"] == [1.5, 2.5]
    # Other patterns survive
    assert "doji" in config["candle_discovery"]["patterns"]


def test_overrides_replace_lists_outright():
    config = build_stage_config(
        "01_quick_scan",
        instrument_symbol="NQ",
        overrides={"candle_discovery": {"timeframes": [5]}},
    )
    assert config["candle_discovery"]["timeframes"] == [5]


# ---------------------------------------------------------------------------
# Report metadata override
# ---------------------------------------------------------------------------

def test_report_title_and_output_filename_override():
    config = build_stage_config(
        "01_quick_scan",
        instrument_symbol="NQ",
        report_title="My run",
        output_filename="my_run.html",
    )
    assert config["report"]["title"] == "My run"
    assert config["report"]["output_filename"] == "my_run.html"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_requires_input_output_market(tmp_path: Path):
    yaml_text = build_stage_yaml("01_quick_scan", instrument_symbol="NQ")
    result = validate_stage_request(
        stage_id="01_quick_scan",
        report_yaml=yaml_text,
        input_folder=None,
        output_folder=None,
        market_data_folder=None,
    )
    assert result["ok"] is False
    msgs = "\n".join(result["errors"])
    assert "Input folder is required" in msgs
    assert "Output folder is required" in msgs
    assert "Market data folder is required" in msgs


def test_validate_passes_when_all_paths_exist(tmp_path: Path):
    in_dir = tmp_path / "in"; in_dir.mkdir()
    md_dir = tmp_path / "md"; md_dir.mkdir()
    yaml_text = build_stage_yaml("01_quick_scan", instrument_symbol="NQ")
    result = validate_stage_request(
        stage_id="01_quick_scan",
        report_yaml=yaml_text,
        input_folder=str(in_dir),
        output_folder=str(tmp_path / "out"),
        market_data_folder=str(md_dir),
    )
    assert result["ok"] is True, result["errors"]


def test_validate_rejects_unknown_section():
    bad_yaml = (
        "report:\n"
        "  title: x\n"
        "  output_filename: x.html\n"
        "  timezone: America/Denver\n"
        "sections:\n"
        "  - id: definitely_not_a_real_section\n"
    )
    result = validate_stage_request(
        stage_id="01_quick_scan",
        report_yaml=bad_yaml,
        input_folder=None,
        output_folder=None,
        market_data_folder=None,
    )
    assert any("Unknown report section id" in err for err in result["errors"])


def test_validate_rejects_unknown_stage_id():
    yaml_text = "report:\n  title: x\n  output_filename: x.html\nsections:\n  - id: comparison_overview\n"
    result = validate_stage_request(
        stage_id="bogus",
        report_yaml=yaml_text,
        input_folder=None,
        output_folder=None,
        market_data_folder=None,
    )
    assert any("Unknown stage id" in err for err in result["errors"])


# ---------------------------------------------------------------------------
# Full payload
# ---------------------------------------------------------------------------

def test_build_stage_payload_returns_all_fields(tmp_path: Path):
    in_dir = tmp_path / "in"; in_dir.mkdir()
    md_dir = tmp_path / "md"; md_dir.mkdir()
    payload = build_stage_payload(
        "01_quick_scan",
        instrument_symbol="NQ",
        input_folder=str(in_dir),
        output_folder=str(tmp_path / "out"),
        market_data_folder=str(md_dir),
        report_config_path=str(tmp_path / "stage.yaml"),
    )
    assert payload.stage_id == "01_quick_scan"
    assert payload.config["candle_discovery"]["enabled"] is True
    assert payload.report_yaml.startswith("report:")
    assert "--report-config" in payload.command_preview
    assert "--no-tick-data" in payload.command_preview
    assert payload.validation["ok"] is True


def test_command_preview_includes_market_data_path(tmp_path: Path):
    in_dir = tmp_path / "in"; in_dir.mkdir()
    md_dir = tmp_path / "md with space"; md_dir.mkdir()
    payload = build_stage_payload(
        "01_quick_scan",
        instrument_symbol="NQ",
        input_folder=str(in_dir),
        output_folder=str(tmp_path / "out"),
        market_data_folder=str(md_dir),
    )
    # Path with whitespace must be quoted in the preview
    assert f'"{md_dir}"' in payload.command_preview
