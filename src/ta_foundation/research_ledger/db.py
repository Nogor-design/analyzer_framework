"""SQLite connection management and migration runner for the research ledger.

The ledger lives at `.ta_artifacts/research_ledger.db` by default. Migrations
are forward-only Python modules under `migrations/`; each exposes a `VERSION`
integer and an `apply(conn)` function.

The schema-version table (`_schema_version`) records which migrations have
run. `init_db` is idempotent — calling it on a fully-migrated database is a
no-op.
"""

from __future__ import annotations

import importlib
import pkgutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path(".ta_artifacts/research_ledger.db")


class LedgerIntegrityError(RuntimeError):
    """Raised when the schema-version table is in an unexpected state."""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with sane defaults: WAL, FK, row factory, busy timeout."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the ledger and apply any pending migrations. Idempotent."""
    conn = connect(db_path)
    _ensure_version_table(conn)
    apply_pending_migrations(conn)
    return conn


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    _ensure_version_table(conn)
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM _schema_version"
    ).fetchone()
    return int(row["v"]) if row else 0


def _discover_migrations() -> list[tuple[int, object]]:
    """Return [(version, module), ...] sorted by version."""
    from ta_foundation.research_ledger import migrations as migrations_pkg

    found: list[tuple[int, object]] = []
    for mod_info in pkgutil.iter_modules(migrations_pkg.__path__):
        name = mod_info.name
        if not name.startswith("m_") and not name[0:4].isdigit():
            continue
        module = importlib.import_module(f"{migrations_pkg.__name__}.{name}")
        version = getattr(module, "VERSION", None)
        if not isinstance(version, int):
            raise LedgerIntegrityError(
                f"Migration module {name} missing integer VERSION attribute"
            )
        if not callable(getattr(module, "apply", None)):
            raise LedgerIntegrityError(
                f"Migration module {name} missing apply(conn) callable"
            )
        found.append((version, module))
    found.sort(key=lambda t: t[0])
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise LedgerIntegrityError(f"Duplicate migration versions: {versions}")
    return found


def apply_pending_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply migrations whose version > current. Returns list of applied versions."""
    _ensure_version_table(conn)
    current = current_schema_version(conn)
    migrations = _discover_migrations()
    applied: list[int] = []
    for version, module in migrations:
        if version <= current:
            continue
        conn.execute("BEGIN")
        try:
            module.apply(conn)
            conn.execute(
                "INSERT INTO _schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now_iso()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
