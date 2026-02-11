# src/ta_foundation/parsers/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Any, Optional, runtime_checkable

import pandas as pd


@dataclass
class ParsedArtifact:
    """
    A single parsed output from one file.

    `kind` identifies the artifact type, e.g.:
      - "trades"
      - "daily"
      - "summary"
      - "market_minute_bars"  (shared market reference data)

    `run_id` identifies which bot/run/export batch this belongs to.
    If run_id is None, the artifact is shared/global (not run-scoped).
    """
    kind: str
    run_id: Optional[str]
    source_path: Path
    df: Optional[pd.DataFrame] = None
    summary: Optional[dict[str, Any]] = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class Parser(Protocol):
    kind: str  # e.g. "trades" | "daily" | "summary" | "market_minute_bars"

    def can_parse(self, path: Path, header: str) -> bool: ...
    def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact: ...
