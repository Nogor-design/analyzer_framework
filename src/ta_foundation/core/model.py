# src/ta_foundation/core/model.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any
import pandas as pd


@dataclass
class SummaryBlock:
    kpis_all: dict[str, Any] = field(default_factory=dict)
    kpis_long: dict[str, Any] = field(default_factory=dict)
    kpis_short: dict[str, Any] = field(default_factory=dict)
    start_dt: Optional[Any] = None  # timezone-aware datetime
    end_dt: Optional[Any] = None


@dataclass
class AnalysisPackage:
    run_id: str
    trades: Optional[pd.DataFrame] = None
    daily: Optional[pd.DataFrame] = None
    summary: Optional[SummaryBlock] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
