from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple, Literal

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.marketdata.resample import (
    ohlcv_resample_from_ticks,
    ohlcv_resample_from_bars,
    slice_dt,
)

Key = Tuple[str, str]  # (instrument_root, contract)
BarsKey = Tuple[str, str, str, str]  # (instrument_root, contract, timeframe, source_policy)


def _merge_frames(frames: list[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """
    Concatenate a list of DataFrames on the 'dt' column, sort chronologically,
    and drop exact duplicate timestamps (keeping first occurrence).
    Returns None if the resulting frame is empty.
    """
    valid = [f for f in frames if f is not None and not f.empty]
    if not valid:
        return None
    merged = pd.concat(valid, ignore_index=True)
    if "dt" in merged.columns:
        merged = merged.sort_values("dt").drop_duplicates(subset=["dt"], keep="first").reset_index(drop=True)
    return merged


BarsSource = Literal["auto", "minute", "ticks"]


@dataclass
class MarketDataStore:
    """
    Shared market reference data across runs.

    Canonical goal:
      - ticks represent the highest granularity stream (when available)
      - minute bars are a fast precomputed view of the same stream

    All stored dataframes MUST have tz-aware America/Denver timestamps in column "dt".
    """
    minute_bars: dict[Key, pd.DataFrame] = field(default_factory=dict)
    ticks: dict[Key, pd.DataFrame] = field(default_factory=dict)

    # Derived cache: timeframe bars computed from minute or ticks
    bars_cache: dict[BarsKey, pd.DataFrame] = field(default_factory=dict)

    def put_minute_bars(self, instrument_root: str, contract: str, df: pd.DataFrame) -> None:
        self.minute_bars[(instrument_root, contract)] = df
        # Invalidate derived bars for this instrument/contract
        self._invalidate(instrument_root, contract)

    def put_ticks(self, instrument_root: str, contract: str, df: pd.DataFrame) -> None:
        self.ticks[(instrument_root, contract)] = df
        self._invalidate(instrument_root, contract)

    def _invalidate(self, instrument_root: str, contract: str) -> None:
        dead = [k for k in self.bars_cache.keys() if k[0] == instrument_root and k[1] == contract]
        for k in dead:
            self.bars_cache.pop(k, None)

    def get_minute_bars(self, instrument_root: str, contract: str) -> Optional[pd.DataFrame]:
        df = self.minute_bars.get((instrument_root, contract))
        if df is None and contract:
            # Fall back to the instrument-wide merged dataset (contract="")
            df = self.minute_bars.get((instrument_root, ""))
        return df

    def get_ticks(self, instrument_root: str, contract: str) -> Optional[pd.DataFrame]:
        df = self.ticks.get((instrument_root, contract))
        if df is None and contract:
            # Fall back to the instrument-wide merged dataset (contract="")
            df = self.ticks.get((instrument_root, ""))
        return df

    # Back-compat: existing callers may use .get(...) for minute bars
    def get(self, instrument_root: str, contract: str) -> Optional[pd.DataFrame]:
        return self.get_minute_bars(instrument_root, contract)

    def finalize(self) -> None:
        """
        Merge all per-contract data for each instrument root into a single
        chronological dataset stored under contract="" (the merged key).

        Call this once after all market data files have been ingested.
        Consumers that request a specific contract that isn't loaded will
        automatically fall back to the merged key via get_minute_bars / get_ticks.
        """
        # ---- minute bars ----
        roots_bars: dict[str, list[pd.DataFrame]] = {}
        for (root, contract), df in self.minute_bars.items():
            if contract == "":
                continue  # skip any previously merged entry
            roots_bars.setdefault(root, []).append(df)

        for root, frames in roots_bars.items():
            merged = _merge_frames(frames)
            if merged is not None and not merged.empty:
                self.minute_bars[(root, "")] = merged
                self._invalidate(root, "")

        # ---- ticks ----
        roots_ticks: dict[str, list[pd.DataFrame]] = {}
        for (root, contract), df in self.ticks.items():
            if contract == "":
                continue
            roots_ticks.setdefault(root, []).append(df)

        for root, frames in roots_ticks.items():
            merged = _merge_frames(frames)
            if merged is not None and not merged.empty:
                self.ticks[(root, "")] = merged
                self._invalidate(root, "")

    def ingest_artifact(self, art: ParsedArtifact) -> bool:
        """
        Attach a shared ParsedArtifact into this store.

        Returns True if ingested, False if not recognized as market data.
        """
        if art is None or art.df is None:
            return False
        if art.run_id is not None:
            return False  # only shared artifacts belong here

        summary = art.summary or {}
        instrument = (summary.get("instrument") or "").strip()
        contract = (summary.get("contract") or "").strip()
        if not instrument or not contract:
            return False

        if art.kind == "market_minute_bars":
            self.put_minute_bars(instrument, contract, art.df)
            return True

        if art.kind == "market_ticks":
            self.put_ticks(instrument, contract, art.df)
            return True

        return False

    def get_bars(
        self,
        instrument_root: str,
        contract: str,
        timeframe: str = "1m",
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
        source: BarsSource = "auto",
    ) -> Optional[pd.DataFrame]:
        """
        Retrieve OHLCV bars for a given timeframe.

        source policy:
          - auto: use minute bars for 1m if available; otherwise derive from ticks
          - minute: force minute bars (1m only) and resample to higher TFs
          - ticks: force tick->bar derivation (then resample)

        Returns a dataframe with columns: dt, open, high, low, close, volume
        """
        tf = (timeframe or "").lower().strip()
        src = source

        # Resolve policy
        if src == "auto":
            if tf == "1m" and self.get_minute_bars(instrument_root, contract) is not None:
                src = "minute"
            elif self.get_ticks(instrument_root, contract) is not None:
                src = "ticks"
            elif self.get_minute_bars(instrument_root, contract) is not None:
                # no ticks; minute available; allow minute-derived bars
                src = "minute"
            else:
                return None

        cache_key: BarsKey = (instrument_root, contract, tf, src)
        if cache_key in self.bars_cache:
            return slice_dt(self.bars_cache[cache_key], start, end)

        base_1m: Optional[pd.DataFrame] = None

        if src == "minute":
            mb = self.get_minute_bars(instrument_root, contract)
            if mb is None or mb.empty:
                return None
            base_1m = mb[["dt", "open", "high", "low", "close", "volume"]].copy()

        elif src == "ticks":
            tk = self.get_ticks(instrument_root, contract)
            if tk is None or tk.empty:
                return None
            # Build requested TF directly from ticks if tf == 1m; else 1m then resample.
            base_1m = ohlcv_resample_from_ticks(tk, "1m")

        else:
            raise ValueError(f"Unsupported source policy: {source}")

        if base_1m is None or base_1m.empty:
            self.bars_cache[cache_key] = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
            return slice_dt(self.bars_cache[cache_key], start, end)

        if tf == "1m":
            bars = base_1m
        else:
            bars = ohlcv_resample_from_bars(base_1m, tf)

        bars = bars.sort_values("dt").reset_index(drop=True)
        self.bars_cache[cache_key] = bars
        return slice_dt(bars, start, end)

    def validate_minute_vs_ticks(
        self,
        instrument_root: str,
        contract: str,
        max_close_diff: float = 0.0,
        max_high_diff: float = 0.0,
        max_low_diff: float = 0.0,
    ) -> dict:
        """
        Optional diagnostic: compare provided 1m bars vs 1m bars derived from ticks.

        Returns a dict of counts + examples. Does not mutate stored data.
        """
        mb = self.get_minute_bars(instrument_root, contract)
        tk = self.get_ticks(instrument_root, contract)
        if mb is None or mb.empty or tk is None or tk.empty:
            return {"ok": False, "message": "minute or tick data missing"}

        tick_1m = ohlcv_resample_from_ticks(tk, "1m")
        if tick_1m.empty:
            return {"ok": False, "message": "tick-derived 1m empty"}

        a = mb[["dt", "open", "high", "low", "close", "volume"]].copy()
        b = tick_1m[["dt", "open", "high", "low", "close", "volume"]].copy()

        m = a.merge(b, on="dt", how="inner", suffixes=("_min", "_tick"))
        if m.empty:
            return {"ok": False, "message": "no overlap between minute and tick-derived minutes"}

        m["close_diff"] = (m["close_min"] - m["close_tick"]).abs()
        m["high_diff"] = (m["high_min"] - m["high_tick"]).abs()
        m["low_diff"] = (m["low_min"] - m["low_tick"]).abs()

        bad = m[
            (m["close_diff"] > max_close_diff)
            | (m["high_diff"] > max_high_diff)
            | (m["low_diff"] > max_low_diff)
        ].copy()

        out = {
            "ok": True,
            "overlap_minutes": int(len(m)),
            "bad_minutes": int(len(bad)),
            "max_close_diff": float(m["close_diff"].max()),
            "max_high_diff": float(m["high_diff"].max()),
            "max_low_diff": float(m["low_diff"].max()),
        }
        if not bad.empty:
            out["examples"] = bad.sort_values("close_diff", ascending=False).head(10)[
                ["dt", "close_min", "close_tick", "close_diff", "high_diff", "low_diff"]
            ].to_dict(orient="records")
        return out
