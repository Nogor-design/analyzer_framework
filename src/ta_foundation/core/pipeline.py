# src/ta_foundation/core/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from typing import Dict, Optional

from ta_foundation.core.registry import ParserRegistry, read_header_sample
from ta_foundation.core.model import AnalysisPackage, SummaryBlock
from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.reports.html.embed import file_to_base64_data_uri  # safe reusable helper


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
    include_run_images: bool = False,   # NEW
) -> IngestResult:
    if not folder.exists():
        raise FileNotFoundError(folder)

    pattern = "**/*.csv" if recursive else "*csv"
    files = sorted(folder.glob(pattern))

    packages: dict[str, AnalysisPackage] = {}
    unparsed: list[Path] = []

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

        # NEW: after packages have been assembled, attach run images once per run
        if include_run_images:
            for pkg in packages.values():
                _attach_run_image_if_present(pkg, folder)

    return IngestResult(packages=packages, unparsed_files=unparsed)
