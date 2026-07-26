"""Tests for the shortlist → fixed-backtest promotion path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_promotion import (
    PROMOTED_DIRNAME,
    PROMOTED_HANDOFF_DIRNAME,
    PROMOTED_MANIFEST_FILENAME,
    PromotionError,
    PromotionResult,
    _next_p_nnn_index,
    _resolve_promoted_params,
    promote_pending,
)
from ta_foundation.web.optimizer_recipe import save_recipe
from ta_foundation.web.optimizer_shortlist import (
    ITEM_STATUS_PROMOTED,
    add_items,
    load_shortlist,
    resolve_shortlist,
)


SEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <Strategy>
    <FakeStrategy>
      <StartTimeH>0</StartTimeH>
      <DurationTimeH>1</DurationTimeH>
      <Reverse>false</Reverse>
      <averageSlow>100</averageSlow>
      <averageFast>2</averageFast>
      <MaxStop>50</MaxStop>
      <Category>NinjaScript</Category>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
</StrategyTemplate>
"""


def _recipe_payload() -> dict:
    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_test",
        "recipe_name": "Promotion Test",
        "strategy_id": "FakeStrategy",
        "target_final_candidates": 4,
        "base_matrix": [
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4, 8]},
            {"param": "DurationTimeH", "role": "fixed", "value": 4},
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Broad search",
                "optimize_inside_template": {
                    "averageFast": {"min": 2, "max": 10, "step": 1},
                    "averageSlow": {"min": 50, "max": 400, "step": 50},
                    "MaxStop": {"min": 50, "max": 100, "step": 25},
                },
            },
            {
                "stage_id": "stage_2",
                "stage_type": "optimizer",
                "from": "stage_1.selected_rows",
                "pin": ["StartTimeH", "DurationTimeH", "Reverse"],
                "refine_around_parent_result": {
                    "averageFast": {"source": "parent", "radius": 1, "step": 1, "min": 1, "max": 20},
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_2.selected_rows",
            },
        ],
    }


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _seed_session(tmp_path: Path) -> opt_session.OptimizerSession:
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(SEED_XML, encoding="utf-8")
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ 06-26",
    )
    save_recipe(session, _recipe_payload())
    return session


def _write_stage_rows(session, stage_id: str, rows: list[dict]) -> None:
    stage_dir = session.directory / "parsed_results" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scored_rows.json").write_text(json.dumps(rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# _next_p_nnn_index — pure helper
# ---------------------------------------------------------------------------

def test_next_p_nnn_index_starts_at_one_when_empty():
    assert _next_p_nnn_index(set()) == 1


def test_next_p_nnn_index_skips_past_max_existing():
    assert _next_p_nnn_index({"P_001", "P_003"}) == 4


def test_next_p_nnn_index_ignores_non_p_ids():
    assert _next_p_nnn_index({"F_001", "garbage", "P_2"}) == 3


# ---------------------------------------------------------------------------
# Param resolution
# ---------------------------------------------------------------------------

def test_resolve_promoted_params_row_overrides_pin_and_fixed():
    row = {
        "candidate_id": "stage_1__T__row1",
        "param_StartTimeH": 8,         # canonical code-name
        "param_averageSlow": 250,
        "param_Reverse": True,
    }
    values = _resolve_promoted_params(
        row=row,
        stage=None,
        pin_names=("StartTimeH", "DurationTimeH"),
        base_matrix_fixed={"DurationTimeH": 4},
    )
    # base_matrix fixed flows in
    assert values["DurationTimeH"] == 4
    # row's param_* wins over pin/base_matrix
    assert values["StartTimeH"] == 8
    assert values["averageSlow"] == 250
    assert values["Reverse"] is True


def test_resolve_promoted_params_pin_value_used_when_row_lacks_param_prefix():
    row = {
        "candidate_id": "x",
        "StartTimeH": 12,              # bare column, not param_ prefixed
        "param_averageSlow": 200,
    }
    values = _resolve_promoted_params(
        row=row,
        stage=None,
        pin_names=("StartTimeH",),
        base_matrix_fixed={},
    )
    assert values["StartTimeH"] == 12
    assert values["averageSlow"] == 200


# ---------------------------------------------------------------------------
# promote_pending — happy path
# ---------------------------------------------------------------------------

