"""Unit tests for the research_ledger Repository.

These tests run against an ephemeral SQLite file under pytest's tmp_path.
Coverage targets the canonical queries listed in
docs/designs/agentic_phase_a_foundation.md plus the hard invariants from
the master plan §1 (dedupe, single holdout attempt, journaling).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ta_foundation.research_ledger import (
    DuplicateHypothesisError,
    HoldoutAlreadyAttemptedError,
    LedgerIntegrityError,
    Repository,
    get_repository,
    init_db,
)
from ta_foundation.research_ledger.db import (
    apply_pending_migrations,
    current_schema_version,
)


# ---------- Fixtures ---------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "research_ledger.db"


@pytest.fixture()
def repo(db_path: Path) -> Repository:
    # Migration 0002 seeds all 13 starter families; tests do not need to
    # override anything.
    return get_repository(db_path)


def _register_basic_hypothesis(
    repo: Repository,
    *,
    hypothesis_id: str = "h_test_001",
    family: str = "vwap_reject_fade",
    params: dict | None = None,
    mechanism: str | None = None,
    instrument: str = "NQ",
    timeframe: str = "5m",
    session_window: str | None = "london_00_06_denver",
    direction: str | None = "long",
):
    return repo.register_hypothesis(
        hypothesis_id=hypothesis_id,
        family=family,
        instrument=instrument,
        timeframe=timeframe,
        session_window=session_window,
        direction=direction,
        params=params or {"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=mechanism
        or (
            "London-session traders defend the prior NY VWAP after thin overnight "
            "liquidity sweeps; the inefficiency persists when the breakout fails to "
            "close above and traps short-term momentum entries."
        ),
        registered_by="human:test",
    )


# ---------- TestSchemaInitialization (4 tests) ------------------------------


def test_init_db_creates_file_and_runs_migrations(db_path: Path) -> None:
    assert not db_path.exists()
    conn = init_db(db_path)
    assert db_path.exists()
    assert current_schema_version(conn) >= 3  # 0001 schema + 0002 families + 0003 notes


def test_init_db_is_idempotent(db_path: Path) -> None:
    init_db(db_path).close()
    conn = init_db(db_path)
    # Second call applies no migrations.
    assert apply_pending_migrations(conn) == []
    assert current_schema_version(conn) >= 3  # 0001 schema + 0002 families + 0003 notes


def test_init_db_creates_all_expected_tables(repo: Repository) -> None:
    rows = repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    for required in {
        "_schema_version",
        "candidates",
        "families",
        "hypotheses",
        "runs",
        "shadow_signals",
        "tool_journal",
    }:
        assert required in names, f"missing table: {required}"


def test_seed_family_round_trip(repo: Repository) -> None:
    fam = repo.get_family("vwap_reject_fade")
    assert fam is not None
    assert "reversion" in fam.description.lower()
    whitelist = json.loads(fam.legitimate_params_json)
    assert "min_distance_ticks" in whitelist
    assert whitelist["stop_ticks"]["type"] == "int"


# ---------- TestHypothesisRegistration (7 tests) ----------------------------


def test_register_hypothesis_round_trip(repo: Repository) -> None:
    h = _register_basic_hypothesis(repo)
    assert h.hypothesis_id == "h_test_001"
    assert h.status == "open"
    assert h.dedupe_hash and len(h.dedupe_hash) == 64

    fetched = repo.get_hypothesis("h_test_001")
    assert fetched == h


def test_register_hypothesis_rejects_short_mechanism(repo: Repository) -> None:
    with pytest.raises(ValueError, match="mechanism"):
        repo.register_hypothesis(
            hypothesis_id="h_short",
            family="vwap_reject_fade",
            instrument="NQ",
            timeframe="5m",
            params={"x": 1},
            mechanism="too short",
            registered_by="human:test",
        )


def test_register_hypothesis_rejects_unknown_family(repo: Repository) -> None:
    with pytest.raises(ValueError, match="Unknown family"):
        repo.register_hypothesis(
            hypothesis_id="h_bad_fam",
            family="not_a_real_family",
            instrument="NQ",
            timeframe="5m",
            params={"x": 1},
            mechanism="A" * 60,
            registered_by="human:test",
        )


def test_register_duplicate_raises_with_existing_id(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    with pytest.raises(DuplicateHypothesisError) as exc:
        _register_basic_hypothesis(repo, hypothesis_id="h_test_002")
    assert exc.value.existing_id == "h_test_001"


def test_dedupe_distinguishes_by_session_window(repo: Repository) -> None:
    _register_basic_hypothesis(repo, hypothesis_id="h_a", session_window="london_00_06_denver")
    # Different session window → different hypothesis.
    h2 = _register_basic_hypothesis(repo, hypothesis_id="h_b", session_window="ny_06_12_denver")
    assert h2.hypothesis_id == "h_b"


def test_dedupe_distinguishes_by_mechanism_text(repo: Repository) -> None:
    _register_basic_hypothesis(repo, hypothesis_id="h_a")
    _register_basic_hypothesis(
        repo,
        hypothesis_id="h_b",
        mechanism=(
            "Different mechanism text describing a different counterparty story; "
            "namely momentum CTAs unwinding into the fade rather than trapped breakout "
            "buyers."
        ),
    )
    assert repo.get_hypothesis("h_b") is not None


def test_set_hypothesis_status_transitions(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    repo.set_hypothesis_status("h_test_001", "retired")
    assert repo.get_hypothesis("h_test_001").status == "retired"  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        repo.set_hypothesis_status("h_test_001", "bogus_state")
    with pytest.raises(LedgerIntegrityError):
        repo.set_hypothesis_status("h_does_not_exist", "retired")


# ---------- TestRuns (5 tests) ----------------------------------------------


def test_start_run_requires_existing_hypothesis(repo: Repository) -> None:
    with pytest.raises(LedgerIntegrityError):
        repo.start_run(
            run_id="r_1",
            hypothesis_id="h_missing",
            mode="fast_probe",
            config_hash="abc",
            yaml_path="probe.yaml",
            artifact_dir="/tmp/x",
        )


def test_start_complete_run_round_trip(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    repo.start_run(
        run_id="r_1",
        hypothesis_id="h_test_001",
        mode="fast_probe",
        config_hash="abc",
        yaml_path="probe.yaml",
        artifact_dir="/tmp/x",
    )
    repo.complete_run("r_1")
    run = repo.get_run("r_1")
    assert run is not None
    assert run.status == "completed"
    assert run.completed_at is not None


def test_fail_run_records_error(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    repo.start_run(
        run_id="r_1",
        hypothesis_id="h_test_001",
        mode="fast_probe",
        config_hash="abc",
        yaml_path="probe.yaml",
        artifact_dir="/tmp/x",
    )
    repo.fail_run("r_1", "ingest failed: missing market data")
    run = repo.get_run("r_1")
    assert run is not None
    assert run.status == "failed"
    assert "missing market data" in (run.error or "")


def test_complete_run_twice_is_rejected(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    repo.start_run(
        run_id="r_1",
        hypothesis_id="h_test_001",
        mode="fast_probe",
        config_hash="abc",
        yaml_path="probe.yaml",
        artifact_dir="/tmp/x",
    )
    repo.complete_run("r_1")
    with pytest.raises(LedgerIntegrityError):
        repo.complete_run("r_1")


def test_run_mode_check_constraint(repo: Repository) -> None:
    _register_basic_hypothesis(repo)
    with pytest.raises(sqlite3.IntegrityError):
        repo.start_run(
            run_id="r_x",
            hypothesis_id="h_test_001",
            mode="not_a_real_mode",
            config_hash="abc",
            yaml_path="probe.yaml",
            artifact_dir="/tmp/x",
        )


# ---------- TestCandidates (5 tests) ----------------------------------------


def _seed_run(repo: Repository, run_id: str = "r_1") -> None:
    _register_basic_hypothesis(repo)
    repo.start_run(
        run_id=run_id,
        hypothesis_id="h_test_001",
        mode="hardened",
        config_hash="abc",
        yaml_path="probe.yaml",
        artifact_dir="/tmp/x",
    )


def test_record_candidate_round_trip(repo: Repository) -> None:
    _seed_run(repo)
    c = repo.record_candidate(
        candidate_id="c_1",
        run_id="r_1",
        rank_in_run=1,
        params={"target_ticks": 24, "stop_ticks": 8},
        gate_verdict="survivor",
        n_trades_dev=120,
        pf_dev=1.85,
        expectancy_dev=18.5,
        n_trades_oos=40,
        pf_oos=1.6,
        slippage_stress_pass=True,
    )
    assert c.gate_verdict == "survivor"
    assert c.slippage_stress_pass == 1
    fetched = repo.get_candidate("c_1")
    assert fetched is not None
    assert fetched.pf_dev == pytest.approx(1.85)


def test_record_candidate_requires_existing_run(repo: Repository) -> None:
    with pytest.raises(LedgerIntegrityError):
        repo.record_candidate(
            candidate_id="c_x",
            run_id="r_missing",
            rank_in_run=1,
            params={},
        )


def test_record_candidate_rejects_invalid_gate_verdict(repo: Repository) -> None:
    _seed_run(repo)
    with pytest.raises(ValueError):
        repo.record_candidate(
            candidate_id="c_x",
            run_id="r_1",
            rank_in_run=1,
            params={},
            gate_verdict="amazing",
        )


def test_list_candidates_filters_and_orders(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(
        candidate_id="c_b", run_id="r_1", rank_in_run=2, params={}, gate_verdict="rejected"
    )
    repo.record_candidate(
        candidate_id="c_a", run_id="r_1", rank_in_run=1, params={}, gate_verdict="survivor"
    )
    rows = repo.list_candidates()
    assert [c.candidate_id for c in rows] == ["c_a", "c_b"]

    survivors = repo.list_candidates(gate_verdict="survivor")
    assert {c.candidate_id for c in survivors} == {"c_a"}

    untriaged = repo.list_candidates(untriaged_only=True)
    assert len(untriaged) == 2


def test_list_candidates_filters_by_family_and_instrument(repo: Repository) -> None:
    # Hypothesis 1: vwap_reject_fade / NQ
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_nq", run_id="r_1", rank_in_run=1, params={})
    # Hypothesis 2: orb_failure_reclaim / ES
    repo.register_hypothesis(
        hypothesis_id="h_es_orb",
        family="orb_failure_reclaim",
        instrument="ES",
        timeframe="5m",
        params={"orb_minutes": 5, "sweep_min_ticks": 3, "reclaim_within_bars": 2},
        mechanism="Failed ES open auction reclaim attracts trapped sellers; " * 2,
        registered_by="human:test",
    )
    repo.start_run(
        run_id="r_2",
        hypothesis_id="h_es_orb",
        mode="hardened",
        config_hash="def",
        yaml_path="orb.yaml",
        artifact_dir="/tmp/y",
    )
    repo.record_candidate(candidate_id="c_es", run_id="r_2", rank_in_run=1, params={})

    only_nq = repo.list_candidates(instrument="NQ")
    assert {c.candidate_id for c in only_nq} == {"c_nq"}

    only_orb = repo.list_candidates(family="orb_failure_reclaim")
    assert {c.candidate_id for c in only_orb} == {"c_es"}


# ---------- TestTriage and Graveyard (3 tests) ------------------------------


def test_set_triage_records_state_and_reason(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    repo.set_triage(
        candidate_id="c_1",
        state="graveyard",
        reason="Adjusted t-test failed under multiple-comparison correction.",
        triaged_by="agent:triage",
    )
    c = repo.get_candidate("c_1")
    assert c is not None
    assert c.triage_state == "graveyard"
    assert c.triaged_by == "agent:triage"


def test_set_triage_rejects_short_reason_and_bad_state(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    with pytest.raises(ValueError, match="20 chars"):
        repo.set_triage(
            candidate_id="c_1", state="graveyard", reason="too short", triaged_by="x"
        )
    with pytest.raises(ValueError, match="Invalid triage"):
        repo.set_triage(
            candidate_id="c_1",
            state="brilliant",
            reason="A reasonable length reason here.",
            triaged_by="x",
        )


def test_list_graveyard_filters(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    repo.record_candidate(candidate_id="c_2", run_id="r_1", rank_in_run=2, params={})
    repo.set_triage(
        candidate_id="c_1",
        state="graveyard",
        reason="Failed adjusted t-test on 47 trades.",
        triaged_by="agent:triage",
    )
    repo.set_triage(
        candidate_id="c_2",
        state="research",
        reason="Promising but underpowered; needs more current-period data.",
        triaged_by="agent:triage",
    )
    grave = repo.list_graveyard()
    assert {c.candidate_id for c in grave} == {"c_1"}


# ---------- TestHoldoutLock -------------------------------------------------


def test_holdout_lock_first_attempt_succeeds(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    assert repo.lock_holdout_attempt("c_1") is True


def test_holdout_lock_second_attempt_fails(repo: Repository) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    assert repo.lock_holdout_attempt("c_1") is True
    assert repo.lock_holdout_attempt("c_1") is False
    assert repo.lock_holdout_attempt("c_1") is False


def test_holdout_lock_unknown_candidate_raises(repo: Repository) -> None:
    with pytest.raises(LedgerIntegrityError):
        repo.lock_holdout_attempt("c_does_not_exist")


def test_named_holdout_reservation_is_idempotent_for_same_owner(
    repo: Repository,
) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    assert repo.reserve_holdout_attempt("c_1", "attempt-a") is True
    assert repo.reserve_holdout_attempt("c_1", "attempt-a") is True
    assert repo.reserve_holdout_attempt("c_1", "attempt-b") is False
    assert repo.lock_holdout_attempt("c_1") is False


def test_legacy_holdout_lock_cannot_be_claimed_by_named_owner(
    repo: Repository,
) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    assert repo.lock_holdout_attempt("c_1") is True
    assert repo.reserve_holdout_attempt("c_1", "attempt-a") is False


def test_holdout_result_is_idempotent_but_cannot_be_reinterpreted(
    repo: Repository,
) -> None:
    _seed_run(repo)
    repo.record_candidate(candidate_id="c_1", run_id="r_1", rank_in_run=1, params={})
    assert repo.reserve_holdout_attempt("c_1", "attempt-a") is True
    repo.record_holdout_result(
        candidate_id="c_1",
        n_trades=41,
        profit_factor=1.42,
        expectancy=18.5,
    )
    repo.record_holdout_result(
        candidate_id="c_1",
        n_trades=41,
        profit_factor=1.42,
        expectancy=18.5,
    )
    candidate = repo.get_candidate("c_1")
    assert candidate is not None
    assert candidate.n_trades_holdout == 41
    assert candidate.pf_holdout == pytest.approx(1.42)
    with pytest.raises(LedgerIntegrityError):
        repo.record_holdout_result(
            candidate_id="c_1",
            n_trades=40,
            profit_factor=1.42,
            expectancy=18.5,
        )


# ---------- TestSimilarityAndCounting (4 tests) -----------------------------


def test_find_similar_returns_high_jaccard_matches(repo: Repository) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_a",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism="A" * 60,
        registered_by="human:test",
    )
    similar = repo.find_similar_hypotheses(
        family="vwap_reject_fade",
        instrument="NQ",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        threshold=0.7,
    )
    assert len(similar) == 1
    assert similar[0][0].hypothesis_id == "h_a"
    assert similar[0][1] == pytest.approx(1.0)


def test_find_similar_filters_by_family_instrument(repo: Repository) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_nq",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4},
        mechanism="A" * 60,
        registered_by="human:test",
    )
    similar = repo.find_similar_hypotheses(
        family="orb_failure_reclaim",
        instrument="NQ",
        params={"min_distance_ticks": 4},
    )
    assert similar == []


def test_find_similar_below_threshold_excluded(repo: Repository) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_a",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"a": 1, "b": 2, "c": 3},
        mechanism="A" * 60,
        registered_by="human:test",
    )
    # Single-key overlap with very different rest → low Jaccard.
    similar = repo.find_similar_hypotheses(
        family="vwap_reject_fade",
        instrument="NQ",
        params={"a": 1, "x": 99, "y": 100, "z": 101, "w": 102},
        threshold=0.7,
    )
    assert similar == []


def test_count_hypotheses_tested_only_counts_completed_runs(repo: Repository) -> None:
    _register_basic_hypothesis(repo, hypothesis_id="h_a")
    _register_basic_hypothesis(
        repo,
        hypothesis_id="h_b",
        mechanism="Distinct mechanism describing a different structural story " * 2,
    )
    # h_a has a completed run; h_b has a running run → only h_a counts.
    repo.start_run(
        run_id="r_a",
        hypothesis_id="h_a",
        mode="fast_probe",
        config_hash="x",
        yaml_path="a.yaml",
        artifact_dir="/tmp/a",
    )
    repo.complete_run("r_a")
    repo.start_run(
        run_id="r_b",
        hypothesis_id="h_b",
        mode="fast_probe",
        config_hash="y",
        yaml_path="b.yaml",
        artifact_dir="/tmp/b",
    )
    assert repo.count_hypotheses_tested() == 1
    repo.complete_run("r_b")
    assert repo.count_hypotheses_tested() == 2
    assert repo.count_hypotheses_tested(family="vwap_reject_fade") == 2
    assert repo.count_hypotheses_tested(family="orb_failure_reclaim") == 0


# ---------- TestJournal (3 tests) -------------------------------------------


def test_journal_round_trip(repo: Repository) -> None:
    jid = repo.journal(
        role="agent:triage",
        tool_name="set_triage_state",
        inputs={"candidate_id": "c_1", "state": "graveyard"},
        output_summary="ok",
        duration_ms=42,
    )
    assert jid > 0
    rows = repo.list_journal()
    assert len(rows) == 1
    assert rows[0].tool_name == "set_triage_state"
    assert rows[0].duration_ms == 42


def test_journal_filters_with_errors_only(repo: Repository) -> None:
    repo.journal(
        role="agent:operator",
        tool_name="run_probe",
        inputs={"hypothesis_id": "h_a"},
        output_summary="ok",
        duration_ms=10,
    )
    repo.journal(
        role="agent:operator",
        tool_name="run_probe",
        inputs={"hypothesis_id": "h_b"},
        output_summary="failed",
        duration_ms=5,
        error="missing market data",
    )
    failed = repo.list_journal(with_errors_only=True)
    assert len(failed) == 1
    assert failed[0].error == "missing market data"


def test_journal_canonicalizes_inputs_json(repo: Repository) -> None:
    repo.journal(
        role="agent:author",
        tool_name="author_probe",
        inputs={"b": 2, "a": 1},
        output_summary="ok",
        duration_ms=1,
    )
    rows = repo.list_journal()
    # Sorted keys → 'a' before 'b'.
    assert rows[0].inputs_json == '{"a":1,"b":2}'


# ---------- TestMigrationsIdempotency (1 test) ------------------------------


def test_running_migrations_twice_does_nothing(db_path: Path) -> None:
    init_db(db_path).close()
    conn = init_db(db_path)
    applied = apply_pending_migrations(conn)
    assert applied == []
    assert current_schema_version(conn) >= 3  # 0001 schema + 0002 families + 0003 notes
