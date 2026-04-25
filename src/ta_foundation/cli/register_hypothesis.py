from __future__ import annotations

"""
Pre-Registration CLI
====================
Run before any sweep to lock in a hypothesis and freeze the holdout date range.

    python -m ta_foundation.cli.register_hypothesis --db-path experiments.duckdb

Interactive prompts collect the hypothesis details, then write a row to the
``experiments`` table with ``status="registered"`` and ``holdout_locked_at``
set immediately.  This prevents the researcher from peeking at results before
committing to a hypothesis.

Non-interactive (scripted) use
-------------------------------
Pass all values as flags to skip prompts:

    python -m ta_foundation.cli.register_hypothesis \\
        --db-path experiments.duckdb \\
        --family candle \\
        --signal-id bull_engulf \\
        --instrument NQ \\
        --contract H25 \\
        --timeframe 5m \\
        --date-range-start 2024-01-01 \\
        --date-range-end 2024-12-31 \\
        --hypothesis "Bullish engulfing at VWAP support on NQ 5m produces +EV entries"

Enforcement flag
----------------
Other tools (sweeps, validation runs) can call ``assert_registered()`` to
refuse execution unless the experiment exists in the registry:

    from ta_foundation.cli.register_hypothesis import assert_registered
    assert_registered(db_path, family="candle", signal_id="bull_engulf", ...)
"""

import argparse
import sys
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Prompt helper
# ---------------------------------------------------------------------------

def _prompt(label: str, default: Optional[str] = None, required: bool = True) -> str:
    hint = f" [{default}]" if default else ""
    suffix = " (required)" if required and not default else ""
    while True:
        raw = input(f"  {label}{hint}{suffix}: ").strip()
        value = raw or (default or "")
        if value or not required:
            return value
        print(f"    [!] {label} is required.")


# ---------------------------------------------------------------------------
# Core: write experiment row
# ---------------------------------------------------------------------------

def register(
    db_path: str,
    *,
    hypothesis: str,
    family: Optional[str] = None,
    signal_id: Optional[str] = None,
    instrument: Optional[str] = None,
    contract: Optional[str] = None,
    timeframe: Optional[str] = None,
    date_range_start: Optional[str] = None,
    date_range_end: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    cost_model: Optional[Dict[str, Any]] = None,
    status: str = "registered",
) -> int:
    """
    Write one experiment row and return the new experiment id.
    Safe to call from scripts; does not prompt.
    """
    from ta_foundation.persistence.db import ExperimentRegistry
    reg = ExperimentRegistry(db_path)
    exp_id = reg.register_experiment(
        hypothesis_text=hypothesis,
        family=family,
        signal_id=signal_id,
        params=params,
        instrument=instrument,
        contract=contract,
        timeframe=timeframe,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        cost_model=cost_model,
        status=status,
    )
    return exp_id


# ---------------------------------------------------------------------------
# Enforcement helper
# ---------------------------------------------------------------------------

