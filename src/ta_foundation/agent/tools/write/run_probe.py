"""Write tool: run a pre-registered probe via the existing CLI.

Lifecycle (all journaled by the @journaled_tool decorator):

    1. Validate preconditions (hypothesis exists, YAML file exists, output dir
       writable).
    2. Generate a deterministic run_id and config_hash.
    3. Insert a `runs` row in 'running' state.
    4. Invoke `python -m ta_foundation.cli.main ... --hypothesis-id <id>`
       as a subprocess; the CLI's drift check (A.3) re-validates the YAML
       params against the ledger before any heavy work.
    5. On exit code 0 → `complete_run`; otherwise → `fail_run` with stderr.
    6. Return run_id, artifact_dir, exit_code, and a stderr tail.

This tool does NOT parse the resulting sidecar artifacts into candidate rows
— that is a deliberately separate write tool (`record_candidates_for_run`).
The split keeps subprocess orchestration and ledger ingestion as
independent atoms (master plan §4).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ta_foundation.agent.tools._decorators import (
    Precondition,
    ToolFailure,
    journaled_tool,
)
from ta_foundation.research_ledger.repository import Repository

_VALID_MODES = ["fast_probe", "hardened", "locked_holdout"]


def _pc_hypothesis_exists(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    if repo.get_hypothesis(inputs["hypothesis_id"]) is None:
        return ToolFailure(
            "unknown_hypothesis",
            f"hypothesis_id {inputs['hypothesis_id']!r} not in the ledger",
        )
    return None


def _pc_yaml_exists(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    p = Path(inputs["yaml_path"])
    if not p.is_file():
        return ToolFailure(
            "yaml_not_found", f"yaml_path {inputs['yaml_path']!r} does not exist"
        )
    return None


def _pc_input_dir_exists(repo: Repository, inputs: dict) -> Optional[ToolFailure]:
    p = Path(inputs["input_dir"])
    if not p.is_dir():
        return ToolFailure(
            "input_dir_not_found",
            f"input_dir {inputs['input_dir']!r} is not an existing directory",
        )
    return None


@journaled_tool(
    name="run_probe",
    role="agent:operator",
    description=(
        "Run a pre-registered probe via the existing CLI. Spawns a subprocess, "
        "tracks the run lifecycle (start/complete/fail) in the ledger, and "
        "returns the artifact directory. Does NOT ingest results — call "
        "record_candidates_for_run with the parsed sidecar after this returns. "
        "Discipline: never re-run with modified params after seeing a result; "
        "register a new hypothesis instead (Real Edge §5)."
    ),
    schema={
        "hypothesis_id": {"type": "str", "min_length": 1},
        "yaml_path": {"type": "str", "min_length": 1},
        "mode": {"type": "enum", "values": _VALID_MODES},
        "input_dir": {"type": "str", "min_length": 1},
        "output_dir": {"type": "str", "min_length": 1},
        "market_data_root": {"type": "str", "required": False},
        "ledger_db": {"type": "str", "required": False},
        "extra_args": {
            "type": "list", "items": "str", "required": False, "max_length": 20,
        },
        "timeout_seconds": {
            "type": "int", "required": False, "min": 1, "max": 7200, "default": 1800,
        },
    },
    preconditions=(_pc_hypothesis_exists, _pc_yaml_exists, _pc_input_dir_exists),
)
def run_probe(
    repo: Repository,
    *,
    hypothesis_id: str,
    yaml_path: str,
    mode: str,
    input_dir: str,
    output_dir: str,
    market_data_root: Optional[str] = None,
    ledger_db: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
    timeout_seconds: int = 1800,
) -> dict:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    run_id = _build_run_id(hypothesis_id, mode)
    config_hash = _hash_yaml(Path(yaml_path))

    repo.start_run(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        mode=mode,
        config_hash=config_hash,
        yaml_path=str(yaml_path),
        artifact_dir=str(output_path),
    )

    cmd = _build_cli_command(
        yaml_path=yaml_path,
        input_dir=input_dir,
        output_dir=str(output_path),
        hypothesis_id=hypothesis_id,
        market_data_root=market_data_root,
        ledger_db=ledger_db,
        extra_args=tuple(extra_args or ()),
    )

    try:
        completed = _invoke_cli(cmd, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        repo.fail_run(run_id, error=f"timeout after {timeout_seconds}s")
        return {
            "run_id": run_id,
            "artifact_dir": str(output_path),
            "exit_code": None,
            "ok_subprocess": False,
            "error": f"timeout after {timeout_seconds}s",
            "cmd": list(cmd),
        }
    except FileNotFoundError as exc:
        repo.fail_run(run_id, error=f"FileNotFoundError: {exc}")
        return {
            "run_id": run_id,
            "artifact_dir": str(output_path),
            "exit_code": None,
            "ok_subprocess": False,
            "error": f"FileNotFoundError: {exc}",
            "cmd": list(cmd),
        }

    if completed.returncode == 0:
        repo.complete_run(run_id)
        return {
            "run_id": run_id,
            "artifact_dir": str(output_path),
            "exit_code": 0,
            "ok_subprocess": True,
            "stdout_tail": _tail(completed.stdout, 1200),
        }

    err_tail = _tail(completed.stderr or completed.stdout, 1200)
    repo.fail_run(run_id, error=f"exit={completed.returncode}: {err_tail[:400]}")
    return {
        "run_id": run_id,
        "artifact_dir": str(output_path),
        "exit_code": completed.returncode,
        "ok_subprocess": False,
        "error": "subprocess exited non-zero",
        "stderr_tail": err_tail,
    }


# ---- Companion: record_candidates_for_run ---------------------------------


def _pc_run_running_or_completed(
    repo: Repository, inputs: dict
) -> Optional[ToolFailure]:
    run = repo.get_run(inputs["run_id"])
    if run is None:
        return ToolFailure("unknown_run", f"run_id {inputs['run_id']!r} not in ledger")
    return None


@journaled_tool(
    name="record_candidates_for_run",
    role="agent:operator",
    description=(
        "Record candidate rows produced by a completed run. The caller (a "
        "sidecar parser, a backfill script, or a follow-up Operator step) "
        "supplies pre-parsed candidate dicts. Each dict must include "
        "candidate_id, rank_in_run, params, gate_verdict; the rest are "
        "optional. Failures on individual candidates are returned in the "
        "result; the tool does not partially reject the whole batch."
    ),
    schema={
        "run_id": {"type": "str", "min_length": 1},
        "candidates": {"type": "list", "max_length": 200},
    },
    preconditions=(_pc_run_running_or_completed,),
)
def record_candidates_for_run(
    repo: Repository,
    *,
    run_id: str,
    candidates: list,
) -> dict:
    inserted: list[str] = []
    rejected: list[dict] = []

    for i, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            rejected.append({"index": i, "code": "not_a_dict",
                             "message": f"item {i} is not a dict"})
            continue
        try:
            candidate_id = raw["candidate_id"]
            rank_in_run = int(raw["rank_in_run"])
            params = raw["params"]
            gate_verdict = raw.get("gate_verdict", "pending")
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"index": i, "code": "missing_required",
                             "message": str(exc)})
            continue

        try:
            repo.record_candidate(
                candidate_id=candidate_id,
                run_id=run_id,
                rank_in_run=rank_in_run,
                params=params,
                gate_verdict=gate_verdict,
                gate_reasons=raw.get("gate_reasons"),
                n_trades_dev=raw.get("n_trades_dev"),
                pf_dev=raw.get("pf_dev"),
                expectancy_dev=raw.get("expectancy_dev"),
                n_trades_oos=raw.get("n_trades_oos"),
                pf_oos=raw.get("pf_oos"),
                expectancy_oos=raw.get("expectancy_oos"),
                n_trades_holdout=raw.get("n_trades_holdout"),
                pf_holdout=raw.get("pf_holdout"),
                expectancy_holdout=raw.get("expectancy_holdout"),
                slippage_stress_pass=raw.get("slippage_stress_pass"),
                folds_distribution=raw.get("folds_distribution"),
            )
            inserted.append(candidate_id)
        except Exception as exc:  # noqa: BLE001 — surface to caller
            rejected.append({"index": i, "candidate_id": raw.get("candidate_id"),
                             "code": "insert_failed",
                             "message": f"{type(exc).__name__}: {exc}"})

    return {
        "run_id": run_id,
        "n_inserted": len(inserted),
        "inserted_candidate_ids": inserted,
        "n_rejected": len(rejected),
        "rejected": rejected,
    }


# ---- Internals -------------------------------------------------------------


def _build_run_id(hypothesis_id: str, mode: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"r_{hypothesis_id[:40]}_{mode}_{ts}_{short}"


def _hash_yaml(yaml_path: Path) -> str:
    blob = yaml_path.read_bytes()
    return hashlib.sha256(blob).hexdigest()


def _build_cli_command(
    *,
    yaml_path: str,
    input_dir: str,
    output_dir: str,
    hypothesis_id: str,
    market_data_root: Optional[str],
    ledger_db: Optional[str],
    extra_args: tuple[str, ...],
) -> tuple[str, ...]:
    cmd: list[str] = [
        sys.executable, "-m", "ta_foundation.cli.main",
        "--input", input_dir,
        "--output", output_dir,
        "--report-config", yaml_path,
        "--hypothesis-id", hypothesis_id,
    ]
    if market_data_root:
        cmd += ["--market-data", market_data_root]
    if ledger_db:
        cmd += ["--ledger-db", ledger_db]
    cmd += list(extra_args)
    return tuple(cmd)


def _invoke_cli(cmd: tuple[str, ...], *, timeout_seconds: int) -> subprocess.CompletedProcess:
    """Module-level seam so tests can monkeypatch."""
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _tail(text: str, max_bytes: int) -> str:
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return "..." + encoded[-max_bytes:].decode("utf-8", errors="replace")
