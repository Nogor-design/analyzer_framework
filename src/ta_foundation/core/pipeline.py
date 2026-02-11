# src/ta_foundation/core/pipeline.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

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
    """
    Derives run_id from filename.

    If run_id_regex is provided:
      - it must contain at least one capture group
      - the first capture group is used as run_id

    Else:
      - strip known suffixes (default behavior)
    """
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
    """
    If a file named <run_id>.<ext> exists next to the CSVs, embed it as a base64 data URI.
    """
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
            return  # first match wins


def _attach_market_artifact(market: MarketDataStore, art: ParsedArtifact, warnings_sink: list[dict]) -> None:
    """
    Attaches shared artifacts (run_id=None) to the shared MarketDataStore.
    """
    if art.kind == "market_minute_bars":
        instrument = (art.summary or {}).get("instrument")
        contract = (art.summary or {}).get("contract")
        if instrument and contract and art.df is not None:
            market.add_minute_bars(instrument, contract, art.df, art.source_path)
        else:
            warnings_sink.append({
                "code": "MARKET_BARS_INCOMPLETE",
                "message": "market_minute_bars missing instrument/contract/df",
                "path": str(art.source_path),
            })
        return

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
    market_data_folder: Path | None = None,  # ✅ NEW
) -> IngestResult:
    if not folder.exists():
        raise FileNotFoundError(folder)

    packages: dict[str, AnalysisPackage] = {}
    unparsed: list[Path] = []
    market = MarketDataStore()

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
            _attach_market_artifact(market, art, warnings_sink=[])
            continue

        pkg = packages.get(run_id)
        if pkg is None:
            pkg = AnalysisPackage(
                run_id=run_id,
                metadata={
                    "timezone": "America/Denver",
                    "timestamp_source": "ninjatrader_local_pc_time",
                    "datetime_policy": "localized_on_ingest",
                    # recursive-safe source folder
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
    # 2) Parse shared market data (*.Last.txt)
    # -------------------------
    if market_data_folder is not None:
        if not market_data_folder.exists():
            raise FileNotFoundError(market_data_folder)

        for path in sorted(market_data_folder.glob("**/*.Last.txt")):
            # No header; but registry expects (path, header) so just pass empty
            parser = registry.find_parser(path, header="")
            if parser is None:
                continue

            art = parser.parse(path, run_id=None)
            if art.run_id is not None:
                # defensive: ensure shared artifacts stay shared
                art.run_id = None  # type: ignore[attr-defined]
            _attach_market_artifact(market, art, warnings_sink=[])

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

    return IngestResult(
        packages=packages,
        unparsed_files=unparsed,
        market=market if market.minute_bars else None,
    )
