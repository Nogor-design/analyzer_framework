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
    # -----------------------------------------------------------------------
    # Instrument / bars
    # -----------------------------------------------------------------------
    instrument: Optional[str] = None
    contract: Optional[str] = None
    timeframe: str = "1m"
    timezone: str = "America/Denver"

    # -----------------------------------------------------------------------
    # Mode and anchor source
    # -----------------------------------------------------------------------
    # mode:
    #   "discovery"      — anchors come entirely from YAML; no pkg.trades needed.
    #   "strategy_aware" — anchors are derived per-run from pkg.settings, with
    #                      YAML anchors as fallback; trade-time TP/SL is enabled
    #                      by default when trades are present.
    mode: str = "discovery"

    # anchor_source:
    #   "yaml"     — use the anchors list from report.yaml (always).
    #   "settings" — extract from pkg.settings; fall back to YAML on failure.
    #   "auto"     — extract from pkg.settings when available, else YAML.
    anchor_source: str = "yaml"

    # strategy_family is passed to settings_extractor to select the right
    # extractor (e.g. "PantheonMasterBotV01TesterV2").
    strategy_family: str = ""

    # -----------------------------------------------------------------------
    # Anchors and structural analysis
    # -----------------------------------------------------------------------
    anchors: List[AnchorSpec] = field(default_factory=list)

    cross_mode: str = "close"           # close | touch | hybrid
    exit_mode: str = "close"            # close | touch
    recross_policy: str = "first_return"
    return_band_atr: float = 0.0
    return_band_ticks: float = 0.0
    min_bars_after_entry: int = 1

    descriptive_sample_floor: int = 100
    regime_sample_floor: int = 75

    # -----------------------------------------------------------------------
    # Structural TP/SL (segment-based, existing behaviour)
    # -----------------------------------------------------------------------
    tp_sl_enabled: bool = True
    tp_sl_unit: str = "atr"
    tp_grid: List[float] = field(default_factory=lambda: [0.8, 1.0, 1.3, 1.6, 2.0])
    sl_grid: List[float] = field(default_factory=lambda: [0.6, 0.8, 1.0, 1.2])
    tp_sl_fold_mode: str = "anchored_walk_forward"
    tp_sl_min_train_segments: int = 150
    tp_sl_min_test_segments: int = 50

    # -----------------------------------------------------------------------
    # Trade-time TP/SL (entry-simulation, new)
    # -----------------------------------------------------------------------
    # Enabled automatically when mode=="strategy_aware" and trades are present;
    # can be explicitly set to True in any mode via YAML.
    trade_time_tp_sl_enabled: bool = False

    # ATR parameters for the trade-time simulation.  The ATR timeframe defaults
    # to the same as the main analysis timeframe; override via trade_time_atr_tf.
    trade_time_atr_period: int = 14
    trade_time_atr_tf: str = ""          # empty → use `timeframe`

    # TP/SL grids for the simulation (ATR multiples).
    trade_time_tp_grid: List[float] = field(
        default_factory=lambda: [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    )
    trade_time_sl_grid: List[float] = field(
        default_factory=lambda: [0.3, 0.5, 0.75, 1.0, 1.25]
    )

    # How many bars forward to look from each trade entry.
    trade_time_max_bars_forward: int = 120

    # -----------------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------------

    @classmethod
    def from_options(cls, options: Dict[str, Any]) -> "EngineConfig":
        # --- anchors ---
        anchor_specs = [
            AnchorSpec(
                family=str(a.get("family", "SMA")).upper(),
                length=int(a.get("length", 20)),
                source=str(a.get("source", "close")).lower(),
            )
            for a in (options.get("anchors") or [])
        ]

        # --- structural TP/SL ---
        tp_sl = options.get("tp_sl") or {}
        folds_raw = tp_sl.get("folds")

        folds_mode = "anchored_walk_forward"
        folds_min_train = 150
        folds_min_test = 50
        if isinstance(folds_raw, dict):
            folds_mode = str(folds_raw.get("mode", folds_mode) or folds_mode).lower()
            folds_min_train = int(folds_raw.get("min_train_segments", folds_min_train) or folds_min_train)
            folds_min_test = int(folds_raw.get("min_test_segments", folds_min_test) or folds_min_test)

        # --- trade-time TP/SL ---
        tt = options.get("trade_time_tp_sl") or {}
        mode = str(options.get("mode", "discovery") or "discovery").lower()
        # In strategy_aware mode, trade-time TP/SL defaults to enabled.
        tt_default_enabled = (mode == "strategy_aware")
        tt_enabled = bool(tt.get("enabled", tt_default_enabled))

        return cls(
            instrument=options.get("instrument"),
            contract=options.get("contract"),
            timeframe=str(options.get("timeframe", "1m")),
            timezone=str(options.get("timezone", "America/Denver")),
            # mode / source
            mode=mode,
            anchor_source=str(options.get("anchor_source", "yaml") or "yaml").lower(),
            strategy_family=str(options.get("strategy_family", "") or ""),
            # structural analysis
            anchors=anchor_specs,
            cross_mode=str(options.get("cross_mode", "close")).lower(),
            exit_mode=str(options.get("exit_mode", "close")).lower(),
            recross_policy=str(options.get("recross_policy", "first_return")).lower(),
            return_band_atr=float(options.get("return_band_atr", 0.0) or 0.0),
            return_band_ticks=float(options.get("return_band_ticks", 0.0) or 0.0),
            min_bars_after_entry=int(options.get("min_bars_after_entry", 1) or 1),
            descriptive_sample_floor=int(options.get("descriptive_sample_floor", 100) or 100),
            regime_sample_floor=int(options.get("regime_sample_floor", 75) or 75),
            # structural TP/SL
            tp_sl_enabled=bool(tp_sl.get("enabled", True)),
            tp_sl_unit=str(tp_sl.get("unit", "atr")).lower(),
            tp_grid=[float(x) for x in (tp_sl.get("tp_grid") or [0.8, 1.0, 1.3, 1.6, 2.0])],
            sl_grid=[float(x) for x in (tp_sl.get("sl_grid") or [0.6, 0.8, 1.0, 1.2])],
            tp_sl_fold_mode=folds_mode,
            tp_sl_min_train_segments=folds_min_train,
            tp_sl_min_test_segments=folds_min_test,
            # trade-time TP/SL
            trade_time_tp_sl_enabled=tt_enabled,
            trade_time_atr_period=int(tt.get("atr_period", 14) or 14),
            trade_time_atr_tf=str(tt.get("atr_tf", "") or ""),
            trade_time_tp_grid=[
                float(x) for x in (tt.get("tp_grid") or [0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
            ],
            trade_time_sl_grid=[
                float(x) for x in (tt.get("sl_grid") or [0.3, 0.5, 0.75, 1.0, 1.25])
            ],
            trade_time_max_bars_forward=int(tt.get("max_bars_forward", 120) or 120),
        )
