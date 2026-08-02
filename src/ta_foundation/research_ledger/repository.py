"""Repository layer over the research ledger.

This is a thin, typed data access layer. It deliberately contains no
LLM-aware logic: the agent tool layer wraps these calls. The repository
enforces the invariants the agentic plan depends on:

    - Hypothesis dedupe by (family, instrument, timeframe, session_window,
      direction, params, mechanism).
    - Atomic single-attempt holdout lock per candidate.
    - Append-only tool journal.

See docs/designs/agentic_phase_a_foundation.md for the full schema and the
list of canonical queries.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ta_foundation.research_ledger.db import (
    DEFAULT_DB_PATH,
    LedgerIntegrityError,
    init_db,
)
from ta_foundation.research_ledger.models import (
    Candidate,
    Family,
    Hypothesis,
    JournalEntry,
    Run,
    ShadowSignal,
)


class DuplicateHypothesisError(ValueError):
    """Raised when a registration matches an existing dedupe_hash."""

    def __init__(self, dedupe_hash: str, existing_id: str) -> None:
        super().__init__(
            f"Hypothesis with dedupe_hash={dedupe_hash} already exists "
            f"as {existing_id}"
        )
        self.dedupe_hash = dedupe_hash
        self.existing_id = existing_id


class HoldoutAlreadyAttemptedError(RuntimeError):
    """Raised when lock_holdout_attempt is called more than once per candidate."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(
            f"Locked holdout already attempted for candidate {candidate_id}"
        )
        self.candidate_id = candidate_id


_VALID_TRIAGE_STATES = frozenset(
    {"graveyard", "research", "hardening_queue", "shadow", "decayed"}
)
_VALID_HYPOTHESIS_STATUS = frozenset({"open", "retired", "superseded"})


