from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pandas as pd


@dataclass(frozen=True)
class MinuteBarsKey:
    instrument: str  # e.g. "NQ"
    contract: str    # e.g. "03-26"


@dataclass
class MinuteBars:
    """
    Canonical minute bars:
      dt: tz-aware America/Denver
      open/high/low/close: float
      volume: int
    """
    key: MinuteBarsKey
    df: pd.DataFrame
    source_path: Path
    source_tz: str
    target_tz: str
