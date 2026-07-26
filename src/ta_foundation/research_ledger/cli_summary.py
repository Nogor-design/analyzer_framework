"""CLI for summarizing the research ledger.

Provides views into the best-performing candidates, survivors, and
cross-run comparisons.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ta_foundation.research_ledger.repository import Repository, get_repository


def print_ledger_summary(
    repo: Repository,
    *,
    family: Optional[str] = None,
    instrument: Optional[str] = None,
    triage_state: Optional[str] = None,
    gate_verdict: Optional[str] = "survivor",
    limit: int = 20,
) -> None:
    """Print a formatted summary of candidates in the ledger."""
    candidates = repo.list_candidates(
        family=family,
        instrument=instrument,
        triage_state=triage_state,
        gate_verdict=gate_verdict,
        limit=limit,
    )

    if not candidates:
        print("No candidates found matching the criteria.")
        return

    print(f"\n{'ID':<15} {'Family':<25} {'Inst':<5} {'TF':<3} {'PF OOS':<6} {'Exp OOS':<7} {'Verdict':<10} {'State':<10}")
    print("-" * 90)

    for c in candidates:
        notes = json.loads(c.notes_json) if c.notes_json else {}
        h = repo.get_hypothesis(c.hypothesis_id)
        
        pf_oos = f"{c.pf_oos:.2f}" if c.pf_oos is not None else "N/A"
        exp_oos = f"{c.expectancy_oos:.1f}" if c.expectancy_oos is not None else "N/A"
        
        family_id = h.family if h else "unknown"
        inst = h.instrument if h else "???"
        tf = h.timeframe if h else "?"
        
        print(f"{c.candidate_id:<15} {family_id:<25} {inst:<5} {tf:<3} {pf_oos:<6} {exp_oos:<7} {c.gate_verdict:<10} {c.triage_state or '':<10}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Research Ledger Summary")
    ap.add_argument("--db", type=str, help="Path to ledger SQLite file")
    ap.add_argument("--family", type=str, help="Filter by family ID")
    ap.add_argument("--instrument", type=str, help="Filter by instrument")
    ap.add_argument("--state", type=str, help="Filter by triage state (shadow, decayed, research, graveyard)")
    ap.add_argument("--verdict", type=str, default="survivor", help="Filter by gate verdict (survivor, rejected, pending)")
    ap.add_argument("--limit", type=int, default=20, help="Max candidates to show")

    args = ap.parse_args()

    repo = get_repository(args.db) if args.db else get_repository()
    print_ledger_summary(
        repo,
        family=args.family,
        instrument=args.instrument,
        triage_state=args.state,
        gate_verdict=args.verdict,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
