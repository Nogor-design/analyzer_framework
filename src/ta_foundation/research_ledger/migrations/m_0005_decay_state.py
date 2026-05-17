"""Migration 0005 — decay_state_json on candidates (Phase D.3).

The Phase D plan's edge-decay test (Page CUSUM on
`realized_expectancy - backtest_expectancy`) needs a per-candidate running
state that survives between shadow passes. The math itself lives in
`ta_foundation.shadow.decay`; this migration just gives it a place to
persist:

    candidates.decay_state_json TEXT NULL

The column is a JSON blob holding the CUSUM statistic, the reference
expectancy / σ used at init, the highest signal_id already consumed (so
re-runs are idempotent), and a `triggered` flag. The runner sets
`triage_state='decayed'` and journals the decision when the statistic
crosses the threshold; existing readers that did not select this column
do not need to change.
"""

from __future__ import annotations

import sqlite3

VERSION = 5


def apply(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE candidates ADD COLUMN decay_state_json TEXT")
