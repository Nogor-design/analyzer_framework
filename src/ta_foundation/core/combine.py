from __future__ import annotations
import pandas as pd
from ta_foundation.core.model import AnalysisPackage

def combine_trades(packages: dict[str, AnalysisPackage]) -> pd.DataFrame:
    frames = [p.trades for p in packages.values() if p.trades is not None]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def combine_daily(packages: dict[str, AnalysisPackage]) -> pd.DataFrame:
    frames = [p.daily for p in packages.values() if p.daily is not None]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
