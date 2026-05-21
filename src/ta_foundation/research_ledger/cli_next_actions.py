"""Dry-run pipeline inspector for the research ledger.

The Phase 0 exit criterion (`docs/runbooks/manual_pipeline_proof.md`): a
command that lists every ledger candidate and the next legal pipeline
transition for it, and triggers nothing. This is the read-only shadow of the
conductor - it shows what an autonomous conductor WOULD do next, without
doing it. No runs, no subprocesses, no ledger writes.

    python -m ta_foundation.research_ledger.cli_next_actions [--db PATH]
            [--all] [--limit N] [--next TOOL]

Transition rules mirror the journaled tools' own preconditions
(`agent/tools/write/promote.py`, `triage.py`, `run_probe.py`):

  gate_verdict='pending'                       -> triage-pass
  gate_verdict='rejected'                      -> set_triage_state(graveyard)
  gate_verdict='survivor':
    no dev/oos metrics, not yet promoted       -> promote_to_hardening
    no dev/oos metrics, in hardening_queue     -> run_probe(mode=hardened)
    dev+oos present, holdout lock free         -> request_locked_holdout
    dev+oos present, holdout lock spent        -> run_probe(mode=locked_holdout)
    locked-holdout PF > 1.0                    -> set_triage_state(shadow)
    locked-holdout PF <= 1.0                   -> set_triage_state(graveyard)
  triage_state in {shadow, graveyard, decayed} -> terminal (no transition)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

from ta_foundation.research_ledger.repository import Repository, get_repository


@dataclass(frozen=True)
class NextAction:
    candidate_id: str
    hypothesis_id: str
    stage: str                  # current pipeline position
    next_tool: Optional[str]    # journaled tool/command that advances it
    detail: str
    terminal: bool              # True when there is no forward transition


def next_action(candidate) -> NextAction:
    """Classify a candidate row into its next legal pipeline transition.

    Pure: reads the candidate, returns a verdict, touches nothing.
    """
    c = candidate

    def make(stage: str, tool: Optional[str], detail: str,
             terminal: bool = False) -> NextAction:
        return NextAction(c.candidate_id, c.hypothesis_id, stage, tool,
                          detail, terminal)

    ts = c.triage_state
    if ts == "shadow":
        return make("shadow", None,
                    "enrolled in forward shadow observation", terminal=True)
    if ts == "graveyard":
        return make("graveyard", None, "retired to the graveyard",
                    terminal=True)
    if ts == "decayed":
        return make("decayed", None,
                    "disabled after a live decay signal", terminal=True)

    gv = c.gate_verdict
    if gv == "rejected":
        return make("rejected", "set_triage_state(graveyard)",
                    "failed the gate; triage to the graveyard")
    if gv == "pending":
        if ts == "research":
            return make("research", None,
                        "triaged to research; awaiting manual re-evaluation",
                        terminal=True)
        return make("untriaged", "triage-pass",
                    "awaiting a triage verdict (research vs graveyard)")

    # gate_verdict == 'survivor'
    has_dev_oos = c.n_trades_dev is not None and c.n_trades_oos is not None
    has_holdout = c.n_trades_holdout is not None and c.pf_holdout is not None

    if has_holdout:
        if c.pf_holdout > 1.0:
            return make("holdout-passed", "set_triage_state(shadow)",
                        f"locked-holdout PF {c.pf_holdout:.2f} > 1.0; "
                        "enroll in shadow")
        return make("holdout-failed", "set_triage_state(graveyard)",
                    f"locked-holdout PF {c.pf_holdout:.2f} <= 1.0; graveyard")

    if not has_dev_oos:
        if ts == "hardening_queue":
            return make("hardening-queue", "run_probe(mode=hardened)",
                        "promoted; awaiting the hardened run")
        return make("survivor", "promote_to_hardening",
                    "fast-probe survivor; promote to the hardening queue")

    # dev + oos on record, no locked holdout yet
    if c.holdout_attempted:
        return make("holdout-locked", "run_probe(mode=locked_holdout)",
                    "one-shot holdout lock acquired; awaiting the "
                    "locked-holdout run")
    return make("hardened", "request_locked_holdout",
                "hardening complete (dev+oos on record); request the "
                "one-shot holdout lock")


def inspect_pipeline(
    repo: Repository,
    *,
    include_terminal: bool = False,
    next_tool: Optional[str] = None,
) -> list[NextAction]:
    """Return the next action for every candidate in the ledger (read-only)."""
    candidates = repo.list_candidates(limit=10_000_000)
    actions = [next_action(c) for c in candidates]
    if not include_terminal:
        actions = [a for a in actions if not a.terminal]
    if next_tool is not None:
        actions = [a for a in actions if a.next_tool == next_tool]
    return actions


# ---- CLI -------------------------------------------------------------------


def _summary_label(a: NextAction) -> str:
    return a.next_tool if a.next_tool is not None else f"(terminal: {a.stage})"


def print_dry_run(
    repo: Repository,
    *,
    include_terminal: bool,
    next_tool: Optional[str],
    limit: int,
) -> None:
    # The summary always counts every candidate; the detail list honours the
    # filters.
    every = inspect_pipeline(repo, include_terminal=True)
    print("Research-ledger pipeline dry-run  -  read-only, no runs triggered")
    print(f"candidates in ledger: {len(every)}")

    counts: dict[str, int] = {}
    for a in every:
        label = _summary_label(a)
        counts[label] = counts.get(label, 0) + 1
    print("\nnext-action summary:")
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {label:<32} {n:>5}")

    shown = inspect_pipeline(
        repo, include_terminal=include_terminal, next_tool=next_tool)
    shown.sort(key=lambda a: (_summary_label(a), a.candidate_id))
    scope = "all candidates" if include_terminal else "eligible candidates"
    print(f"\n{scope}: {len(shown)}"
          + (f" (showing first {limit})" if len(shown) > limit else ""))
    print(f"  {'CANDIDATE':<24} {'STAGE':<16} {'NEXT':<30} DETAIL")
    print("  " + "-" * 100)
    for a in shown[:limit]:
        print(f"  {a.candidate_id:<24} {a.stage:<16} "
              f"{(a.next_tool or '-'):<30} {a.detail}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ta_foundation.research_ledger.cli_next_actions",
        description="Dry-run: next legal pipeline transition per candidate. "
                    "Read-only - triggers no runs.",
    )
    ap.add_argument("--db", default=None,
                    help="Path to research_ledger.db (default: the canonical "
                         ".ta_artifacts/research_ledger.db).")
    ap.add_argument("--all", action="store_true",
                    help="Include terminal candidates (shadow/graveyard/"
                         "decayed/research) in the detail list.")
    ap.add_argument("--next", default=None, dest="next_tool",
                    help="Show only candidates whose next transition matches "
                         "this tool string, e.g. promote_to_hardening.")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max rows in the detail list (default 50).")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    repo = get_repository(args.db) if args.db else get_repository()
    print_dry_run(
        repo,
        include_terminal=args.all,
        next_tool=args.next_tool,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
