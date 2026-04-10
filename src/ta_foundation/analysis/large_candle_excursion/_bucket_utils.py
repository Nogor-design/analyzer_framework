from __future__ import annotations

"""
Shared bucket assignment utility for context enricher modules.
Isolated here to break the circular import between context_enricher and
signal_candle_context.
"""

from typing import Dict, List


def assign_labeled_bucket(value: float, buckets: List[Dict]) -> str:
    """
    Assign *value* to the first matching bucket label.

    Each bucket dict has optional "min" (inclusive, default -inf) and
    "max" (exclusive, default +inf) plus a required "label".
    Returns "other" if no bucket matches, "unknown" for None/NaN input.
    """
    if value is None or (isinstance(value, float) and value != value):  # NaN guard
        return "unknown"
    for b in buckets:
        lo = float(b.get("min", float("-inf")))
        hi = float(b.get("max", float("inf")))
        if lo <= value < hi:
            return b["label"]
    return "other"
