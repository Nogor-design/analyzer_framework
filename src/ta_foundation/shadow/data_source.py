"""Bar-loading abstraction for the shadow runner.

The runner does not care where bars come from; it cares that it can ask
"give me the 1-minute bars for instrument X since timestamp T" and get
back a tz-aware DataFrame in the canonical `dt, open, high, low, close,
volume` shape used by the rest of the analysis layer.

Two implementations are provided:
    - FolderBarsDataSource: scans a directory for NinjaTrader minute-bar
      `.Last.txt` exports matching the instrument and concatenates them.
    - InMemoryBarsDataSource: hands back a preloaded DataFrame. Used by
      tests and by callers that already loaded bars via the main pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Protocol

import pandas as pd

from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import (
    MinuteBarsLastTxtParser,
)


class BarsDataSource(Protocol):
    def get_bars(
        self,
        *,
        instrument: str,
        since_ts: Optional[pd.Timestamp] = None,
        until_ts: Optional[pd.Timestamp] = None,
    ) -> pd.DataFrame: ...


class InMemoryBarsDataSource:
    """Wrap a {instrument: DataFrame} dict.

    The DataFrames must have a tz-aware `dt` column and the standard
    OHLCV columns. The constructor copies the dict but not the frames.
    """

    def __init__(self, bars_by_instrument: dict[str, pd.DataFrame]) -> None:
        self._bars = dict(bars_by_instrument)

    def get_bars(
        self,
        *,
        instrument: str,
        since_ts: Optional[pd.Timestamp] = None,
        until_ts: Optional[pd.Timestamp] = None,
    ) -> pd.DataFrame:
        df = self._bars.get(instrument)
        if df is None or df.empty:
            return _empty_bars()
        return _filter_window(df, since_ts, until_ts)


class FolderBarsDataSource:
    """Discover NinjaTrader-style minute-bar exports under a folder.

    File-name convention is the same one `MinuteBarsLastTxtParser` recognises:
    ``"<INSTRUMENT> <MM>-<YY>.Last.txt"`` (e.g. ``"NQ 03-26.Last.txt"``).
    All files matching the instrument prefix are loaded, deduplicated by
    timestamp, and returned together as a single tz-aware DataFrame.
    """

    def __init__(self, folder: Path | str) -> None:
        self._folder = Path(folder)
        self._parser = MinuteBarsLastTxtParser()

    def get_bars(
        self,
        *,
        instrument: str,
        since_ts: Optional[pd.Timestamp] = None,
        until_ts: Optional[pd.Timestamp] = None,
    ) -> pd.DataFrame:
        if not self._folder.is_dir():
            return _empty_bars()
        candidates = sorted(self._folder.glob(f"{instrument} *.Last.txt"))
        frames: list[pd.DataFrame] = []
        for path in candidates:
            try:
                artifact = self._parser.parse(path, run_id=None)
            except Exception:
                continue
            df = getattr(artifact, "df", None)
            if df is None or df.empty:
                continue
            frames.append(df)
        if not frames:
            return _empty_bars()
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=["dt"]).sort_values("dt").reset_index(drop=True)
        return _filter_window(merged, since_ts, until_ts)


# ----- helpers -------------------------------------------------------------


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])


def _filter_window(
    df: pd.DataFrame,
    since_ts: Optional[pd.Timestamp],
    until_ts: Optional[pd.Timestamp],
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if since_ts is not None:
        ts = pd.Timestamp(since_ts)
        out = out[out["dt"] >= ts]
    if until_ts is not None:
        ts = pd.Timestamp(until_ts)
        out = out[out["dt"] <= ts]
    return out.reset_index(drop=True)
