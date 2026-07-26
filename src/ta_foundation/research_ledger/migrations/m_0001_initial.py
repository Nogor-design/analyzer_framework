"""Initial schema for the research ledger.

Tables: families, hypotheses, runs, candidates, tool_journal, shadow_signals.
Indexes on the canonical query paths called out in
docs/designs/agentic_phase_a_foundation.md.
"""

from __future__ import annotations

import sqlite3

VERSION = 1


def apply(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE families (
            family_id              TEXT PRIMARY KEY,
            description            TEXT NOT NULL,
            legitimate_params_json TEXT NOT NULL,
            mechanism_template     TEXT,
            seeded_at              TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE hypotheses (
            hypothesis_id   TEXT PRIMARY KEY,
            family          TEXT NOT NULL REFERENCES families(family_id),
            instrument      TEXT NOT NULL,
            timeframe       TEXT NOT NULL,
            session_window  TEXT,
            direction       TEXT,
            params_json     TEXT NOT NULL,
            mechanism       TEXT NOT NULL,
            dedupe_hash     TEXT NOT NULL UNIQUE,
            registered_at   TEXT NOT NULL,
            registered_by   TEXT NOT NULL,
            parent_id       TEXT REFERENCES hypotheses(hypothesis_id),
            status          TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'retired', 'superseded'))
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE runs (
            run_id         TEXT PRIMARY KEY,
            hypothesis_id  TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
            mode           TEXT NOT NULL
                           CHECK (mode IN ('fast_probe', 'hardened', 'locked_holdout')),
            config_hash    TEXT NOT NULL,
            yaml_path      TEXT NOT NULL,
            artifact_dir   TEXT NOT NULL,
            started_at     TEXT NOT NULL,
            completed_at   TEXT,
            status         TEXT NOT NULL
                           CHECK (status IN ('running', 'completed', 'failed')),
            error          TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE candidates (
            candidate_id          TEXT PRIMARY KEY,
            run_id                TEXT NOT NULL REFERENCES runs(run_id),
            hypothesis_id         TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
            rank_in_run           INTEGER NOT NULL,
            params_json           TEXT NOT NULL,
            n_trades_dev          INTEGER,
            pf_dev                REAL,
            expectancy_dev        REAL,
            n_trades_oos          INTEGER,
            pf_oos                REAL,
            expectancy_oos        REAL,
            n_trades_holdout      INTEGER,
            pf_holdout            REAL,
            expectancy_holdout    REAL,
            gate_verdict          TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (gate_verdict IN ('survivor', 'rejected', 'pending')),
            gate_reasons_json     TEXT,
            slippage_stress_pass  INTEGER,
            folds_distribution    TEXT,
            triage_state          TEXT
                                  CHECK (triage_state IS NULL OR triage_state IN
                                         ('graveyard', 'research', 'hardening_queue',
                                          'shadow', 'decayed')),
            triage_reason         TEXT,
            triaged_at            TEXT,
            triaged_by            TEXT,
            holdout_attempted     INTEGER NOT NULL DEFAULT 0
                                  CHECK (holdout_attempted IN (0, 1))
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE tool_journal (
            journal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                   TEXT NOT NULL,
            role                 TEXT NOT NULL,
            tool_name            TEXT NOT NULL,
            inputs_json          TEXT NOT NULL,
            output_summary       TEXT NOT NULL,
            output_artifact_path TEXT,
            duration_ms          INTEGER NOT NULL,
            error                TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE shadow_signals (
            signal_id              INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id           TEXT NOT NULL REFERENCES candidates(candidate_id),
            ts                     TEXT NOT NULL,
            instrument             TEXT NOT NULL,
            direction              TEXT NOT NULL,
            planned_entry          REAL,
            planned_stop           REAL,
            planned_target         REAL,
            realized_outcome_json  TEXT
        )
        """
    )

    cur.execute("CREATE INDEX idx_runs_hypothesis ON runs(hypothesis_id)")
    cur.execute("CREATE INDEX idx_candidates_run ON candidates(run_id)")
    cur.execute("CREATE INDEX idx_candidates_hypothesis ON candidates(hypothesis_id)")
    cur.execute("CREATE INDEX idx_candidates_triage ON candidates(triage_state)")
    cur.execute("CREATE INDEX idx_journal_ts ON tool_journal(ts)")
    cur.execute("CREATE INDEX idx_shadow_candidate ON shadow_signals(candidate_id)")
    cur.execute("CREATE INDEX idx_hypotheses_family ON hypotheses(family, instrument)")
