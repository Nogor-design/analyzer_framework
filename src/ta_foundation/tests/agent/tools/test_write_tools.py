"""Tests for the write-side tools.

Each tool gets ≥ 5 cases per the phase-A spec: happy path plus each
precondition failure, and a schema-validation failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.agent.tools.write.author_probe import (
    GENERATED_PROBE_DIR,
    author_probe,
)
from ta_foundation.agent.tools.write.post_mortem import (
    GRAVEYARD_DIR,
    write_post_mortem,
)
from ta_foundation.agent.tools.write.promote import (
    promote_to_hardening,
    request_locked_holdout,
)
from ta_foundation.agent.tools.write.shadow import enroll_shadow_trader
from ta_foundation.agent.tools.write.triage import set_triage_state
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


@pytest.fixture(autouse=True)
def _isolate_disk_writes(tmp_path: Path, monkeypatch):
    """Redirect disk side effects into pytest tmp_path so tests don't touch
    the working tree's discovery/ folder."""
    from ta_foundation.agent.tools.write import author_probe as ap_mod
    from ta_foundation.agent.tools.write import post_mortem as pm_mod
    monkeypatch.setattr(ap_mod, "GENERATED_PROBE_DIR", tmp_path / "generated")
    monkeypatch.setattr(pm_mod, "GRAVEYARD_DIR", tmp_path / "graveyard")


# ---------- helpers ---------------------------------------------------------


def _seed_candidate(
    repo: Repository,
    *,
    cid: str = "c_t_001",
    gate_verdict: str = "survivor",
    n_trades_dev: int = 120,
    pf_dev: float = 1.85,
    n_trades_oos: int = 40,
    pf_oos: float = 1.6,
    n_trades_holdout: int | None = None,
    pf_holdout: float | None = None,
) -> None:
    if repo.get_hypothesis("h_t_001") is None:
        repo.register_hypothesis(
            hypothesis_id="h_t_001",
            family="vwap_reject_fade",
            instrument="NQ",
            timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
            mechanism=("A reasonable mechanism paragraph that is well above the 50-char "
                       "minimum the repository enforces."),
            registered_by="test",
        )
    if repo.get_run("r_t_001") is None:
        repo.start_run(
            run_id="r_t_001", hypothesis_id="h_t_001", mode="hardened",
            config_hash="x", yaml_path="probe.yaml", artifact_dir="/tmp/x",
        )
        repo.complete_run("r_t_001")
    repo.record_candidate(
        candidate_id=cid, run_id="r_t_001", rank_in_run=1, params={"a": 1},
        gate_verdict=gate_verdict,
        n_trades_dev=n_trades_dev, pf_dev=pf_dev,
        n_trades_oos=n_trades_oos, pf_oos=pf_oos,
        n_trades_holdout=n_trades_holdout, pf_holdout=pf_holdout,
    )


# ============================================================================
# author_probe
# ============================================================================


def _author_inputs(**overrides) -> dict:
    base = {
        "hypothesis_id": "h_authored_001",
        "family": "vwap_reject_fade",
        "instrument": "NQ",
        "timeframe": "5m",
        "session_window": "ny_open_06_07_denver",
        "direction": "long",
        "params": {
            "min_distance_ticks": 4,
            "max_distance_ticks": 12,
            "stop_ticks": 8,
            "target_ticks": 24,
        },
        "mechanism": (
            "A counterparty story long enough to satisfy the 50-char floor and "
            "describe a structural rationale for the proposed inefficiency."
        ),
        "registered_by": "test",
    }
    base.update(overrides)
    return base


def test_author_probe_happy(repo: Repository, tmp_path: Path) -> None:
    out = author_probe(repo, **_author_inputs())
    assert out["ok"] and out["result"]["registered"] is True
    yaml_path = Path(out["result"]["yaml_path"])
    assert yaml_path.exists()


def test_author_probe_unknown_family(repo: Repository) -> None:
    out = author_probe(repo, **_author_inputs(family="not_a_family"))
    assert out["ok"] is False
    assert out["code"] == "unknown_family"


def test_author_probe_param_outside_whitelist(repo: Repository) -> None:
    out = author_probe(repo, **_author_inputs(params={"min_distance_ticks": -5}))
    assert out["ok"] is False
    assert out["code"] == "params_not_in_whitelist"


def test_author_probe_short_mechanism_rejected_by_schema(repo: Repository) -> None:
    out = author_probe(repo, **_author_inputs(mechanism="too short"))
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"


def test_author_probe_duplicate_returns_existing_id(repo: Repository) -> None:
    out1 = author_probe(repo, **_author_inputs())
    assert out1["ok"]
    out2 = author_probe(repo, **_author_inputs(hypothesis_id="h_authored_002"))
    assert out2["ok"] and out2["result"]["registered"] is False
    assert out2["result"]["existing_hypothesis_id"] == "h_authored_001"


