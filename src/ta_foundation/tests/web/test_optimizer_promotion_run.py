"""Tests for the NinjaTrader RunBatch dispatch + completion pipeline
for promoted shortlist rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_promotion import (
    PROMOTED_DIRNAME,
    PROMOTED_MANIFEST_FILENAME,
)
from ta_foundation.web import optimizer_promotion_run as run_mod
from ta_foundation.web.optimizer_promotion_run import (
    PromotionRunError,
    advance_promoted_run,
    cancel_promoted_run,
    load_promoted_run,
    start_promoted_run,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session(*, seed_path: Path | None = None) -> opt_session.OptimizerSession:
    """Create a session with an on-disk seed XML so promote_pending's
    seed-existence guard doesn't 400 the endpoint tests."""
    return opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path) if seed_path else "C:/fake/seed.xml",
        instrument="NQ 06-26",
    )


def _make_session_with_real_seed(tmp_path: Path) -> opt_session.OptimizerSession:
    tmp_path.mkdir(parents=True, exist_ok=True)
    seed = tmp_path / "seed.xml"
    seed.write_text("<StrategyTemplate/>", encoding="utf-8")
    return _make_session(seed_path=seed)


def _save_minimal_recipe(session) -> None:
    """promote_pending requires a recipe; the endpoint tests don't actually
    promote anything new, but the route still calls promote_pending first."""
    from ta_foundation.web.optimizer_recipe import save_recipe
    save_recipe(session, {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": "rec_test",
        "recipe_name": "test",
        "strategy_id": "FakeStrategy",
        "base_matrix": [],
        "stages": [
            {"stage_id": "stage_1", "stage_type": "optimizer"},
            {"stage_id": "final_backtest", "stage_type": "fixed_backtest",
             "from": "stage_1.selected_rows"},
        ],
    })


def _stamp_promoted_templates(session, template_ids: list[str]) -> None:
    """Pretend promote_pending already ran: write the manifest + XMLs."""
    promoted_dir = session.directory / "generated_templates" / PROMOTED_DIRNAME
    promoted_dir.mkdir(parents=True, exist_ok=True)
    templates = []
    for tid in template_ids:
        xml = promoted_dir / f"{tid}.xml"
        xml.write_text("<StrategyTemplate/>", encoding="utf-8")
        templates.append({
            "template_id": tid,
            "stage_id": PROMOTED_DIRNAME,
            "source_stage_id": "stage_1",
            "source_candidate_id": f"stage_1__T__{tid}",
            "path": str(xml),
            "combination_count": 1,
        })
    (promoted_dir / PROMOTED_MANIFEST_FILENAME).write_text(
        json.dumps({
            "schema_version": 1,
            "stage_id": PROMOTED_DIRNAME,
            "stage_type": "promoted_backtest",
            "template_count": len(templates),
            "templates": templates,
        }),
        encoding="utf-8",
    )


