from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd


IndicatorFn = Callable[[pd.DataFrame, dict], pd.DataFrame]


def normalize_key(s: str) -> str:
    s = (s or "").lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    params: dict

    def cache_key(self) -> str:
        items = sorted((k, self.params[k]) for k in self.params.keys())
        return f"{self.name}|" + "|".join([f"{k}={v}" for k, v in items])


class IndicatorRegistry:
    def __init__(self) -> None:
        self._fns: Dict[str, IndicatorFn] = {}

    def register(self, name: str, fn: IndicatorFn) -> None:
        self._fns[normalize_key(name)] = fn

    def apply(self, bars: pd.DataFrame, specs: List[IndicatorSpec]) -> pd.DataFrame:
        """
        Apply a list of indicators in order. Indicators are pure functions.
        """
        if bars is None or bars.empty or not specs:
            return bars
        out = bars.copy()
        for spec in specs:
            key = normalize_key(spec.name)
            fn = self._fns.get(key)
            if fn is None:
                raise KeyError(f"Indicator not registered: {spec.name}")
            out = fn(out, dict(spec.params or {}))
        return out


# Global default registry instance (importable)
DEFAULT_INDICATORS = IndicatorRegistry()
