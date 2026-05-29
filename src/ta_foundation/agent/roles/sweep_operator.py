"""Sweep Operator — runs probes for pre-registered hypotheses (C.2).

The Operator is the only role allowed to invoke `run_probe`. Its discipline
is narrow and brittle by design:

    1. Take an already-accepted hypothesis_id.
    2. Call run_probe(mode='fast_probe').
    3. Read the candidate rows that the run produced (via the ledger).
    4. If any candidate has gate_verdict='survivor', call
       run_probe(mode='hardened') on the same hypothesis.
    5. Stop. The locked holdout attempt is a one-shot human decision and the
       Operator MUST NOT request it.

What the Operator cannot do (enforced in code, not in the prompt):

    - Edit YAMLs.
    - Re-run with modified params. (No tweak-and-retry — Real Edge §5.)
    - Call run_probe with mode='locked_holdout'.
    - Touch a hypothesis whose status is not 'open'.

Failure handling:

    - Subprocess fails → the failure is journaled by run_probe; the Operator
      records it on the per-hypothesis report and moves on. No retry with
      different inputs.
    - Subprocess succeeds but produced zero candidate rows → the Operator
      retires the hypothesis with status='retired'. This is NOT graveyarding;
      it means "no signal triggered any trade, the hypothesis is untested",
      and it stops the hypothesis from counting against the multiple-testing
      denominator going forward.
    - Hypothesis already has a completed run → skipped as 'already_run', not
      re-run.

There is no LLM in the Operator's hot path. A short prompt template is kept
under `_prompts/sweep_operator.md` purely for human-readable diagnostics; the
control flow is Python.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ta_foundation.agent.tools.write.run_probe import record_candidates_for_run
from ta_foundation.research_ledger.repository import Repository
from ta_foundation.research_ledger.sidecar_parser import candidate_dicts_for_run

# A `run_probe_call` matches the signature of the journaled run_probe wrapper:
#     run_probe(repo, **kwargs) -> {"ok": bool, "result"|"error": ...}
RunProbeCall = Callable[..., dict]

REGISTERED_BY = "agent:sweep_operator"

DEFAULT_OPERATOR_LIMIT = 25


# ---- Reports ---------------------------------------------------------------


@dataclass
class HypothesisRunRecord:
    hypothesis_id: str
    status: str  # "completed_fast_only" | "completed_hardened" | "no_trades"
                 # | "fast_run_failed" | "hardened_run_failed"
                 # | "skipped_already_run" | "skipped_not_open"
                 # | "skipped_unknown_hypothesis"
    fast_run_id: Optional[str] = None
    hardened_run_id: Optional[str] = None
    n_candidates_fast: int = 0
    n_survivors_fast: int = 0
    n_candidates_hardened: int = 0
    n_survivors_hardened: int = 0
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class OperatorReport:
    requested: int = 0
    processed: int = 0
    survivors_to_triage: int = 0
    no_trades_retired: int = 0
    failures: int = 0
    skipped: int = 0
    records: list[HypothesisRunRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "processed": self.processed,
            "survivors_to_triage": self.survivors_to_triage,
            "no_trades_retired": self.no_trades_retired,
            "failures": self.failures,
            "skipped": self.skipped,
            "records": [
                {
                    "hypothesis_id": r.hypothesis_id,
                    "status": r.status,
                    "fast_run_id": r.fast_run_id,
                    "hardened_run_id": r.hardened_run_id,
                    "n_candidates_fast": r.n_candidates_fast,
                    "n_survivors_fast": r.n_survivors_fast,
                    "n_candidates_hardened": r.n_candidates_hardened,
                    "n_survivors_hardened": r.n_survivors_hardened,
                    "error": r.error,
                    "notes": list(r.notes),
                }
                for r in self.records
            ],
        }


# ---- Queue discovery -------------------------------------------------------


PROPOSALS_ACCEPTED_DIR = Path("runs/proposals_accepted")


def discover_accepted_hypotheses(
    repo: Repository,
    *,
    accepted_dir: Optional[Path] = None,
    limit: int = DEFAULT_OPERATOR_LIMIT,
) -> list[str]:
    """Return hypothesis_ids whose proposal draft has been moved to
    `runs/proposals_accepted/` and which have no completed run yet.

    The "accepted" signal is the markdown file under proposals_accepted/.
    The Operator does NOT re-run hypotheses with a completed run.
    """
    root = accepted_dir or PROPOSALS_ACCEPTED_DIR
    if not root.exists():
        return []
    out: list[str] = []
    for p in sorted(root.glob("*.md")):
        hid = p.stem
        h = repo.get_hypothesis(hid)
        if h is None or h.status != "open":
            continue
        if _has_completed_or_running_run(repo, hid):
            continue
        out.append(hid)
        if len(out) >= limit:
            break
    return out


def _has_completed_or_running_run(repo: Repository, hypothesis_id: str) -> bool:
    row = repo._conn.execute(  # noqa: SLF001 — narrow read against shared conn
        """
        SELECT 1 FROM runs
        WHERE hypothesis_id = ? AND status IN ('running', 'completed')
        LIMIT 1
        """,
        (hypothesis_id,),
    ).fetchone()
    return row is not None


# ---- Main entry points ----------------------------------------------------


def run_one_hypothesis(
    repo: Repository,
    *,
    hypothesis_id: str,
    yaml_path: str,
    input_dir: str,
    output_dir: str,
    run_probe_call: RunProbeCall,
    market_data_root: Optional[str] = None,
    ledger_db: Optional[str] = None,
    timeout_seconds: int = 1800,
) -> HypothesisRunRecord:
    """Process a single accepted hypothesis end-to-end (fast → optional hardened).

    `run_probe_call` is injected so tests can substitute it without spawning
    subprocesses. In production it is the journaled `run_probe` write tool.
    """
    record = HypothesisRunRecord(hypothesis_id=hypothesis_id, status="skipped")

    h = repo.get_hypothesis(hypothesis_id)
    if h is None:
        record.status = "skipped_unknown_hypothesis"
        record.error = f"hypothesis {hypothesis_id!r} not in ledger"
        return record
    if h.status != "open":
        record.status = "skipped_not_open"
        record.notes.append(f"hypothesis.status={h.status!r}")
        return record
    if _has_completed_or_running_run(repo, hypothesis_id):
        record.status = "skipped_already_run"
        return record

    # ---- Step 1: fast probe ------------------------------------------------
    fast_call = _run_probe(
        run_probe_call,
        repo=repo,
        hypothesis_id=hypothesis_id,
        yaml_path=yaml_path,
        mode="fast_probe",
        input_dir=input_dir,
        output_dir=output_dir,
        market_data_root=market_data_root,
        ledger_db=ledger_db,
        timeout_seconds=timeout_seconds,
    )

    if not fast_call.get("ok"):
        record.status = "fast_run_failed"
        record.error = str(fast_call.get("error") or fast_call.get("code")
                            or "run_probe rejected")
        return record

    fast_result = _unwrap_tool_payload(fast_call)
    fast_run_id = fast_result.get("run_id")
    record.fast_run_id = fast_run_id
    if not fast_result.get("ok_subprocess"):
        record.status = "fast_run_failed"
        record.error = str(fast_result.get("error") or
                            fast_result.get("stderr_tail") or
                            f"exit={fast_result.get('exit_code')}")
        return record

    # ---- Step 2: ingest the sidecar, then assess candidates ---------------
    # run_probe writes a sidecar but does not ledger candidate rows; the
    # Operator must (Phase 0 defect #10).
    if fast_run_id:
        ingest = ingest_run_candidates(
            repo, fast_run_id, fast_result.get("artifact_dir") or output_dir)
        record.notes.append(_ingest_note("fast", ingest))
    fast_candidates = (
        repo.list_candidates(run_id=fast_run_id, limit=200)
        if fast_run_id else []
    )
    record.n_candidates_fast = len(fast_candidates)
    fast_survivors = [c for c in fast_candidates if c.gate_verdict == "survivor"]
    record.n_survivors_fast = len(fast_survivors)

    if record.n_candidates_fast == 0:
        # No signal triggered any trade — retire so this stops counting against
        # the multiple-testing denominator. NOT graveyard (tested-and-failed
        # is a different beast).
        try:
            repo.set_hypothesis_status(hypothesis_id, "retired")
        except Exception as exc:  # noqa: BLE001
            record.notes.append(f"retire_failed: {type(exc).__name__}: {exc}")
        repo.journal(
            role=REGISTERED_BY,
            tool_name="operator.retire_no_trades",
            inputs={"hypothesis_id": hypothesis_id, "fast_run_id": fast_run_id},
            output_summary=f"no candidates from {fast_run_id}; retired hypothesis",
            duration_ms=0,
        )
        record.status = "no_trades"
        return record

    if record.n_survivors_fast == 0:
        record.status = "completed_fast_only"
        return record

    # ---- Step 3: hardened probe (only when dev survivors exist) -----------
    hardened_call = _run_probe(
        run_probe_call,
        repo=repo,
        hypothesis_id=hypothesis_id,
        yaml_path=yaml_path,
        mode="hardened",
        input_dir=input_dir,
        output_dir=output_dir,
        market_data_root=market_data_root,
        ledger_db=ledger_db,
        timeout_seconds=timeout_seconds,
    )

    if not hardened_call.get("ok"):
        record.status = "hardened_run_failed"
        record.error = str(hardened_call.get("error") or
                            hardened_call.get("code") or
                            "run_probe rejected")
        return record

    hardened_result = _unwrap_tool_payload(hardened_call)
    hardened_run_id = hardened_result.get("run_id")
    record.hardened_run_id = hardened_run_id
    if not hardened_result.get("ok_subprocess"):
        record.status = "hardened_run_failed"
        record.error = str(hardened_result.get("error") or
                            hardened_result.get("stderr_tail") or
                            f"exit={hardened_result.get('exit_code')}")
        return record

    if hardened_run_id:
        ingest = ingest_run_candidates(
            repo, hardened_run_id,
            hardened_result.get("artifact_dir") or output_dir)
        record.notes.append(_ingest_note("hardened", ingest))
    hardened_candidates = (
        repo.list_candidates(run_id=hardened_run_id, limit=200)
        if hardened_run_id else []
    )
    record.n_candidates_hardened = len(hardened_candidates)
    record.n_survivors_hardened = sum(
        1 for c in hardened_candidates if c.gate_verdict == "survivor"
    )
    record.status = "completed_hardened"
    return record


def run_operator_pass(
    repo: Repository,
    *,
    yaml_path_resolver: Callable[[str], Optional[str]],
    input_dir: str,
    output_dir: str,
    run_probe_call: RunProbeCall,
    hypothesis_ids: Optional[list[str]] = None,
    accepted_dir: Optional[Path] = None,
    market_data_root: Optional[str] = None,
    ledger_db: Optional[str] = None,
    limit: int = DEFAULT_OPERATOR_LIMIT,
    timeout_seconds: int = 1800,
) -> OperatorReport:
    """Process the accepted-hypothesis queue (fast → optional hardened each).

    `yaml_path_resolver(hypothesis_id)` returns the YAML path the run_probe
    write tool should drive. Returning None signals "no YAML known" and the
    hypothesis is recorded as a per-row failure (no run is started).
    """
    report = OperatorReport()
    if hypothesis_ids is None:
        hypothesis_ids = discover_accepted_hypotheses(
            repo, accepted_dir=accepted_dir, limit=limit,
        )
    hypothesis_ids = list(hypothesis_ids)[:limit]
    report.requested = len(hypothesis_ids)

    for hid in hypothesis_ids:
        yaml_path = yaml_path_resolver(hid)
        if yaml_path is None:
            rec = HypothesisRunRecord(
                hypothesis_id=hid, status="fast_run_failed",
                error="no yaml path resolved for hypothesis",
            )
            report.records.append(rec)
            report.failures += 1
            continue

        rec = run_one_hypothesis(
            repo,
            hypothesis_id=hid,
            yaml_path=yaml_path,
            input_dir=input_dir,
            output_dir=output_dir,
            run_probe_call=run_probe_call,
            market_data_root=market_data_root,
            ledger_db=ledger_db,
            timeout_seconds=timeout_seconds,
        )
        report.records.append(rec)
        if rec.status in {"completed_fast_only", "completed_hardened"}:
            report.processed += 1
            report.survivors_to_triage += rec.n_survivors_hardened or 0
        elif rec.status == "no_trades":
            report.processed += 1
            report.no_trades_retired += 1
        elif rec.status.startswith("skipped"):
            report.skipped += 1
        else:
            report.failures += 1

    return report


# ---- YAML path resolution -------------------------------------------------


def resolve_yaml_path_via_author_probe(hypothesis_id: str) -> Optional[str]:
    """Default resolver: look in discovery/generated/<hypothesis_id>.yaml.

    This matches the convention the author_probe write tool uses today. The
    resolver is a pluggable seam so callers can override (e.g., to pick from
    a curated subdirectory).
    """
    candidate = Path("discovery/generated") / f"{hypothesis_id}.yaml"
    return str(candidate) if candidate.is_file() else None


# ---- Sidecar ingestion -----------------------------------------------------


def _unwrap_tool_payload(payload: dict) -> dict:
    """Return a journaled tool's `result`, transparently loading the spill
    file when `@journaled_tool` truncated a large output to disk. Returns {}
    when the tool itself failed (`ok=False`)."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return {}
    if payload.get("truncated"):
        try:
            full = json.loads(
                Path(payload["artifact_path"]).read_text(encoding="utf-8"))
            return full.get("result") or {}
        except (OSError, ValueError, KeyError):
            return {}
    return payload.get("result") or {}


