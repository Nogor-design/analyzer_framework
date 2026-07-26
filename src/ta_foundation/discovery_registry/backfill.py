"""One-shot backfill of probe + graveyard registries from existing sidecars.

Walks `output/*_summary.json`, locates the matching YAML in `discovery/`,
computes the probe identity hash, and writes records to both registries.

Run via `python -m ta_foundation.discovery_registry.backfill --output ./output`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .hashing import compute_probe_identity, probe_hash
from .registry import (
    GraveyardRecord,
    GraveyardRegistry,
    ProbeRecord,
    ProbeRegistry,
)


@dataclass
class BackfillSummary:
    sidecars_scanned: int
    probe_records_added: int
    graveyard_records_added: int
    skipped_missing_yaml: List[str]
    skipped_no_signals: List[str]


def _find_yaml_for_sidecar(
    sidecar_path: Path,
    discovery_dirs: List[Path],
) -> Optional[Path]:
    """The sidecar `<stem>_summary.json` is produced by `<stem>.yaml` in
    `discovery/`. Probe-yaml may also live in `discovery/generated/`."""
    base = sidecar_path.stem
    if base.endswith("_summary"):
        base = base[: -len("_summary")]
    candidate_names = [f"{base}.yaml", f"{base}.yml"]
    for d in discovery_dirs:
        for name in candidate_names:
            p = d / name
            if p.is_file():
                return p
    return None


def _classify_sidecar(sidecar_data: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return ("ok", None) or ("graveyard", failure_info_dict).

    A sidecar is graveyard if any of:
      - diagnostics.empty_reason mentions "below" / "PF" / "no edge" / "rejected"
        AND tier_breakdown is empty-or-only-rejected.
      - rankings[*].hardening.passed == False (any rank).
      - tier_breakdown contains only "rejected" entries.
    """
    diag = sidecar_data.get("diagnostics") or {}
    tier_breakdown = diag.get("tier_breakdown") or {}
    rankings = sidecar_data.get("rankings") or []

    # Hardening verdict (06_validate stage) wins if present.
    hardening_failures: List[Dict[str, Any]] = []
    for rank in rankings:
        if not isinstance(rank, dict):
            continue
        hardening = rank.get("hardening")
        if isinstance(hardening, dict) and hardening.get("enabled") and hardening.get("passed") is False:
            issues = hardening.get("issues") or []
            stress = hardening.get("slippage_stress") or {}
            stress_cell = stress.get("stress_cell") if isinstance(stress, dict) else None
            hardening_failures.append({
                "rank": rank.get("rank"),
                "issues": issues,
                "stress_cell": stress_cell,
            })
    if hardening_failures:
        reason_parts = []
        for f in hardening_failures[:1]:
            reason_parts.append(
                "hardening failed: " + "; ".join(f.get("issues") or ["unspecified"])
            )
        return "graveyard", {
            "reason": " | ".join(reason_parts) or "hardening failed",
            "stress_failure_cell": hardening_failures[0].get("stress_cell"),
        }

    # Broad probe with rejection-only results.
    if rankings and all(
        isinstance(r, dict)
        and (r.get("tier") or {}).get("id") == "rejected"
        for r in rankings
    ):
        return "graveyard", {
            "reason": "all ranked rows rejected (broad PF<1.0)",
            "stress_failure_cell": None,
        }

    # Empty-reason heuristic.
    empty_reason = diag.get("empty_reason")
    if empty_reason and (
        "PF" in str(empty_reason) or "below" in str(empty_reason).lower()
        or "no edge" in str(empty_reason).lower()
    ):
        # Only flag if tier breakdown is empty-or-rejected-only.
        if not tier_breakdown or set(tier_breakdown.keys()) <= {"rejected"}:
            return "graveyard", {
                "reason": str(empty_reason),
                "stress_failure_cell": None,
            }

    return "ok", None


