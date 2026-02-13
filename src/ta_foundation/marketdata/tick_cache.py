from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class TickCacheConfig:
    enabled: bool = True
    # If None: use market_data_folder / ".ta_cache"
    cache_dir: Optional[Path] = None


def is_tick_last_txt(path: Path) -> bool:
    # Your tick export naming: "NQ 03-26 Tick.Last.txt"
    return path.name.endswith("Tick.Last.txt")


def parse_instrument_contract_from_tick_filename(path: Path) -> Optional[Tuple[str, str]]:
    # "NQ 03-26 Tick.Last.txt" -> instrument="NQ", contract="03-26"
    name = path.name
    if not name.endswith("Tick.Last.txt"):
        return None
    stem = name[:-len("Tick.Last.txt")].strip()  # "NQ 03-26"
    parts = stem.split(" ")
    if len(parts) != 2:
        return None
    instr, contract = parts[0].strip(), parts[1].strip()
    if not instr or len(contract) != 5 or contract[2] != "-":
        return None
    return instr, contract


def cache_path_for_tick_file(path: Path, *, market_data_folder: Path, cfg: TickCacheConfig) -> Path:
    cache_root = cfg.cache_dir if cfg.cache_dir is not None else (market_data_folder / ".ta_cache")
    cache_root.mkdir(parents=True, exist_ok=True)

    # Include original filename + a stable suffix
    # Safer than instrument/contract only because users may keep multiple exports around.
    return cache_root / f"{path.name}.parquet"


def try_load_tick_cache(path: Path, *, market_data_folder: Path, cfg: TickCacheConfig) -> Optional[pd.DataFrame]:
    if not cfg.enabled:
        return None
    cpath = cache_path_for_tick_file(path, market_data_folder=market_data_folder, cfg=cfg)
    if not cpath.exists():
        return None
    try:
        df = pd.read_parquet(cpath)
        # Ensure dt is datetime64[ns, tz] if possible
        if "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
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
        # Parquet write needs pyarrow or fastparquet (we’ll add pyarrow dependency)
        df.to_parquet(cpath, index=False)
        return cpath
    except Exception:
        return None
