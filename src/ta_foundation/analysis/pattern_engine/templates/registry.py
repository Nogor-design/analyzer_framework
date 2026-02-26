from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Tuple

import pandas as pd


DetectFn = Callable[..., pd.Series]
TemplateKey = Tuple[str, str]  # (family, structure)


@dataclass(frozen=True)
class PatternTemplate:
    family: str
    structure: str
    detect_fn: DetectFn
    requires_ticks: bool = False


class TemplateRegistry:
    def __init__(self) -> None:
        self._by_key: Dict[TemplateKey, PatternTemplate] = {}

    def register(self, t: PatternTemplate) -> None:
        key = (t.family, t.structure)
        self._by_key[key] = t

    def get(self, family: str, structure: str) -> PatternTemplate:
        key = (family, structure)
        if key not in self._by_key:
            avail = sorted([f"{k[0]}::{k[1]}" for k in self._by_key.keys()])
            raise KeyError(f"{family}::{structure} (available templates: {avail})")
        return self._by_key[key]

    def keys(self) -> Iterable[TemplateKey]:
        return self._by_key.keys()