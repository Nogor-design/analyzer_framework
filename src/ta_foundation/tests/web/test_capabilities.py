from __future__ import annotations

import pytest

from ta_foundation.web.capabilities import (
    get_capability,
    list_capabilities,
    list_capability_groups,
    list_report_section_categories,
    list_report_sections,
)


def test_capability_catalog_exposes_core_report_metadata():
    capabilities = list_capabilities()

    core = next(cap for cap in capabilities if cap["id"] == "core_comparison_report")

    assert core["status"] == "ready"
    assert "input_folder" in core["required_inputs"]
    assert "output_folder" in core["required_inputs"]
    assert "comparison_overview" in core["report_sections"]
    assert core["config_blocks"]["report"]["timezone"] == "America/Denver"


def test_get_capability_returns_none_for_unknown_id():
    assert get_capability("does_not_exist") is None


def test_report_section_discovery_derives_registry_metadata():
    sections = list_report_sections()
    by_id = {section["id"]: section for section in sections}

    assert "horizon_overview" in by_id
    assert by_id["horizon_overview"]["title"] == "Horizon Prediction Overview"
    assert by_id["horizon_overview"]["category"] == "Prediction"
    assert by_id["horizon_overview"]["config_block_keys"] == ["horizon"]

    assert "optimization_overview" in by_id
    assert by_id["optimization_overview"]["category"] == "Optimization"


def test_capability_groups_include_existing_analysis_and_report_packs():
    groups = list_capability_groups()
    by_id = {group["id"]: group for group in groups}

    assert "anchor_interaction_full" in by_id
    assert "large_candle_excursion" in by_id
    assert "horizon_prediction_overview" in by_id
    assert "anchor_interaction_overview" in by_id["anchor_interaction_full"]["report_sections"]
    assert by_id["anchor_interaction_full"]["config_blocks"]["anchor_interaction"]["enabled"] is True


def test_report_section_categories_group_section_ids():
    categories = list_report_section_categories()
    by_category = {item["category"]: item["section_ids"] for item in categories}

    assert "horizon_overview" in by_category["Prediction"]
    assert "optimization_overview" in by_category["Optimization"]


def test_capabilities_endpoint_returns_catalog():
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web.app import create_app

    app = create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.get("/api/capabilities")

    assert response.status_code == 200
    payload = response.get_json()
    capability_ids = {cap["id"] for cap in payload["capabilities"]}
    group_ids = {group["id"] for group in payload["capability_groups"]}
    section_ids = {section["id"] for section in payload["report_sections"]}
    assert "core_comparison_report" in capability_ids
    assert "strategy_discovery" in capability_ids
    assert "strategy_discovery_full" in group_ids
    assert "horizon_overview" in section_ids
