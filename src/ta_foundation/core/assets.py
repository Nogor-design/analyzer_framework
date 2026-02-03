from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from ta_foundation.core.pipeline import derive_run_id  # reuse canonical run_id logic


@dataclass(frozen=True)
class RunAssets:
    """
    Non-tabular assets associated with a run_id.
    Store paths only; report layer is responsible for embedding as base64.
    """
    card_png: Optional[Path] = None


def discover_run_assets(folder: Path, recursive: bool = False) -> Dict[str, RunAssets]:
    """
    Discover supported non-CSV assets in a folder and map them to run_id.

    Current supported assets:
      - *_Card.png

    Notes:
      - Uses the same run_id derivation as CSV ingest.
      - If multiple card images exist for one run_id, the first one found wins.
    """
    folder = Path(folder)
    if not folder.exists():
        return {}

    pattern = "**/*_Card.png" if recursive else "*_Card.png"
    hits: Iterable[Path] = folder.glob(pattern)

    out: Dict[str, RunAssets] = {}
    for p in hits:
        if not p.is_file():
            continue

        # Ensure derive_run_id knows how to strip _Card.png; pipeline.py change adds it.
        rid = derive_run_id(p, run_id_regex=None)

        existing = out.get(rid)
        if existing and existing.card_png:
            continue

        out[rid] = RunAssets(card_png=p)

    return out
