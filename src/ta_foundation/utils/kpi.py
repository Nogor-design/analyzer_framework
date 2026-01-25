from __future__ import annotations

import re
from typing import Any


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_kpi_key(key: str) -> str:
    """
    Normalizes KPI keys so that:
      - case differences disappear
      - punctuation is ignored
      - spacing differences disappear

    Examples:
      "Total net profit"     -> "totalnetprofit"
      "Profit factor"        -> "profitfactor"
      "Max. drawdown"        -> "maxdrawdown"
      "Total # of trades"    -> "totaloftrades"
      "% profitable"         -> "profitable"
    """
    if key is None:
        return ""
    s = key.strip().lower()
    s = _NORMALIZE_RE.sub("", s)
    return s


class NormalizedKpiDict(dict):
    """
    Dictionary wrapper that allows tolerant KPI lookup.

    Example:
      k = NormalizedKpiDict({"Total net profit": 10860.0})
      k["total net profit"] -> 10860.0
      k["TotalNetProfit"]   -> 10860.0
    """

    def __init__(self, raw: dict[str, Any] | None = None):
        super().__init__()
        self._raw = raw or {}
        for k, v in self._raw.items():
            nk = normalize_kpi_key(k)
            if nk:
                self[nk] = v

    def get(self, key: str, default: Any = None) -> Any:
        nk = normalize_kpi_key(key)
        return super().get(nk, default)
