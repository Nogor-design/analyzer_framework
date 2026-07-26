"""Migration 0003 — adds candidates.notes_json + seeds the legacy_imported family.

`notes_json` is a flexible JSON column the sidecar parser uses to preserve
metadata that doesn't fit the typed candidate columns (discovery-pipeline
family name, signal name, tier verdict, hardening issues list, etc.).

`legacy_imported` is the catch-all family the A.4 backfill script uses when
a pre-program YAML cannot be mapped to one of the 13 starter families. It
exists only so the FK from hypotheses → families is satisfied for backfill
rows. It is not for forward authoring; the Hypothesis Author should never
propose new hypotheses against it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSION = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("ALTER TABLE candidates ADD COLUMN notes_json TEXT")
    cur.execute(
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
            "legacy_imported",
            ("Backfill catch-all for pre-program YAMLs that don't map to one of "
             "the 13 starter families. Not for forward authoring."),
            json.dumps({}, separators=(",", ":")),
            None,
            _now_iso(),
        ),
    )
