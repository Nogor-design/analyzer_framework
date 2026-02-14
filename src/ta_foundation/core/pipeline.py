from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

import pandas as pd
from ta_foundation.marketdata.tick_cache import (
    TickCacheConfig,
    is_tick_last_txt,
    is_tick_cache_parquet,
    parse_instrument_contract_from_tick_filename,
    try_load_tick_cache,
    write_tick_cache,
    load_tick_cache_parquet,
    parquet_engine_available,
)

from ta_foundation.core.registry import ParserRegistry, read_header_sample
from ta_foundation.core.model import AnalysisPackage, SummaryBlock
from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.core.derived_metrics import (
    compute_and_attach_derived_metrics,
    attach_background_image,
    load_default_images,
    attach_card_image,
    attach_detail_chart_images,
)
from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.market_time_profile import derive_trade_time_profile_for_package
from ta_foundation.reports.html.embed import file_to_base64_data_uri
from ta_foundation.marketdata.store import MarketDataStore

from ta_foundation.marketdata.tick_cache import (
    TickCacheConfig,
    is_tick_last_txt,
    parse_instrument_contract_from_tick_filename,
    try_load_tick_cache,
    write_tick_cache,
    parquet_engine_available,
)

KNOWN_SUFFIXES = ("_Trades.csv", "_Analysis.csv", "_Summery.csv", "_Settings.csv")
RUN_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _ensure_derived_bucket(pkg) -> dict:
    if pkg.metadata is None:
        pkg.metadata = {}
    if "derived" not in pkg.metadata or pkg.metadata["derived"] is None:
        pkg.metadata["derived"] = {}
    return pkg.metadata["derived"]


def _apply_trade_time_profile(pkg, *, bin_minutes: int = 15) -> None:
    derived = _ensure_derived_bucket(pkg)
    derived["trade_time_profile"] = derive_trade_time_profile_for_package(pkg, bin_minutes=bin_minutes)


def _apply_daily_outcomes(pkg) -> None:
    derived = _ensure_derived_bucket(pkg)
    derived["daily_outcomes"] = derive_daily_outcomes_for_package(pkg)


def derive_run_id(path: Path, run_id_regex: Optional[str] = None) -> str:
    name = path.name

    if run_id_regex:
        m = re.search(run_id_regex, name)
        if m and m.groups():
            candidate = m.group(1).strip()
            if candidate:
                return candidate

    for suf in KNOWN_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]

    return path.stem


@dataclass
class IngestResult:
    packages: dict[str, AnalysisPackage]
    unparsed_files: list[Path] = field(default_factory=list)
    market: Optional[MarketDataStore] = None


def _attach_run_image_if_present(pkg: "AnalysisPackage", folder: Path) -> None:
    for ext in RUN_IMAGE_EXTS:
        candidate = folder / f"{pkg.run_id}{ext}"
        if candidate.exists() and candidate.is_file():
            try:
                pkg.assets["run_image_path"] = str(candidate)
                pkg.assets["run_image_uri"] = file_to_base64_data_uri(candidate)
            except Exception as e:
                pkg.warnings.append({
                    "code": "RUN_IMAGE_EMBED_FAILED",
                    "message": f"Failed to embed run image {candidate.name}: {e}",
                })
            return


def _attach_market_artifact(market: MarketDataStore, art: ParsedArtifact, warnings_sink: list[dict]) -> None:
    """
    Attaches shared artifacts (run_id=None) to the shared MarketDataStore.

    Delegates to MarketDataStore.ingest_artifact(...) so the store owns kind routing.
    """
    try:
        ok = market.ingest_artifact(art)
    except Exception as e:
        warnings_sink.append({
            "code": "MARKET_INGEST_FAILED",
            "message": f"Failed to ingest shared market artifact: {e}",
            "kind": getattr(art, "kind", None),
            "path": str(getattr(art, "source_path", "")),
        })
        return

    if not ok:
        warnings_sink.append({
            "code": "UNKNOWN_SHARED_KIND",
            "message": f"Unhandled shared artifact kind: {art.kind}",
            "path": str(art.source_path),
        })


