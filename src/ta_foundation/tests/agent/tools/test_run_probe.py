"""Tests for run_probe and record_candidates_for_run.

The actual CLI subprocess is monkey-patched via the `_invoke_cli` seam so
tests run instantly and do not require a working ingest pipeline. The
production wiring is tested manually as part of A.4 backfill / Phase B.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ta_foundation.agent.tools.write import run_probe as run_probe_mod
from ta_foundation.agent.tools.write.run_probe import (
    record_candidates_for_run,
    run_probe,
)
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


@pytest.fixture()
def hypothesis_and_yaml(repo: Repository, tmp_path: Path):
    repo.register_hypothesis(
        hypothesis_id="h_runprobe_001",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=("A reasonable mechanism paragraph that is well above the 50-char "
                   "minimum the repository enforces."),
        registered_by="test",
    )
    yaml = tmp_path / "probe.yaml"
    yaml.write_text("pre_registration:\n  hypothesis_id: h_runprobe_001\n",
                    encoding="utf-8")
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"
    return SimpleNamespace(
        hypothesis_id="h_runprobe_001",
        yaml_path=str(yaml),
        input_dir=str(input_dir),
        output_dir=str(output_dir),
    )


def _patch_invoke(monkeypatch, *, returncode: int = 0,
                  stdout: str = "ok", stderr: str = "") -> list[tuple]:
    """Replace _invoke_cli with a recorder; returns the list of recorded calls."""
    calls: list[tuple] = []

    def fake(cmd, *, timeout_seconds: int):
        calls.append((tuple(cmd), timeout_seconds))
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=returncode, stdout=stdout, stderr=stderr,
        )

    monkeypatch.setattr(run_probe_mod, "_invoke_cli", fake)
    return calls


# ============================================================================
# run_probe — happy and failure paths
# ============================================================================


def test_run_probe_happy_path_marks_run_completed(
    repo: Repository, hypothesis_and_yaml, monkeypatch
) -> None:
    calls = _patch_invoke(monkeypatch, returncode=0, stdout="all good")

    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    assert out["ok"], out
    result = out["result"]
    assert result["exit_code"] == 0
    assert result["ok_subprocess"] is True
    run = repo.get_run(result["run_id"])
    assert run is not None and run.status == "completed"
    assert run.config_hash and len(run.config_hash) == 64
    assert len(calls) == 1
    cmd, _to = calls[0]
    assert "--hypothesis-id" in cmd
    assert hypothesis_and_yaml.hypothesis_id in cmd


def test_run_probe_nonzero_exit_marks_run_failed(
    repo: Repository, hypothesis_and_yaml, monkeypatch
) -> None:
    _patch_invoke(monkeypatch, returncode=3, stderr="drift detected\nthings bad")

    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="hardened",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    result = out["result"]
    assert out["ok"] is True  # tool succeeded; subprocess failed.
    assert result["ok_subprocess"] is False
    assert result["exit_code"] == 3
    run = repo.get_run(result["run_id"])
    assert run is not None and run.status == "failed"
    assert run.error and "drift detected" in run.error or "things bad" in run.error


def test_run_probe_subprocess_timeout_marks_failed(
    repo: Repository, hypothesis_and_yaml, monkeypatch
) -> None:
    def boom(cmd, *, timeout_seconds: int):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_seconds)

    monkeypatch.setattr(run_probe_mod, "_invoke_cli", boom)

    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
        timeout_seconds=60,
    )
    result = out["result"]
    assert result["ok_subprocess"] is False
    run = repo.get_run(result["run_id"])
    assert run is not None and run.status == "failed"
    assert "timeout" in (run.error or "")


def test_run_probe_unknown_hypothesis_rejected(
    repo: Repository, tmp_path: Path, monkeypatch
) -> None:
    yaml = tmp_path / "probe.yaml"
    yaml.write_text("x", encoding="utf-8")
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    out = run_probe(
        repo,
        hypothesis_id="h_does_not_exist",
        yaml_path=str(yaml),
        mode="fast_probe",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
    )
    assert out["ok"] is False
    assert out["code"] == "unknown_hypothesis"


def test_run_probe_missing_yaml_rejected(
    repo: Repository, hypothesis_and_yaml, tmp_path: Path
) -> None:
    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=str(tmp_path / "nope.yaml"),
        mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    assert out["ok"] is False
    assert out["code"] == "yaml_not_found"


def test_run_probe_missing_input_dir_rejected(
    repo: Repository, hypothesis_and_yaml, tmp_path: Path
) -> None:
    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="fast_probe",
        input_dir=str(tmp_path / "no_such_dir"),
        output_dir=hypothesis_and_yaml.output_dir,
    )
    assert out["ok"] is False
    assert out["code"] == "input_dir_not_found"


def test_run_probe_invalid_mode_schema_fail(
    repo: Repository, hypothesis_and_yaml
) -> None:
    out = run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="not_a_mode",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"


def test_run_probe_creates_output_dir(
    repo: Repository, hypothesis_and_yaml, monkeypatch, tmp_path: Path
) -> None:
    fresh_out = tmp_path / "auto_created_output"
    assert not fresh_out.exists()
    _patch_invoke(monkeypatch, returncode=0)

    run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=str(fresh_out),
    )
    assert fresh_out.is_dir()


def test_run_probe_passes_market_and_ledger_flags(
    repo: Repository, hypothesis_and_yaml, monkeypatch, tmp_path: Path
) -> None:
    calls = _patch_invoke(monkeypatch, returncode=0)

    run_probe(
        repo,
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path,
        mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
        market_data_root=str(tmp_path / "MarketData"),
        ledger_db=str(tmp_path / "ledger_alt.db"),
    )
    cmd, _to = calls[0]
    assert "--market-data" in cmd
    assert "--ledger-db" in cmd


def test_run_probe_records_config_hash(
    repo: Repository, hypothesis_and_yaml, monkeypatch
) -> None:
    _patch_invoke(monkeypatch, returncode=0)
    out1 = run_probe(
        repo, hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path, mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    # Mutate the YAML and run again — config_hash should differ.
    Path(hypothesis_and_yaml.yaml_path).write_text(
        "pre_registration:\n  hypothesis_id: h_runprobe_001\n  altered: true\n",
        encoding="utf-8",
    )
    out2 = run_probe(
        repo, hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        yaml_path=hypothesis_and_yaml.yaml_path, mode="fast_probe",
        input_dir=hypothesis_and_yaml.input_dir,
        output_dir=hypothesis_and_yaml.output_dir,
    )
    r1 = repo.get_run(out1["result"]["run_id"])
    r2 = repo.get_run(out2["result"]["run_id"])
    assert r1.config_hash != r2.config_hash  # type: ignore[union-attr]


# ============================================================================
# record_candidates_for_run
# ============================================================================


@pytest.fixture()
def started_run(repo: Repository, hypothesis_and_yaml):
    repo.start_run(
        run_id="r_completed",
        hypothesis_id=hypothesis_and_yaml.hypothesis_id,
        mode="hardened",
        config_hash="x" * 64,
        yaml_path=hypothesis_and_yaml.yaml_path,
        artifact_dir=hypothesis_and_yaml.output_dir,
    )
    repo.complete_run("r_completed")
    return "r_completed"


def test_record_candidates_happy(repo: Repository, started_run: str) -> None:
    out = record_candidates_for_run(
        repo,
        run_id=started_run,
        candidates=[
            {
                "candidate_id": "c_a",
                "rank_in_run": 1,
                "params": {"target_ticks": 24},
                "gate_verdict": "survivor",
                "n_trades_dev": 100, "pf_dev": 1.7,
            },
            {
                "candidate_id": "c_b",
                "rank_in_run": 2,
                "params": {"target_ticks": 50},
                "gate_verdict": "rejected",
            },
        ],
    )
    assert out["ok"]
    assert out["result"]["n_inserted"] == 2
    assert set(out["result"]["inserted_candidate_ids"]) == {"c_a", "c_b"}
    assert out["result"]["n_rejected"] == 0
    assert repo.get_candidate("c_a") is not None
    assert repo.get_candidate("c_b") is not None


def test_record_candidates_unknown_run(repo: Repository) -> None:
    out = record_candidates_for_run(
        repo, run_id="r_does_not_exist",
        candidates=[{"candidate_id": "c_x", "rank_in_run": 1, "params": {}}],
    )
    assert out["ok"] is False
    assert out["code"] == "unknown_run"


def test_record_candidates_partial_rejection(repo: Repository, started_run: str) -> None:
    out = record_candidates_for_run(
        repo,
        run_id=started_run,
        candidates=[
            {"candidate_id": "c_ok", "rank_in_run": 1, "params": {}},
            {"candidate_id": "c_bad", "rank_in_run": 2, "params": {},
             "gate_verdict": "amazing"},  # invalid value
            "not a dict at all",
            {"rank_in_run": 3, "params": {}},  # missing candidate_id
        ],
    )
    res = out["result"]
    assert res["n_inserted"] == 1
    assert res["inserted_candidate_ids"] == ["c_ok"]
    assert res["n_rejected"] == 3
    codes = {row["code"] for row in res["rejected"]}
    assert codes == {"insert_failed", "not_a_dict", "missing_required"}


def test_record_candidates_empty_list(repo: Repository, started_run: str) -> None:
    out = record_candidates_for_run(repo, run_id=started_run, candidates=[])
    assert out["ok"]
    assert out["result"]["n_inserted"] == 0
    assert out["result"]["n_rejected"] == 0


def test_record_candidates_schema_validation_fails_for_too_long(
    repo: Repository, started_run: str
) -> None:
    too_many = [
        {"candidate_id": f"c_{i}", "rank_in_run": i, "params": {}}
        for i in range(250)
    ]
    out = record_candidates_for_run(repo, run_id=started_run, candidates=too_many)
    assert out["ok"] is False
    assert out["code"] == "schema_validation_failed"
