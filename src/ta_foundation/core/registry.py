# src/ta_foundation/core/registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ta_foundation.parsers.base import Parser


@dataclass
class ParserRegistry:
    parsers: list[Parser]

    def find_parser(self, path: Path, header: str) -> Parser | None:
        for p in self.parsers:
            if p.can_parse(path, header):
                return p
        return None


def read_header_sample(path: Path, max_bytes: int = 64_000) -> str:
    with path.open("rb") as f:
        data = f.read(max_bytes)
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode(errors="replace")

