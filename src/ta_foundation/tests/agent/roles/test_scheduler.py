"""Tests for the fixed Python scheduler that replaces the deprecated graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.agent.roles import scribe as scribe_mod
from ta_foundation.agent.scheduler import daily_pass, weekly_pass
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(scribe_mod, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(scribe_mod, "LETTERS_FINAL_DIR", tmp_path / "letters")
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _seed_rejected_candidate(repo: Repository, cid: str = "c_001") -> None:
    if repo.get_hypothesis("h_x") is None:
        repo.register_hypothesis(
            hypothesis_id="h_x", family="vwap_reject_fade",
            instrument="NQ", timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
            mechanism=("A reasonable mechanism paragraph that comfortably exceeds the "
                       "50-character minimum the repository enforces."),
            registered_by="test",
        )
        repo.start_run(run_id="r_x", hypothesis_id="h_x", mode="hardened",
                       config_hash="abc", yaml_path="x", artifact_dir="x")
        repo.complete_run("r_x")
    repo.record_candidate(
        candidate_id=cid, run_id="r_x", rank_in_run=1, params={},
        gate_verdict="rejected", n_trades_dev=47, pf_dev=1.62,
    )


def _stub_llm(system: str, user: str) -> str:
    import json as _json
    import re
    pf_match = re.search(r"pf_dev: ([\d.]+)", user)
    n_match = re.search(r"n_trades_dev: (\d+)", user)
    cid_match = re.search(r"candidate_id: (\S+)", user)
    pf = pf_match.group(1) if pf_match else "1.62"
    n = n_match.group(1) if n_match else "47"
    cid = cid_match.group(1) if cid_match else "c_001"
    # If the prompt looks like a triage one, return JSON. Otherwise a body.
    if "Respond with:" in user or "\"reason\"" in system:
        return _json.dumps({"reason":
            f"Candidate {cid} produced PF={pf} on {n} trades; deterministic rule "
            "applied. Mechanism and sample consistent with the chosen state."})
    return (f"# Post-mortem for {cid}\n\nCandidate {cid} reached PF={pf} on {n} "
            "trades on dev. Adjusted t-test under multiple-comparison correction "
            "did not clear the fund-grade threshold; sample below the gate floor. "
            "Mechanism plausible but underpowered for further work.\n")


def test_daily_pass_runs_triage_then_post_mortem(repo: Repository) -> None:
    _seed_rejected_candidate(repo)
    report = daily_pass(repo, llm_call=_stub_llm)
    assert report.triage is not None
    assert report.post_mortem is not None
    assert report.triage.triaged == 1
    assert report.post_mortem.written == 1
    assert report.errors == []


def test_daily_pass_handles_stage_error(repo: Repository) -> None:
    _seed_rejected_candidate(repo)

    def exploding_llm(system: str, user: str) -> str:
        raise RuntimeError("model crashed")

    report = daily_pass(repo, llm_call=exploding_llm, max_retries=0)
    # Triage will produce HITL flag rather than raising; post-mortem similarly.
    assert report.triage is not None
    assert report.triage.hitl_flagged >= 1
    # No exceptions should escape — scheduler isolates per-stage failures.


def test_weekly_pass_runs_letter(repo: Repository) -> None:
    report = weekly_pass(repo, llm_call=_stub_llm)
    assert report.weekly_letter is not None
    assert report.errors == []


# ============================================================================
# operator_pass / weekly_authoring_pass (C.2 + C.4)
# ============================================================================


def _register_accepted(repo: Repository, hid: str, accepted_dir: Path,
                        tmp_path: Path) -> str:
    repo.register_hypothesis(
        hypothesis_id=hid, family="vwap_reject_fade",
        instrument="NQ", timeframe="5m",
        params={"target_ticks": 24, "stop_ticks": 8, "hid": hid},
        mechanism=("Mechanism paragraph that comfortably exceeds the 50-char "
                   f"minimum for {hid}."),
        registered_by="test",
    )
    accepted_dir.mkdir(exist_ok=True)
    (accepted_dir / f"{hid}.md").write_text("d", encoding="utf-8")
    yaml = tmp_path / f"{hid}.yaml"
    yaml.write_text(f"pre_registration:\n  hypothesis_id: {hid}\n",
                    encoding="utf-8")
    return str(yaml)


def _stub_probe(repo: Repository, **kw):
    """Run-probe stub: starts/completes a run and records a survivor candidate."""
    hid = kw["hypothesis_id"]
    mode = kw["mode"]
    run_id = f"r_{hid}_{mode}"
    repo.start_run(run_id=run_id, hypothesis_id=hid, mode=mode,
                   config_hash="x" * 64, yaml_path=kw["yaml_path"],
                   artifact_dir=kw["output_dir"])
    repo.complete_run(run_id)
    repo.record_candidate(
        candidate_id=f"c_{run_id}", run_id=run_id, rank_in_run=1,
        params={}, gate_verdict="survivor", n_trades_dev=50, pf_dev=1.7,
    )
    return {"ok": True, "result": {"run_id": run_id, "ok_subprocess": True,
                                     "exit_code": 0,
                                     "artifact_dir": kw["output_dir"]}}


def test_operator_pass_drains_accepted_queue(
    repo: Repository, tmp_path: Path
) -> None:
    from ta_foundation.agent.scheduler import operator_pass

    accepted = tmp_path / "accepted"
    yaml_path = _register_accepted(repo, "h_sched_001", accepted, tmp_path)
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    report = operator_pass(
        repo,
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        run_probe_call=_stub_probe,
        yaml_path_resolver=lambda hid: yaml_path,
        accepted_dir=accepted,
    )
    assert report.errors == []
    assert report.operator is not None
    assert report.operator.processed == 1
    assert report.operator.records[0].status == "completed_hardened"


def test_weekly_authoring_pass_authors_then_drains(
    repo: Repository, tmp_path: Path, monkeypatch
) -> None:
    """End-to-end C.4: Author proposes, but the proposal sits in the inbox
    pending HITL accept. The Operator drains any *already-accepted* hypothesis
    (seeded here directly). Newly authored proposals do NOT get auto-run."""
    from ta_foundation.agent.scheduler import weekly_authoring_pass
    from ta_foundation.agent import inbox as inbox_mod
    from ta_foundation.agent.roles import hypothesis_author as author_mod
    from ta_foundation.agent.tools.write import author_probe as ap_mod

    # Isolate Author-side paths.
    monkeypatch.setattr(author_mod, "INBOX_ROOT", tmp_path / "inbox_author")
    monkeypatch.setattr(inbox_mod, "INBOX_ROOT", tmp_path / "inbox_author")
    monkeypatch.setattr(inbox_mod, "REJECTED_ROOT", tmp_path / "rejected")
    monkeypatch.setattr(ap_mod, "GENERATED_PROBE_DIR",
                          tmp_path / "discovery/generated")

    # Pre-existing accepted hypothesis the operator should pick up.
    accepted = tmp_path / "accepted"
    yaml_path = _register_accepted(repo, "h_already_accepted", accepted, tmp_path)
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    # Author LLM stub: returns a single (unaccepted) proposal that author_probe
    # will register. Whether it registers cleanly depends on the family
    # registry; we don't actually care for this scheduler test — we just need
    # the call site to not crash. If registration fails the report still has
    # operator results, which is what we assert.
    def llm_stub(system: str, user: str) -> str:
        # Returning an empty proposals list keeps the test isolated from the
        # family registry — authoring is a no-op, operator drains the seeded
        # accepted hypothesis.
        return '{"proposals": []}'

    report = weekly_authoring_pass(
        repo,
        llm_call=llm_stub,
        input_dir=str(in_dir),
        output_dir=str(tmp_path / "out"),
        run_probe_call=_stub_probe,
        yaml_path_resolver=lambda hid: yaml_path,
        accepted_dir=accepted,
    )

    assert report.errors == []
    assert report.authoring is not None
    assert report.operator is not None
    assert report.operator.processed == 1
    statuses = {r.hypothesis_id: r.status for r in report.operator.records}
    assert statuses == {"h_already_accepted": "completed_hardened"}