def get_repository(db_path: Path | str = DEFAULT_DB_PATH) -> "Repository":
    """Open the ledger (running migrations if needed) and return a repository."""
    conn = init_db(db_path)
    return Repository(conn)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ensure_ascii."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _compute_dedupe_hash(
    family: str,
    instrument: str,
    timeframe: str,
    session_window: Optional[str],
    direction: Optional[str],
    params: dict,
    mechanism: str,
) -> str:
    payload = {
        "family": family,
        "instrument": instrument,
        "timeframe": timeframe,
        "session_window": session_window or "",
        "direction": direction or "",
        "params": params,
        "mechanism": mechanism.strip(),
    }
    blob = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class Repository:
    """Typed CRUD + canonical queries over the research ledger.

    Construct via `get_repository(db_path)` for normal use, or directly with a
    `sqlite3.Connection` for tests that already opened one.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- Internal helpers ------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    # ---- Families --------------------------------------------------------

    def seed_family(
        self,
        family_id: str,
        description: str,
        legitimate_params: dict,
        mechanism_template: Optional[str] = None,
    ) -> None:
        """Insert or replace a family registry entry. Used by seed migrations."""
        self._conn.execute(
            """
            INSERT INTO families (family_id, description, legitimate_params_json,
                                  mechanism_template, seeded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id) DO UPDATE SET
                description = excluded.description,
                legitimate_params_json = excluded.legitimate_params_json,
                mechanism_template = excluded.mechanism_template
            """,
            (
                family_id,
                description,
                _canonical_json(legitimate_params),
                mechanism_template,
                _now_iso(),
            ),
        )

    def get_family(self, family_id: str) -> Optional[Family]:
        row = self._conn.execute(
            "SELECT * FROM families WHERE family_id = ?", (family_id,)
        ).fetchone()
        return _row_to_family(row) if row else None

    def list_families(self) -> list[Family]:
        rows = self._conn.execute(
            "SELECT * FROM families ORDER BY family_id"
        ).fetchall()
        return [_row_to_family(r) for r in rows]

    # ---- Hypotheses ------------------------------------------------------

    def register_hypothesis(
        self,
        *,
        hypothesis_id: str,
        family: str,
        instrument: str,
        timeframe: str,
        params: dict,
        mechanism: str,
        registered_by: str,
        session_window: Optional[str] = None,
        direction: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> Hypothesis:
        """Insert a new hypothesis. Raises DuplicateHypothesisError if the
        (family, instrument, timeframe, session_window, direction, params,
        mechanism) tuple has already been registered.
        """
        if not mechanism or len(mechanism.strip()) < 50:
            raise ValueError(
                "mechanism must be at least 50 chars (master plan §1: "
                "pre-registration is mandatory)"
            )
        if self.get_family(family) is None:
            raise ValueError(f"Unknown family: {family}")

        dedupe_hash = _compute_dedupe_hash(
            family, instrument, timeframe, session_window, direction, params, mechanism
        )

        existing = self._conn.execute(
            "SELECT hypothesis_id FROM hypotheses WHERE dedupe_hash = ?",
            (dedupe_hash,),
        ).fetchone()
        if existing:
            raise DuplicateHypothesisError(dedupe_hash, existing["hypothesis_id"])

        try:
            self._conn.execute(
                """
                INSERT INTO hypotheses (
                    hypothesis_id, family, instrument, timeframe, session_window,
                    direction, params_json, mechanism, dedupe_hash,
                    registered_at, registered_by, parent_id, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    hypothesis_id,
                    family,
                    instrument,
                    timeframe,
                    session_window,
                    direction,
                    _canonical_json(params),
                    mechanism.strip(),
                    dedupe_hash,
                    _now_iso(),
                    registered_by,
                    parent_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # Race against another writer — treat as duplicate.
            existing = self._conn.execute(
                "SELECT hypothesis_id FROM hypotheses WHERE dedupe_hash = ?",
                (dedupe_hash,),
            ).fetchone()
            if existing:
                raise DuplicateHypothesisError(
                    dedupe_hash, existing["hypothesis_id"]
                ) from exc
            raise

        return self.get_hypothesis(hypothesis_id)  # type: ignore[return-value]

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        row = self._conn.execute(
            "SELECT * FROM hypotheses WHERE hypothesis_id = ?", (hypothesis_id,)
        ).fetchone()
        return _row_to_hypothesis(row) if row else None

    def set_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        if status not in _VALID_HYPOTHESIS_STATUS:
            raise ValueError(f"Invalid hypothesis status: {status}")
        cur = self._conn.execute(
            "UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?",
            (status, hypothesis_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(f"No hypothesis: {hypothesis_id}")

    def find_similar_hypotheses(
        self,
        *,
        family: str,
        instrument: str,
        params: dict,
        threshold: float = 0.7,
        limit: int = 20,
    ) -> list[tuple[Hypothesis, float]]:
        """Return existing hypotheses with Jaccard similarity ≥ threshold on
        their (key, value) param sets. Filters by family + instrument first
        because cross-family similarity is meaningless.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM hypotheses
            WHERE family = ? AND instrument = ?
            """,
            (family, instrument),
        ).fetchall()
        candidate_set = _params_to_set(params)
        scored: list[tuple[Hypothesis, float]] = []
        for row in rows:
            existing_params = json.loads(row["params_json"])
            existing_set = _params_to_set(existing_params)
            sim = _jaccard(candidate_set, existing_set)
            if sim >= threshold:
                scored.append((_row_to_hypothesis(row), sim))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    def count_hypotheses_tested(
        self,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        family: Optional[str] = None,
    ) -> int:
        """Count hypotheses with at least one completed run, optionally
        filtered. This is the multiple-testing denominator referenced by
        T12 in discovery_hardening_plan.md.
        """
        clauses = ["r.status = 'completed'"]
        params: list[Any] = []
        if since:
            clauses.append("h.registered_at >= ?")
            params.append(since)
        if until:
            clauses.append("h.registered_at < ?")
            params.append(until)
        if family:
            clauses.append("h.family = ?")
            params.append(family)
        sql = f"""
            SELECT COUNT(DISTINCT h.hypothesis_id) AS n
            FROM hypotheses h
            JOIN runs r ON r.hypothesis_id = h.hypothesis_id
            WHERE {" AND ".join(clauses)}
        """
        row = self._conn.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    def count_hypotheses_by_family(
        self,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        registered_by: Optional[str] = None,
    ) -> dict[str, int]:
        """Group hypothesis counts by family for coverage tracking (C.3).

        When `registered_by` is provided, restrict to hypotheses authored by
        that role (e.g. 'agent:hypothesis_author'). When None, counts all
        registrations regardless of source (useful for cross-program views).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("registered_at >= ?")
            params.append(since)
        if until:
            clauses.append("registered_at < ?")
            params.append(until)
        if registered_by:
            clauses.append("registered_by = ?")
            params.append(registered_by)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT family, COUNT(*) AS n
            FROM hypotheses
            {where}
            GROUP BY family
        """
        rows = self._conn.execute(sql, params).fetchall()
        return {r["family"]: int(r["n"]) for r in rows}

    # ---- Runs ------------------------------------------------------------

    def start_run(
        self,
        *,
        run_id: str,
        hypothesis_id: str,
        mode: str,
        config_hash: str,
        yaml_path: str,
        artifact_dir: str,
    ) -> Run:
        if self.get_hypothesis(hypothesis_id) is None:
            raise LedgerIntegrityError(
                f"Cannot start run for unknown hypothesis {hypothesis_id}"
            )
        self._conn.execute(
            """
            INSERT INTO runs (run_id, hypothesis_id, mode, config_hash,
                              yaml_path, artifact_dir, started_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                run_id,
                hypothesis_id,
                mode,
                config_hash,
                yaml_path,
                artifact_dir,
                _now_iso(),
            ),
        )
        return self.get_run(run_id)  # type: ignore[return-value]

    def complete_run(self, run_id: str) -> None:
        cur = self._conn.execute(
            """
            UPDATE runs SET status = 'completed', completed_at = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (_now_iso(), run_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(
                f"complete_run on missing or non-running run {run_id}"
            )

    def fail_run(self, run_id: str, error: str) -> None:
        cur = self._conn.execute(
            """
            UPDATE runs SET status = 'failed', completed_at = ?, error = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (_now_iso(), error, run_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(
                f"fail_run on missing or non-running run {run_id}"
            )

    def get_run(self, run_id: str) -> Optional[Run]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    # ---- Candidates ------------------------------------------------------

    def record_candidate(
        self,
        *,
        candidate_id: str,
        run_id: str,
        rank_in_run: int,
        params: dict,
        gate_verdict: str = "pending",
        gate_reasons: Optional[list[dict]] = None,
        n_trades_dev: Optional[int] = None,
        pf_dev: Optional[float] = None,
        expectancy_dev: Optional[float] = None,
        n_trades_oos: Optional[int] = None,
        pf_oos: Optional[float] = None,
        expectancy_oos: Optional[float] = None,
        n_trades_holdout: Optional[int] = None,
        pf_holdout: Optional[float] = None,
        expectancy_holdout: Optional[float] = None,
        slippage_stress_pass: Optional[bool] = None,
        folds_distribution: Optional[list[dict]] = None,
        notes: Optional[dict] = None,
    ) -> Candidate:
        run = self.get_run(run_id)
        if run is None:
            raise LedgerIntegrityError(f"Cannot record candidate against unknown run {run_id}")
        if gate_verdict not in {"survivor", "rejected", "pending"}:
            raise ValueError(f"Invalid gate_verdict: {gate_verdict}")
        self._conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, run_id, hypothesis_id, rank_in_run, params_json,
                n_trades_dev, pf_dev, expectancy_dev,
                n_trades_oos, pf_oos, expectancy_oos,
                n_trades_holdout, pf_holdout, expectancy_holdout,
                gate_verdict, gate_reasons_json,
                slippage_stress_pass, folds_distribution, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                run_id,
                run.hypothesis_id,
                rank_in_run,
                _canonical_json(params),
                n_trades_dev,
                pf_dev,
                expectancy_dev,
                n_trades_oos,
                pf_oos,
                expectancy_oos,
                n_trades_holdout,
                pf_holdout,
                expectancy_holdout,
                gate_verdict,
                _canonical_json(gate_reasons) if gate_reasons is not None else None,
                None if slippage_stress_pass is None else int(bool(slippage_stress_pass)),
                _canonical_json(folds_distribution) if folds_distribution is not None else None,
                _canonical_json(notes) if notes is not None else None,
            ),
        )
        return self.get_candidate(candidate_id)  # type: ignore[return-value]

    def get_candidate(self, candidate_id: str) -> Optional[Candidate]:
        row = self._conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return _row_to_candidate(row) if row else None

    def list_candidates(
        self,
        *,
        family: Optional[str] = None,
        instrument: Optional[str] = None,
        triage_state: Optional[str] = None,
        gate_verdict: Optional[str] = None,
        untriaged_only: bool = False,
        run_id: Optional[str] = None,
        hypothesis_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[Candidate]:
        clauses: list[str] = []
        params: list[Any] = []
        if family or instrument:
            clauses.append(
                "candidate_id IN (SELECT c.candidate_id FROM candidates c "
                "JOIN hypotheses h ON h.hypothesis_id = c.hypothesis_id WHERE 1=1"
                + (" AND h.family = ?" if family else "")
                + (" AND h.instrument = ?" if instrument else "")
                + ")"
            )
            if family:
                params.append(family)
            if instrument:
                params.append(instrument)
        if triage_state is not None:
            if triage_state not in _VALID_TRIAGE_STATES:
                raise ValueError(f"Invalid triage_state filter: {triage_state}")
            clauses.append("triage_state = ?")
            params.append(triage_state)
        if untriaged_only:
            clauses.append("triage_state IS NULL")
        if gate_verdict is not None:
            clauses.append("gate_verdict = ?")
            params.append(gate_verdict)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if hypothesis_id is not None:
            clauses.append("hypothesis_id = ?")
            params.append(hypothesis_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM candidates {where} "
            f"ORDER BY rank_in_run ASC, candidate_id ASC LIMIT ?"
        )
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def list_graveyard(
        self,
        *,
        family: Optional[str] = None,
        instrument: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> list[Candidate]:
        clauses = ["c.triage_state = 'graveyard'"]
        params: list[Any] = []
        if family:
            clauses.append("h.family = ?")
            params.append(family)
        if instrument:
            clauses.append("h.instrument = ?")
            params.append(instrument)
        if since:
            clauses.append("c.triaged_at >= ?")
            params.append(since)
        sql = f"""
            SELECT c.* FROM candidates c
            JOIN hypotheses h ON h.hypothesis_id = c.hypothesis_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.triaged_at DESC LIMIT ?
        """
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def set_triage(
        self,
        *,
        candidate_id: str,
        state: str,
        reason: str,
        triaged_by: str,
    ) -> None:
        if state not in _VALID_TRIAGE_STATES:
            raise ValueError(f"Invalid triage state: {state}")
        if not reason or len(reason.strip()) < 20:
            raise ValueError("triage reason must be at least 20 chars")
        cur = self._conn.execute(
            """
            UPDATE candidates
            SET triage_state = ?, triage_reason = ?, triaged_at = ?, triaged_by = ?
            WHERE candidate_id = ?
            """,
            (state, reason.strip(), _now_iso(), triaged_by, candidate_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(f"set_triage on unknown candidate {candidate_id}")

    def lock_holdout_attempt(self, candidate_id: str) -> bool:
        """Atomic compare-and-swap: returns True if this is the first attempt
        for the candidate, False on every subsequent call. Raises
        LedgerIntegrityError if the candidate doesn't exist.
        """
        if self.get_candidate(candidate_id) is None:
            raise LedgerIntegrityError(
                f"lock_holdout_attempt on unknown candidate {candidate_id}"
            )
        cur = self._conn.execute(
            """
            UPDATE candidates SET holdout_attempted = 1
            WHERE candidate_id = ? AND holdout_attempted = 0
            """,
            (candidate_id,),
        )
        return cur.rowcount == 1

    def reserve_holdout_attempt(self, candidate_id: str, attempt_id: str) -> bool:
        """Reserve the one-shot holdout for a named crash-resumable attempt.

        Returns True when this call acquired the lock *or* when ``attempt_id``
        already owns it. Returns False when another/legacy attempt spent it.
        """
        owner = attempt_id.strip()
        if not owner:
            raise ValueError("attempt_id is required")
        if self.get_candidate(candidate_id) is None:
            raise LedgerIntegrityError(
                f"reserve_holdout_attempt on unknown candidate {candidate_id}"
            )
        cur = self._conn.execute(
            """
            UPDATE candidates
            SET holdout_attempted = 1,
                holdout_attempt_id = ?,
                holdout_locked_at = ?
            WHERE candidate_id = ? AND holdout_attempted = 0
            """,
            (owner, _now_iso(), candidate_id),
        )
        if cur.rowcount == 1:
            return True
        row = self._conn.execute(
            """
            SELECT holdout_attempt_id
            FROM candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        return bool(row and row["holdout_attempt_id"] == owner)

    def record_holdout_result(
        self,
        *,
        candidate_id: str,
        n_trades: Optional[int],
        profit_factor: Optional[float],
        expectancy: Optional[float] = None,
    ) -> None:
        """Persist the sealed result once; identical crash retries are a no-op."""
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            raise LedgerIntegrityError(
                f"record_holdout_result on unknown candidate {candidate_id}"
            )
        if not candidate.holdout_attempted:
            raise LedgerIntegrityError(
                f"record_holdout_result before holdout reservation for {candidate_id}"
            )
        desired = (
            None if n_trades is None else int(n_trades),
            None if profit_factor is None else float(profit_factor),
            None if expectancy is None else float(expectancy),
        )
        existing = (
            candidate.n_trades_holdout,
            candidate.pf_holdout,
            candidate.expectancy_holdout,
        )
        if any(value is not None for value in existing):
            if existing != desired:
                raise LedgerIntegrityError(
                    f"holdout result already recorded for {candidate_id}"
                )
            return
        self._conn.execute(
            """
            UPDATE candidates
            SET n_trades_holdout = ?,
                pf_holdout = ?,
                expectancy_holdout = ?
            WHERE candidate_id = ?
            """,
            (*desired, candidate_id),
        )

    # ---- Tool journal ----------------------------------------------------

    def journal(
        self,
        *,
        role: str,
        tool_name: str,
        inputs: dict,
        output_summary: str,
        duration_ms: int,
        output_artifact_path: Optional[str] = None,
        error: Optional[str] = None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO tool_journal (
                ts, role, tool_name, inputs_json, output_summary,
                output_artifact_path, duration_ms, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                role,
                tool_name,
                _canonical_json(inputs),
                output_summary,
                output_artifact_path,
                int(duration_ms),
                error,
            ),
        )
        return int(cur.lastrowid or 0)

    # ---- Shadow signals --------------------------------------------------

    def insert_shadow_signal_if_absent(
        self,
        *,
        candidate_id: str,
        ts: str,
        instrument: str,
        direction: str,
        planned_entry: Optional[float],
        planned_stop: Optional[float],
        planned_target: Optional[float],
        realized_outcome: Optional[dict] = None,
    ) -> Optional[int]:
        """Insert a shadow signal idempotently keyed on (candidate_id, ts, direction).

        Returns the new signal_id on insert, or None if the row already exists.
        Idempotency is guaranteed by the unique index in migration 0004.
        """
        payload_json: Optional[str] = (
            _canonical_json(realized_outcome) if realized_outcome is not None else None
        )
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO shadow_signals (
                candidate_id, ts, instrument, direction,
                planned_entry, planned_stop, planned_target, realized_outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                ts,
                instrument,
                direction,
                planned_entry,
                planned_stop,
                planned_target,
                payload_json,
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid or 0)

    def update_shadow_outcome(
        self,
        *,
        signal_id: int,
        realized_outcome: dict,
    ) -> None:
        cur = self._conn.execute(
            "UPDATE shadow_signals SET realized_outcome_json = ? WHERE signal_id = ?",
            (_canonical_json(realized_outcome), int(signal_id)),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(
                f"update_shadow_outcome on unknown signal_id {signal_id}"
            )

    def list_shadow_signals(
        self,
        *,
        candidate_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        limit: int = 1000,
    ) -> list[ShadowSignal]:
        clauses: list[str] = []
        params: list[Any] = []
        if candidate_id is not None:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM shadow_signals {where} "
            f"ORDER BY ts ASC, signal_id ASC LIMIT ?"
        )
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_shadow_signal(r) for r in rows]

    def list_open_shadow_signals(
        self,
        *,
        candidate_id: Optional[str] = None,
    ) -> list[ShadowSignal]:
        """Return signals that have no terminal outcome yet.

        A terminal status is ``"resolved"`` or ``"no_fill"``. Anything else
        (``"pending"``, ``"open"``, NULL, or unrecognised) is reported as
        open so the runner gets a chance to advance the state machine.
        The pattern match is a coarse SQL filter; callers must still
        inspect the payload.
        """
        clauses = [
            "(realized_outcome_json IS NULL "
            " OR (realized_outcome_json NOT LIKE '%\"status\":\"resolved\"%' "
            "     AND realized_outcome_json NOT LIKE '%\"status\":\"no_fill\"%'))"
        ]
        params: list[Any] = []
        if candidate_id is not None:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        sql = (
            "SELECT * FROM shadow_signals WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ts ASC, signal_id ASC"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_shadow_signal(r) for r in rows]

    def get_shadow_cursor(self, candidate_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT shadow_cursor_ts FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise LedgerIntegrityError(
                f"get_shadow_cursor on unknown candidate {candidate_id}"
            )
        return _safe_row_get(row, "shadow_cursor_ts")

    def set_shadow_cursor(self, *, candidate_id: str, cursor_ts: str) -> None:
        cur = self._conn.execute(
            "UPDATE candidates SET shadow_cursor_ts = ? WHERE candidate_id = ?",
            (cursor_ts, candidate_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(
                f"set_shadow_cursor on unknown candidate {candidate_id}"
            )

    # ---- Decay state (Phase D.3) -----------------------------------------

    def get_decay_state(self, candidate_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT decay_state_json FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise LedgerIntegrityError(
                f"get_decay_state on unknown candidate {candidate_id}"
            )
        raw = _safe_row_get(row, "decay_state_json")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set_decay_state(
        self, *, candidate_id: str, state: Optional[dict],
    ) -> None:
        """Persist a decay-state blob to ``candidates.decay_state_json``.

        Pass ``state=None`` to clear the column (e.g. when a candidate is
        re-enrolled into shadow and we want a fresh statistic).
        """
        payload = None if state is None else _canonical_json(state)
        cur = self._conn.execute(
            "UPDATE candidates SET decay_state_json = ? WHERE candidate_id = ?",
            (payload, candidate_id),
        )
        if cur.rowcount == 0:
            raise LedgerIntegrityError(
                f"set_decay_state on unknown candidate {candidate_id}"
            )

    # ---- Tool journal ----------------------------------------------------

    def list_journal(
        self,
        *,
        role: Optional[str] = None,
        tool_name: Optional[str] = None,
        with_errors_only: bool = False,
        limit: int = 100,
    ) -> list[JournalEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if role:
            clauses.append("role = ?")
            params.append(role)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if with_errors_only:
            clauses.append("error IS NOT NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tool_journal {where} ORDER BY journal_id DESC LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_journal(r) for r in rows]


# ----- Row mapping helpers ----------------------------------------------------


def _row_to_family(row: sqlite3.Row) -> Family:
    return Family(
        family_id=row["family_id"],
        description=row["description"],
        legitimate_params_json=row["legitimate_params_json"],
        mechanism_template=row["mechanism_template"],
        seeded_at=row["seeded_at"],
    )


def _row_to_hypothesis(row: sqlite3.Row) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=row["hypothesis_id"],
        family=row["family"],
        instrument=row["instrument"],
        timeframe=row["timeframe"],
        session_window=row["session_window"],
        direction=row["direction"],
        params_json=row["params_json"],
        mechanism=row["mechanism"],
        dedupe_hash=row["dedupe_hash"],
        registered_at=row["registered_at"],
        registered_by=row["registered_by"],
        parent_id=row["parent_id"],
        status=row["status"],
    )


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        hypothesis_id=row["hypothesis_id"],
        mode=row["mode"],
        config_hash=row["config_hash"],
        yaml_path=row["yaml_path"],
        artifact_dir=row["artifact_dir"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        status=row["status"],
        error=row["error"],
    )


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        candidate_id=row["candidate_id"],
        run_id=row["run_id"],
        hypothesis_id=row["hypothesis_id"],
        rank_in_run=row["rank_in_run"],
        params_json=row["params_json"],
        n_trades_dev=row["n_trades_dev"],
        pf_dev=row["pf_dev"],
        expectancy_dev=row["expectancy_dev"],
        n_trades_oos=row["n_trades_oos"],
        pf_oos=row["pf_oos"],
        expectancy_oos=row["expectancy_oos"],
        n_trades_holdout=row["n_trades_holdout"],
        pf_holdout=row["pf_holdout"],
        expectancy_holdout=row["expectancy_holdout"],
        gate_verdict=row["gate_verdict"],
        gate_reasons_json=row["gate_reasons_json"],
        slippage_stress_pass=row["slippage_stress_pass"],
        folds_distribution=row["folds_distribution"],
        triage_state=row["triage_state"],
        triage_reason=row["triage_reason"],
        triaged_at=row["triaged_at"],
        triaged_by=row["triaged_by"],
        holdout_attempted=row["holdout_attempted"],
        notes_json=_safe_row_get(row, "notes_json"),
    )


def _safe_row_get(row: sqlite3.Row, key: str):
    """Safely access a row column that may not exist in older schemas."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _row_to_shadow_signal(row: sqlite3.Row) -> ShadowSignal:
    return ShadowSignal(
        signal_id=int(row["signal_id"]),
        candidate_id=row["candidate_id"],
        ts=row["ts"],
        instrument=row["instrument"],
        direction=row["direction"],
        planned_entry=row["planned_entry"],
        planned_stop=row["planned_stop"],
        planned_target=row["planned_target"],
        realized_outcome_json=row["realized_outcome_json"],
    )


def _row_to_journal(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        journal_id=row["journal_id"],
        ts=row["ts"],
        role=row["role"],
        tool_name=row["tool_name"],
        inputs_json=row["inputs_json"],
        output_summary=row["output_summary"],
        output_artifact_path=row["output_artifact_path"],
        duration_ms=row["duration_ms"],
        error=row["error"],
    )


# ----- Pure helpers ---------------------------------------------------------


def _params_to_set(params: dict) -> set[tuple[str, str]]:
    return {(k, _canonical_json(v)) for k, v in params.items()}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