def ingest_folder(
    folder: Path,
    registry: ParserRegistry,
    recursive: bool = False,
    run_id_regex: str | None = None,
    include_run_images: bool = False,
    market_data_folder: Path | None = None,
    tick_cache_enabled: bool = True,
) -> IngestResult:
    if not folder.exists():
        raise FileNotFoundError(folder)

    packages: dict[str, AnalysisPackage] = {}
    unparsed: list[Path] = []
    market = MarketDataStore()
    market_warnings: list[dict] = []

    # -------------------------
    # 1) Parse run-scoped CSVs
    # -------------------------
    pattern = "**/*.csv" if recursive else "*.csv"
    files = sorted(folder.glob(pattern))

    for path in files:
        header = read_header_sample(path)
        parser = registry.find_parser(path, header)
        if parser is None:
            unparsed.append(path)
            continue

        run_id = derive_run_id(path, run_id_regex=run_id_regex)
        art: ParsedArtifact = parser.parse(path, run_id=run_id)

        # If a parser ever returns run_id=None, treat as shared
        if art.run_id is None:
            _attach_market_artifact(market, art, warnings_sink=market_warnings)
            continue

        pkg = packages.get(run_id)
        if pkg is None:
            pkg = AnalysisPackage(
                run_id=run_id,
                metadata={
                    "timezone": "America/Denver",
                    "timestamp_source": "ninjatrader_local_pc_time",
                    "datetime_policy": "localized_on_ingest",
                    "source_folder": str(path.parent),
                },
            )
            packages[run_id] = pkg

        if art.warnings:
            pkg.warnings.extend(art.warnings)

        if art.kind == "trades":
            pkg.trades = art.df
        elif art.kind == "daily":
            pkg.daily = art.df
        elif art.kind == "summary":
            pkg.summary = SummaryBlock(**(art.summary or {}))
        elif art.kind == "settings":
            pkg.settings = art.df
        else:
            pkg.warnings.append({"code": "UNKNOWN_KIND", "message": f"Unknown kind: {art.kind}"})

    # -------------------------
    # 2) Parse shared market data (*.Last*.txt and tick)
    # -------------------------
    if market_data_folder is not None:
        if not market_data_folder.exists():
            raise FileNotFoundError(market_data_folder)

        tick_cache_cfg = TickCacheConfig(enabled=bool(tick_cache_enabled), cache_dir=None)

        # If parquet engine isn't installed, disable caching *and* surface a warning.
        if tick_cache_cfg.enabled and (not parquet_engine_available()):
            tick_cache_cfg = TickCacheConfig(
                enabled=False,
                cache_dir=tick_cache_cfg.cache_dir,
                target_tz=tick_cache_cfg.target_tz,
            )
            market_warnings.append({
                "code": "PARQUET_ENGINE_MISSING",
                "message": "Tick cache requested but no parquet engine found. Install 'pyarrow' (recommended) or 'fastparquet' to enable .parquet caching.",
                "path": str(market_data_folder),
            })
        # 2a) Ingest tick cache parquet files directly (so cache works even if the source .txt was removed)
        cache_dir = market_data_folder / ".ta_cache"
        if cache_dir.exists() and cache_dir.is_dir():
            for p in sorted(cache_dir.glob("*.parquet")):
                if not is_tick_cache_parquet(p):
                    continue
                df = load_tick_cache_parquet(p, cfg=tick_cache_cfg)
                if df is None or df.empty:
                    continue
                ic = parse_instrument_contract_from_tick_filename(p)
                if ic is None:
                    market_warnings.append({
                        "code": "TICK_CACHE_PARSE_FAILED",
                        "message": f"Could not parse instrument/contract from cache filename: {p.name}",
                        "path": str(p),
                    })
                    continue
                instrument, contract = ic
                art = ParsedArtifact(
                    kind="market_ticks",
                    run_id=None,
                    source_path=p,
                    df=df,
                    summary={"instrument": instrument, "contract": contract, "cache": "parquet_only"},
                    warnings=[],
                )
                _attach_market_artifact(market, art, warnings_sink=market_warnings)

        # IMPORTANT:
        # Your original glob '**/*.Last.txt' misses common NinjaTrader duplicate exports like:
        #   'NQ 03-26 Tick.Last (1).txt'
        # So we scan txt files containing 'last' and let the registry decide.
        txt_candidates = sorted(market_data_folder.glob("**/*.txt"))
        lastish = [p for p in txt_candidates if "last" in p.name.lower()]

        for path in lastish:
            header = read_header_sample(path)
            parser = registry.find_parser(path, header=header)
            if parser is None:
                continue

            # Tick caching shortcut
            if is_tick_last_txt(path):
                cached = try_load_tick_cache(path, market_data_folder=market_data_folder, cfg=tick_cache_cfg)
                if cached is not None and not cached.empty:
                    ic = parse_instrument_contract_from_tick_filename(path)
                    if ic is not None:
                        instrument, contract = ic
                        art = ParsedArtifact(
                            kind="market_ticks",
                            run_id=None,
                            source_path=path,
                            df=cached,
                            summary={"instrument": instrument, "contract": contract, "cache": "parquet"},
                            warnings=[],
                        )
                        _attach_market_artifact(market, art, warnings_sink=market_warnings)
                        continue
                    # If filename parse fails, fall through to parser.parse

            art = parser.parse(path, run_id=None)
            if art.run_id is not None:
                art.run_id = None  # defensive

            _attach_market_artifact(market, art, warnings_sink=market_warnings)

            # Write cache for ticks after successful parse
            if is_tick_last_txt(path) and art.df is not None and not art.df.empty:
                wrote = write_tick_cache(path, art.df, market_data_folder=market_data_folder, cfg=tick_cache_cfg)
                if wrote is not None:
                    market_warnings.append({
                        "code": "TICK_CACHE_WRITTEN",
                        "message": f"Wrote tick parquet cache: {wrote.name}",
                        "path": str(wrote),
                    })
                else:
                    # If caching is enabled but write failed, surface a warning (write_tick_cache swallows exceptions).
                    if tick_cache_cfg.enabled:
                        market_warnings.append({
                            "code": "TICK_CACHE_WRITE_FAILED",
                            "message": "Tick cache write failed (unknown reason). If this persists, verify parquet engine install and filesystem permissions.",
                            "path": str(path),
                        })

    # -----------------------------------------
    # 3) Post-processing (once per ingest call)
    # -----------------------------------------
    compute_and_attach_derived_metrics(packages)

    if include_run_images:
        for pkg in packages.values():
            derived = pkg.metadata.setdefault("derived", {})
            run_folder = Path(pkg.metadata.get("source_folder") or folder)

            _attach_run_image_if_present(pkg, run_folder)

            assets = getattr(pkg, "assets", None) or {}
            if "run_image_uri" in assets and "run_image_uri" not in derived:
                derived["run_image_uri"] = assets["run_image_uri"]
                derived["run_image_source"] = assets.get("run_image_source", "run")

            attach_background_image(pkg, run_folder)
            attach_card_image(pkg, run_folder)

    default_images = load_default_images(folder)
    for pkg in packages.values():
        derived = pkg.metadata.setdefault("derived", {})

        if "run_image_uri" not in derived and "default_image_uri" in default_images:
            derived["run_image_uri"] = default_images["default_image_uri"]
            derived["run_image_source"] = "default"

        if "background_image_uri" not in derived and "default_background_uri" in default_images:
            derived["background_image_uri"] = default_images["default_background_uri"]
            derived["background_image_source"] = "default"

        if hasattr(pkg, "assets"):
            pkg.assets.setdefault("run_image_uri", derived.get("run_image_uri"))
            pkg.assets.setdefault("background_image_uri", derived.get("background_image_uri"))
            pkg.assets.setdefault("card_image_uri", derived.get("card_image_uri"))

    if include_run_images:
        for pkg in packages.values():
            run_folder = Path(pkg.metadata.get("source_folder") or folder)
            attach_detail_chart_images(pkg, run_folder)
            attach_card_image(pkg, run_folder)

    for pkg in packages.values():
        _apply_daily_outcomes(pkg)
        _apply_trade_time_profile(pkg, bin_minutes=15)
        if market_warnings:
            pkg.warnings.extend(market_warnings)

    return IngestResult(
        packages=packages,
        unparsed_files=unparsed,
        market=market if (market.minute_bars or market.ticks) else None,
    )