def test_promote_pending_stamps_p001_and_marks_shortlist(tmp_path: Path):
    session = _seed_session(tmp_path)
    _write_stage_rows(session, "stage_1", [
        {
            "candidate_id": "stage_1__T_A__row1",
            "param_StartTimeH": 0,
            "param_averageSlow": 150,
            "param_averageFast": 3,
            "param_MaxStop": 75,
            "param_Reverse": False,
            "profit_factor": 2.0, "total_net_profit": 1000, "trades": 30,
        },
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "stage_1__T_A__row1"}])

    result = promote_pending(session)

    assert isinstance(result, PromotionResult)
    assert len(result.promoted) == 1
    entry = result.promoted[0]
    assert entry.template_id == "P_001"
    assert entry.source_stage_id == "stage_1"
    assert entry.source_candidate_id == "stage_1__T_A__row1"
    assert Path(entry.path).exists()
    assert Path(entry.deployment_package_path).exists()

    # Manifest mirrors final_backtest schema
    manifest_path = (
        session.directory / "generated_templates" / PROMOTED_DIRNAME / PROMOTED_MANIFEST_FILENAME
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["stage_id"] == PROMOTED_DIRNAME
    assert manifest["stage_type"] == "promoted_backtest"
    assert manifest["template_count"] == 1
    only = manifest["templates"][0]
    assert only["template_id"] == "P_001"
    assert only["source_stage_id"] == "stage_1"
    assert only["source_candidate_id"] == "stage_1__T_A__row1"
    assert only["strategy_values"]["averageSlow"] == 150
    # base_matrix fixed value flows in
    assert only["strategy_values"]["DurationTimeH"] == 4

    # Shortlist mutated with promoted_run_id + promoted_at
    raw = load_shortlist(session)
    saved = next(i for i in raw.items if i.candidate_id == "stage_1__T_A__row1")
    assert saved.promoted_run_id == "P_001"
    assert saved.promoted_at

    # Resolved shortlist surfaces the promoted status
    resolved = resolve_shortlist(session)
    promo = next(i for i in resolved.items if i.candidate_id == "stage_1__T_A__row1")
    assert promo.status == ITEM_STATUS_PROMOTED
    assert promo.promoted_run_id == "P_001"
    assert resolved.promoted_count == 1


def test_promote_pending_records_error_when_row_param_absent_from_seed(tmp_path: Path):
    # A param the seed doesn't define must surface as a per-row error, not a
    # silently stamped template that would backtest with seed defaults.
    session = _seed_session(tmp_path)
    _write_stage_rows(session, "stage_1", [
        {
            "candidate_id": "stage_1__T_A__row1",
            "param_averageSlow": 150,
            "param_GhostParam": 999,   # not present in SEED_XML
        },
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "stage_1__T_A__row1"}])

    result = promote_pending(session)

    assert result.promoted == []
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err["candidate_id"] == "stage_1__T_A__row1"
    assert "GhostParam" in err["reason"]
    # No template stamped, shortlist not marked promoted.
    assert not (session.directory / "generated_templates" / PROMOTED_DIRNAME / "P_001.xml").exists()


def test_promote_pending_is_idempotent_on_reclick(tmp_path: Path):
    session = _seed_session(tmp_path)
    _write_stage_rows(session, "stage_1", [
        {"candidate_id": "cid_a", "param_averageSlow": 100, "param_averageFast": 2, "param_MaxStop": 50},
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])

    first = promote_pending(session)
    assert len(first.promoted) == 1
    second = promote_pending(session)
    assert second.promoted == []
    assert len(second.skipped) == 1
    skipped = second.skipped[0]
    assert skipped["candidate_id"] == "cid_a"
    assert skipped["promoted_run_id"] == "P_001"
    assert skipped["reason"] == "already_promoted"


def test_promote_pending_assigns_p_nnn_sequentially(tmp_path: Path):
    session = _seed_session(tmp_path)
    _write_stage_rows(session, "stage_1", [
        {"candidate_id": "cid_a", "param_averageSlow": 100, "param_averageFast": 2, "param_MaxStop": 50},
        {"candidate_id": "cid_b", "param_averageSlow": 200, "param_averageFast": 3, "param_MaxStop": 75},
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    promote_pending(session)

    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_b"}])
    second = promote_pending(session)

    assert [e.template_id for e in second.promoted] == ["P_002"]
    manifest = json.loads(
        (session.directory / "generated_templates" / PROMOTED_DIRNAME / PROMOTED_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    ids = sorted(t["template_id"] for t in manifest["templates"])
    assert ids == ["P_001", "P_002"]


def test_promote_pending_records_error_when_stage_row_missing(tmp_path: Path):
    session = _seed_session(tmp_path)
    # Shortlist points at stage_1 row that was never scored
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "ghost"}])

    result = promote_pending(session)
    assert result.promoted == []
    # Ghost rows resolve to "stale" first, then this guard fires; either
    # way the row never gets stamped and the operator sees the issue.
    assert result.errors
    err = result.errors[0]
    assert err["candidate_id"] == "ghost"


def test_promote_pending_skips_finalist_shortlist_items(tmp_path: Path):
    session = _seed_session(tmp_path)
    # Finalist shortlist item — promotion should ignore it entirely
    review = session.directory / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "evaluated_candidates.json").write_text(
        json.dumps({"rows": [{"run_id": "F_001", "profit_factor": 1.5}]}),
        encoding="utf-8",
    )
    add_items(session, [{"stage_id": "final_backtest", "candidate_id": "F_001"}])

    result = promote_pending(session)
    assert result.promoted == []
    assert result.errors == []
    assert result.skipped == []


def test_promote_pending_raises_when_seed_missing(tmp_path: Path):
    session = opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "does_not_exist.xml"),
    )
    save_recipe(session, _recipe_payload())
    with pytest.raises(PromotionError):
        promote_pending(session)


# ---------------------------------------------------------------------------
# Flask API round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from ta_foundation.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_promote_endpoint_returns_result_and_shortlist(tmp_path: Path, client):
    session = _seed_session(tmp_path)
    _write_stage_rows(session, "stage_1", [
        {"candidate_id": "cid_a", "param_averageSlow": 100, "param_averageFast": 2, "param_MaxStop": 50},
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])

    # dispatch=False: this test only covers the stamp + shortlist round-trip,
    # not the NT RunBatch. (The autouse bridge fixture already redirects the
    # command file away from C:\temp, so a stray dispatch can't reach NT, but
    # being explicit keeps the intent clear.)
    res = client.post(
        f"/api/optimizer/sessions/{session.id}/shortlist/promote",
        json={"dispatch": False},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["result"]["promoted_count"] == 1
    assert body["shortlist"]["promoted_count"] == 1


def test_api_promote_endpoint_404s_unknown_session(client):
    res = client.post("/api/optimizer/sessions/no_such/shortlist/promote", json={})
    assert res.status_code == 404
