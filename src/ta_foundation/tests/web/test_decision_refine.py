"""End-to-end tests for the ``/api/optimizer/sessions/<id>/decision/refine``
route and the clone_session recipe-carry-over fix.

These cover the Decision Dashboard refinement entry point added in the
2026-05-28 UX redesign: pick finalists, spawn a refinement stage *in this
session* (no clone), re-anchor the final fixed-backtest stage, rearm the
orchestrator, and land the operator on the recipe Stages tab with the new
stage focused via ``?focus_stage=`` (the focus_stage handling itself is
client-side and lives in ``optimizer_recipe.html``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session


@pytest.fixture
def client(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    app = web_app.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    opt_session.set_storage_root(None)


def _seed_session_with_recipe_and_manifest(client, tmp_path: Path) -> str:
    """Create a session, save a minimal recipe, write final_backtest manifest
    + parent stage scored rows so the refine endpoint has everything it needs.
    """
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text("<StrategyTemplate />", encoding="utf-8")
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    session = opt_session.get_session(sid)
    assert session is not None

    recipe = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_refine_test",
        "recipe_name": "refine_test",
        "strategy_id": "FakeStrategy",
        "base_matrix": [
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 8]},
            {"param": "UseTimeFilter", "role": "fixed", "value": True},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "First sweep",
                "optimize_inside_template": {
                    "averageSlow": {"min": 50, "max": 200, "step": 50},
                    "MaxStop": {"min": 50, "max": 150, "step": 50},
                },
                "selection": {
                    "group_by": ["StartTimeH", "Reverse"],
                    "keep_per_group": 1,
                    "fitness_metrics": ["profit_factor"],
                    "min_trades": 5,
                    "min_profit_factor": 1.2,
                    "max_drawdown": 2500,
                    "min_net_profit": 0,
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_1.selected_rows",
                "finalists_per_bucket": 2,
            },
        ],
    }
    res = client.put(f"/api/optimizer/sessions/{sid}/recipe", json={"recipe": recipe})
    assert res.status_code == 200

    # Write the final_backtest manifest with two finalists, both descended
    # from stage_1, plus a third whose parent is a *different* stage so we
    # can prove cross-source selections are refused.
    #
    # Note: ``parent_stage_id`` is INTENTIONALLY ``stage_3`` for every entry
    # to mirror production. The final_backtest stage's ``from`` always points
    # at the last refinement stage, but ``final_selection_source_stage`` (and
    # the ``parent_candidate_id`` prefix) records the stage whose
    # scored_rows.json the row actually lives in. The helper must follow the
    # second signal, not the first.
    manifest = {
        "schema_version": 1,
        "stage_id": "final_backtest",
        "stage_type": "fixed_backtest",
        "template_count": 3,
        "templates": [
            {
                "stage_id": "final_backtest",
                "template_id": "final_backtest__F_001",
                "parent_candidate_id": "stage_1_row1",
                "parent_stage_id": "stage_3",
                "final_selection_source_stage": "stage_1",
                "initial_bucket_key": "starttimeh_0__reverse_false",
                "initial_bucket_values": {"StartTimeH": 0, "Reverse": False},
            },
            {
                "stage_id": "final_backtest",
                "template_id": "final_backtest__F_002",
                "parent_candidate_id": "stage_1_row2",
                "parent_stage_id": "stage_3",
                "final_selection_source_stage": "stage_1",
                "initial_bucket_key": "starttimeh_8__reverse_true",
                "initial_bucket_values": {"StartTimeH": 8, "Reverse": True},
            },
            {
                "stage_id": "final_backtest",
                "template_id": "final_backtest__F_003",
                "parent_candidate_id": "stage_other_row1",
                "parent_stage_id": "stage_3",
                "final_selection_source_stage": "stage_other",
                "initial_bucket_key": "other",
                "initial_bucket_values": {},
            },
        ],
    }
    manifest_path = session.directory / "generated_templates" / "final_backtest" / "recipe_template_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Parent stage_1 scored rows that the finalists reference.
    scored_dir = session.directory / "parsed_results" / "stage_1"
    scored_dir.mkdir(parents=True, exist_ok=True)
    scored_rows = [
        {
            "candidate_id": "stage_1_row1",
            "param_StartTimeH": 0,
            "param_Reverse": False,
            "param_averageSlow": 100,
            "param_MaxStop": 100,
            "profit_factor": 1.8,
            "total_net_profit": 5000,
        },
        {
            "candidate_id": "stage_1_row2",
            "param_StartTimeH": 8,
            "param_Reverse": True,
            "param_averageSlow": 150,
            "param_MaxStop": 50,
            "profit_factor": 1.5,
            "total_net_profit": 3000,
        },
    ]
    (scored_dir / "scored_rows.json").write_text(json.dumps(scored_rows), encoding="utf-8")

    return sid


def test_decision_refine_spawns_new_stage_in_same_session(client, tmp_path: Path):
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)

    res = client.post(
        f"/api/optimizer/sessions/{sid}/decision/refine",
        json={"candidate_ids": ["F_001", "F_002"]},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["parent_stage_id"] == "stage_1"
    assert body["selected_count"] == 2
    assert body["new_stage_id"].startswith("stage_")
    # Redirect routes through the resume route so the ``_optimizer_session``
    # cookie gets pinned to *this* session before the recipe editor reads it.
    # Going directly to ``/optimizer/recipe`` would load whatever session the
    # cookie last pointed at and the focus_stage param would target the wrong
    # session's recipe.
    assert body["focus_url"] == (
        f"/optimizer/sessions/{sid}/resume?focus_stage={body['new_stage_id']}"
    )

    session = opt_session.get_session(sid)
    assert session is not None

    # Parent stage selected.json now contains only the two hand-picked rows.
    selected_path = session.directory / "parsed_results" / "stage_1" / "selected.json"
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    assert {row["candidate_id"] for row in payload} == {"stage_1_row1", "stage_1_row2"}
    assert all(row["selection_status"] == "selected" for row in payload)
    assert all("Decision Dashboard" in row["selection_reason"] for row in payload)

    # Recipe now has the new stage spliced in BEFORE final_backtest, and the
    # final_backtest stage's ``from`` now points at the new stage.
    recipe = json.loads((session.directory / "recipe.json").read_text(encoding="utf-8"))
    stage_ids = [s["stage_id"] for s in recipe["stages"]]
    assert stage_ids[0] == "stage_1"
    assert stage_ids[-1] == "final_backtest"
    assert body["new_stage_id"] in stage_ids
    new_idx = stage_ids.index(body["new_stage_id"])
    assert new_idx == len(stage_ids) - 2  # immediately before final_backtest
    new_stage = recipe["stages"][new_idx]
    assert new_stage["stage_type"] == "optimizer"
    assert new_stage["from"] == "stage_1.selected_rows"
    # Matrix axes pinned, parent's optimized params refined around parent value.
    assert set(new_stage["pin"]) == {"Reverse", "StartTimeH"}
    assert set(new_stage["refine_around_parent_result"]) == {"averageSlow", "MaxStop"}
    # Final-backtest stage now consumes the new stage's rows, not stage_1's.
    final_stage = recipe["stages"][-1]
    assert final_stage["from"] == f"{body['new_stage_id']}.selected_rows"

    # Orchestrator state is re-armed at the new stage so a single Advance
    # click on the Run dashboard will launch it without restarting stage_1.
    state = json.loads((session.directory / "recipe_state.json").read_text(encoding="utf-8"))
    assert state["state"] == "generating_child_stage"
    assert state["current_stage_id"] == body["new_stage_id"]
    assert state["pause_requested"] is False
    assert state["stop_requested"] is False


def test_decision_refine_rejects_cross_source_selection(client, tmp_path: Path):
    """F_003 in the fixture has ``final_selection_source_stage = stage_other``,
    so picking F_001 and F_003 together must be refused with a message that
    spells out which finalist came from which stage."""
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)

    res = client.post(
        f"/api/optimizer/sessions/{sid}/decision/refine",
        json={"candidate_ids": ["F_001", "F_003"]},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert "multiple source stages" in body["error"]
    assert "F_001 (from stage_1)" in body["error"]
    assert "F_003 (from stage_other)" in body["error"]


def test_decision_refine_uses_candidate_id_prefix_when_field_missing(
    client, tmp_path: Path,
):
    """Older manifests don't carry ``final_selection_source_stage``. The
    helper must then derive the source from the candidate_id prefix
    (``stage_N__...``) and still resolve the right scored_rows.json file.
    """
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)
    session = opt_session.get_session(sid)
    assert session is not None

    # Re-write the manifest WITHOUT the explicit field, using a
    # production-shaped candidate_id whose prefix is the true source stage.
    manifest_path = (
        session.directory
        / "generated_templates" / "final_backtest"
        / "recipe_template_manifest.json"
    )
    manifest = {
        "schema_version": 1,
        "stage_id": "final_backtest",
        "stage_type": "fixed_backtest",
        "template_count": 1,
        "templates": [
            {
                "stage_id": "final_backtest",
                "template_id": "final_backtest__F_010",
                "parent_candidate_id": "stage_1__rowX",
                "parent_stage_id": "stage_3",  # misleading; the source is stage_1
                "initial_bucket_key": "starttimeh_0__reverse_false",
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Add the matching parent row.
    scored_path = session.directory / "parsed_results" / "stage_1" / "scored_rows.json"
    rows = json.loads(scored_path.read_text(encoding="utf-8"))
    rows.append({"candidate_id": "stage_1__rowX", "param_StartTimeH": 0})
    scored_path.write_text(json.dumps(rows), encoding="utf-8")

    res = client.post(
        f"/api/optimizer/sessions/{sid}/decision/refine",
        json={"candidate_ids": ["F_010"]},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["parent_stage_id"] == "stage_1"
    assert body["selected_count"] == 1


def test_decision_refine_rejects_unknown_finalist(client, tmp_path: Path):
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)
    res = client.post(
        f"/api/optimizer/sessions/{sid}/decision/refine",
        json={"candidate_ids": ["F_999"]},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert "Unknown finalist" in body["error"]


def test_decision_refine_requires_candidate_ids(client, tmp_path: Path):
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)
    for payload in ({}, {"candidate_ids": []}, {"candidate_ids": "F_001"}):
        res = client.post(
            f"/api/optimizer/sessions/{sid}/decision/refine",
            json=payload,
        )
        assert res.status_code == 400, payload


def test_decision_refine_requires_manifest(client, tmp_path: Path):
    """Without a final_backtest manifest the route refuses politely instead
    of crashing — the user simply hasn't run the recipe through final yet."""
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text("<StrategyTemplate />", encoding="utf-8")
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    res = client.post(
        f"/api/optimizer/sessions/{sid}/decision/refine",
        json={"candidate_ids": ["F_001"]},
    )
    assert res.status_code == 400
    assert "manifest" in res.get_json()["error"].lower()


