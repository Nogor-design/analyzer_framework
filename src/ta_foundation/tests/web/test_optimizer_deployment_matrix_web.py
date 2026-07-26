from __future__ import annotations

"""Flask test-client coverage that the deployment-matrix bundle recipe survives the
web save -> plan path. This guards the LATENT app.py risk: that a route reader
assumes scalar matrix axes and chokes on ``matrix_bundle_axis``. The bundle axis is
a plan-time construct (by results-time every param is a flat pinned column), so this
hermetic check plus the generation guard in test_optimizer_deployment_matrix.py cover
the integration without needing live NinjaTrader."""

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_deployment_matrix import (
    build_deployment_matrix_recipe,
    load_naming_rules,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "naming_rules.json"


@pytest.fixture
def client(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    opt_session.set_storage_root(None)


def _small_bundle_recipe() -> dict:
    """Real deployment-matrix recipe, shrunk to a couple of root lanes so the plan
    is quick while still exercising the Session bundle axis."""
    rules = load_naming_rules(FIXTURE_PATH)
    recipe = build_deployment_matrix_recipe(
        strategy_id="FakeStrategy", recipe_name="dm_web", rules=rules
    )
    for entry in recipe["base_matrix"]:
        if entry["param"] == "Session":
            entry["values"] = entry["values"][:2]  # 2 session bundles
        elif entry["param"] == "averageSlow":
            entry["values"] = entry["values"][:1]  # 1 tier
    return recipe


def test_deployment_matrix_bundle_recipe_survives_web_save_and_plan(client, tmp_path: Path):
    seed = tmp_path / "seed.xml"
    seed.write_text("<NinjaTrader><StrategyTemplate /></NinjaTrader>", encoding="utf-8")

    res = client.post(
        "/api/optimizer/sessions",
        json={"strategy_id": "FakeStrategy", "seed_template_path": str(seed), "instrument": "NQ 06-26"},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    sid = res.get_json()["session"]["session_id"]

    recipe = _small_bundle_recipe()

    # Save: app.py must accept the matrix_bundle_axis entry, not 4xx on it.
    res = client.put(f"/api/optimizer/sessions/{sid}/recipe", json={"recipe": recipe})
    assert res.status_code == 200, res.get_data(as_text=True)

    # Round-trip: the bundle axis survives persistence with values as dicts.
    res = client.get(f"/api/optimizer/sessions/{sid}/recipe")
    assert res.status_code == 200
    saved = res.get_json()["recipe"]
    session_axis = next(e for e in saved["base_matrix"] if e["param"] == "Session")
    assert session_axis["role"] == "matrix_bundle_axis"
    assert isinstance(session_axis["values"][0], dict)
    assert {"StartTimeH", "StartTimeM", "DurationTimeH", "DurationTimeM"} <= set(
        session_axis["values"][0]
    )

    # Plan: the planner expands the bundle into root lanes (2 sessions x 2 reverse x 1 tier = 4).
    res = client.post(f"/api/optimizer/sessions/{sid}/recipe/plan")
    assert res.status_code == 200, res.get_data(as_text=True)

    res = client.get(f"/api/optimizer/sessions/{sid}/recipe")
    plan = res.get_json()["plan"]
    assert plan is not None
    assert plan["template_count"] == 4

    # Each root lane carries all four bundle time params merged with Reverse.
    stage1 = plan["stages"][0]
    assert len(stage1["jobs"]) == 4
    for job in stage1["jobs"]:
        mv = job["matrix_values"]
        assert {"StartTimeH", "StartTimeM", "DurationTimeH", "DurationTimeM", "Reverse"} <= set(mv)
