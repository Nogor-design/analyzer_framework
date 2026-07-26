from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import pandas as pd


@dataclass
class OptimizationBatch:
    """
    Represents one imported NinjaTrader Optimization CSV export.

    Unlike AnalysisPackage (which represents a single backtest run),
    OptimizationBatch holds many backtests across parameter combinations
    from a single parameter-sweep export. Stored in OptimizationStore —
    never in AnalysisPackage.

    Attributes
    ----------
    batch_id            : identifier derived from the source filename
                          (e.g. "PantheonMasterBotV01TesterV2")
    source_path         : absolute path to the original CSV
    strategy_name       : display name (same as batch_id by default)
    instrument          : e.g. "NQ 06-26", forward-filled from the CSV
    imported_at         : wall-clock UTC timestamp of the ingest call
    results             : normalized DataFrame — one row per tested combination
    parameter_names     : canonical ordered list of parameter names
                          (extracted from the parenthesized names block)
    metric_columns      : list of numeric KPI column names present in results
    warnings            : parse warnings emitted during ingest
    row_count           : total rows in the source file
    successfully_parsed_rows : rows where the Parameters column parsed cleanly
    """

    batch_id: str
    source_path: Path
    strategy_name: str
    instrument: Optional[str]
    imported_at: datetime
    results: pd.DataFrame
    parameter_names: list[str]
    metric_columns: list[str]
    warnings: list[dict[str, Any]]
    row_count: int
    successfully_parsed_rows: int

    @property
    def failed_rows(self) -> int:
        return self.row_count - self.successfully_parsed_rows


@dataclass
class OptimizationStore:
    """
    Holds all imported OptimizationBatch objects, keyed by batch_id.

    Analogous to MarketDataStore but for optimization parameter-sweep data.
    Multiple optimization CSV files imported in one run each produce one
    OptimizationBatch stored here.
    """

    batches: dict[str, OptimizationBatch] = field(default_factory=dict)

    def add(self, batch: OptimizationBatch) -> None:
        self.batches[batch.batch_id] = batch

    @property
    def is_empty(self) -> bool:
        return len(self.batches) == 0

    def combined_results(self) -> Optional[pd.DataFrame]:
        """
        Concatenate results from all batches.
        A ``batch_id`` column is inserted as the first column to preserve
        traceability back to the source file.
        """
        if self.is_empty:
            return None
        parts: list[pd.DataFrame] = []
        for bid, batch in self.batches.items():
            if batch.results is not None and not batch.results.empty:
                df = batch.results.copy()
                df.insert(0, "batch_id", bid)
                parts.append(df)
        if not parts:
            return None
        return pd.concat(parts, ignore_index=True)