def test_resume_route_forwards_focus_stage(client, tmp_path: Path):
    """Regression: the ``Refine selected`` button on the Decision Dashboard
    sends the operator to ``/optimizer/sessions/<sid>/resume?focus_stage=...``.
    The resume route must (a) set the ``_optimizer_session`` cookie to this
    session and (b) preserve the ``focus_stage`` query param through the
    redirect, otherwise the recipe editor opens on the wrong session or on
    the default Recipe Setup tab.
    """
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text("<StrategyTemplate />", encoding="utf-8")
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]
    session = opt_session.get_session(sid)
    assert session is not None
    (session.directory / "recipe.json").write_text("{}", encoding="utf-8")

    res = client.get(
        f"/optimizer/sessions/{sid}/resume?focus_stage=stage_3",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert res.headers["Location"] == "/optimizer/recipe?focus_stage=stage_3"
    set_cookie = res.headers.get("Set-Cookie") or ""
    assert "ta_optimizer_session_id=" in set_cookie
    assert sid in set_cookie


def test_resume_route_works_without_focus_stage(client, tmp_path: Path):
    """The classic resume flow (Sessions list -> Resume) must still land at
    plain ``/optimizer/recipe`` without a trailing query string."""
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text("<StrategyTemplate />", encoding="utf-8")
    res = client.post("/api/optimizer/sessions", json={
        "strategy_id": "FakeStrategy",
        "seed_template_path": str(seed_path),
        "instrument": "NQ 06-26",
    })
    sid = res.get_json()["session"]["session_id"]

    res = client.get(f"/optimizer/sessions/{sid}/resume", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"] == "/optimizer/recipe"


def test_clone_session_carries_recipe_with_fresh_recipe_id(client, tmp_path: Path):
    """Regression for the Clone & Refine confusion: a clone used to land
    in Recipe Setup with every parameter role reset to "Fixed" because
    recipe.json was not copied. It should arrive with the parent's
    sweep config intact and a new recipe_id so history stays separate.
    """
    sid = _seed_session_with_recipe_and_manifest(client, tmp_path)
    source = opt_session.get_session(sid)
    assert source is not None
    parent_recipe = json.loads((source.directory / "recipe.json").read_text(encoding="utf-8"))

    cloned = opt_session.clone_session(source, label="my clone")
    cloned_recipe_path = cloned.directory / "recipe.json"
    assert cloned_recipe_path.exists(), "clone_session should copy recipe.json"

    cloned_recipe = json.loads(cloned_recipe_path.read_text(encoding="utf-8"))
    assert cloned_recipe["recipe_id"] != parent_recipe["recipe_id"]
    assert cloned_recipe["recipe_id"].startswith(parent_recipe["recipe_id"])
    # Everything else identical so the editor restores the same UI state.
    assert cloned_recipe["base_matrix"] == parent_recipe["base_matrix"]
    assert cloned_recipe["stages"] == parent_recipe["stages"]
    assert cloned_recipe["strategy_id"] == parent_recipe["strategy_id"]

    # Parent's results and deployment package are intentionally NOT copied;
    # the clone starts fresh on disk.
    assert not (cloned.directory / "parsed_results").exists()
    assert not (cloned.directory / "deployment_package").exists()
