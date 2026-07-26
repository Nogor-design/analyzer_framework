from __future__ import annotations

"""
Promote Strategy CLI
====================
Runs holdout evaluation exactly once for a registered experiment.

    python -m ta_foundation.cli.promote_strategy \\
        --db-path experiments.duckdb \\
        --experiment-id 3 \\
        --trades-parquet path/to/trades.parquet

Prints the holdout ValidationResult summary and updates experiment status to
"promoted" (passed) or "holdout_failed" (failed).

Holdout evaluation is a one-shot operation — attempting to run it a second time
on the same experiment raises an error.
"""

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ta_foundation.cli.promote_strategy",
        description="Run holdout evaluation for a pre-registered experiment.",
    )
    ap.add_argument("--db-path", required=True,
                    help="Path to DuckDB experiment registry file.")
    ap.add_argument("--experiment-id", required=True, type=int,
                    help="Experiment id to evaluate.")
    ap.add_argument("--trades-parquet", default=None,
                    help="Parquet file containing the full trade history. "
                         "Must have an 'entry_time' column and a 'profit' column.")
    ap.add_argument("--trades-csv", default=None,
                    help="CSV file containing the full trade history (alternative to parquet).")
    ap.add_argument("--is-frac", type=float, default=0.60,
                    help="IS fraction used when the partition was originally defined (default 0.60).")
    ap.add_argument("--val-frac", type=float, default=0.20,
                    help="Validation fraction (default 0.20). Holdout = 1 - is_frac - val_frac.")
    ap.add_argument("--time-col", default="entry_time",
                    help="Name of the timestamp column in the trades file (default: entry_time).")
    return ap


def main() -> None:
    ap = _build_parser()
    args = ap.parse_args()

    import pandas as pd
    from ta_foundation.persistence.db import ExperimentRegistry
    from ta_foundation.analysis.strategy_discovery.holdout import run_holdout_evaluation

    # Load trades
    trades: pd.DataFrame
    if args.trades_parquet:
        trades = pd.read_parquet(args.trades_parquet)
    elif args.trades_csv:
        trades = pd.read_csv(args.trades_csv)
    else:
        print("Error: provide --trades-parquet or --trades-csv.", file=sys.stderr)
        sys.exit(1)

    reg = ExperimentRegistry(args.db_path)
    exp = reg.get_experiment(args.experiment_id)
    if exp is None:
        print(f"Error: experiment id={args.experiment_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== Holdout Evaluation: experiment id={args.experiment_id} ===")
    print(f"  Hypothesis: {exp['hypothesis_text']}")
    print(f"  Family:     {exp.get('family') or '(none)'}")
    print(f"  Instrument: {exp.get('instrument') or '(none)'}")
    print(f"  Status:     {exp.get('status')}")
    print()

    try:
        vr = run_holdout_evaluation(
            trades,
            db_path=args.db_path,
            experiment_id=args.experiment_id,
            is_frac=args.is_frac,
            val_frac=args.val_frac,
            time_col=args.time_col,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(vr.summary)

    exp_after = reg.get_experiment(args.experiment_id)
    print(f"\nExperiment status updated → {exp_after.get('status')}")
    sys.exit(0 if vr.passed else 1)


if __name__ == "__main__":
    main()
