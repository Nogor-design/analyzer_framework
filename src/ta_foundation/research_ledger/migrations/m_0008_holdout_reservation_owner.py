"""Migration 0008 — make holdout reservation crash-resumable.

The original ``holdout_attempted`` boolean prevents a second attempt but
cannot distinguish a duplicate request from the owner resuming after a crash.
The reservation id preserves that ownership without weakening the one-shot
gate. Existing spent rows remain spent with a NULL owner and cannot be
reclaimed.
"""

from __future__ import annotations

import sqlite3

VERSION = 8


def apply(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE candidates ADD COLUMN holdout_attempt_id TEXT")
    conn.execute("ALTER TABLE candidates ADD COLUMN holdout_locked_at TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_holdout_attempt_id
            ON candidates(holdout_attempt_id)
            WHERE holdout_attempt_id IS NOT NULL
        """
    )