def test_author_probe_revival_required_for_graveyard_collision(repo: Repository) -> None:
    # Seed and graveyard a near-identical hypothesis.
    out1 = author_probe(repo, **_author_inputs())
    cid = "c_collision"
    repo.start_run(run_id="r_coll", hypothesis_id="h_authored_001",
                   mode="fast_probe", config_hash="x",
                   yaml_path="x", artifact_dir="/tmp/x")
    repo.complete_run("r_coll")
    repo.record_candidate(candidate_id=cid, run_id="r_coll", rank_in_run=1,
                          params={"a": 1}, gate_verdict="rejected")
    repo.set_triage(candidate_id=cid, state="graveyard",
                    reason="failed adjusted t-test under multiple-comparison correction",
                    triaged_by="test")
    # Now propose a near-identical hypothesis — must require revival_reason.
    out = author_probe(repo, **_author_inputs(
        hypothesis_id="h_revival_attempt",
        # Same params + same family + same instrument; mechanism slightly different
        # so dedupe doesn't trip first.
        mechanism=("Distinct mechanism rephrasing the original story but equally "
                   "long, naming the same counterparty."),
    ))
    assert out["ok"] is False
    assert out["code"] == "graveyard_collision_requires_revival_reason"


# ============================================================================
# set_triage_state
# ============================================================================


def test_triage_happy(repo: Repository) -> None:
    _seed_candidate(repo)
    out = set_triage_state(
        repo, candidate_id="c_t_001", state="research",
        reason="promising but underpowered; needs more recent data to validate",
        triaged_by="agent:triage",
    )
    assert out["ok"] and out["result"]["triaged"] is True


def test_triage_unknown_candidate(repo: Repository) -> None:
    out = set_triage_state(
        repo, candidate_id="c_none", state="research",
        reason="reason long enough to be valid here.", triaged_by="x",
    )
    assert out["ok"] is False and out["code"] == "unknown_candidate"


def test_triage_invalid_state(repo: Repository) -> None:
    _seed_candidate(repo)
    out = set_triage_state(
        repo, candidate_id="c_t_001", state="brilliant",
        reason="reason long enough to be valid here.", triaged_by="x",
    )
    assert out["ok"] is False and out["code"] == "schema_validation_failed"


def test_triage_short_reason(repo: Repository) -> None:
    _seed_candidate(repo)
    out = set_triage_state(
        repo, candidate_id="c_t_001", state="research",
        reason="short", triaged_by="x",
    )
    assert out["ok"] is False and out["code"] == "schema_validation_failed"


