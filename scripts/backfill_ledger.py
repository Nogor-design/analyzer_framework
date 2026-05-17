"""Backfill the research ledger from on-disk discovery outputs.

Usage:
    python scripts/backfill_ledger.py
    python scripts/backfill_ledger.py --output-root outputs --output-root outputs5-9
    python scripts/backfill_ledger.py --ledger-db .ta_artifacts/research_ledger.db

Idempotent: re-running on the same set of sidecars is a no-op. New sidecars
since the last run are added; previously-imported runs are skipped via
deterministic run_id dedupe.

See `src/ta_foundation/research_ledger/backfill.py` for the implementation
and `docs/designs/agentic_phase_a_foundation.md` (A.4) for context.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository
from ta_foundation.research_ledger.backfill import backfill_from_outputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output-root", action="append", default=None,
        help=("Root directory to scan for *_summary.json sidecars. May be "
              "repeated. Defaults to scanning every 'outputs*' folder in the "
              "current working directory."),
    )
    ap.add_argument(
        "--ledger-db", default=str(DEFAULT_DB_PATH),
        help="Path to the research ledger SQLite file.",
    )
    ap.add_argument(
        "--registered-by", default="backfill",
        help="Value to record in hypotheses.registered_by for new rows.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Discover sidecars and print the plan, but do not write to the ledger.",
    )
    args = ap.parse_args(argv)

    roots = _resolve_roots(args.output_root)
    if not roots:
        print("[backfill] No output roots found to scan.", file=sys.stderr)
        return 2

    print(f"[backfill] scanning {len(roots)} output roots:")
    for r in roots:
        print(f"  - {r}")

    if args.dry_run:
        from ta_foundation.research_ledger.backfill import discover_sidecars
        sidecars = discover_sidecars(roots)
        print(f"[backfill] dry-run: {len(sidecars)} sidecars would be imported")
        for s in sidecars[:20]:
            print(f"  - {s}")
        if len(sidecars) > 20:
            print(f"  ... and {len(sidecars) - 20} more")
        return 0

    repo = get_repository(args.ledger_db)
    report = backfill_from_outputs(repo, roots, registered_by=args.registered_by)
    print("[backfill] complete")
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def _resolve_roots(explicit: list[str] | None) -> list[Path]:
    if explicit:
        return [Path(r) for r in explicit]
    cwd = Path(".")
    discovered = sorted(p for p in cwd.iterdir() if p.is_dir() and p.name.startswith("outputs"))
    return discovered


if __name__ == "__main__":
    sys.exit(main())
