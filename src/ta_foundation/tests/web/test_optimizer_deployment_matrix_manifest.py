from __future__ import annotations

import json
from pathlib import Path
import pytest

from ta_foundation.web.optimizer_deployment_matrix_manifest import (
    assign_cell,
    cell_name,
    build_deployment_manifest,
    apply_best_effort_fallback,
    write_manifest,
    render_coverage_grid_html,
)


@pytest.fixture
def naming_rules() -> dict:
    path = Path("src/ta_foundation/tests/web/fixtures/naming_rules.json")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_assign_cell_logic(naming_rules: dict):
    # London Early / single / tier 4 / god
    row = {
        "start_minute": 60,
        "average_fast": 5,
        "average_slow": 200,
        "reverse": False,
        "max_trades": 1,
        "profit_stop": 500,
        "loss_stop": 500,
    }
    coords = assign_cell(row, naming_rules)
    assert coords["session"] == "London Early"
    assert coords["single_multi"] == "single"
    assert coords["tier_index"] == 4
    assert coords["side"] == "god"


def test_cell_name_rise_apollo_balance_l(naming_rules: dict):
    # RiseApolloBalanceL
    row = {
        "start_minute": 60,
        "average_fast": 5,
        "average_slow": 200,
        "reverse": False,
        "max_trades": 1,
        "profit_stop": 500,
        "loss_stop": 500,
        "max_stop": 200,
        "max_tp_ratio": 2.0,
        "long_enabled": True,
        "short_enabled": False,
    }
    name = cell_name(row, naming_rules)
    assert name == "RiseApolloBalanceL"


def test_build_deployment_manifest_full_set(naming_rules: dict):
    rows = [
        {
            "start_minute": 60,
            "average_fast": 5,
            "average_slow": 200,
            "reverse": False,
            "max_trades": 1,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 1.5,
            "total_net_profit": 1000,
            "trades": 10,
        },
        {
            "start_minute": 300,  # London Late
            "average_fast": 5,
            "average_slow": 200,
            "reverse": True,
            "max_trades": 3,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 2.0,
            "total_net_profit": 2000,
            "trades": 20,
        },
    ]
    manifest = build_deployment_manifest(rows, naming_rules)

    assert manifest["total"] == 252
    assert manifest["covered"] == 2
    assert manifest["missing"] == 250
    assert len(manifest["cells"]) == 252

    # Verify order and structure
    covered_cells = [c for c in manifest["cells"] if c["status"] == "covered"]
    assert len(covered_cells) == 2
    assert covered_cells[0]["session"] == "London Early"
    assert covered_cells[1]["session"] == "London Late"


def test_fitness_selection_higher_pf(naming_rules: dict):
    # Two rows in SAME cell
    rows = [
        {
            "start_minute": 60,
            "average_fast": 5,
            "average_slow": 200,
            "reverse": False,
            "max_trades": 1,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 1.5,
            "total_net_profit": 1000,
        },
        {
            "start_minute": 60,
            "average_fast": 5,
            "average_slow": 200,
            "reverse": False,
            "max_trades": 1,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 2.5,  # Better PF
            "total_net_profit": 500,  # Lower net profit
        },
    ]
    manifest = build_deployment_manifest(rows, naming_rules)
    assert manifest["covered"] == 1
    cell = next(c for c in manifest["cells"] if c["status"] == "covered")
    assert cell["profit_factor"] == 2.5
    assert cell["total_net_profit"] == 500


