from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
import importlib.util

import pandas as pd


@dataclass(frozen=True)
class TickCacheConfig:
    enabled: bool = True
    cache_dir: Optional[Path] = None
    target_tz: str = "America/Denver"


def parquet_engine_available() -> bool:
    return (importlib.util.find_spec("pyarrow") is not None) or (
        importlib.util.find_spec("fastparquet") is not None
    )


def is_tick_last_txt(path: Path) -> bool:
    n = path.name.lower()
    if not n.endswith(".txt"):
        return False
    return ("tick" in n) and ("last" in n)


def is_tick_cache_parquet(path: Path) -> bool:
    """
    Cache files look like: 'NQ 03-26 Tick.Last.txt.parquet'
    """
    n = path.name.lower()
    if not n.endswith(".txt.parquet"):
        return False
    return ("tick" in n) and ("last" in n)


def parse_instrument_contract_from_tick_filename(path: Path) -> Optional[Tuple[str, str]]:
    # Accept both .txt and .txt.parquet as input
    name = path.name
    low = name.lower()
    if not (is_tick_last_txt(path) or is_tick_cache_parquet(path)):
        return None

    # strip trailing extensions
    if low.endswith(".txt.parquet"):
        stem = name[:-len(".txt.parquet")]
    elif low.endswith(".txt"):
        stem = name[:-len(".txt")]
    else:
        stem = path.stem

    # remove common tokens
    stem = stem.replace("Tick.Last", "").replace("tick.last", "")
    stem = stem.replace("Tick Last", "").replace("tick last", "")
    stem = stem.replace("Tick", "").replace("tick", "")
    stem = stem.replace("Last", "").replace("last", "")
    stem = stem.replace(".", " ").replace("_", " ")
    stem = " ".join(stem.split())

    parts = stem.split()
    if not parts:
        return None

    instr = parts[0].strip().upper()
    contract = None
    for p in parts[1:]:
        p = p.strip()
        if len(p) == 5 and p[2] == "-" and p[:2].isdigit() and p[3:].isdigit():
            contract = p
            break

    if not instr or not contract:
        return None
    return instr, contract


def cache_path_for_tick_file(path: Path, *, market_data_folder: Path, cfg: TickCacheConfig) -> Path:
    cache_root = cfg.cache_dir if cfg.cache_dir is not None else (market_data_folder / ".ta_cache")
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root / f"{path.name}.parquet"


def try_load_tick_cache(path: Path, *, market_data_folder: Path, cfg: TickCacheConfig) -> Optional[pd.DataFrame]:
    if not cfg.enabled:
        return None
    cpath = cache_path_for_tick_file(path, market_data_folder=market_data_folder, cfg=cfg)
    if not cpath.exists():
        return None
    try:
        df = pd.read_parquet(cpath)
        if "dt" in df.columns:
            dt = pd.to_datetime(df["dt"], errors="coerce")
            if getattr(dt.dt, "tz", None) is None:
                dt = dt.dt.tz_localize(cfg.target_tz, nonexistent="shift_forward", ambiguous="NaT")
            df["dt"] = dt
        return df
    except Exception:
        return None


def load_tick_cache_parquet(path: Path, *, cfg: TickCacheConfig) -> Optional[pd.DataFrame]:
    """
    Load a cache parquet directly (used when original txt is absent).
    """
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if "dt" in df.columns:
            dt = pd.to_datetime(df["dt"], errors="coerce")
            if getattr(dt.dt, "tz", None) is None:
                dt = dt.dt.tz_localize(cfg.target_tz, nonexistent="shift_forward", ambiguous="NaT")
            df["dt"] = dt
        return df
    except Exception:
        return None


def write_tick_cache(
    path: Path,
    df: pd.DataFrame,
    *,
    market_data_folder: Path,
    cfg: TickCacheConfig,
) -> Optional[Path]:
    if not cfg.enabled:
        return None
    cpath = cache_path_for_tick_file(path, market_data_folder=market_data_folder, cfg=cfg)
    try:
        df.to_parquet(cpath, index=False)
        return cpath
    except Exception:
        return None