def backfill(
    output_dir: Path,
    discovery_dirs: Optional[List[Path]] = None,
    *,
    verbose: bool = True,
) -> BackfillSummary:
    output_dir = Path(output_dir)
    discovery_dirs = discovery_dirs or [
        Path("discovery"),
        Path("discovery") / "generated",
    ]
    probe_reg = ProbeRegistry(output_dir)
    graveyard = GraveyardRegistry(output_dir)

    probe_added = 0
    graveyard_added = 0
    skipped_missing_yaml: List[str] = []
    skipped_no_signals: List[str] = []
    scanned = 0

    sidecars = sorted(output_dir.glob("*_summary.json"))
    for sidecar_path in sidecars:
        if sidecar_path.name.startswith("_"):
            continue
        scanned += 1
        try:
            with sidecar_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            if verbose:
                print(f"[backfill] skipping unreadable sidecar {sidecar_path.name}: {e}")
            continue

        yaml_path = _find_yaml_for_sidecar(sidecar_path, discovery_dirs)
        if yaml_path is None:
            skipped_missing_yaml.append(sidecar_path.name)
            if verbose:
                print(f"[backfill] no YAML found for {sidecar_path.name}, skipping")
            continue

        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as e:
            if verbose:
                print(f"[backfill] skipping unreadable yaml {yaml_path.name}: {e}")
            continue

        identity = compute_probe_identity(raw)
        if not identity.signals:
            skipped_no_signals.append(sidecar_path.name)
            continue
        h = probe_hash(identity)

        families = [f"{s.block}::{s.signal}" for s in identity.signals]
        diagnostics = data.get("diagnostics") or {}
        instrument_obj = data.get("instrument") or {}
        instrument = identity.instrument or instrument_obj.get("symbol") or "UNKNOWN"
        generated_at = data.get("generated_at") or ""

        probe_rec = ProbeRecord(
            hash=h,
            yaml_path=str(yaml_path).replace("\\", "/"),
            sidecar_path=str(sidecar_path).replace("\\", "/"),
            run_date=generated_at,
            instrument=instrument,
            stage=(data.get("stage") or {}).get("id") if isinstance(data.get("stage"), dict) else None,
            families=families,
            n_combinations_run=int(diagnostics.get("total_combos_tested") or 0),
        )
        if probe_reg.append(probe_rec):
            probe_added += 1

        classification, failure_info = _classify_sidecar(data)
        if classification == "graveyard":
            grec = GraveyardRecord(
                hash=h,
                yaml_path=str(yaml_path).replace("\\", "/"),
                sidecar_path=str(sidecar_path).replace("\\", "/"),
                verdict_date=generated_at,
                reason=(failure_info or {}).get("reason", "graveyard"),
                families=families,
                instrument=instrument,
                stress_failure_cell=(failure_info or {}).get("stress_failure_cell"),
            )
            if graveyard.append(grec):
                graveyard_added += 1

    return BackfillSummary(
        sidecars_scanned=scanned,
        probe_records_added=probe_added,
        graveyard_records_added=graveyard_added,
        skipped_missing_yaml=skipped_missing_yaml,
        skipped_no_signals=skipped_no_signals,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ta_foundation.discovery_registry.backfill",
        description="Backfill probe and graveyard registries from existing output/*_summary.json sidecars.",
    )
    parser.add_argument("--output", default="./output", help="Output directory containing sidecars and registry JSONs.")
    parser.add_argument("--discovery", action="append", default=None,
                        help="Discovery directory containing YAMLs (repeatable). Default: ./discovery and ./discovery/generated.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    discovery_dirs = [Path(d) for d in args.discovery] if args.discovery else None
    summary = backfill(Path(args.output), discovery_dirs=discovery_dirs, verbose=not args.quiet)
    print(
        f"[backfill] scanned={summary.sidecars_scanned} "
        f"probe_added={summary.probe_records_added} "
        f"graveyard_added={summary.graveyard_records_added} "
        f"missing_yaml={len(summary.skipped_missing_yaml)} "
        f"no_signals={len(summary.skipped_no_signals)}"
    )
    if not args.quiet and summary.skipped_missing_yaml:
        print("[backfill] sidecars without matching YAML (informational):")
        for s in summary.skipped_missing_yaml[:20]:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
