"""Forward-only migrations for the research ledger.

Each migration module must export:
    VERSION: int        -- monotonic, no gaps required, no duplicates
    apply(conn)         -- runs the schema/data change inside an open transaction
"""
