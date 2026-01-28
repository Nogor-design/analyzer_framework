# src/ta_foundation/core/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from typing import Dict, Optional

from ta_foundation.core.registry import ParserRegistry, read_header_sample
from ta_foundation.core.model import AnalysisPackage, SummaryBlock
from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.core.derived_metrics import attach_detail_chart_images
from ta_foundation.reports.html.embed import file_to_base64_data_uri  # safe reusable helper
from ta_foundation.core.derived_metrics import compute_and_attach_derived_metrics
from ta_foundation.core.derived_metrics import attach_background_image
from ta_foundation.core.derived_metrics import (
    compute_and_attach_derived_metrics,
    attach_background_image,
    load_default_images,
)



KNOWN_SUFFIXES = ("_Trades.csv", "_Analysis.csv", "_Summery.csv", "_Settings.csv")


import re
from typing import Optional

KNOWN_SUFFIXES = ("_Trades.csv", "_Analysis.csv", "_Summery.csv", "_Settings.csv")
RUN_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

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
    unparsed_files: list[Path]


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


def ingest_folder(
    folder: Path,
    registry: ParserRegistry,
    recursive: bool = False,
    run_id_regex: str | None = None,
    include_run_images: bool = False,
) -> IngestResult:
    if not folder.exists():
        raise FileNotFoundError(folder)

    pattern = "**/*.csv" if recursive else "*.csv"
    files = sorted(folder.glob(pattern))

    packages: dict[str, AnalysisPackage] = {}
    unparsed: list[Path] = []

    # -------------------------
    # 1) Parse files (per-file)
    # -------------------------
    for path in files:
        header = read_header_sample(path)
        parser = registry.find_parser(path, header)
        if parser is None:
            unparsed.append(path)
            continue

        run_id = derive_run_id(path, run_id_regex=run_id_regex)
        art: ParsedArtifact = parser.parse(path, run_id=run_id)

        pkg = packages.get(run_id)
        if pkg is None:
            pkg = AnalysisPackage(
                run_id=run_id,
                metadata={
                    "timezone": "America/Denver",
                    "timestamp_source": "ninjatrader_local_pc_time",
                    "datetime_policy": "localized_on_ingest",
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
            sb = SummaryBlock(**(art.summary or {}))
            pkg.summary = sb
        elif art.kind == "settings":
            pkg.settings = art.df
        else:
            pkg.warnings.append({"code": "UNKNOWN_KIND", "message": f"Unknown kind: {art.kind}"})

    # -----------------------------------------
    # 2) Post-processing (once per ingest call)
    # -----------------------------------------
    # Derived metrics should be computed once all data is present
    compute_and_attach_derived_metrics(packages)

    # Images & defaults should also be applied once per run
    if include_run_images:
        for pkg in packages.values():
            # Ensure derived dict exists
            derived = pkg.metadata.setdefault("derived", {})

            # Run image: store in derived (canonical)
            # Your existing helper probably stores into pkg.assets; we normalize it below.
            _attach_run_image_if_present(pkg, folder)

            # Normalize run image location:
            # If your helper wrote to pkg.assets, mirror into derived
            assets = getattr(pkg, "assets", None) or {}
            if "run_image_uri" in assets and "run_image_uri" not in derived:
                derived["run_image_uri"] = assets["run_image_uri"]
                derived["run_image_source"] = assets.get("run_image_source", "run")

            # Background image: store in derived
            attach_background_image(pkg, folder)

    # Load default images once (if present) and apply only when missing
    default_images = load_default_images(folder)
    for pkg in packages.values():
        derived = pkg.metadata.setdefault("derived", {})

        # Only fill if missing
        if "run_image_uri" not in derived and "default_image_uri" in default_images:
            derived["run_image_uri"] = default_images["default_image_uri"]
            derived["run_image_source"] = "default"

        if "background_image_uri" not in derived and "default_background_uri" in default_images:
            derived["background_image_uri"] = default_images["default_background_uri"]
            derived["background_image_source"] = "default"

        # Optional: mirror back into pkg.assets for older report sections
        if hasattr(pkg, "assets"):
            pkg.assets.setdefault("run_image_uri", derived.get("run_image_uri"))
            pkg.assets.setdefault("background_image_uri", derived.get("background_image_uri"))

    if include_run_images:
        for pkg in packages.values():
            # existing:
            _attach_run_image_if_present(pkg, folder)
            attach_background_image(pkg, folder)

            # NEW:
            attach_detail_chart_images(pkg, folder)

    return IngestResult(packages=packages, unparsed_files=unparsed)
