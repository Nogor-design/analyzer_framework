"""Backfill the research ledger from on-disk discovery outputs.

A.4 from `docs/designs/agentic_phase_a_foundation.md`. Walks the
configured outputs root(s) for `*_summary.json` sidecars, registers
hypothesis rows (idempotently via `dedupe_hash`), records the run, and
inserts candidate rows from the sidecar rankings.

The agent-facing concern this serves is the **multiple-testing
denominator**: `count_hypotheses_tested()` must reflect every hypothesis
ever evaluated, not just ones the agent layer authored. Without backfill,
the ledger starts empty and DSR / Romano-Wolf corrections are too
generous.

Idempotent:
    - Hypothesis dedupe: same dedupe_hash short-circuits to the existing id.
    - Run dedupe: deterministic run_id (sha256 over yaml_path + sidecar_path
      + dir name) makes repeated insert attempts a no-op.
    - Candidate dedupe: deterministic candidate_id (`c_<run_short>_<rank:03d>`)
      hits the candidates PK and is skipped.

The backfill does NOT set triage states. The triage agent (Phase B.1) is
responsible for that, reading the parsed sidecars + gate_verdicts and
deciding graveyard / research / hardening_queue / shadow.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ta_foundation.research_ledger.repository import (
    DuplicateHypothesisError,
    Repository,
)
from ta_foundation.research_ledger.sidecar_parser import (
    ParsedSidecar,
    infer_family,
    parse_summary_sidecar,
)


@dataclass
class BackfillReport:
    sidecars_scanned: int = 0
    sidecars_parsed: int = 0
    sidecars_skipped: int = 0
    hypotheses_registered: int = 0
    hypotheses_reused: int = 0
    runs_inserted: int = 0
    runs_skipped: int = 0
    candidates_inserted: int = 0
    candidates_skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sidecars_scanned": self.sidecars_scanned,
            "sidecars_parsed": self.sidecars_parsed,
            "sidecars_skipped": self.sidecars_skipped,
            "hypotheses_registered": self.hypotheses_registered,
            "hypotheses_reused": self.hypotheses_reused,
            "runs_inserted": self.runs_inserted,
            "runs_skipped": self.runs_skipped,
            "candidates_inserted": self.candidates_inserted,
            "candidates_skipped": self.candidates_skipped,
            "n_errors": len(self.errors),
            "errors": self.errors[:20],  # truncate for readability
        }


def discover_sidecars(roots: Iterable[Path]) -> list[Path]:
    """Walk one or more output roots and return all `*_summary.json` paths."""
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*_summary.json"):
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(p)
    out.sort()
    return out


def backfill_from_outputs(
    repo: Repository,
    output_roots: Iterable[Path],
    *,
    registered_by: str = "backfill",
) -> BackfillReport:
    """Walk `output_roots` for summary sidecars and import them into the ledger."""
    report = BackfillReport()
    for sidecar_path in discover_sidecars(output_roots):
        report.sidecars_scanned += 1
        ingest_sidecar(repo, sidecar_path, report=report, registered_by=registered_by)
    return report


def ingest_sidecar(
    repo: Repository,
    sidecar_path: Path,
    *,
    report: Optional[BackfillReport] = None,
    registered_by: str = "backfill",
) -> BackfillReport:
    """Ingest a single sidecar into the ledger."""
    if report is None:
        report = BackfillReport()

    try:
        parsed = parse_summary_sidecar(sidecar_path)
        if parsed.schema_version != 1:
            raise ValueError(f"Unsupported schema version: {parsed.schema_version}")
        report.sidecars_parsed += 1
    except Exception as exc:  # noqa: BLE001
        report.sidecars_skipped += 1
        report.errors.append({
            "sidecar": str(sidecar_path),
            "error": f"Parse failed: {exc}",
        })
        return report

    # 1. Hypothesis registration
    dir_name = sidecar_path.parent.name
    bucket = f"{dir_name}/{sidecar_path.stem}"
    hypothesis_id = _backfill_hypothesis_id(bucket)
    first_signal = parsed.candidates[0].get("notes", {}).get("signal") if parsed.candidates else None
    family = infer_family(sidecar_path.name, first_signal)
    instrument = parsed.instrument_symbol
    timeframe = parsed.timeframe
    mode = _infer_mode(str(sidecar_path))

    first_signal = parsed.candidates[0].get("params", {}).get("signal_id") if parsed.candidates else None
    mechanism = (
        f"Backfilled from {sidecar_path.name}; bucket={bucket}; "
        f"first_signal={first_signal or '<none>'}; inferred family: {family}; mode: {mode}. "
        "Pre-program work; mechanism statement not recoverable from sidecar alone."
    )

    try:
        h = repo.register_hypothesis(
            hypothesis_id=hypothesis_id,
            family=family,
            instrument=instrument,
            timeframe=timeframe,
            params={"_backfill_bucket": bucket},
            mechanism=mechanism,
            registered_by=registered_by,
        )
        report.hypotheses_registered += 1
    except DuplicateHypothesisError as exc:
        existing = repo.get_hypothesis(exc.existing_id)
        assert existing is not None
        h = existing
        report.hypotheses_reused += 1

    # 2. Run insertion
    run_id = _backfill_run_id(sidecar_path)
    if repo.get_run(run_id) is not None:
        report.runs_skipped += 1
        return report

    yaml_path = _guess_yaml_path(dir_name, sidecar_path.stem)
    repo.start_run(
        run_id=run_id,
        hypothesis_id=h.hypothesis_id,
        mode=mode,
        config_hash=_hash_path(sidecar_path),
        yaml_path=str(yaml_path or sidecar_path),
        artifact_dir=str(sidecar_path.parent),
    )
    repo.complete_run(run_id)
    report.runs_inserted += 1

    # 3. Candidate insertion
    run_short = run_id.split("_")[-1]
    for cand in parsed.candidates:
        candidate_id = f"c_{run_short}_{cand['rank_in_run']:03d}"
        try:
            repo.record_candidate(
                candidate_id=candidate_id,
                run_id=run_id,
                rank_in_run=cand["rank_in_run"],
                params=cand.get("params") or {},
                gate_verdict=cand.get("gate_verdict") or "pending",
                gate_reasons=cand.get("gate_reasons"),
                n_trades_dev=cand.get("n_trades_dev"),
                pf_dev=cand.get("pf_dev"),
                expectancy_dev=cand.get("expectancy_dev"),
                n_trades_oos=cand.get("n_trades_oos"),
                pf_oos=cand.get("pf_oos"),
                expectancy_oos=cand.get("expectancy_oos"),
                n_trades_holdout=cand.get("n_trades_holdout"),
                pf_holdout=cand.get("pf_holdout"),
                expectancy_holdout=cand.get("expectancy_holdout"),
                slippage_stress_pass=cand.get("slippage_stress_pass"),
                notes=cand.get("notes"),
            )
            report.candidates_inserted += 1
        except Exception as exc:  # noqa: BLE001
            report.candidates_skipped += 1
            report.errors.append({
                "sidecar": str(sidecar_path),
                "candidate_id": candidate_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return report


def _backfill_hypothesis_id(bucket: str) -> str:
    digest = hashlib.sha256(bucket.encode("utf-8")).hexdigest()[:16]
    return f"h_backfill_{digest}"


def _backfill_run_id(sidecar_path: Path) -> str:
    raw = f"{sidecar_path.resolve()}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"r_backfill_{digest}"


def _hash_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _infer_mode(text: str) -> str:
    t = text.lower()
    if "holdout" in t or "locked" in t:
        return "locked_holdout"
    if "harden" in t:
        return "hardened"
    return "fast_probe"


def _guess_yaml_path(dir_name: str, sidecar_stem: str) -> Optional[Path]:
    """Best-effort: look for a matching YAML under discovery/ by stripping
    `_summary` from the sidecar stem. Returns None if not found. This is
    purely informational; backfill does not require the YAML to exist."""
    stem = sidecar_stem
    if stem.endswith("_summary"):
        stem = stem[:-len("_summary")]
    candidate = Path("discovery") / f"{stem}.yaml"
    if candidate.exists():
        return candidate
    return None
