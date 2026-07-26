from __future__ import annotations

import yaml

from ta_foundation.web.report_catalog import (
    build_template_config,
    build_template_yaml,
    list_cli_parameters,
    list_report_templates,
)


def test_report_catalog_exposes_cli_and_yaml_parameter_descriptions():
    cli_params = {param["path"]: param for param in list_cli_parameters()}
    templates = {template["id"]: template for template in list_report_templates()}

    assert "input_folder" in cli_params
    assert "exec_cards_dir" in cli_params
    assert cli_params["recursive"]["group"] == "CLI ingest options"
    assert cli_params["input_folder"]["default"]
    assert cli_params["output_folder"]["default"] == "./outputs/web_reports"
    assert cli_params["report_config_path"]["default"].endswith("generated_report.yaml")
    assert "NinjaTrader backtest exports" in cli_params["input_folder"]["description"]
    assert "weekly_prop_dashboard" in templates
    assert "executive_cards_report" in templates
    assert templates["weekly_prop_dashboard"]["requires_backtest_data"] is True
    assert templates["horizon_overview"]["requires_backtest_data"] is False
    assert templates["strategy_discovery_full"]["needs_market_data"] is True
    assert any(param["group"] == "Signal corpus discovery" for param in templates["strategy_discovery_full"]["parameters"])


def test_build_template_yaml_applies_dotted_overrides():
    report_yaml = build_template_yaml(
        "weekly_prop_dashboard",
        {
            "report.title": "Custom Weekly",
            "sections.0.options.top_n": "25",
            "sections.0.options.show_card_image": False,
        },
    )
    config = yaml.safe_load(report_yaml)

    assert config["report"]["title"] == "Custom Weekly"
    assert config["sections"][0]["options"]["top_n"] == 25
    assert config["sections"][0]["options"]["show_card_image"] is False


def test_strategy_discovery_template_keeps_behavior_in_yaml():
    config = build_template_config(
        "strategy_discovery_full",
        {
            "strategy_discovery.instrument": "ES",
            "strategy_discovery.signal_exit_sweep.stop_grid": "6, 10, 14",
        },
    )

    assert config["strategy_discovery"]["enabled"] is True
    assert config["strategy_discovery"]["instrument"] == "ES"
    assert config["strategy_discovery"]["signal_exit_sweep"]["stop_grid"] == [6, 10, 14]
    assert any(section["id"] == "strategy_discovery_nt_template" for section in config["sections"])
    assert any(section["id"] == "strategy_discovery_signal_entries" for section in config["sections"])


def test_executive_cards_template_keeps_card_behavior_in_yaml():
    config = build_template_config(
        "executive_cards_report",
        {
            "sections.0.options.show_detail_charts": "false",
            "sections.0.options.timeline_render_bin_minutes": "30",
        },
    )

    assert config["sections"][0]["id"] == "run_executive_profile_cards"
    assert config["sections"][0]["options"]["show_detail_charts"] is False
    assert config["sections"][0]["options"]["timeline_render_bin_minutes"] == 30


def test_report_catalog_endpoint_returns_templates():
    import pytest

    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web.app import create_app

    app = create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.get("/api/report-catalog")

    payload = response.get_json()
    assert response.status_code == 200
    assert any(item["id"] == "strategy_discovery_full" for item in payload["report_templates"])
    assert any(item["path"] == "output_folder" for item in payload["cli_parameters"])


def test_report_builder_from_template_endpoint_validates_yaml(tmp_path):
    import pytest

    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web.app import create_app

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    app = create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/from-template",
            json={
                "template_id": "core_comparison",
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "report_config_path": str(tmp_path / "report.yaml"),
                "values": {
                    "report.title": "Generated From Template",
                    "sections.2.options.include_summary_table": True,
                },
            },
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["validation"]["ok"] is True
    assert "Generated From Template" in payload["report_yaml"]
