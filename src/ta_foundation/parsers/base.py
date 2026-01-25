# src/ta_foundation/parsers/base.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Any, Optional, runtime_checkable

import pandas as pd


@dataclass
class ParsedArtifact:
    """
    A single parsed output from one file.
    `kind` is one of: "trades", "daily", "summary".
    `run_id` identifies which bot/run/export batch this belongs to.
    """
    kind: str
    run_id: str
    source_path: Path
    df: Optional[pd.DataFrame] = None
    summary: Optional[dict[str, Any]] = None
    warnings: list[dict[str, Any]] = None


@runtime_checkable
class Parser(Protocol):
    kind: str  # "trades" | "daily" | "summary"

    def can_parse(self, path: Path, header: str) -> bool: ...
    def parse(self, path: Path, run_id: str) -> ParsedArtifact: ...
