"""Tests for the per-session shortlist (server-side persistence + API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_shortlist import (
    ITEM_STATUS_OK,
    ITEM_STATUS_PENDING,
    ITEM_STATUS_PROMOTED,
    ITEM_STATUS_STALE,
    SCHEMA_VERSION,
    SHORTLIST_FILENAME,
    ShortlistError,
    add_items,
    clear,
    load_shortlist,
    mark_promoted,
    remove_item,
    resolve_shortlist,
)


@pytest.fixture
def storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session() -> opt_session.OptimizerSession:
    return opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path="C:/fake/seed.xml",
        instrument="NQ 06-26",
    )


def _write_scored(session, stage_id, rows):
    stage_dir = session.directory / "parsed_results" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scored_rows.json").write_text(json.dumps(rows), encoding="utf-8")


def _write_evaluated(session, rows):
    review_dir = (
        session.directory / "deployment_package"
        / "final_backtest_handoff" / "final_backtest_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "evaluated_candidates.json").write_text(
        json.dumps({"rows": rows}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Raw persistence
# ---------------------------------------------------------------------------

def test_load_returns_empty_when_no_file(storage):
    session = _make_session()
    sl = load_shortlist(session)
    assert sl.items == ()


def test_add_persists_to_disk(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}], source="leaderboard")
    assert (session.directory / SHORTLIST_FILENAME).exists()

    sl = load_shortlist(session)
    assert len(sl.items) == 1
    assert sl.items[0].stage_id == "stage_1"
    assert sl.items[0].candidate_id == "cid_a"
    assert sl.items[0].source == "leaderboard"


def test_add_is_idempotent_on_stage_and_candidate(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}],
              source="another_source")
    sl = load_shortlist(session)
    assert len(sl.items) == 1
    # Original source/added_at survives — re-adds don't overwrite metadata.
    assert sl.items[0].source == "api"


def test_add_preserves_insertion_order(storage):
    session = _make_session()
    add_items(session, [
        {"stage_id": "stage_1", "candidate_id": "cid_a"},
        {"stage_id": "stage_2", "candidate_id": "cid_b"},
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_c"}])
    sl = load_shortlist(session)
    assert [(i.stage_id, i.candidate_id) for i in sl.items] == [
        ("stage_1", "cid_a"),
        ("stage_2", "cid_b"),
        ("stage_1", "cid_c"),
    ]


def test_remove_item(storage):
    session = _make_session()
    add_items(session, [
        {"stage_id": "stage_1", "candidate_id": "cid_a"},
        {"stage_id": "stage_1", "candidate_id": "cid_b"},
    ])
    remove_item(session, stage_id="stage_1", candidate_id="cid_a")
    sl = load_shortlist(session)
    assert len(sl.items) == 1
    assert sl.items[0].candidate_id == "cid_b"


def test_remove_unknown_item_is_noop(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    remove_item(session, stage_id="stage_1", candidate_id="missing")
    sl = load_shortlist(session)
    assert len(sl.items) == 1


def test_clear(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    clear(session)
    sl = load_shortlist(session)
    assert sl.items == ()


def test_add_rejects_empty_ids(storage):
    session = _make_session()
    add_items(session, [
        {"stage_id": "", "candidate_id": "cid_a"},          # skipped
        {"stage_id": "stage_1", "candidate_id": ""},        # skipped
        {"stage_id": "stage_1", "candidate_id": "cid_ok"},  # kept
    ])
    sl = load_shortlist(session)
    assert len(sl.items) == 1
    assert sl.items[0].candidate_id == "cid_ok"


def test_remove_requires_both_ids(storage):
    session = _make_session()
    with pytest.raises(ShortlistError):
        remove_item(session, stage_id="", candidate_id="cid_a")
    with pytest.raises(ShortlistError):
        remove_item(session, stage_id="stage_1", candidate_id="")


def test_load_dedupes_duplicate_entries_in_file(storage):
    """If shortlist.json gets hand-edited with dupes, load_shortlist collapses
    them rather than letting the operator see ghost entries."""
    session = _make_session()
    payload = {
        "schema_version": 1,
        "updated_at": "2026-05-30T00:00:00+00:00",
        "items": [
            {"stage_id": "stage_1", "candidate_id": "cid_a", "source": "x",
             "added_at": "2026-05-30T00:00:00+00:00"},
            {"stage_id": "stage_1", "candidate_id": "cid_a", "source": "y",
             "added_at": "2026-05-30T00:00:01+00:00"},
        ],
    }
    (session.directory / SHORTLIST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    sl = load_shortlist(session)
    assert len(sl.items) == 1


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_resolve_hydrates_stage_row_as_pending(storage):
    session = _make_session()
    _write_scored(session, "stage_1", [
        {
            "candidate_id": "cid_a",
            "template_id": "T_X",
            "profit_factor": 2.5,
            "total_net_profit": 1000,
            "max_drawdown": -100,
            "trades": 50,
        }
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    resolved = resolve_shortlist(session)
    assert resolved.count == 1
    item = resolved.items[0]
    assert item.status == ITEM_STATUS_PENDING
    assert item.is_finalist is False
    assert item.kpis["profit_factor"] == pytest.approx(2.5)
    assert item.kpis["trades"] == 50
    assert item.links["stage_results_url"].endswith("focus_stage=stage_1&focus_tab=results")


def test_resolve_hydrates_finalist_as_ok(storage):
    session = _make_session()
    _write_evaluated(session, [
        {"run_id": "F_001", "profit_factor": 1.5, "total_net_profit": 700,
         "max_drawdown": -120, "trades": 48,
         "template_id": "final_backtest__F_001"},
    ])
    add_items(session, [{"stage_id": "final_backtest", "candidate_id": "F_001"}])
    resolved = resolve_shortlist(session)
    item = resolved.items[0]
    assert item.status == ITEM_STATUS_OK
    assert item.is_finalist is True
    assert item.template_id == "final_backtest__F_001"
    assert item.kpis["profit_factor"] == pytest.approx(1.5)
    assert "report_url" in item.links
    assert resolved.finalist_candidate_ids == ["F_001"]


def test_resolve_flags_stale_items(storage):
    session = _make_session()
    # Stage row never written
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "ghost_cid"}])
    # Finalist never written
    add_items(session, [{"stage_id": "final_backtest", "candidate_id": "ghost_F"}])
    resolved = resolve_shortlist(session)
    assert resolved.count == 2
    assert resolved.stale_count == 2
    statuses = {i.candidate_id: i.status for i in resolved.items}
    assert statuses == {"ghost_cid": ITEM_STATUS_STALE, "ghost_F": ITEM_STATUS_STALE}
    assert resolved.finalist_candidate_ids == []  # stale items excluded


def test_resolve_counts_split_correctly(storage):
    session = _make_session()
    _write_scored(session, "stage_1", [{
        "candidate_id": "cid_live", "profit_factor": 2.0,
        "total_net_profit": 500, "max_drawdown": -50, "trades": 30,
    }])
    _write_evaluated(session, [
        {"run_id": "F_001", "profit_factor": 1.5, "total_net_profit": 600,
         "max_drawdown": -60, "trades": 40}
    ])
    add_items(session, [
        {"stage_id": "stage_1", "candidate_id": "cid_live"},
        {"stage_id": "stage_1", "candidate_id": "cid_ghost"},
        {"stage_id": "final_backtest", "candidate_id": "F_001"},
        {"stage_id": "final_backtest", "candidate_id": "F_GHOST"},
    ])
    resolved = resolve_shortlist(session)
    assert resolved.count == 4
    assert resolved.pending_count == 1
    assert resolved.stale_count == 2
    assert resolved.finalist_candidate_ids == ["F_001"]


# ---------------------------------------------------------------------------
# Flask API round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def client(storage):
    from ta_foundation.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_via_session_module() -> str:
    """Make a session and return its id (the API client doesn't need to
    drive the create flow; we just need a session dir to point at)."""
    session = _make_session()
    return session.id


def test_api_get_empty_shortlist(client):
    sid = _create_via_session_module()
    res = client.get(f"/api/optimizer/sessions/{sid}/shortlist")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 0
    assert body["items"] == []


def test_api_add_and_remove_round_trip(client):
    sid = _create_via_session_module()
    # Add via POST
    res = client.post(
        f"/api/optimizer/sessions/{sid}/shortlist",
        json={
            "items": [
                {"stage_id": "stage_1", "candidate_id": "cid_a"},
                {"stage_id": "stage_2", "candidate_id": "cid_b"},
            ],
            "source": "leaderboard",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 2

    # Remove one via DELETE
    res = client.delete(f"/api/optimizer/sessions/{sid}/shortlist/stage_1/cid_a")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 1
    assert body["items"][0]["candidate_id"] == "cid_b"

    # Clear via DELETE
    res = client.delete(f"/api/optimizer/sessions/{sid}/shortlist")
    assert res.status_code == 200
    assert res.get_json()["count"] == 0


def test_api_post_rejects_empty_items(client):
    sid = _create_via_session_module()
    res = client.post(
        f"/api/optimizer/sessions/{sid}/shortlist",
        json={"items": []},
    )
    assert res.status_code == 400


def test_api_404_for_unknown_session(client):
    res = client.get("/api/optimizer/sessions/no_such_session/shortlist")
    assert res.status_code == 404


def test_api_get_hydrates_kpis(client):
    sid = _create_via_session_module()
    session = opt_session.get_session(sid)
    _write_scored(session, "stage_1", [
        {"candidate_id": "cid_a", "profit_factor": 2.5,
         "total_net_profit": 1000, "max_drawdown": -100, "trades": 50},
    ])
    client.post(
        f"/api/optimizer/sessions/{sid}/shortlist",
        json={"items": [{"stage_id": "stage_1", "candidate_id": "cid_a"}]},
    )
    res = client.get(f"/api/optimizer/sessions/{sid}/shortlist")
    body = res.get_json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["status"] == ITEM_STATUS_PENDING
    assert item["kpis"]["profit_factor"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Schema v2 + promotion fields
# ---------------------------------------------------------------------------

def test_load_v1_shortlist_file_is_still_readable(storage):
    """Old sessions wrote schema_version=1 without promoted_* fields. Loading
    them must succeed and report status=pending (not promoted)."""
    session = _make_session()
    payload = {
        "schema_version": 1,
        "updated_at": "2026-05-30T00:00:00+00:00",
        "items": [
            {"stage_id": "stage_1", "candidate_id": "cid_a", "source": "leaderboard",
             "added_at": "2026-05-30T00:00:00+00:00"},
        ],
    }
    (session.directory / SHORTLIST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    sl = load_shortlist(session)
    assert len(sl.items) == 1
    assert sl.items[0].promoted_run_id is None
    assert sl.items[0].promoted_at is None


def test_save_writes_schema_v2(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    written = json.loads((session.directory / SHORTLIST_FILENAME).read_text(encoding="utf-8"))
    assert written["schema_version"] == SCHEMA_VERSION == 2
    # Round-trip of plain item should NOT include promoted_* keys
    assert "promoted_run_id" not in written["items"][0]
    assert "promoted_at" not in written["items"][0]


def test_mark_promoted_updates_only_target_item(storage):
    session = _make_session()
    add_items(session, [
        {"stage_id": "stage_1", "candidate_id": "cid_a"},
        {"stage_id": "stage_1", "candidate_id": "cid_b"},
    ])
    mark_promoted(
        session, stage_id="stage_1", candidate_id="cid_a",
        promoted_run_id="P_001", promoted_at="2026-05-30T01:00:00+00:00",
    )
    sl = load_shortlist(session)
    a = next(i for i in sl.items if i.candidate_id == "cid_a")
    b = next(i for i in sl.items if i.candidate_id == "cid_b")
    assert a.promoted_run_id == "P_001"
    assert a.promoted_at == "2026-05-30T01:00:00+00:00"
    assert b.promoted_run_id is None


def test_mark_promoted_requires_run_id(storage):
    session = _make_session()
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    with pytest.raises(ShortlistError):
        mark_promoted(session, stage_id="stage_1", candidate_id="cid_a", promoted_run_id="")


def test_resolved_status_flips_to_promoted_when_marked(storage):
    session = _make_session()
    _write_scored(session, "stage_1", [
        {"candidate_id": "cid_a", "profit_factor": 2.0, "total_net_profit": 500,
         "max_drawdown": -50, "trades": 30},
    ])
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "cid_a"}])
    mark_promoted(
        session, stage_id="stage_1", candidate_id="cid_a",
        promoted_run_id="P_001",
    )
    resolved = resolve_shortlist(session)
    item = resolved.items[0]
    assert item.status == ITEM_STATUS_PROMOTED
    assert item.promoted_run_id == "P_001"
    assert resolved.promoted_count == 1
    assert resolved.pending_count == 0
    assert "promoted_template_url" in item.links
