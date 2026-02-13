# src/ta_foundation/core/registry.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ta_foundation.parsers.base import Parser


@dataclass
class ParserRegistry:
    parsers: list[Parser] = field(default_factory=list)

    def register(self, parser: Parser) -> None:
        self.parsers.append(parser)

    def find_parser(self, path: Path, header: str) -> Parser | None:
        for p in self.parsers:
            try:
                if p.can_parse(path, header):
                    return p
            except Exception:
                # Defensive: a bad can_parse should not break ingest
                continue
        return None


def read_header_sample(path: Path, max_bytes: int = 64_000) -> str:
    with path.open("rb") as f:
        data = f.read(max_bytes)
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode(errors="replace")
