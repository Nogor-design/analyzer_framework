# src/ta_foundation/core/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


from ta_foundation.core.registry import ParserRegistry, read_header_sample
from ta_foundation.core.model import AnalysisPackage, SummaryBlock
from ta_foundation.parsers.base import ParsedArtifact


KNOWN_SUFFIXES = ("_Trades.csv", "_Analysis.csv", "_Summery.csv", "_Summary.csv")


import re
from typing import Optional

KNOWN_SUFFIXES = ("_Trades.csv", "_Analysis.csv", "_Summery.csv", "_Summary.csv")


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




def ingest_folder(
    folder: Path,
    registry: ParserRegistry,
    recursive: bool = False,
    run_id_regex: str | None = None,
) -> IngestResult:
    if not folder.exists():
        raise FileNotFoundError(folder)

    pattern = "**/*{.csv,.xml}" if recursive else "*{.csv,.xml}"
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
        elif art.kind == "strategy_xml":
            # artifact.payload is dict from XML parser
            pkg.metadata["strategy_config"] = art.df
        else:
            pkg.warnings.append({"code": "UNKNOWN_KIND", "message": f"Unknown kind: {art.kind}"})

    return IngestResult(packages=packages, unparsed_files=unparsed)
