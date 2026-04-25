from __future__ import annotations

"""
DuckDB Experiment Registry
==========================
Persistent store for edge-discovery experiments and validation results.

Tables
------
experiments
    One row per registered hypothesis. Locked before running any sweep.
    ``holdout_locked_at`` is set on first registration to prevent leakage.

validation_results
    One row per ``run_validation()`` call.
    ``n_hypotheses_at_time`` captures the Bonferroni denominator at run-time,
    making retroactive correction tractable.

optimization_batches
    Lightweight ledger of imported NinjaTrader optimization sweeps.
    Heavy data lives in parquet; only metadata is stored here.

Usage
-----
    from ta_foundation.persistence.db import ExperimentRegistry
    reg = ExperimentRegistry("experiments.duckdb")
    exp_id = reg.register_experiment(...)
    reg.record_validation(experiment_id=exp_id, ...)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# DuckDB is an optional dependency — fail loudly only when actually used.
try:
    import duckdb
    _DUCKDB_AVAILABLE = True
except ImportError:
    _DUCKDB_AVAILABLE = False


_DDL = """
CREATE TABLE IF NOT EXISTS experiments (
    id                  INTEGER PRIMARY KEY,
    hypothesis_text     TEXT    NOT NULL,
    family              TEXT,
    signal_id           TEXT,
    params_json         TEXT,
    instrument          TEXT,
    contract            TEXT,
    timeframe           TEXT,
    date_range_start    TEXT,
    date_range_end      TEXT,
    cost_model_json     TEXT,
    registered_at       TEXT    NOT NULL,
    holdout_locked_at   TEXT,
    status              TEXT    NOT NULL DEFAULT 'registered'
);

CREATE SEQUENCE IF NOT EXISTS experiments_id_seq START 1;

