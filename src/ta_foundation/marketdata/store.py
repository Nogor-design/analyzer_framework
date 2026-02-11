from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class MinuteBarsKey:
    instrument: str  # "NQ"
    contract: str    # "03-26"


@dataclass
class MarketDataStore:
    """
    Shared market data loaded once and reused across all runs.
    """
    minute_bars: dict[MinuteBarsKey, pd.DataFrame] = field(default_factory=dict)
    sources: dict[MinuteBarsKey, Path] = field(default_factory=dict)

    def add_minute_bars(self, instrument: str, contract: str, df: pd.DataFrame, source_path: Path) -> None:
        key = MinuteBarsKey(instrument=instrument, contract=contract)
        self.minute_bars[key] = df
        self.sources[key] = source_path

    def get(self, instrument: str, contract: str) -> Optional[pd.DataFrame]:
        return self.minute_bars.get(MinuteBarsKey(instrument, contract))
