from __future__ import annotations

"""
Holdout Management
==================
Partitions a trade series into three non-overlapping date slices:

    IS (in-sample)   60%  — used for strategy development & walk-forward
    VAL (validation) 20%  — used by run_validation() IS/OOS split
    HOLDOUT          20%  — locked on registration; evaluated once at promotion

Partition is by *date* (chronological), not by row count, so the same calendar
periods are always used regardless of trade density.

Public API
----------
    partition_trades(trades, is_frac, val_frac)
        -> TradePartition (is_df, val_df, holdout_df, date boundaries)

    lock_holdout(db_path, experiment_id, trades)
        -> writes holdout_start/holdout_end into the experiments row

    filter_for_validation(trades, db_path, experiment_id)
        -> returns IS+VAL slice (everything before holdout start)

    run_holdout_evaluation(trades, db_path, experiment_id, ...)
        -> ValidationResult on the holdout slice; marks experiment promoted
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class TradePartition:
    is_df: pd.DataFrame
    val_df: pd.DataFrame
    holdout_df: pd.DataFrame
    is_start: Optional[pd.Timestamp]
    is_end: Optional[pd.Timestamp]
    val_start: Optional[pd.Timestamp]
    val_end: Optional[pd.Timestamp]
    holdout_start: Optional[pd.Timestamp]
    holdout_end: Optional[pd.Timestamp]
    n_is: int
    n_val: int
    n_holdout: int

    def to_dict(self) -> Dict[str, Any]:
        def _ts(t: Optional[pd.Timestamp]) -> Optional[str]:
            return t.isoformat() if t is not None else None
        return {
            "is_start": _ts(self.is_start),
            "is_end": _ts(self.is_end),
            "val_start": _ts(self.val_start),
            "val_end": _ts(self.val_end),
            "holdout_start": _ts(self.holdout_start),
            "holdout_end": _ts(self.holdout_end),
            "n_is": self.n_is,
            "n_val": self.n_val,
            "n_holdout": self.n_holdout,
        }


# ---------------------------------------------------------------------------
# Core: partition
# ---------------------------------------------------------------------------

def partition_trades(
    trades: pd.DataFrame,
    is_frac: float = 0.60,
    val_frac: float = 0.20,
    time_col: str = "entry_time",
) -> TradePartition:
    """
    Split trades chronologically into IS / VAL / HOLDOUT slices.

    Partition is by *date boundary* derived from trade timestamps:
      IS:      first is_frac of the date range
      VAL:     next val_frac of the date range
      HOLDOUT: remaining (1 - is_frac - val_frac) of the date range

    Parameters
    ----------
    trades
        Trade DataFrame.  Must contain a datetime column named ``time_col``.
    is_frac
        Fraction of the date span to use as in-sample. Default 0.60.
    val_frac
        Fraction for validation. Default 0.20.  Holdout = 1 - is_frac - val_frac.
    time_col
        Column containing trade entry timestamps.
    """
    if trades is None or len(trades) == 0:
        empty = pd.DataFrame()
        return TradePartition(
            is_df=empty, val_df=empty, holdout_df=empty,
            is_start=None, is_end=None,
            val_start=None, val_end=None,
            holdout_start=None, holdout_end=None,
            n_is=0, n_val=0, n_holdout=0,
        )

    df = trades.copy()
    if time_col not in df.columns:
        raise ValueError(f"time column '{time_col}' not found in trades")

    df[time_col] = pd.to_datetime(df[time_col], utc=False, errors="coerce")
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

    t_min = df[time_col].min()
    t_max = df[time_col].max()
    span = t_max - t_min

    is_boundary = t_min + span * is_frac
    val_boundary = t_min + span * (is_frac + val_frac)

    is_df = df[df[time_col] < is_boundary].copy()
    val_df = df[(df[time_col] >= is_boundary) & (df[time_col] < val_boundary)].copy()
    holdout_df = df[df[time_col] >= val_boundary].copy()

    def _first(frame: pd.DataFrame) -> Optional[pd.Timestamp]:
        return frame[time_col].min() if len(frame) > 0 else None

    def _last(frame: pd.DataFrame) -> Optional[pd.Timestamp]:
        return frame[time_col].max() if len(frame) > 0 else None

    return TradePartition(
        is_df=is_df,
        val_df=val_df,
        holdout_df=holdout_df,
        is_start=_first(is_df),
        is_end=_last(is_df),
        val_start=_first(val_df),
        val_end=_last(val_df),
        holdout_start=_first(holdout_df),
        holdout_end=_last(holdout_df),
        n_is=len(is_df),
        n_val=len(val_df),
        n_holdout=len(holdout_df),
    )


# ---------------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------------

def lock_holdout(
    db_path: str,
    experiment_id: int,
    trades: pd.DataFrame,
    is_frac: float = 0.60,
    val_frac: float = 0.20,
    time_col: str = "entry_time",
) -> TradePartition:
    """
    Compute the partition and write the holdout date range into the experiments table.

    The holdout boundary is stored as ISO timestamps in two new columns:
    ``holdout_start`` and ``holdout_end`` (written via a raw SQL UPDATE so the
    ExperimentRegistry schema stays minimal).

    Call this once, immediately after ``register_experiment()``, before any sweep.
    """
    import duckdb

    part = partition_trades(trades, is_frac=is_frac, val_frac=val_frac, time_col=time_col)

    holdout_start_iso = part.holdout_start.isoformat() if part.holdout_start is not None else None
    holdout_end_iso = part.holdout_end.isoformat() if part.holdout_end is not None else None

    with duckdb.connect(db_path) as conn:
        # Add columns if absent (idempotent)
        try:
            conn.execute("ALTER TABLE experiments ADD COLUMN holdout_start TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE experiments ADD COLUMN holdout_end TEXT")
        except Exception:
            pass

        conn.execute(
            "UPDATE experiments SET holdout_start = ?, holdout_end = ? WHERE id = ?",
            [holdout_start_iso, holdout_end_iso, experiment_id],
        )

    return part


def filter_for_validation(
    trades: pd.DataFrame,
    db_path: str,
    experiment_id: int,
    time_col: str = "entry_time",
) -> pd.DataFrame:
    """
    Return only the IS+VAL slice (everything before the locked holdout start).

    If no holdout boundary is stored for this experiment, returns all trades.
    """
    import duckdb

    with duckdb.connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT holdout_start FROM experiments WHERE id = ?", [experiment_id]
            ).fetchone()
        except Exception:
            return trades

    if row is None or row[0] is None:
        return trades

    holdout_start = pd.to_datetime(row[0], utc=False, errors="coerce")
    if pd.isna(holdout_start):
        return trades

    df = trades.copy()
    df[time_col] = pd.to_datetime(df[time_col], utc=False, errors="coerce")
    return df[df[time_col] < holdout_start].copy()


def run_holdout_evaluation(
    trades: pd.DataFrame,
    db_path: str,
    experiment_id: int,
    *,
    is_frac: float = 0.60,
    val_frac: float = 0.20,
    time_col: str = "entry_time",
    wf_config: Optional[Dict[str, Any]] = None,
    cost_model: Optional[Dict[str, Any]] = None,
    n_hypotheses_tested: Optional[int] = None,
) -> Any:
    """
    Run validation on the holdout slice and mark the experiment as promoted.

    This function is designed to be called exactly once per experiment.  It:
    1. Checks that the experiment has not already been promoted.
    2. Isolates the holdout slice using the locked boundary.
    3. Runs run_validation() on the holdout trades.
    4. Sets experiment status to "promoted" (or "holdout_failed").
    5. Writes the holdout validation result to the DB.

    Returns the ValidationResult.
    """
    from ta_foundation.analysis.strategy_discovery.validation import run_validation
    from ta_foundation.persistence.db import ExperimentRegistry

    reg = ExperimentRegistry(db_path)
    exp = reg.get_experiment(experiment_id)

    if exp is None:
        raise ValueError(f"Experiment {experiment_id} not found in {db_path}")

    if exp.get("status") in ("promoted", "holdout_failed"):
        raise RuntimeError(
            f"Experiment {experiment_id} has already been evaluated "
            f"(status={exp['status']}). Holdout can only be run once."
        )

    part = partition_trades(trades, is_frac=is_frac, val_frac=val_frac, time_col=time_col)
    holdout_trades = part.holdout_df

    n_hyp = n_hypotheses_tested if n_hypotheses_tested is not None else reg.count_experiments()

    vr = run_validation(
        holdout_trades,
        wf_config=wf_config,
        cost_model=cost_model,
        n_hypotheses_tested=n_hyp,
        db_path=db_path,
        experiment_id=experiment_id,
    )

    new_status = "promoted" if vr.passed else "holdout_failed"
    reg.update_experiment_status(experiment_id, new_status)

    return vr