def assert_registered(
    db_path: str,
    *,
    family: Optional[str] = None,
    signal_id: Optional[str] = None,
    instrument: Optional[str] = None,
    contract: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> int:
    """
    Raise RuntimeError if no registered experiment matches the given fields.

    Returns the matching experiment id when found.
    Callers pass only the fields they care about; unset fields are not filtered.

    Usage::

        from ta_foundation.cli.register_hypothesis import assert_registered
        exp_id = assert_registered(
            db_path,
            family="candle",
            signal_id="bull_engulf",
            instrument="NQ",
        )
    """
    from ta_foundation.persistence.db import ExperimentRegistry
    reg = ExperimentRegistry(db_path)
    rows = reg.list_experiments(status="registered", family=family)

    matches = []
    for row in rows:
        if signal_id and row.get("signal_id") != signal_id:
            continue
        if instrument and row.get("instrument") != instrument:
            continue
        if contract and row.get("contract") != contract:
            continue
        if timeframe and row.get("timeframe") != timeframe:
            continue
        matches.append(row)

    if not matches:
        parts = [f"family={family}", f"signal_id={signal_id}",
                 f"instrument={instrument}", f"contract={contract}",
                 f"timeframe={timeframe}"]
        criteria = ", ".join(p for p in parts if not p.endswith("=None"))
        raise RuntimeError(
            f"No registered experiment found matching: {criteria}\n"
            f"Pre-register with: python -m ta_foundation.cli.register_hypothesis --db-path {db_path}"
        )

    return int(matches[0]["id"])


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def _interactive_register(args: argparse.Namespace) -> None:
    db_path: str = args.db_path

    print("\n=== Hypothesis Pre-Registration ===")
    print(f"Registry: {db_path}")
    print("Lock in your hypothesis before running any sweep.\n")

    family = args.family or _prompt("Signal family (candle/ma/bb/orb/breakout/pullback/level/lcr)")
    signal_id = args.signal_id or _prompt("Signal ID (e.g. bull_engulf)", required=False)
    instrument = args.instrument or _prompt("Instrument (e.g. NQ)")
    contract = args.contract or _prompt("Contract (e.g. H25)", required=False)
    timeframe = args.timeframe or _prompt("Timeframe (e.g. 5m)", required=False)
    date_range_start = args.date_range_start or _prompt("Date range start (YYYY-MM-DD)", required=False)
    date_range_end = args.date_range_end or _prompt("Date range end (YYYY-MM-DD)", required=False)
    hypothesis = args.hypothesis or _prompt(
        "Hypothesis statement (one sentence describing the expected edge)",
        required=True,
    )

    print("\n--- Summary ---")
    print(f"  Family:      {family}")
    print(f"  Signal ID:   {signal_id or '(none)'}")
    print(f"  Instrument:  {instrument}")
    print(f"  Contract:    {contract or '(none)'}")
    print(f"  Timeframe:   {timeframe or '(none)'}")
    print(f"  Date range:  {date_range_start or '?'} → {date_range_end or '?'}")
    print(f"  Hypothesis:  {hypothesis}")
    print()

    confirm = input("Register this hypothesis? [Y/n]: ").strip().lower()
    if confirm and confirm not in ("y", "yes"):
        print("Aborted.")
        sys.exit(0)

    exp_id = register(
        db_path,
        hypothesis=hypothesis,
        family=family,
        signal_id=signal_id or None,
        instrument=instrument,
        contract=contract or None,
        timeframe=timeframe or None,
        date_range_start=date_range_start or None,
        date_range_end=date_range_end or None,
    )

    print(f"\n[OK] Registered experiment id={exp_id}")
    print("     Holdout slice is now locked. Run your sweep next.")
    print(f"     To validate: python -m ta_foundation.cli.main --db-path {db_path} ...")


# ---------------------------------------------------------------------------
# Non-interactive flow (all flags supplied)
# ---------------------------------------------------------------------------

def _noninteractive_register(args: argparse.Namespace) -> None:
    if not args.hypothesis:
        print("Error: --hypothesis is required in non-interactive mode.", file=sys.stderr)
        sys.exit(1)
    if not args.instrument:
        print("Error: --instrument is required in non-interactive mode.", file=sys.stderr)
        sys.exit(1)

    exp_id = register(
        args.db_path,
        hypothesis=args.hypothesis,
        family=args.family or None,
        signal_id=args.signal_id or None,
        instrument=args.instrument,
        contract=args.contract or None,
        timeframe=args.timeframe or None,
        date_range_start=args.date_range_start or None,
        date_range_end=args.date_range_end or None,
    )
    print(f"Registered experiment id={exp_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ta_foundation.cli.register_hypothesis",
        description="Pre-register a trading hypothesis before running any sweep.",
    )
    ap.add_argument("--db-path", required=True,
                    help="Path to DuckDB experiment registry file.")
    ap.add_argument("--family", default=None,
                    help="Signal family (candle/ma/bb/orb/breakout/pullback/level/lcr).")
    ap.add_argument("--signal-id", default=None,
                    help="Signal identifier within the family (e.g. bull_engulf).")
    ap.add_argument("--instrument", default=None,
                    help="Instrument root (e.g. NQ).")
    ap.add_argument("--contract", default=None,
                    help="Contract code (e.g. H25).")
    ap.add_argument("--timeframe", default=None,
                    help="Bar timeframe (e.g. 5m).")
    ap.add_argument("--date-range-start", default=None,
                    help="IS start date (YYYY-MM-DD).")
    ap.add_argument("--date-range-end", default=None,
                    help="IS end date (YYYY-MM-DD).")
    ap.add_argument("--hypothesis", default=None,
                    help="One-sentence hypothesis statement.")
    ap.add_argument("--non-interactive", action="store_true",
                    help="Skip all prompts; fail if required flags are missing.")
    ap.add_argument("--list", action="store_true",
                    help="List all registered experiments and exit.")
    return ap


def main() -> None:
    ap = _build_parser()
    args = ap.parse_args()

    if args.list:
        from ta_foundation.persistence.db import ExperimentRegistry
        reg = ExperimentRegistry(args.db_path)
        rows = reg.list_experiments()
        if not rows:
            print("No experiments registered.")
            return
        print(f"{'ID':>4}  {'Status':<12}  {'Family':<10}  {'Signal':<16}  {'Instrument':<10}  Hypothesis")
        print("-" * 90)
        for r in rows:
            print(
                f"{r['id']:>4}  {r['status']:<12}  {(r['family'] or ''):<10}  "
                f"{(r['signal_id'] or ''):<16}  {(r['instrument'] or ''):<10}  "
                f"{r['hypothesis_text'][:50]}"
            )
        return

    if args.non_interactive:
        _noninteractive_register(args)
    else:
        _interactive_register(args)


if __name__ == "__main__":
    main()
