"""Migration 0004 — shadow_signals idempotency + cursor.

Adds a UNIQUE index on (candidate_id, ts, direction) so that re-running
the shadow pass over already-processed bars is a no-op (the repository's
insert helper uses INSERT OR IGNORE). Also adds a `shadow_cursor_ts`
column on candidates: the most recent bar timestamp the runner has
already consumed for that candidate. The runner only needs to scan
bars at-or-after this watermark; the watermark is advanced on every
successful pass.

This migration is the contract the Phase D.1 shadow runner depends on:
- Idempotency comes from the unique index.
- Restart safety comes from `shadow_cursor_ts`.

No application code change is required to coexist with the new column —
existing readers do not select it.
"""

from __future__ import annotations

import sqlite3

VERSION = 4


def apply(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_signal_per_candidate_ts_dir
            ON shadow_signals(candidate_id, ts, direction)
        """
    )
    cur.execute("ALTER TABLE candidates ADD COLUMN shadow_cursor_ts TEXT")
