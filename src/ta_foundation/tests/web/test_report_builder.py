from __future__ import annotations

import pytest
import yaml

from ta_foundation.web.report_builder import (
    build_command_preview,
    build_report_builder_payload,
    build_report_config,
    validate_report_request,
)


def test_report_builder_merges_capabilities_into_valid_yaml(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"

    body = {
        "input_folder": str(input_dir),
        "output_folder": str(output_dir),
        "report_title": "My UI Report",
        "output_filename": "ui_report.html",
        "capability_ids": ["core_comparison_report", "optimization_overview"],
    }

    result = build_report_builder_payload(body)
    config = yaml.safe_load(result.report_yaml)

    assert result.validation["ok"] is True
    assert config["report"]["title"] == "My UI Report"
    assert config["report"]["output_filename"] == "ui_report.html"
    assert {"id": "comparison_overview"} in config["sections"]
    assert {"id": "optimization_overview", "options": {"top_n": 10, "min_trades": 1}} in config["sections"]
    assert "ta_foundation.cli.main" in result.command_preview


def test_report_builder_accepts_individual_report_sections(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    body = {
        "input_folder": str(input_dir),
        "output_folder": str(tmp_path / "output"),
        "capability_ids": [],
        "report_section_ids": ["comparison_overview", "optimization_overview"],
    }

    result = build_report_builder_payload(body)
    config = yaml.safe_load(result.report_yaml)

    assert result.validation["ok"] is True
    assert config["sections"] == [
        {"id": "comparison_overview"},
        {"id": "optimization_overview"},
    ]
    assert result.validation["selected_report_sections"] == [
        "comparison_overview",
        "optimization_overview",
    ]


def test_report_builder_merges_high_level_group_config_blocks(tmp_path):
    input_dir = tmp_path / "input"
    market_dir = tmp_path / "market"
    input_dir.mkdir()
    market_dir.mkdir()

    result = build_report_builder_payload(
        {
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "market_data_folder": str(market_dir),
            "capability_ids": ["anchor_interaction_full"],
        }
    )
    config = yaml.safe_load(result.report_yaml)

    assert result.validation["ok"] is True
    assert config["anchor_interaction"]["enabled"] is True
    assert {"id": "anchor_interaction_overview"} in config["sections"]


def test_report_builder_requires_market_data_for_market_capability(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    validation = validate_report_request(
        {
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "capability_ids": ["anchor_interaction"],
            "report_yaml": yaml.safe_dump(build_report_config({"capability_ids": ["anchor_interaction"]})),
        }
    )

    assert validation["ok"] is False
    assert "Market data folder is required by the selected capabilities." in validation["errors"]


def test_report_builder_requires_market_data_for_market_section(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    validation = validate_report_request(
        {
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "report_section_ids": ["horizon_overview"],
            "report_yaml": yaml.safe_dump(
                build_report_config({"report_section_ids": ["horizon_overview"]})
            ),
        }
    )

    assert validation["ok"] is False
    assert "Market data folder is required by the selected capabilities." in validation["errors"]


def test_report_builder_rejects_unknown_section(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    validation = validate_report_request(
        {
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "capability_ids": ["core_comparison_report"],
            "report_yaml": "report:\n  title: Bad\nsections:\n  - id: not_a_section\n",
        }
    )

    assert validation["ok"] is False
    assert "Unknown report section id: not_a_section" in validation["errors"]


def test_report_builder_command_preview_keeps_paths_as_cli_args(tmp_path):
    command = build_command_preview(
        {
            "input_folder": str(tmp_path / "input data"),
            "output_folder": str(tmp_path / "output data"),
            "market_data_folder": str(tmp_path / "market data"),
            "recursive": True,
            "include_run_images": True,
            "export_exec_cards_png": True,
            "exec_cards_dir": str(tmp_path / "cards"),
        }
    )

    assert "--input" in command
    assert "--market-data" in command
    assert "--recursive" in command
    assert "--include-run-images" in command
    assert "--export-exec-cards-png" in command
    assert "--exec-cards-dir" in command


def test_report_builder_preview_endpoint(tmp_path):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web.app import create_app

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    app = create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/preview",
            json={
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "capability_ids": ["core_comparison_report"],
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["validation"]["ok"] is True
    assert "sections:" in payload["report_yaml"]


def test_report_builder_validate_endpoint_returns_400_for_bad_yaml(tmp_path):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web.app import create_app

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    app = create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/validate",
            json={
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "capability_ids": ["core_comparison_report"],
                "report_yaml": "sections:\n  - id: nope\n",
            },
        )

    assert response.status_code == 400
    assert response.get_json()["validation"]["ok"] is False