def test_write_manifest_io(naming_rules: dict, tmp_path: Path):
    rows = [
        {
            "start_minute": 60,
            "average_fast": 5,
            "average_slow": 200,
            "reverse": False,
            "max_trades": 1,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 1.5,
            "total_net_profit": 1000,
        }
    ]
    manifest = build_deployment_manifest(rows, naming_rules)
    paths = write_manifest(manifest, tmp_path)

    assert paths["json"].exists()
    assert paths["csv"].exists()

    with paths["json"].open("r", encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded == manifest
    assert len(manifest["cells"]) == 252


def _god_row(start_minute: int, average_slow: int, *, max_trades: int = 1, pf: float = 1.5) -> dict:
    return {
        "start_minute": start_minute,
        "average_fast": 5,
        "average_slow": average_slow,
        "reverse": False,
        "max_trades": max_trades,
        "profit_stop": 500,
        "loss_stop": 500,
        "max_stop": 200,
        "max_tp_ratio": 2.0,
        "profit_factor": pf,
        "total_net_profit": 1000,
        "trades": 10,
        "template_name": f"tpl_{start_minute}_{average_slow}_{max_trades}",
        "template_path": f"/x/tpl_{start_minute}_{average_slow}_{max_trades}.xml",
    }


def test_fallback_fills_missing_from_same_side_donor(naming_rules: dict):
    # One covered god/single cell. All other god cells should become fallback;
    # every monster cell stays missing (never cross side).
    rows = [_god_row(60, 200, max_trades=1, pf=1.5)]  # London Early / single / tier 4 / god
    manifest = build_deployment_manifest(rows, naming_rules)
    assert manifest["covered"] == 1 and manifest["missing"] == 251

    filled = apply_best_effort_fallback(manifest, naming_rules)
    assert filled["covered"] == 1
    # 7 sessions * 2 single_multi * 9 tiers = 126 god cells; 125 become fallback.
    assert filled["fallback"] == 125
    assert filled["missing"] == 126  # all monster cells unfillable (no monster donor)
    assert filled["covered"] + filled["fallback"] + filled["missing"] == 252

    # No monster cell was filled from a god donor.
    for c in filled["cells"]:
        if c["side"] == "monster":
            assert c["status"] == "missing"
        if c["status"] == "fallback":
            assert c["fallback_source"]["side"] == "god"


def test_fallback_prefers_nearest_tier_donor(naming_rules: dict):
    # Two god/single donors at tier 1 (avg_slow=3) and tier 9 (avg_slow=450).
    # A missing god/single tier-2 cell should borrow the tier-1 donor (closer).
    rows = [
        _god_row(60, 3, max_trades=1, pf=2.0),    # tier 1
        _god_row(60, 450, max_trades=1, pf=9.0),  # tier 9, higher PF but far
    ]
    manifest = build_deployment_manifest(rows, naming_rules)
    filled = apply_best_effort_fallback(manifest, naming_rules)
    cell = next(
        c
        for c in filled["cells"]
        if c["session"] == "London Early"
        and c["single_multi"] == "single"
        and c["tier_index"] == 2
        and c["side"] == "god"
    )
    assert cell["status"] == "fallback"
    # tier 1 is distance 1 from tier 2; tier 9 is distance 7 → nearest wins over PF.
    assert cell["fallback_source"]["tier_index"] == 1


def test_write_manifest_handles_fallback_rows(naming_rules: dict, tmp_path: Path):
    rows = [_god_row(60, 200, max_trades=1, pf=1.5)]
    filled = apply_best_effort_fallback(build_deployment_manifest(rows, naming_rules), naming_rules)
    paths = write_manifest(filled, tmp_path)
    assert paths["json"].exists() and paths["csv"].exists()
    # CSV must not crash on the heterogeneous fallback_source key.
    text = paths["csv"].read_text(encoding="utf-8")
    assert "fallback_source" in text.splitlines()[0]
    with paths["json"].open(encoding="utf-8") as f:
        assert json.load(f) == filled


def test_write_manifest_flattens_feature_columns(naming_rules: dict, tmp_path: Path):
    manifest = build_deployment_manifest([_god_row(60, 200, max_trades=1, pf=1.5)], naming_rules)
    cell = next(c for c in manifest["cells"] if c["status"] == "covered")
    cell["features"] = {
        "best_policy": "atr_trail",
        "exit_robustness_margin": 12.5,
        "prop_max_daily_loss": 500.0,
    }
    paths = write_manifest(manifest, tmp_path)
    header = paths["csv"].read_text(encoding="utf-8").splitlines()[0]
    assert "features.best_policy" in header
    assert "features.exit_robustness_margin" in header
    assert "features.prop_max_daily_loss" in header


def test_render_grid_shows_fallback_amber(naming_rules: dict):
    rows = [_god_row(60, 200, max_trades=1, pf=1.5)]
    filled = apply_best_effort_fallback(build_deployment_manifest(rows, naming_rules), naming_rules)
    html = render_coverage_grid_html(filled, naming_rules)
    assert "class='fallback'" in html


def test_render_coverage_grid_html(naming_rules: dict):
    rows = [
        {
            "start_minute": 60,
            "average_fast": 5,
            "average_slow": 200,
            "reverse": False,
            "max_trades": 1,
            "profit_stop": 500,
            "loss_stop": 500,
            "max_stop": 200,
            "max_tp_ratio": 2.0,
            "profit_factor": 1.5,
            "total_net_profit": 1000,
        }
    ]
    manifest = build_deployment_manifest(rows, naming_rules)
    html = render_coverage_grid_html(manifest, naming_rules)

    assert "God Coverage" in html
    assert "Monster Coverage" in html
    assert "class='covered'" in html
    assert "class='missing'" in html
    assert "T1" in html
    assert "T9" in html