def test_triage_shadow_requires_holdout(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")  # no holdout metrics
    out = set_triage_state(
        repo, candidate_id="c_t_001", state="shadow",
        reason="want to forward observe this survivor for shadow trading",
        triaged_by="x",
    )
    assert out["ok"] is False
    assert out["code"] == "shadow_requires_locked_holdout"


def test_triage_shadow_requires_passing_holdout(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor",
                    n_trades_holdout=20, pf_holdout=0.95)
    out = set_triage_state(
        repo, candidate_id="c_t_001", state="shadow",
        reason="want to forward observe this candidate for shadow trading",
        triaged_by="x",
    )
    assert out["ok"] is False
    assert out["code"] == "shadow_requires_passing_holdout"


# ============================================================================
# promote_to_hardening
# ============================================================================


def test_promote_happy(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")
    out = promote_to_hardening(
        repo, candidate_id="c_t_001",
        reason="dev/oos PF healthy; promote to full hardening run",
        promoted_by="agent:triage",
    )
    assert out["ok"] and out["result"]["promoted"] is True


def test_promote_rejects_non_survivor(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="rejected")
    out = promote_to_hardening(
        repo, candidate_id="c_t_001",
        reason="trying to promote a rejected candidate",
        promoted_by="x",
    )
    assert out["ok"] is False and out["code"] == "not_a_survivor"


def test_promote_unknown_candidate(repo: Repository) -> None:
    out = promote_to_hardening(
        repo, candidate_id="c_none",
        reason="reason text here that is long enough", promoted_by="x",
    )
    assert out["ok"] is False and out["code"] == "unknown_candidate"


def test_promote_short_reason(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")
    out = promote_to_hardening(
        repo, candidate_id="c_t_001", reason="short", promoted_by="x",
    )
    assert out["ok"] is False and out["code"] == "schema_validation_failed"


def test_promote_missing_required(repo: Repository) -> None:
    out = promote_to_hardening(repo, candidate_id="c_t_001")
    assert out["ok"] is False


# ============================================================================
# request_locked_holdout
# ============================================================================


def test_holdout_first_attempt_acquires(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")
    out = request_locked_holdout(
        repo, candidate_id="c_t_001", requested_by="agent:triage",
    )
    assert out["ok"] and out["result"]["lock_acquired"] is True


def test_holdout_second_attempt_returns_false(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")
    request_locked_holdout(repo, candidate_id="c_t_001", requested_by="x")
    out = request_locked_holdout(repo, candidate_id="c_t_001", requested_by="x")
    assert out["ok"] and out["result"]["lock_acquired"] is False


def test_holdout_rejected_when_dev_oos_missing(repo: Repository) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_x", family="vwap_reject_fade", instrument="NQ", timeframe="5m",
        params={"stop_ticks": 8, "target_ticks": 24},
        mechanism=("A reasonable mechanism paragraph that is well above the 50-char "
                   "minimum the repository enforces."),
        registered_by="test",
    )
    repo.start_run(run_id="r_x", hypothesis_id="h_x", mode="hardened",
                   config_hash="x", yaml_path="x", artifact_dir="x")
    repo.complete_run("r_x")
    repo.record_candidate(
        candidate_id="c_x", run_id="r_x", rank_in_run=1, params={},
        gate_verdict="survivor",  # but no n_trades_dev/oos
    )
    out = request_locked_holdout(repo, candidate_id="c_x", requested_by="x")
    assert out["ok"] is False and out["code"] == "hardening_incomplete"


def test_holdout_rejected_for_non_survivor(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="rejected")
    out = request_locked_holdout(repo, candidate_id="c_t_001", requested_by="x")
    assert out["ok"] is False and out["code"] == "not_a_survivor"


def test_holdout_unknown_candidate(repo: Repository) -> None:
    out = request_locked_holdout(repo, candidate_id="c_none", requested_by="x")
    assert out["ok"] is False and out["code"] == "unknown_candidate"


# ============================================================================
# write_post_mortem
# ============================================================================


def _graveyard_candidate(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="rejected")
    repo.set_triage(
        candidate_id="c_t_001", state="graveyard",
        reason="failed adjusted t-test under multiple-comparison correction",
        triaged_by="agent:triage",
    )


def test_post_mortem_happy(repo: Repository, tmp_path: Path) -> None:
    _graveyard_candidate(repo)
    body = (
        "# Post-mortem for c_t_001\n\n"
        "The candidate c_t_001 failed the adjusted t-test on a small sample.\n"
        "Reasonable amount of additional narrative detail to clear the 200-char floor "
        "the schema enforces. Mechanism caveats noted.\n"
    )
    out = write_post_mortem(
        repo, candidate_id="c_t_001", markdown=body, authored_by="agent:scribe",
    )
    assert out["ok"] and out["result"]["written"] is True


def test_post_mortem_requires_graveyard_state(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")
    body = "x" * 220 + " c_t_001"
    out = write_post_mortem(
        repo, candidate_id="c_t_001", markdown=body, authored_by="x",
    )
    assert out["ok"] is False and out["code"] == "not_graveyarded"


def test_post_mortem_requires_citation(repo: Repository) -> None:
    _graveyard_candidate(repo)
    body = "y" * 220
    out = write_post_mortem(
        repo, candidate_id="c_t_001", markdown=body, authored_by="x",
    )
    assert out["ok"] is False and out["code"] == "markdown_missing_citation"


def test_post_mortem_unknown_candidate(repo: Repository) -> None:
    body = "this body cites c_none and is otherwise long enough to pass " * 5
    out = write_post_mortem(
        repo, candidate_id="c_none", markdown=body, authored_by="x",
    )
    assert out["ok"] is False and out["code"] == "unknown_candidate"


def test_post_mortem_short_body_schema_fail(repo: Repository) -> None:
    _graveyard_candidate(repo)
    out = write_post_mortem(
        repo, candidate_id="c_t_001", markdown="too short c_t_001", authored_by="x",
    )
    assert out["ok"] is False and out["code"] == "schema_validation_failed"


# ============================================================================
# enroll_shadow_trader
# ============================================================================


def test_shadow_enroll_happy(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor",
                    n_trades_holdout=30, pf_holdout=1.4)
    out = enroll_shadow_trader(
        repo, candidate_id="c_t_001",
        reason="passed dev/oos and locked holdout; ready for forward shadow",
        enrolled_by="agent:triage",
    )
    assert out["ok"] and out["result"]["enrolled"] is True


def test_shadow_enroll_requires_survivor(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="rejected",
                    n_trades_holdout=30, pf_holdout=1.4)
    out = enroll_shadow_trader(
        repo, candidate_id="c_t_001",
        reason="should be rejected because gate verdict says so",
        enrolled_by="x",
    )
    assert out["ok"] is False and out["code"] == "not_a_survivor"


def test_shadow_enroll_requires_holdout(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor")  # no holdout
    out = enroll_shadow_trader(
        repo, candidate_id="c_t_001",
        reason="should be rejected because no locked-holdout evaluation exists",
        enrolled_by="x",
    )
    assert out["ok"] is False and out["code"] == "holdout_not_evaluated"


def test_shadow_enroll_requires_passing_holdout(repo: Repository) -> None:
    _seed_candidate(repo, gate_verdict="survivor",
                    n_trades_holdout=20, pf_holdout=0.95)
    out = enroll_shadow_trader(
        repo, candidate_id="c_t_001",
        reason="should be rejected because PF below 1.0 on holdout",
        enrolled_by="x",
    )
    assert out["ok"] is False and out["code"] == "holdout_did_not_pass"


def test_shadow_enroll_unknown(repo: Repository) -> None:
    out = enroll_shadow_trader(
        repo, candidate_id="c_none",
        reason="trying to enroll a non-existent candidate", enrolled_by="x",
    )
    assert out["ok"] is False and out["code"] == "unknown_candidate"