def _find_sidecar(artifact_dir: Path, hypothesis_id: Optional[str] = None) -> Optional[Path]:
    """Newest discovery summary sidecar under a run's artifact directory, matching the hypothesis_id."""
    if not artifact_dir.is_dir():
        return None
    if hypothesis_id:
        target = artifact_dir / f"{hypothesis_id}_summary.json"
        if target.is_file():
            return target
        found = sorted(
            artifact_dir.rglob(f"*{hypothesis_id}*_summary.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if found:
            return found[0]
        return None
    found = sorted(
        artifact_dir.rglob("*_summary.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return found[0] if found else None


def ingest_run_candidates(
    repo: Repository, run_id: str, artifact_dir: str
) -> dict:
    """Parse the discovery sidecar a run produced and feed it to the journaled
    `record_candidates_for_run` tool.

    This is the seam the Operator was missing (Phase 0 defect #10): `run_probe`
    writes a sidecar but never ledgers candidate rows, so the Operator's
    `list_candidates(run_id=...)` always came back empty and every hypothesis
    was retired as `no_trades`. Graceful by design — a run that produced no
    sidecar (no discovery block, or a failed sweep) returns `sidecar=None` and
    the caller's existing no-candidates path handles it.
    """
    run = repo.get_run(run_id)
    hypothesis_id = run.hypothesis_id if run else None
    sidecar = _find_sidecar(Path(artifact_dir), hypothesis_id)
    if sidecar is None:
        return {"sidecar": None, "ingested": 0, "rejected": 0, "ok": True}
    cand_dicts = candidate_dicts_for_run(sidecar, run_id)
    payload = record_candidates_for_run(repo, run_id=run_id,
                                        candidates=cand_dicts)
    result = _unwrap_tool_payload(payload)
    return {
        "sidecar": str(sidecar),
        "ingested": result.get("n_inserted", 0),
        "rejected": result.get("n_rejected", 0),
        "ok": bool(payload.get("ok")),
        "error": None if payload.get("ok") else payload.get("error"),
    }


def _ingest_note(stage: str, ingest: dict) -> str:
    if ingest.get("sidecar") is None:
        return f"{stage}: no discovery sidecar found in artifact_dir"
    if not ingest.get("ok"):
        return f"{stage}: sidecar ingest failed — {ingest.get('error')}"
    return (f"{stage}: ingested {ingest.get('ingested', 0)} candidates "
            f"({ingest.get('rejected', 0)} rejected)")


# ---- Internals -------------------------------------------------------------


def _run_probe(
    run_probe_call: RunProbeCall,
    *,
    repo: Repository,
    hypothesis_id: str,
    yaml_path: str,
    mode: str,
    input_dir: str,
    output_dir: str,
    market_data_root: Optional[str],
    ledger_db: Optional[str],
    timeout_seconds: int,
) -> dict:
    if mode not in {"fast_probe", "hardened"}:
        # Hard guard: the Operator NEVER initiates a locked_holdout. This is
        # belt-and-suspenders next to the journaled tool's own enum schema.
        return {
            "ok": False,
            "code": "operator_forbidden_mode",
            "error": f"sweep operator may not invoke run_probe with mode={mode!r}",
        }
    kwargs: dict[str, Any] = {
        "hypothesis_id": hypothesis_id,
        "yaml_path": yaml_path,
        "mode": mode,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "timeout_seconds": timeout_seconds,
    }
    if market_data_root:
        kwargs["market_data_root"] = market_data_root
    if ledger_db:
        kwargs["ledger_db"] = ledger_db
    return run_probe_call(repo, **kwargs)
