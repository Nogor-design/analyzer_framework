from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RegimeFeatureVector:
    timestamp: str
    instrument: str
    contract: str
    feature_values: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "instrument": self.instrument,
            "contract": self.contract,
            "feature_values": dict(self.feature_values),
            "diagnostics": dict(self.diagnostics),
            "warnings": list(self.warnings),
        }


@dataclass
class RegimeClassification:
    regime_id: str
    primary: str
    secondary: List[str] = field(default_factory=list)
    confidence: float = 0.0
    feature_influences: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "regime_id": self.regime_id,
            "primary": self.primary,
            "secondary": list(self.secondary),
            "confidence": float(self.confidence),
            "feature_influences": list(self.feature_influences),
            "diagnostics": dict(self.diagnostics),
        }
