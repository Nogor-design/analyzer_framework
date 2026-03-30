from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TemplatePreset:
    name: str
    strategy_type: str
    path: str
    values: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyProfile:
    strategy_id: str
    strategy_dir: str
    source_files: Dict[str, str]
    defaults: Dict[str, Any] = field(default_factory=dict)
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    template_presets: List[TemplatePreset] = field(default_factory=list)
    annotations: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_dir": self.strategy_dir,
            "source_files": dict(self.source_files),
            "defaults": dict(self.defaults),
            "parameters": dict(self.parameters),
            "template_presets": [
                {
                    "name": t.name,
                    "strategy_type": t.strategy_type,
                    "path": t.path,
                    "values": dict(t.values),
                }
                for t in self.template_presets
            ],
            "annotations": dict(self.annotations),
            "warnings": list(self.warnings),
        }