CREATE TABLE IF NOT EXISTS validation_results (
    id                      INTEGER PRIMARY KEY,
    experiment_id           INTEGER REFERENCES experiments(id),
    run_at                  TEXT    NOT NULL,
    n_hypotheses_at_time    INTEGER NOT NULL DEFAULT 1,
    validation_result_json  TEXT    NOT NULL,
    passed                  BOOLEAN NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS validation_results_id_seq START 1;

CREATE TABLE IF NOT EXISTS optimization_batches (
    id                  INTEGER PRIMARY KEY,
    batch_id            TEXT    NOT NULL UNIQUE,
    strategy_name       TEXT,
    instrument          TEXT,
    imported_at         TEXT    NOT NULL,
    row_count           INTEGER,
    results_parquet_path TEXT
);

CREATE SEQUENCE IF NOT EXISTS optimization_batches_id_seq START 1;
"""


def _require_duckdb() -> None:
    if not _DUCKDB_AVAILABLE:
        raise ImportError(
            "duckdb is required for the experiment registry. "
            "Install it with: pip install duckdb"
        )


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentRegistry:
    """
    Thin wrapper around a DuckDB file database.

    All methods open and close a connection per call to stay safe across
    multi-process usage.  For high-frequency writes, pass ``conn`` to the
    lower-level helpers directly.
    """

    def __init__(self, db_path: str | Path) -> None:
        _require_duckdb()
        self.db_path = Path(db_path)
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        stmts = [s.strip() for s in _DDL.split(";") if s.strip()]
        with duckdb.connect(str(self.db_path)) as conn:
            for stmt in stmts:
                conn.execute(stmt)

    # ------------------------------------------------------------------
    # experiments
    # ------------------------------------------------------------------

    def register_experiment(
        self,
        hypothesis_text: str,
        *,
        family: Optional[str] = None,
        signal_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        instrument: Optional[str] = None,
        contract: Optional[str] = None,
        timeframe: Optional[str] = None,
        date_range_start: Optional[str] = None,
        date_range_end: Optional[str] = None,
        cost_model: Optional[Dict[str, Any]] = None,
        status: str = "registered",
    ) -> int:
        """
        Insert a new experiment row and lock the holdout slice.

        Returns the new experiment id.
        """
        now = _now_utc()
        params_json = json.dumps(params) if params is not None else None
        cost_model_json = json.dumps(cost_model) if cost_model is not None else None

        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO experiments (
                    id, hypothesis_text, family, signal_id, params_json,
                    instrument, contract, timeframe,
                    date_range_start, date_range_end,
                    cost_model_json, registered_at, holdout_locked_at, status
                ) VALUES (
                    nextval('experiments_id_seq'), ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?
                )
                """,
                [
                    hypothesis_text, family, signal_id, params_json,
                    instrument, contract, timeframe,
                    date_range_start, date_range_end,
                    cost_model_json, now, now, status,
                ],
            )
            row = conn.execute(
                "SELECT MAX(id) FROM experiments"
            ).fetchone()
        return int(row[0])

    def get_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?", [experiment_id]
            ).fetchone()
            if row is None:
                return None
            cols = [d[0] for d in conn.description]
        return dict(zip(cols, row))

    def list_experiments(
        self,
        status: Optional[str] = None,
        family: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if family:
            clauses.append("family = ?")
            params.append(family)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                f"SELECT * FROM experiments {where} ORDER BY id", params
            ).fetchall()
            cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def update_experiment_status(self, experiment_id: int, status: str) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE experiments SET status = ? WHERE id = ?",
                [status, experiment_id],
            )

    def count_experiments(self) -> int:
        """Total experiments registered — used as Bonferroni denominator."""
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # validation_results
    # ------------------------------------------------------------------

    def record_validation(
        self,
        experiment_id: Optional[int],
        validation_result: Any,
        *,
        n_hypotheses_at_time: Optional[int] = None,
    ) -> int:
        """
        Persist the result of a ``run_validation()`` call.

        Parameters
        ----------
        experiment_id
            Foreign key to ``experiments``.  May be ``None`` for ad-hoc runs.
        validation_result
            A ``ValidationResult`` object (must have ``.to_dict()`` and ``.passed``),
            or a plain dict with ``passed`` key.
        n_hypotheses_at_time
            Override for the Bonferroni denominator.  If omitted, queries
            ``COUNT(*) FROM experiments`` automatically.
        """
        if n_hypotheses_at_time is None:
            n_hypotheses_at_time = max(self.count_experiments(), 1)

        if hasattr(validation_result, "to_dict"):
            result_dict = validation_result.to_dict()
            passed = bool(validation_result.passed)
        else:
            result_dict = dict(validation_result)
            passed = bool(result_dict.get("passed", False))

        result_json = json.dumps(result_dict)

        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO validation_results (
                    id, experiment_id, run_at, n_hypotheses_at_time,
                    validation_result_json, passed
                ) VALUES (nextval('validation_results_id_seq'), ?, ?, ?, ?, ?)
                """,
                [experiment_id, _now_utc(), n_hypotheses_at_time, result_json, passed],
            )
            row = conn.execute(
                "SELECT MAX(id) FROM validation_results"
            ).fetchone()
        return int(row[0])

    def list_validation_results(
        self,
        experiment_id: Optional[int] = None,
        passed: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            params.append(experiment_id)
        if passed is not None:
            clauses.append("passed = ?")
            params.append(passed)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                f"SELECT * FROM validation_results {where} ORDER BY id", params
            ).fetchall()
            cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------
    # optimization_batches
    # ------------------------------------------------------------------

    def record_optimization_batch(
        self,
        batch_id: str,
        *,
        strategy_name: Optional[str] = None,
        instrument: Optional[str] = None,
        row_count: Optional[int] = None,
        results_parquet_path: Optional[str] = None,
    ) -> None:
        """
        Upsert an optimization batch record.  Silently ignores duplicate batch_ids.
        """
        with duckdb.connect(str(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT id FROM optimization_batches WHERE batch_id = ?", [batch_id]
            ).fetchone()
            if existing:
                return
            conn.execute(
                """
                INSERT INTO optimization_batches (
                    id, batch_id, strategy_name, instrument,
                    imported_at, row_count, results_parquet_path
                ) VALUES (nextval('optimization_batches_id_seq'), ?, ?, ?, ?, ?, ?)
                """,
                [batch_id, strategy_name, instrument, _now_utc(), row_count, results_parquet_path],
            )

    def list_optimization_batches(self) -> List[Dict[str, Any]]:
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM optimization_batches ORDER BY id"
            ).fetchall()
            cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]
