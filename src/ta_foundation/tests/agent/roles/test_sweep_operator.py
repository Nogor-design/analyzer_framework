"""Tests for the Sweep Operator role (C.2) and the operator queue helpers.

The real run_probe write tool spawns a subprocess; here we inject a stub via
the `run_probe_call` parameter so the Operator's logic is exercised without
touching the discovery CLI. Candidate rows are written directly to the
ledger via the repository to simulate the post-run sidecar ingest that the
CLI does in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from ta_foundation.agent.roles.sweep_operator import (
    DEFAULT_OPERATOR_LIMIT,
    HypothesisRunRecord,
    OperatorReport,
    discover_accepted_hypotheses,
    resolve_yaml_path_via_author_probe,
    run_one_hypothesis,
    run_operator_pass,
)
from ta_foundation.research_ledger import Repository, get_repository


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


@pytest.fixture()
def hyp(repo: Repository) -> str:
    repo.register_hypothesis(
        hypothesis_id="h_op_001",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=("A reasonable mechanism paragraph that comfortably exceeds "
                   "the 50-character minimum the repository enforces."),
        registered_by="test",
    )
    return "h_op_001"


@pytest.fixture()
def yaml_and_dirs(tmp_path: Path) -> dict:
    yaml = tmp_path / "h_op_001.yaml"
    yaml.write_text("pre_registration:\n  hypothesis_id: h_op_001\n",
                    encoding="utf-8")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return {
        "yaml_path": str(yaml),
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
    }


# ============================================================================
# Stubbed run_probe
# ============================================================================


class _ProbeRecorder:
    """Stub for run_probe_call.

    Tracks every call. Optionally seeds candidates into the ledger so the
    Operator's downstream gating logic has something to read. By default
    returns ok_subprocess=True. Callers can flip flags per-mode.
    """

    def __init__(
        self,
        repo: Repository,
        hypothesis_id: str,
        yaml_path: str,
        *,
        fast_candidates: list[dict] | None = None,
        hardened_candidates: list[dict] | None = None,
        fast_ok: bool = True,
        hardened_ok: bool = True,
        fast_tool_ok: bool = True,
        hardened_tool_ok: bool = True,
    ) -> None:
        self.repo = repo
        self.hypothesis_id = hypothesis_id
        self.yaml_path = yaml_path
        self.fast_candidates = fast_candidates or []
        self.hardened_candidates = hardened_candidates or []
        self.fast_ok = fast_ok
        self.hardened_ok = hardened_ok
        self.fast_tool_ok = fast_tool_ok
        self.hardened_tool_ok = hardened_tool_ok
        self.calls: list[dict] = []
        self._counter = 0

    def __call__(self, repo: Repository, **kwargs: Any) -> dict:
        self.calls.append(dict(kwargs))
        mode = kwargs["mode"]
        self._counter += 1
        run_id = f"r_{self.hypothesis_id}_{mode}_{self._counter}"

        # Simulate the journaled-tool envelope rejection path.
        if mode == "fast_probe" and not self.fast_tool_ok:
            return {"ok": False, "code": "rejected_by_test",
                    "error": "fake tool failure"}
        if mode == "hardened" and not self.hardened_tool_ok:
            return {"ok": False, "code": "rejected_by_test",
                    "error": "fake tool failure"}

        # Start + end a run so list_candidates(run_id=...) returns the seeded rows.
        repo.start_run(
            run_id=run_id,
            hypothesis_id=self.hypothesis_id,
            mode=mode,
            config_hash="x" * 64,
            yaml_path=self.yaml_path,
            artifact_dir=kwargs["output_dir"],
        )
        ok_subprocess = self.fast_ok if mode == "fast_probe" else self.hardened_ok
        if not ok_subprocess:
            repo.fail_run(run_id, error="stubbed subprocess failure")
            return {
                "ok": True,
                "result": {
                    "run_id": run_id,
                    "exit_code": 1,
                    "ok_subprocess": False,
                    "error": "stubbed subprocess failure",
                    "artifact_dir": kwargs["output_dir"],
                },
            }

        repo.complete_run(run_id)
        seed = self.fast_candidates if mode == "fast_probe" else self.hardened_candidates
        for i, c in enumerate(seed):
            repo.record_candidate(
                candidate_id=c["candidate_id"],
                run_id=run_id,
                rank_in_run=c.get("rank_in_run", i + 1),
                params=c.get("params", {}),
                gate_verdict=c.get("gate_verdict", "pending"),
                n_trades_dev=c.get("n_trades_dev"),
                pf_dev=c.get("pf_dev"),
            )

        return {
            "ok": True,
            "result": {
                "run_id": run_id,
                "exit_code": 0,
                "ok_subprocess": True,
                "artifact_dir": kwargs["output_dir"],
                "stdout_tail": "ok",
            },
        }


# ============================================================================
# Happy paths
# ============================================================================


def test_fast_survivor_escalates_to_hardened(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"],
        fast_candidates=[
            {"candidate_id": "c_fast_a", "gate_verdict": "survivor",
             "n_trades_dev": 60, "pf_dev": 1.9},
            {"candidate_id": "c_fast_b", "gate_verdict": "rejected"},
        ],
        hardened_candidates=[
            {"candidate_id": "c_hard_a", "gate_verdict": "survivor",
             "n_trades_dev": 47, "pf_dev": 1.7},
        ],
    )

    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )

    assert rec.status == "completed_hardened"
    assert rec.n_candidates_fast == 2
    assert rec.n_survivors_fast == 1
    assert rec.n_candidates_hardened == 1
    assert rec.n_survivors_hardened == 1
    assert [c["mode"] for c in probe.calls] == ["fast_probe", "hardened"]


def test_fast_only_no_survivor_does_not_escalate(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"],
        fast_candidates=[
            {"candidate_id": "c_fast_a", "gate_verdict": "rejected"},
            {"candidate_id": "c_fast_b", "gate_verdict": "pending"},
        ],
    )

    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )

    assert rec.status == "completed_fast_only"
    assert rec.n_candidates_fast == 2
    assert rec.n_survivors_fast == 0
    assert rec.hardened_run_id is None
    assert [c["mode"] for c in probe.calls] == ["fast_probe"]


# ============================================================================
# no_trades retirement
# ============================================================================


def test_zero_candidates_retires_hypothesis_not_graveyards(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(repo, hyp, yaml_and_dirs["yaml_path"],
                            fast_candidates=[])

    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )

    assert rec.status == "no_trades"
    h = repo.get_hypothesis(hyp)
    assert h is not None and h.status == "retired"
    # No hardened call should have been attempted.
    assert [c["mode"] for c in probe.calls] == ["fast_probe"]
    # No candidate rows means there is nothing in the graveyard either.
    assert repo.list_graveyard(family="vwap_reject_fade", limit=10) == []


# ============================================================================
# Failure paths
# ============================================================================


def test_fast_subprocess_failure_reports_and_stops(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"], fast_ok=False,
    )
    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "fast_run_failed"
    assert "stubbed subprocess failure" in (rec.error or "")
    assert [c["mode"] for c in probe.calls] == ["fast_probe"]
    # The hypothesis must remain 'open' — failure is not a retirement.
    assert repo.get_hypothesis(hyp).status == "open"  # type: ignore[union-attr]


def test_fast_tool_rejection_no_run_started(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"], fast_tool_ok=False,
    )
    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "fast_run_failed"
    assert rec.fast_run_id is None
    assert "fake tool failure" in (rec.error or "")


def test_hardened_failure_after_fast_survivor(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"],
        fast_candidates=[{"candidate_id": "c_fast_a", "gate_verdict": "survivor"}],
        hardened_ok=False,
    )
    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "hardened_run_failed"
    assert rec.fast_run_id is not None
    assert rec.hardened_run_id is not None
    assert rec.n_survivors_fast == 1


# ============================================================================
# Discipline guards
# ============================================================================


def test_locked_holdout_never_invoked_across_all_paths(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(
        repo, hyp, yaml_and_dirs["yaml_path"],
        fast_candidates=[{"candidate_id": "c_fast_a", "gate_verdict": "survivor"}],
        hardened_candidates=[{"candidate_id": "c_hard_a", "gate_verdict": "survivor"}],
    )
    run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    modes = {c["mode"] for c in probe.calls}
    assert "locked_holdout" not in modes
    assert modes == {"fast_probe", "hardened"}


def test_already_completed_run_skips(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    repo.start_run(
        run_id="r_existing", hypothesis_id=hyp, mode="fast_probe",
        config_hash="z" * 64, yaml_path=yaml_and_dirs["yaml_path"],
        artifact_dir=yaml_and_dirs["output_dir"],
    )
    repo.complete_run("r_existing")

    probe = _ProbeRecorder(repo, hyp, yaml_and_dirs["yaml_path"])
    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "skipped_already_run"
    assert probe.calls == []  # no run_probe invoked


def test_retired_hypothesis_is_skipped(
    repo: Repository, hyp: str, yaml_and_dirs: dict
) -> None:
    repo.set_hypothesis_status(hyp, "retired")
    probe = _ProbeRecorder(repo, hyp, yaml_and_dirs["yaml_path"])
    rec = run_one_hypothesis(
        repo, hypothesis_id=hyp,
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "skipped_not_open"
    assert probe.calls == []


def test_unknown_hypothesis_is_skipped(
    repo: Repository, yaml_and_dirs: dict
) -> None:
    probe = _ProbeRecorder(repo, "h_ghost", yaml_and_dirs["yaml_path"])
    rec = run_one_hypothesis(
        repo, hypothesis_id="h_ghost",
        yaml_path=yaml_and_dirs["yaml_path"],
        input_dir=yaml_and_dirs["input_dir"],
        output_dir=yaml_and_dirs["output_dir"],
        run_probe_call=probe,
    )
    assert rec.status == "skipped_unknown_hypothesis"
    assert probe.calls == []


# ============================================================================
# Queue discovery
# ============================================================================


def test_discover_accepted_skips_retired_and_already_run(
    repo: Repository, tmp_path: Path
) -> None:
    accepted_dir = tmp_path / "accepted"
    accepted_dir.mkdir()

    # Three open hypotheses + drafts.
    for hid in ["h_a", "h_b", "h_c"]:
        repo.register_hypothesis(
            hypothesis_id=hid,
            family="vwap_reject_fade", instrument="NQ", timeframe="5m",
            params={"target_ticks": 24, "stop_ticks": 8},
            mechanism=("Mechanism paragraph that comfortably exceeds the 50 "
                       f"character minimum for hypothesis {hid}."),
            registered_by="test",
        )
        (accepted_dir / f"{hid}.md").write_text("draft", encoding="utf-8")

    # h_b already has a completed run; h_c has been retired.
    repo.start_run(run_id="r_b", hypothesis_id="h_b", mode="fast_probe",
                   config_hash="y" * 64, yaml_path="x", artifact_dir="x")
    repo.complete_run("r_b")
    repo.set_hypothesis_status("h_c", "retired")

    queue = discover_accepted_hypotheses(repo, accepted_dir=accepted_dir)
    assert queue == ["h_a"]


def test_discover_accepted_returns_empty_when_dir_missing(
    repo: Repository, tmp_path: Path
) -> None:
    queue = discover_accepted_hypotheses(
        repo, accepted_dir=tmp_path / "nope",
    )
    assert queue == []


def test_run_operator_pass_processes_queue_and_aggregates(
    repo: Repository, tmp_path: Path
) -> None:
    accepted_dir = tmp_path / "accepted"
    accepted_dir.mkdir()
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    yaml_paths: dict[str, str] = {}
    for hid in ["h_q1", "h_q2"]:
        repo.register_hypothesis(
            hypothesis_id=hid,
            family="vwap_reject_fade", instrument="NQ", timeframe="5m",
            params={"target_ticks": 24, "stop_ticks": 8, "hid": hid},
            mechanism=("Mechanism paragraph longer than the 50-character minimum "
                       f"for {hid}."),
            registered_by="test",
        )
        y = tmp_path / f"{hid}.yaml"
        y.write_text(f"pre_registration:\n  hypothesis_id: {hid}\n",
                     encoding="utf-8")
        yaml_paths[hid] = str(y)
        (accepted_dir / f"{hid}.md").write_text("d", encoding="utf-8")

    # h_q1 → fast survivor → hardened; h_q2 → zero candidates → retire.
    counter = {"n": 0}

    def fake_probe(repo_: Repository, **kw: Any) -> dict:
        counter["n"] += 1
        hid = kw["hypothesis_id"]
        mode = kw["mode"]
        run_id = f"r_{hid}_{mode}_{counter['n']}"
        repo_.start_run(run_id=run_id, hypothesis_id=hid, mode=mode,
                        config_hash="a" * 64, yaml_path=kw["yaml_path"],
                        artifact_dir=kw["output_dir"])
        repo_.complete_run(run_id)
        if hid == "h_q1" and mode == "fast_probe":
            repo_.record_candidate(
                candidate_id=f"c_{counter['n']}", run_id=run_id,
                rank_in_run=1, params={}, gate_verdict="survivor",
                n_trades_dev=50, pf_dev=1.8,
            )
        elif hid == "h_q1" and mode == "hardened":
            repo_.record_candidate(
                candidate_id=f"c_{counter['n']}", run_id=run_id,
                rank_in_run=1, params={}, gate_verdict="survivor",
                n_trades_dev=42, pf_dev=1.6,
            )
        # h_q2: no candidates recorded → triggers retire-no-trades.
        return {"ok": True, "result": {"run_id": run_id, "exit_code": 0,
                                         "ok_subprocess": True,
                                         "artifact_dir": kw["output_dir"]}}

    report = run_operator_pass(
        repo,
        yaml_path_resolver=lambda hid: yaml_paths.get(hid),
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        run_probe_call=fake_probe,
        accepted_dir=accepted_dir,
    )

    assert report.requested == 2
    assert report.processed == 2
    assert report.no_trades_retired == 1
    assert report.survivors_to_triage == 1
    assert report.failures == 0
    assert {r.hypothesis_id: r.status for r in report.records} == {
        "h_q1": "completed_hardened",
        "h_q2": "no_trades",
    }
    assert repo.get_hypothesis("h_q2").status == "retired"  # type: ignore[union-attr]
    assert repo.get_hypothesis("h_q1").status == "open"  # type: ignore[union-attr]


def test_run_operator_pass_missing_yaml_path_is_failure(
    repo: Repository, hyp: str, tmp_path: Path
) -> None:
    accepted_dir = tmp_path / "accepted"
    accepted_dir.mkdir()
    (accepted_dir / f"{hyp}.md").write_text("d", encoding="utf-8")
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    probe_calls: list[dict] = []

    def fake_probe(repo_: Repository, **kw: Any) -> dict:
        probe_calls.append(kw)
        return {"ok": True, "result": {"run_id": "x", "ok_subprocess": True}}

    report = run_operator_pass(
        repo,
        yaml_path_resolver=lambda hid: None,
        input_dir=str(in_dir),
        output_dir=str(tmp_path / "out"),
        run_probe_call=fake_probe,
        accepted_dir=accepted_dir,
    )
    assert report.failures == 1
    assert probe_calls == []
    assert report.records[0].status == "fast_run_failed"
    assert "no yaml path" in (report.records[0].error or "").lower()


# ============================================================================
# Resolver
# ============================================================================


def test_default_resolver_returns_existing_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    gen_dir = tmp_path / "discovery" / "generated"
    gen_dir.mkdir(parents=True)
    target = gen_dir / "h_real.yaml"
    target.write_text("x", encoding="utf-8")
    assert resolve_yaml_path_via_author_probe("h_real") == str(
        Path("discovery/generated/h_real.yaml")
    )


def test_default_resolver_returns_none_when_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_yaml_path_via_author_probe("h_missing") is None
