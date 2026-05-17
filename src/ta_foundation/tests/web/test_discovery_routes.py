from __future__ import annotations

"""Flask test-client coverage for the /api/discovery/sessions/* routes."""

import json
import time
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web.discovery_session import set_storage_root
from ta_foundation.web.discovery_summary import write_summary, build_summary
from ta_foundation.web.jobs import JobManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Stand-in for subprocess.Popen used by the JobManager tests."""

    returncode = 0

    def communicate(self):
        return ("[ta_foundation] discovery dispatched\n", None)


@pytest.fixture
def isolated_sessions(tmp_path: Path):
    set_storage_root(tmp_path / "sessions")
    yield tmp_path / "sessions"
    set_storage_root(None)


@pytest.fixture
def client(tmp_path: Path, isolated_sessions):
    # Replace the module-global JobManager with one that doesn't actually
    # spawn a subprocess. create_app() leaves it alone if non-None.
    web_app._job_manager = JobManager(
        popen_factory=lambda *args, **kwargs: _FakeProcess(),
        cwd=tmp_path,
    )
    flask_app = web_app.create_app()
    flask_app.testing = True
    with flask_app.test_client() as test_client:
        yield test_client
    web_app._job_manager = None


def _wait_for_job(manager: JobManager, job_id: str, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = manager.get(job_id)
        if rec is not None and rec.finished_at:
            return
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# Sessions CRUD
# ---------------------------------------------------------------------------


def test_create_then_get_session(client):
    resp = client.post(
        "/api/discovery/sessions",
        json={
            "label": "NQ exploration",
            "instrument_symbol": "NQ",
            "context": {
                "input_folder": "/tmp/in",
                "output_folder": "/tmp/out",
                "market_data_folder": "/tmp/md",
            },
            "current_stage": "01_quick_scan",
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    sid = body["session"]["session_id"]

    got = client.get(f"/api/discovery/sessions/{sid}")
    assert got.status_code == 200
    payload = got.get_json()
    assert payload["session"]["label"] == "NQ exploration"
    assert payload["session"]["instrument"]["symbol"] == "NQ"
    assert payload["session"]["current_stage"] == "01_quick_scan"
    assert payload["runs"] == []
    assert payload["promotions"] == []


def test_list_sessions_returns_summaries(client):
    client.post("/api/discovery/sessions", json={"label": "A"})
    client.post("/api/discovery/sessions", json={"label": "B"})
    resp = client.get("/api/discovery/sessions")
    assert resp.status_code == 200
    sessions = resp.get_json()["sessions"]
    labels = sorted(s["label"] for s in sessions)
    assert labels == ["A", "B"]


def test_patch_session_updates_label_instrument_and_context(client):
    sid = client.post("/api/discovery/sessions", json={"label": "old"}).get_json()["session"]["session_id"]

    resp = client.patch(
        f"/api/discovery/sessions/{sid}",
        json={
            "label": "new",
            "instrument_symbol": "ES",
            "current_stage": "02_candle_patterns",
            "context": {"output_folder": "/tmp/elsewhere"},
        },
    )
    assert resp.status_code == 200
    doc = resp.get_json()["session"]
    assert doc["label"] == "new"
    assert doc["instrument"]["symbol"] == "ES"
    assert doc["current_stage"] == "02_candle_patterns"
    assert doc["context"]["output_folder"] == "/tmp/elsewhere"


def test_patch_session_rejects_unknown_instrument(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.patch(f"/api/discovery/sessions/{sid}", json={"instrument_symbol": "ZZZ"})
    assert resp.status_code == 400


def test_patch_unknown_session_returns_404(client):
    assert client.patch("/api/discovery/sessions/nope", json={}).status_code == 404


def test_delete_session(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.delete(f"/api/discovery/sessions/{sid}")
    assert resp.status_code == 200
    # Second delete is idempotent-ish: 404 because the directory is gone.
    assert client.delete(f"/api/discovery/sessions/{sid}").status_code == 404


def test_set_form_values_persists(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.put(
        f"/api/discovery/sessions/{sid}/stages/01_quick_scan/form",
        json={"values": {"min_trades": 25, "timeframes": [1, 5]}},
    )
    assert resp.status_code == 200
    doc = resp.get_json()["session"]
    assert doc["stage_form_values"]["01_quick_scan"]["min_trades"] == 25


def test_set_form_values_rejects_unknown_stage(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.put(
        f"/api/discovery/sessions/{sid}/stages/bogus/form",
        json={"values": {}},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Run dispatch + summary
# ---------------------------------------------------------------------------


def test_run_dispatch_writes_yaml_and_records_run(client, tmp_path: Path):
    in_dir = tmp_path / "in"
    md_dir = tmp_path / "md"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    md_dir.mkdir()
    out_dir.mkdir()

    sid = client.post(
        "/api/discovery/sessions",
        json={
            "context": {
                "input_folder": str(in_dir),
                "output_folder": str(out_dir),
                "market_data_folder": str(md_dir),
            },
        },
    ).get_json()["session"]["session_id"]

    resp = client.post(
        f"/api/discovery/sessions/{sid}/runs",
        json={"stage_id": "01_quick_scan"},
    )
    assert resp.status_code == 202
    payload = resp.get_json()
    assert payload["run"]["stage_id"] == "01_quick_scan"
    assert payload["job"]["kind"] == "discovery.01_quick_scan"

    yaml_path = Path(payload["run"]["yaml_path"])
    assert yaml_path.exists()
    text = yaml_path.read_text(encoding="utf-8")
    assert "candle_discovery" in text

    # Run shows up in the listing with live status from the JobManager
    _wait_for_job(web_app._job_manager, payload["job"]["id"])
    listing = client.get(f"/api/discovery/sessions/{sid}/runs").get_json()["runs"]
    assert len(listing) == 1
    assert listing[0]["status"] == "succeeded"


def test_run_dispatch_requires_stage_id(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.post(f"/api/discovery/sessions/{sid}/runs", json={})
    assert resp.status_code == 400


def test_run_dispatch_validation_failure_short_circuits(client):
    # No folders configured anywhere → the builder validator returns ok=False
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.post(
        f"/api/discovery/sessions/{sid}/runs",
        json={"stage_id": "01_quick_scan"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "validation" in body
    assert body["validation"]["ok"] is False


def test_run_summary_returns_sidecar_when_present(client, tmp_path: Path):
    in_dir = tmp_path / "in"
    md_dir = tmp_path / "md"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    md_dir.mkdir()
    out_dir.mkdir()

    sid = client.post(
        "/api/discovery/sessions",
        json={
            "context": {
                "input_folder": str(in_dir),
                "output_folder": str(out_dir),
                "market_data_folder": str(md_dir),
            },
        },
    ).get_json()["session"]["session_id"]

    dispatched = client.post(
        f"/api/discovery/sessions/{sid}/runs",
        json={"stage_id": "01_quick_scan"},
    ).get_json()
    job_id = dispatched["job"]["id"]
    summary_path = Path(dispatched["run"]["summary_json_path"])

    # Stand in for what the CLI would have written
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={
            "candle": [
                {
                    "tf": 1,
                    "pattern_id": "large_body",
                    "params": {"body_multiplier": 2.0, "tp_ticks": 20, "sl_ticks": 10},
                    "direction_mode": "long",
                    "entry_timing": "next_open",
                    "outcome_mode": "ticks",
                    "n_trades": 40,
                    "metrics": {"profit_factor": 1.4, "win_rate": 0.55, "n_trades": 40},
                    "is_oos_degradation": 0.08,
                }
            ]
        },
        report_html_path=str(summary_path.with_name("01_quick_scan.html")),
    )
    write_summary(summary, summary_path)

    resp = client.get(f"/api/discovery/sessions/{sid}/runs/{job_id}/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["stage"]["id"] == "01_quick_scan"
    assert data["summary"]["rankings"][0]["family"] == "candle"


def test_run_summary_returns_404_before_sidecar_is_written(client, tmp_path: Path):
    in_dir = tmp_path / "in"
    md_dir = tmp_path / "md"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    md_dir.mkdir()
    out_dir.mkdir()

    sid = client.post(
        "/api/discovery/sessions",
        json={
            "context": {
                "input_folder": str(in_dir),
                "output_folder": str(out_dir),
                "market_data_folder": str(md_dir),
            },
        },
    ).get_json()["session"]["session_id"]

    dispatched = client.post(
        f"/api/discovery/sessions/{sid}/runs",
        json={"stage_id": "01_quick_scan"},
    ).get_json()
    job_id = dispatched["job"]["id"]

    resp = client.get(f"/api/discovery/sessions/{sid}/runs/{job_id}/summary")
    assert resp.status_code == 404


def test_run_summary_falls_back_to_legacy_filename(client, tmp_path: Path):
    """Reports rendered before the per-stage rename used a shared
    discovery_summary.json. The summary endpoint must still find them."""
    in_dir = tmp_path / "in"
    md_dir = tmp_path / "md"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    md_dir.mkdir()
    out_dir.mkdir()

    sid = client.post(
        "/api/discovery/sessions",
        json={
            "context": {
                "input_folder": str(in_dir),
                "output_folder": str(out_dir),
                "market_data_folder": str(md_dir),
            },
        },
    ).get_json()["session"]["session_id"]

    dispatched = client.post(
        f"/api/discovery/sessions/{sid}/runs",
        json={"stage_id": "01_quick_scan"},
    ).get_json()
    job_id = dispatched["job"]["id"]
    report_html_path = Path(dispatched["run"]["report_html_path"])

    # Pre-rename runs wrote here, not to <stem>_summary.json.
    legacy_path = report_html_path.with_name("discovery_summary.json")
    summary = build_summary(
        stage_id="01_quick_scan",
        instrument_symbol="NQ",
        raw_results={},
        report_html_path=str(report_html_path),
    )
    write_summary(summary, legacy_path)

    resp = client.get(f"/api/discovery/sessions/{sid}/runs/{job_id}/summary")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["summary"]["stage"]["id"] == "01_quick_scan"


# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------


def test_promotion_append_and_list(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.post(
        f"/api/discovery/sessions/{sid}/promotions",
        json={
            "from_stage": "01_quick_scan",
            "to_stage": "02_candle_patterns",
            "rank": 2,
            "yaml_overrides": {"candle_discovery": {"enabled": True}},
            "explain": "Strong PF on candle family",
        },
    )
    assert resp.status_code == 201
    promotion = resp.get_json()["promotion"]
    assert promotion["from_stage"] == "01_quick_scan"
    assert promotion["yaml_overrides"]["candle_discovery"]["enabled"] is True

    listing = client.get(f"/api/discovery/sessions/{sid}/promotions").get_json()
    assert len(listing["promotions"]) == 1


def test_promotion_requires_from_and_to_stage(client):
    sid = client.post("/api/discovery/sessions", json={}).get_json()["session"]["session_id"]
    resp = client.post(
        f"/api/discovery/sessions/{sid}/promotions",
        json={"from_stage": "", "to_stage": ""},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /discovery page route + cookie session resolution
# ---------------------------------------------------------------------------


def _cookie_value(response, name: str) -> str | None:
    """Pull a single cookie value out of a Flask test response."""
    headers = response.headers.getlist("Set-Cookie")
    for raw in headers:
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def test_discovery_page_creates_session_when_cookie_missing(client):
    resp = client.get("/discovery")
    assert resp.status_code == 200
    sid = _cookie_value(resp, "ta_discovery_session_id")
    assert sid is not None and sid.startswith("ses_")
    # Page injects the session id so JS can hit the API immediately
    assert sid in resp.get_data(as_text=True)


def test_discovery_page_reuses_session_from_cookie(client):
    # First visit creates the session
    first = client.get("/discovery")
    sid = _cookie_value(first, "ta_discovery_session_id")
    assert sid is not None

    # Send the cookie back; the route should reuse the same id, no new cookie
    client.set_cookie("ta_discovery_session_id", sid, domain="localhost")
    second = client.get("/discovery")
    assert second.status_code == 200
    # No Set-Cookie header on the reuse path
    assert _cookie_value(second, "ta_discovery_session_id") is None
    assert sid in second.get_data(as_text=True)


def test_discovery_page_recovers_from_stale_cookie(client):
    # Visit, capture the session id, delete the session, visit again.
    first = client.get("/discovery")
    sid = _cookie_value(first, "ta_discovery_session_id")
    assert sid is not None

    delete_resp = client.delete(f"/api/discovery/sessions/{sid}")
    assert delete_resp.status_code == 200

    client.set_cookie("ta_discovery_session_id", sid, domain="localhost")
    third = client.get("/discovery")
    new_sid = _cookie_value(third, "ta_discovery_session_id")
    # A fresh session should have been created and the cookie reset
    assert new_sid is not None
    assert new_sid != sid


def test_discovery_page_renders_template_chrome(client):
    resp = client.get("/discovery")
    text = resp.get_data(as_text=True)
    assert "Discovery" in text
    assert "Funnel" in text
    assert "Event studies" in text


def test_discovery_page_includes_help_surfaces(client):
    """Tour/Glossary buttons and help.js must be wired into the page."""
    resp = client.get("/discovery")
    text = resp.get_data(as_text=True)
    assert 'id="open-tour-btn"' in text
    assert 'id="open-glossary-btn"' in text
    assert "/static/discovery/help.js" in text


def test_discovery_help_static_assets_served(client):
    """The new help.js asset must be reachable through the static handler."""
    resp = client.get("/static/discovery/help.js")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Sanity-check the public surface the page wires against.
    assert "DiscoveryHelp" in body
    assert "Onboarding" in body
    assert "GlossaryPanel" in body
    assert "StuckHelp" in body


# ---------------------------------------------------------------------------
# Sessions index page (Step 15)
# ---------------------------------------------------------------------------


def test_sessions_index_lists_existing_sessions(client):
    """The sessions index page should render every session as a row."""
    # Create two sessions through the API so they land on disk.
    a = client.post("/api/discovery/sessions", json={"label": "alpha", "instrument_symbol": "NQ"}).get_json()
    b = client.post("/api/discovery/sessions", json={"label": "beta",  "instrument_symbol": "ES"}).get_json()

    resp = client.get("/discovery/sessions")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Saved Sessions" in text
    assert a["session"]["session_id"] in text
    assert b["session"]["session_id"] in text
    assert "alpha" in text
    assert "beta" in text


def test_sessions_index_handles_empty_state(client):
    """With no sessions on disk, the index page shows the empty-state CTA."""
    resp = client.get("/discovery/sessions")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "No sessions yet." in text


def test_session_resume_sets_cookie_and_redirects(client):
    """Visiting the resume route should set the session cookie and 302 to /discovery."""
    created = client.post("/api/discovery/sessions", json={"label": "to-resume"}).get_json()
    sid = created["session"]["session_id"]

    resp = client.get(f"/discovery/sessions/{sid}/resume", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/discovery")
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "ta_discovery_session_id=" in set_cookie
    assert sid in set_cookie


def test_session_resume_with_unknown_id_does_not_set_cookie(client):
    """Resuming an unknown session id falls through without rotating the cookie."""
    resp = client.get("/discovery/sessions/ses_doesnotexist/resume", follow_redirects=False)
    assert resp.status_code == 302
    # No Set-Cookie for the discovery session — endpoint just redirects.
    assert "ta_discovery_session_id=" not in resp.headers.get("Set-Cookie", "")


# ---------------------------------------------------------------------------
# Expert mode wiring (Step 16)
# ---------------------------------------------------------------------------


def test_discovery_page_includes_expert_mode_toggle(client):
    """The Expert toggle and its localStorage wiring must appear in the page."""
    resp = client.get("/discovery")
    text = resp.get_data(as_text=True)
    assert 'id="expert-mode-toggle"' in text
    assert "ta_discovery_expert_mode_v1" in text
    assert "DiscoveryExpertMode" in text


def test_discovery_form_static_includes_expert_section(client):
    """The discovery_form.js must implement the Expert overrides JSON section."""
    resp = client.get("/static/discovery/discovery_form.js")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Expert overrides" in body
    assert "getExpertOverrides" in body
    assert "expert_overrides_json" in body


# ---------------------------------------------------------------------------
# LCE sibling page (Step 17)
# ---------------------------------------------------------------------------


def test_lce_page_renders_with_lce_only_flag(client):
    """The /discovery/lce route should render the discovery template in LCE mode."""
    resp = client.get("/discovery/lce")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # The bootstrap injects a JS constant the page checks at init time.
    assert "const LCE_ONLY = true" in text
    # Header should still expose the navigation siblings.
    assert "/discovery/sessions" in text
    assert "Funnel" in text  # element still present even if hidden by JS


def test_lce_page_creates_session_when_cookie_missing(client):
    """The LCE page should auto-create a session like /discovery does."""
    resp = client.get("/discovery/lce")
    assert resp.status_code == 200
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "ta_discovery_session_id=" in set_cookie


def test_lce_page_link_present_on_main_discovery_page(client):
    """The /discovery header should expose a way to open the focused LCE page."""
    resp = client.get("/discovery")
    text = resp.get_data(as_text=True)
    assert 'href="/discovery/lce"' in text
