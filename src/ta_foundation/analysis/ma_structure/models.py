from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AnchorSpec:
    family: str
    length: int
    source: str = "close"

    @property
    def anchor_id(self) -> str:
        return f"{self.family.upper()}_{int(self.length)}_{self.source.lower()}"


@dataclass
class EngineConfig:
    instrument: Optional[str] = None
    contract: Optional[str] = None
    timeframe: str = "1m"
    timezone: str = "America/Denver"

    anchors: List[AnchorSpec] = field(default_factory=list)

    cross_mode: str = "close"          # close|touch|hybrid
    exit_mode: str = "close"           # close|touch
    recross_policy: str = "first_return"
    return_band_atr: float = 0.0
    return_band_ticks: float = 0.0
    min_bars_after_entry: int = 1

    descriptive_sample_floor: int = 100
    regime_sample_floor: int = 75

    tp_sl_enabled: bool = True
    tp_sl_unit: str = "atr"
    tp_grid: List[float] = field(default_factory=lambda: [0.8, 1.0, 1.3, 1.6, 2.0])
    sl_grid: List[float] = field(default_factory=lambda: [0.6, 0.8, 1.0, 1.2])
    tp_sl_fold_mode: str = "anchored_walk_forward"
    tp_sl_min_train_segments: int = 150
    tp_sl_min_test_segments: int = 50

    @classmethod
    def from_options(cls, options: Dict[str, Any]) -> "EngineConfig":
        anchor_specs = [
            AnchorSpec(
                family=str(a.get("family", "SMA")).upper(),
                length=int(a.get("length", 20)),
                source=str(a.get("source", "close")).lower(),
            )
            for a in (options.get("anchors") or [])
        ]
        tp_sl = options.get("tp_sl") or {}
        folds = tp_sl.get("folds") or {}

        folds_mode = "anchored_walk_forward"
        folds_min_train = 150
        folds_min_test = 50
        folds_raw = tp_sl.get("folds")
        if isinstance(folds_raw, dict):
            folds_mode = str(folds_raw.get("mode", folds_mode) or folds_mode).lower()
            folds_min_train = int(folds_raw.get("min_train_segments", folds_min_train) or folds_min_train)
            folds_min_test = int(folds_raw.get("min_test_segments", folds_min_test) or folds_min_test)


        return cls(
            instrument=options.get("instrument"),
            contract=options.get("contract"),
            timeframe=str(options.get("timeframe", "1m")),
            timezone=str(options.get("timezone", "America/Denver")),
            anchors=anchor_specs,
            cross_mode=str(options.get("cross_mode", "close")).lower(),
            exit_mode=str(options.get("exit_mode", "close")).lower(),
            recross_policy=str(options.get("recross_policy", "first_return")).lower(),
            return_band_atr=float(options.get("return_band_atr", 0.0) or 0.0),
            return_band_ticks=float(options.get("return_band_ticks", 0.0) or 0.0),
            min_bars_after_entry=int(options.get("min_bars_after_entry", 1) or 1),
            descriptive_sample_floor=int(options.get("descriptive_sample_floor", 100) or 100),
            regime_sample_floor=int(options.get("regime_sample_floor", 75) or 75),
            tp_sl_enabled=bool(tp_sl.get("enabled", True)),
            tp_sl_unit=str(tp_sl.get("unit", "atr")).lower(),
            tp_grid=[float(x) for x in (tp_sl.get("tp_grid") or [0.8, 1.0, 1.3, 1.6, 2.0])],
            sl_grid=[float(x) for x in (tp_sl.get("sl_grid") or [0.6, 0.8, 1.0, 1.2])],
            tp_sl_fold_mode=folds_mode,
            tp_sl_min_train_segments=int(folds.get("min_train_segments", 150) or 150),
            tp_sl_min_test_segments=int(folds.get("min_test_segments", 50) or 50),
        )