def _write_summary_for(session, template_id: str) -> None:
    out = session.directory / "nt_output" / PROMOTED_DIRNAME / template_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "Summary.csv").write_text("col\nval\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# start_promoted_run — IPC dispatch
# ---------------------------------------------------------------------------

def test_start_promoted_run_writes_command_file(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001", "P_002"])
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"

    record = start_promoted_run(session, command_file=cmd, status_file=status)

    assert cmd.exists()
    payload = json.loads(cmd.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["runId"] == record.run_id
    assert payload["sourceFolder"].endswith(str(Path("generated_templates") / "promoted"))
    assert payload["destFolder"].endswith(str(Path("nt_output") / "promoted"))
    assert payload["closeTempTabs"] is True
    assert payload["instrument"] == "NQ 06-26"

    assert record.state == "requested"
    assert record.total_templates == 2
    assert (session.directory / "promoted_run.json").exists()


def test_start_promoted_run_raises_when_no_templates():
    session = _make_session()
    with pytest.raises(PromotionRunError):
        start_promoted_run(session)


def _save_recipe_run(session, *, state: str):
    from ta_foundation.web.optimizer_recipe_runner import (
        RecipeStageRunRecord,
        save_recipe_run,
    )
    save_recipe_run(session, RecipeStageRunRecord(
        run_id="recipe_stage_1_20260530_000000",
        stage_id="stage_1",
        state=state,
        started_at="2026-05-30T00:00:00",
    ))


def test_start_promoted_run_refuses_while_recipe_run_active(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    _save_recipe_run(session, state="running")
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"

    with pytest.raises(PromotionRunError, match="recipe stage run is active"):
        start_promoted_run(session, command_file=cmd, status_file=status)
    # Guard fires before the command file is written.
    assert not cmd.exists()


def test_start_promoted_run_proceeds_when_recipe_run_terminal(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    _save_recipe_run(session, state="completed")
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"

    record = start_promoted_run(session, command_file=cmd, status_file=status)
    assert record.state == "requested"
    assert cmd.exists()


def _write_foreign_command(cmd: Path, run_id: str) -> None:
    cmd.write_text(json.dumps({"action": "RunBatch", "runId": run_id}), encoding="utf-8")


def test_start_promoted_run_refuses_when_bridge_owned_by_foreign_run(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"
    # A different session's run currently owns the bridge, still running.
    _write_foreign_command(cmd, "recipe_stage_1_other_session")
    status.write_text(
        json.dumps({"runId": "recipe_stage_1_other_session", "state": "running"}),
        encoding="utf-8",
    )

    with pytest.raises(PromotionRunError, match="bridge is busy"):
        start_promoted_run(session, command_file=cmd, status_file=status)
    # The foreign command is left untouched.
    assert json.loads(cmd.read_text())["runId"] == "recipe_stage_1_other_session"


def test_start_promoted_run_reclaims_bridge_when_foreign_run_terminal(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"
    _write_foreign_command(cmd, "recipe_stage_1_other_session")
    status.write_text(
        json.dumps({"runId": "recipe_stage_1_other_session", "state": "finished"}),
        encoding="utf-8",
    )

    record = start_promoted_run(session, command_file=cmd, status_file=status)
    assert record.state == "requested"
    # Our command replaced the (terminal) foreign one.
    assert json.loads(cmd.read_text())["runId"] == record.run_id


def test_start_promoted_run_clears_stale_status(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"
    status.write_text("{}", encoding="utf-8")

    start_promoted_run(session, command_file=cmd, status_file=status)
    assert not status.exists(), "stale status file should have been removed"


def test_start_promoted_run_wipes_previous_nt_output(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    stale_dir = session.directory / "nt_output" / PROMOTED_DIRNAME / "P_OLD"
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale.csv").write_text("x", encoding="utf-8")

    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)
    assert not stale_dir.exists()


# ---------------------------------------------------------------------------
# advance_promoted_run — completion + failure + cancellation
# ---------------------------------------------------------------------------

def test_advance_returns_none_when_no_run_active():
    session = _make_session()
    assert advance_promoted_run(session) is None


def test_advance_transitions_requested_to_running_on_first_summary(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001", "P_002"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)

    _write_summary_for(session, "P_001")
    record = advance_promoted_run(session)
    assert record.state == "running"
    assert record.completed_templates == 1


def test_advance_runs_post_pipeline_when_all_summaries_present(monkeypatch, tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001", "P_002"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)
    _write_summary_for(session, "P_001")
    _write_summary_for(session, "P_002")

    calls: list[str] = []

    class FakeResults:
        review_dir = "/fake/review"
        evaluated_candidates_path = "/fake/review/evaluated_candidates.json"

    def fake_load_promoted_results(session, **kwargs):
        calls.append("load_promoted_results")
        return FakeResults()

    class FakeBatch:
        per_candidate = [
            type("R", (), {"run_id": "P_001", "html_path": "/x/P_001.html"})(),
            type("R", (), {"run_id": "P_002", "html_path": "/x/P_002.html"})(),
            type("R", (), {"run_id": "F_001", "html_path": "/x/F_001.html"})(),
        ]

    def fake_build_all(session, **kwargs):
        calls.append("build_all_candidate_reports")
        return FakeBatch()

    # Patch at the import sites used inside _run_post_completion_pipeline.
    import ta_foundation.web.optimizer_promotion_results as pr_mod
    import ta_foundation.web.optimizer_candidate_report as cr_mod
    monkeypatch.setattr(pr_mod, "load_promoted_results", fake_load_promoted_results)
    monkeypatch.setattr(cr_mod, "build_all_candidate_reports", fake_build_all)

    record = advance_promoted_run(session)
    assert calls == ["load_promoted_results", "build_all_candidate_reports"]
    assert record.state == "complete"
    assert record.completed_templates == 2
    assert record.report_count == 2  # only P_NNN are counted
    assert record.review_dir == "/fake/review"
    assert record.finished_at


def test_advance_marks_failed_on_addon_error_state(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    record = start_promoted_run(session, command_file=cmd, status_file=status)
    status.write_text(json.dumps({
        "runId": record.run_id,
        "state": "failed",
        "error": "AddOn lost connection",
    }), encoding="utf-8")

    updated = advance_promoted_run(session)
    assert updated.state == "failed"
    assert "lost connection" in (updated.last_error or "")
    assert updated.finished_at


def test_advance_marks_cancelled_when_addon_says_cancelled(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    record = start_promoted_run(session, command_file=cmd, status_file=status)
    status.write_text(json.dumps({
        "runId": record.run_id,
        "state": "cancelled",
    }), encoding="utf-8")

    updated = advance_promoted_run(session)
    assert updated.state == "cancelled"


def test_advance_is_idempotent_after_terminal_state(monkeypatch, tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)
    _write_summary_for(session, "P_001")

    import ta_foundation.web.optimizer_promotion_results as pr_mod
    import ta_foundation.web.optimizer_candidate_report as cr_mod
    monkeypatch.setattr(pr_mod, "load_promoted_results",
                        lambda s, **kw: type("X", (), {"review_dir": None, "evaluated_candidates_path": None})())
    monkeypatch.setattr(cr_mod, "build_all_candidate_reports",
                        lambda s, **kw: type("Y", (), {"per_candidate": []})())

    first = advance_promoted_run(session)
    assert first.state == "complete"
    second = advance_promoted_run(session)
    assert second.state == "complete"
    assert second.finished_at == first.finished_at


def test_advance_status_for_other_runid_is_ignored(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)
    status.write_text(json.dumps({
        "runId": "some_other_run", "state": "failed", "error": "wrong run",
    }), encoding="utf-8")

    record = advance_promoted_run(session)
    # Did not fail — status belongs to another run.
    assert record.state in {"requested", "running"}
    assert record.last_error is None


# ---------------------------------------------------------------------------
# cancel_promoted_run
# ---------------------------------------------------------------------------

def test_cancel_removes_command_file_matching_run(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    record = start_promoted_run(session, command_file=cmd, status_file=status)
    assert cmd.exists()

    updated = cancel_promoted_run(session)
    assert updated.state == "cancelled"
    assert not cmd.exists()


def test_cancel_leaves_command_file_owned_by_other_run(tmp_path: Path):
    session = _make_session()
    _stamp_promoted_templates(session, ["P_001"])
    cmd = tmp_path / "cmd.json"
    status = tmp_path / "status.json"
    start_promoted_run(session, command_file=cmd, status_file=status)
    cmd.write_text(json.dumps({"action": "RunBatch", "runId": "someone_else"}), encoding="utf-8")

    cancel_promoted_run(session)
    assert cmd.exists()
    payload = json.loads(cmd.read_text(encoding="utf-8"))
    assert payload["runId"] == "someone_else"


def test_cancel_with_no_active_run_returns_none():
    session = _make_session()
    assert cancel_promoted_run(session) is None


# ---------------------------------------------------------------------------
# Flask endpoints round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from ta_foundation.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_promote_endpoint_dispatches_run_by_default(monkeypatch, client, tmp_path):
    """One-click promote calls promote_pending AND start_promoted_run."""
    session = _make_session_with_real_seed(tmp_path / "seed")
    _save_minimal_recipe(session)
    _stamp_promoted_templates(session, ["P_001"])
    # Pre-mark the manifest entry on the shortlist so promote_pending is a no-op
    # but start_promoted_run still has templates to fire.
    from ta_foundation.web.optimizer_shortlist import add_items, mark_promoted
    add_items(session, [{"stage_id": "stage_1", "candidate_id": "c"}])
    mark_promoted(session, stage_id="stage_1", candidate_id="c", promoted_run_id="P_001")

    # Redirect IPC at a temp path so the test doesn't touch C:\temp.
    tmp_cmd = session.directory / "nt8_command.json"
    tmp_status = session.directory / "nt8_status.json"
    import ta_foundation.web.optimizer_promotion_run as mod
    monkeypatch.setattr(mod, "DEFAULT_COMMAND_FILE", tmp_cmd)
    monkeypatch.setattr(mod, "DEFAULT_STATUS_FILE", tmp_status)

    res = client.post(f"/api/optimizer/sessions/{session.id}/shortlist/promote", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert body["run"] is not None
    assert body["run"]["state"] == "requested"
    assert body["run"]["total_templates"] == 1
    assert tmp_cmd.exists()


def test_promote_endpoint_skips_dispatch_when_requested(monkeypatch, client, tmp_path):
    session = _make_session_with_real_seed(tmp_path / "seed")
    _save_minimal_recipe(session)
    _stamp_promoted_templates(session, ["P_001"])
    res = client.post(
        f"/api/optimizer/sessions/{session.id}/shortlist/promote",
        json={"dispatch": False},
    )
    body = res.get_json()
    assert body["run"] is None


def test_promote_status_endpoint_advances_state(monkeypatch, client):
    session = _make_session()
    _save_minimal_recipe(session)
    _stamp_promoted_templates(session, ["P_001"])
    tmp_cmd = session.directory / "nt8_command.json"
    tmp_status = session.directory / "nt8_status.json"
    import ta_foundation.web.optimizer_promotion_run as mod
    monkeypatch.setattr(mod, "DEFAULT_COMMAND_FILE", tmp_cmd)
    monkeypatch.setattr(mod, "DEFAULT_STATUS_FILE", tmp_status)
    start_promoted_run(session)

    res = client.get(f"/api/optimizer/sessions/{session.id}/shortlist/promote/status")
    assert res.status_code == 200
    assert res.get_json()["run"]["state"] in {"requested", "running"}


def test_promote_cancel_endpoint(monkeypatch, client):
    session = _make_session()
    _save_minimal_recipe(session)
    _stamp_promoted_templates(session, ["P_001"])
    tmp_cmd = session.directory / "nt8_command.json"
    tmp_status = session.directory / "nt8_status.json"
    import ta_foundation.web.optimizer_promotion_run as mod
    monkeypatch.setattr(mod, "DEFAULT_COMMAND_FILE", tmp_cmd)
    monkeypatch.setattr(mod, "DEFAULT_STATUS_FILE", tmp_status)
    start_promoted_run(session)

    res = client.post(f"/api/optimizer/sessions/{session.id}/shortlist/promote/cancel")
    body = res.get_json()
    assert body["run"]["state"] == "cancelled"
    assert not tmp_cmd.exists()


def test_promote_status_404_unknown_session(client):
    res = client.get("/api/optimizer/sessions/no_such/shortlist/promote/status")
    assert res.status_code == 404
