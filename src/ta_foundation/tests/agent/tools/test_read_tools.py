"""Tests for the read-side tools.

Each tool gets ≥ 3 cases: happy path, empty/missing result, and a
schema-fail (validates the decorator wiring is in place per tool)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.agent.tools.read.candidates import (
    find_similar_hypotheses,
    get_candidate,
    list_candidates,
    list_graveyard,
)
from ta_foundation.agent.tools.read.families import (
    get_family_spec,
    list_probe_families,
)
from ta_foundation.agent.tools.read.ledger import (
    count_hypotheses_tested,
    get_family_coverage,
    get_hypothesis,
)
from ta_foundation.agent.tools.read.market import get_market_data_coverage
from ta_foundation.agent.tools.read.sidecars import read_sidecar
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _seed_hypothesis(repo: Repository, hid: str = "h_t_001",
                     family: str = "vwap_reject_fade") -> None:
    repo.register_hypothesis(
        hypothesis_id=hid,
        family=family,
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=("A reasonable mechanism paragraph that is well above the 50-char "
                   "minimum the repository enforces."),
        registered_by="test",
    )


def _seed_run_and_candidate(
    repo: Repository, hid: str = "h_t_001",
    rid: str = "r_t_001", cid: str = "c_t_001",
    gate_verdict: str = "survivor",
) -> None:
    if repo.get_hypothesis(hid) is None:
        _seed_hypothesis(repo, hid)
    repo.start_run(
        run_id=rid, hypothesis_id=hid, mode="hardened",
        config_hash="abc", yaml_path="probe.yaml", artifact_dir="/tmp/x",
    )
    repo.complete_run(rid)
    repo.record_candidate(
        candidate_id=cid, run_id=rid, rank_in_run=1, params={"a": 1},
        gate_verdict=gate_verdict, n_trades_dev=120, pf_dev=1.85,
        n_trades_oos=40, pf_oos=1.6,
    )


# ---------- list_probe_families & get_family_spec ---------------------------


def test_list_probe_families_returns_starter_set(repo: Repository) -> None:
    out = list_probe_families(repo)
    assert out["ok"]
    ids = {row["family_id"] for row in out["result"]}
    # 13 starter families + the legacy_imported catch-all from migration 0003.
    assert "vwap_reject_fade" in ids
    assert "orb_failure_reclaim" in ids
    assert "legacy_imported" in ids
    assert all("family_id" in row and "description" in row for row in out["result"])


def test_get_family_spec_happy(repo: Repository) -> None:
    out = get_family_spec(repo, family_id="vwap_reject_fade")
    assert out["ok"] and out["result"]["found"] is True
    assert out["result"]["mechanism_template"]


def test_get_family_spec_missing(repo: Repository) -> None:
    out = get_family_spec(repo, family_id="not_real")
    assert out["ok"] and out["result"]["found"] is False


def test_get_family_spec_schema_fail(repo: Repository) -> None:
    out = get_family_spec(repo)
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"


# ---------- list_candidates -------------------------------------------------


def test_list_candidates_empty(repo: Repository) -> None:
    out = list_candidates(repo)
    assert out["ok"] and out["result"] == []


def test_list_candidates_returns_seeded(repo: Repository) -> None:
    _seed_run_and_candidate(repo)
    out = list_candidates(repo)
    assert out["ok"] and len(out["result"]) == 1
    assert out["result"][0]["candidate_id"] == "c_t_001"


def test_list_candidates_filters_by_gate_verdict(repo: Repository) -> None:
    _seed_run_and_candidate(repo, cid="c_a", gate_verdict="survivor")
    repo.record_candidate(
        candidate_id="c_b", run_id="r_t_001", rank_in_run=2, params={},
        gate_verdict="rejected",
    )
    out = list_candidates(repo, gate_verdict="rejected")
    assert {c["candidate_id"] for c in out["result"]} == {"c_b"}


def test_list_candidates_invalid_filter(repo: Repository) -> None:
    out = list_candidates(repo, triage_state="not_a_state")
    assert out["ok"] is False


# ---------- get_candidate ---------------------------------------------------


def test_get_candidate_missing(repo: Repository) -> None:
    out = get_candidate(repo, candidate_id="c_none")
    assert out["ok"] and out["result"]["found"] is False


def test_get_candidate_happy(repo: Repository) -> None:
    _seed_run_and_candidate(repo)
    out = get_candidate(repo, candidate_id="c_t_001")
    assert out["ok"] and out["result"]["found"] is True
    assert out["result"]["pf_dev"] == 1.85


def test_get_candidate_schema_fail(repo: Repository) -> None:
    out = get_candidate(repo)
    assert out["ok"] is False


# ---------- list_graveyard --------------------------------------------------


def test_list_graveyard_empty(repo: Repository) -> None:
    out = list_graveyard(repo)
    assert out["ok"] and out["result"] == []


def test_list_graveyard_filters_by_state(repo: Repository) -> None:
    _seed_run_and_candidate(repo)
    repo.set_triage(
        candidate_id="c_t_001", state="graveyard",
        reason="adjusted t-test failed under multiple-comparison correction",
        triaged_by="test",
    )
    out = list_graveyard(repo)
    assert len(out["result"]) == 1


def test_list_graveyard_limit_respected(repo: Repository) -> None:
    out = list_graveyard(repo, limit=5)
    assert out["ok"]


# ---------- find_similar_hypotheses -----------------------------------------


def test_find_similar_empty(repo: Repository) -> None:
    out = find_similar_hypotheses(
        repo, family="vwap_reject_fade", instrument="NQ", params={"x": 1},
    )
    assert out["ok"] and out["result"] == []


def test_find_similar_match(repo: Repository) -> None:
    _seed_hypothesis(repo)
    out = find_similar_hypotheses(
        repo, family="vwap_reject_fade", instrument="NQ",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
    )
    assert out["ok"] and len(out["result"]) == 1
    assert out["result"][0]["similarity"] >= 0.99


def test_find_similar_schema_fail(repo: Repository) -> None:
    out = find_similar_hypotheses(repo, family="vwap_reject_fade", params={"x": 1})
    assert out["ok"] is False


# ---------- count_hypotheses_tested & get_hypothesis ------------------------


def test_count_hypotheses_tested_zero(repo: Repository) -> None:
    out = count_hypotheses_tested(repo)
    assert out["ok"] and out["result"]["count"] == 0


def test_count_hypotheses_tested_after_completed_run(repo: Repository) -> None:
    _seed_run_and_candidate(repo)
    out = count_hypotheses_tested(repo)
    assert out["result"]["count"] == 1


def test_get_hypothesis_missing(repo: Repository) -> None:
    out = get_hypothesis(repo, hypothesis_id="h_none")
    assert out["ok"] and out["result"]["found"] is False


def test_get_hypothesis_happy(repo: Repository) -> None:
    _seed_hypothesis(repo)
    out = get_hypothesis(repo, hypothesis_id="h_t_001")
    assert out["ok"] and out["result"]["found"] is True
    assert out["result"]["family"] == "vwap_reject_fade"


def test_get_family_coverage_empty(repo: Repository) -> None:
    out = get_family_coverage(repo)
    assert out["ok"] and out["result"]["total"] == 0
    assert out["result"]["by_family"] == {}


def test_get_family_coverage_aggregates_by_family(repo: Repository) -> None:
    _seed_hypothesis(repo, hid="h_a", family="vwap_reject_fade")
    repo.register_hypothesis(
        hypothesis_id="h_b", family="orb_breakout", instrument="NQ", timeframe="5m",
        params={"orb_minutes": 15, "stop_ticks": 8, "target_ticks": 50,
                 "signal_type": "break_close"},
        mechanism="A reasonable mechanism that exceeds the 50-char floor easily. " * 2,
        registered_by="test",
    )
    out = get_family_coverage(repo)
    assert out["result"]["total"] == 2
    assert out["result"]["by_family"]["vwap_reject_fade"] == 1
    assert out["result"]["by_family"]["orb_breakout"] == 1


def test_get_family_coverage_filters_by_registered_by(repo: Repository) -> None:
    _seed_hypothesis(repo, hid="h_a")  # registered_by='test'
    repo.register_hypothesis(
        hypothesis_id="h_agent", family="vwap_reject_fade", instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 6, "stop_ticks": 8, "target_ticks": 30},
        mechanism="Agent-authored hypothesis whose mechanism comfortably "
                  "exceeds the 50-char floor for registration.",
        registered_by="agent:hypothesis_author",
    )
    out = get_family_coverage(repo, registered_by="agent:hypothesis_author")
    assert out["result"]["total"] == 1
    assert out["result"]["by_family"] == {"vwap_reject_fade": 1}


# ---------- get_market_data_coverage ---------------------------------------


def test_market_coverage_missing_root(repo: Repository, tmp_path: Path) -> None:
    out = get_market_data_coverage(repo, market_data_root=str(tmp_path / "missing"))
    assert out["ok"] and out["result"]["exists"] is False


def test_market_coverage_finds_files(repo: Repository, tmp_path: Path) -> None:
    f = tmp_path / "NQ 03-26.Last.txt"
    f.write_text("dummy")
    out = get_market_data_coverage(repo, market_data_root=str(tmp_path))
    assert out["ok"] and out["result"]["n_files"] == 1
    assert out["result"]["by_instrument"] == {"NQ": 1}


def test_market_coverage_filters_instrument(repo: Repository, tmp_path: Path) -> None:
    (tmp_path / "NQ 03-26.Last.txt").write_text("d")
    (tmp_path / "ES 03-26.Last.txt").write_text("d")
    out = get_market_data_coverage(repo, market_data_root=str(tmp_path), instrument="ES")
    assert out["result"]["by_instrument"] == {"ES": 1}


# ---------- read_sidecar ----------------------------------------------------


def test_read_sidecar_missing(repo: Repository) -> None:
    out = read_sidecar(repo, path="/no/such/file")
    assert out["ok"] and out["result"]["found"] is False


def test_read_sidecar_happy(repo: Repository, tmp_path: Path) -> None:
    p = tmp_path / "sidecar.json"
    p.write_text('{"hello": "world"}', encoding="utf-8")
    out = read_sidecar(repo, path=str(p))
    assert out["ok"] and out["result"]["valid_json"] is True
    assert out["result"]["body"] == {"hello": "world"}


def test_read_sidecar_invalid_json(repo: Repository, tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid", encoding="utf-8")
    out = read_sidecar(repo, path=str(p))
    assert out["ok"] and out["result"]["valid_json"] is False


def test_family_registry_is_returned_inline_not_spilled(repo: Repository) -> None:
    """The registry must stay inline as families are added.

    It is the list of families the Hypothesis Author is allowed to choose from,
    so a spilled result -- {ok, truncated, artifact_path, summary} with no
    `result` -- makes the tool useless. At 16 families it already exceeded the
    2 KB global default.
    """
    out = list_probe_families(repo)
    assert out["ok"]
    assert "result" in out, f"registry spilled to disk: {sorted(out)}"
    assert not out.get("truncated")
