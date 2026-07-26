"""
PantheonBotV2 parameter ↔ ta_foundation analysis registry.

This module declares how each `[NinjaScriptProperty]` in
`strategies/PantheonBotV2/PantheonBotV2.cs` maps to a concept in the
Python analysis stack — regime classifiers, exit-policy dataclasses,
MA sweep parameters, and session-window optimizers.

Why: insights produced by `filter_discovery` and `market_regime_discovery`
talk about `trend_regime="up"`, `vol_regime="high"`, etc. Translating
those insights into a PantheonBotV2 strategy template was previously a
human-driven lookup vulnerable to silent drift — e.g. the
`UseCloseForVwapPrice` flag does NOT affect the VWAP accumulator (only
the dist_atr comparison), which was discovered the hard way during the
bar_regime parity work in 2026-05-19.

The registry is the single source of truth for those mappings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

ROLES = frozenset(
    {
        "entry",            # selects when a trade enters
        "exit",             # selects how/when a trade exits
        "filter",           # gates entries based on regime/session
        "classifier_input", # tunes the regime classifier itself
        "time",             # session window
        "risk",             # daily risk caps, kill switches
        "core",             # generic infrastructure (contracts, direction)
        "display",          # debug / markers
    }
)


# ---------------------------------------------------------------------------
# Mapping records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnumMapping:
    """An NT enum value ↔ analysis-string-value pair."""
    nt_enum_value: str
    analysis_value: str  # use "*" for "Any" / no constraint


@dataclass(frozen=True)
class ParamMapping:
    nt_property: str
    nt_type: str              # "int" | "float" | "bool" | "enum"
    role: str
    description: str
    analysis_key: Optional[str] = None    # dotted analysis-output path
    enum_values: tuple[EnumMapping, ...] = ()
    cs_source: Optional[str] = None       # file:line range citation
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown role {self.role!r} for {self.nt_property}")
        if self.nt_type == "enum" and not self.enum_values:
            raise ValueError(f"enum property {self.nt_property} must declare enum_values")
        if self.nt_type != "enum" and self.enum_values:
            raise ValueError(f"non-enum property {self.nt_property} must not declare enum_values")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TREND_FILTER = (
    EnumMapping("Up",   "up"),
    EnumMapping("Down", "down"),
    EnumMapping("Flat", "flat"),
    EnumMapping("Any",  "*"),
)
_VWAP_FILTER = (
    EnumMapping("Above", "above"),
    EnumMapping("Below", "below"),
    EnumMapping("Near",  "near"),
    EnumMapping("Any",   "*"),
)
_VOL_FILTER = (
    EnumMapping("Low",  "low"),
    EnumMapping("Mid",  "mid"),
    EnumMapping("High", "high"),
    EnumMapping("Any",  "*"),
)
_SLOPE_MODE = (
    EnumMapping("0", "raw_points"),
    EnumMapping("1", "atr_normalized"),
)


PANTHEON_BOT_V2_PARAMS: tuple[ParamMapping, ...] = (
    # ── MA cross core (entry) ────────────────────────────────────────────
    ParamMapping(
        nt_property="averageFast",
        nt_type="int",
        role="entry",
        description="Fast SMA period for the cross.",
        analysis_key="ma_discovery.signals.ma_cross.period",
        cs_source="PantheonBotV2.cs:268",
    ),
    ParamMapping(
        nt_property="averageSlow",
        nt_type="int",
        role="entry",
        description="Slow SMA period for the cross.",
        analysis_key="ma_discovery.signals.ma_cross.period",
        cs_source="PantheonBotV2.cs:269",
    ),
    ParamMapping(
        nt_property="averageTrend",
        nt_type="int",
        role="entry",
        description="Trend-filter SMA period (long-term direction).",
        analysis_key="ma_discovery.signals.ma_cross.trend_period",
        cs_source="PantheonBotV2.cs:270",
    ),
    ParamMapping(
        nt_property="UseTrend",
        nt_type="bool",
        role="entry",
        description="Require close on trend side of `averageTrend` SMA for entry.",
        cs_source="PantheonBotV2.cs:521",
    ),
    ParamMapping(
        nt_property="UseTrendReverse",
        nt_type="bool",
        role="entry",
        description="Invert the trend filter (counter-trend mode).",
        cs_source="PantheonBotV2.cs:523-535",
    ),
    ParamMapping(
        nt_property="Long",
        nt_type="bool",
        role="core",
        description="Enable long entries.",
    ),
    ParamMapping(
        nt_property="Short",
        nt_type="bool",
        role="core",
        description="Enable short entries.",
    ),
    ParamMapping(
        nt_property="Reverse",
        nt_type="bool",
        role="entry",
        description="Reverse the direction of every signal (long-cross → short, etc.).",
        cs_source="PantheonBotV2.cs:540, 577",
    ),
    ParamMapping(
        nt_property="Contracts",
        nt_type="int",
        role="core",
        description="Position size.",
    ),

    # ── Risk: fixed stop/target (exit) ───────────────────────────────────
    ParamMapping(
        nt_property="MaxStop",
        nt_type="int",
        role="exit",
        description="Hard stop in ticks. Initial stop is entry ± MaxStop * TickSize.",
        analysis_key="exits.FixedStopTargetPolicy.stop_ticks",
        cs_source="PantheonBotV2.cs:259, 781-791",
    ),
    ParamMapping(
        nt_property="UseMaxStop",
        nt_type="bool",
        role="exit",
        description="Enable the hard stop.",
        cs_source="PantheonBotV2.cs:260, 353",
    ),
    ParamMapping(
        nt_property="MaxTPRatio",
        nt_type="float",
        role="exit",
        description="Target ticks = floor(MaxStop * MaxTPRatio).",
        analysis_key="exits.FixedStopTargetPolicy.target_ticks",
        cs_source="PantheonBotV2.cs:261, 351",
    ),
    ParamMapping(
        nt_property="UseMaxTP",
        nt_type="bool",
        role="exit",
        description="Enable the profit target.",
        cs_source="PantheonBotV2.cs:262, 356",
    ),

    # ── Risk: dynamic stop family (exit) ─────────────────────────────────
    ParamMapping(
        nt_property="UseDynamicStop",
        nt_type="bool",
        role="exit",
        description="Master toggle for lock-in/giveback/trail stop management.",
        cs_source="PantheonBotV2.cs:287, 496-506",
    ),
    ParamMapping(
        nt_property="UseLockIn",
        nt_type="bool",
        role="exit",
        description="Move stop to break-even+offset after a trigger MFE.",
        analysis_key="exits.BreakEvenAtrTrailPolicy",
        cs_source="PantheonBotV2.cs:289, 812-821",
    ),
    ParamMapping(
        nt_property="LockInTriggerTicks",
        nt_type="int",
        role="exit",
        description="MFE ticks required to arm the lock-in.",
        analysis_key="exits.BreakEvenAtrTrailPolicy.be_trigger_ticks",
        cs_source="PantheonBotV2.cs:290",
    ),
    ParamMapping(
        nt_property="LockInPlusTicks",
        nt_type="int",
        role="exit",
        description="Ticks above entry to park the stop once locked in.",
        analysis_key="exits.BreakEvenAtrTrailPolicy.be_offset_ticks",
        cs_source="PantheonBotV2.cs:291",
    ),
    ParamMapping(
        nt_property="UseGiveback",
        nt_type="bool",
        role="exit",
        description="Once MFE >= start, trail stop at (best_price - giveback_ticks).",
        analysis_key="exits.GivebackAfterMfePolicy",
        cs_source="PantheonBotV2.cs:293, 823-831",
    ),
    ParamMapping(
        nt_property="GivebackStartTicks",
        nt_type="int",
        role="exit",
        description="MFE ticks required to arm giveback trailing.",
        analysis_key="exits.GivebackAfterMfePolicy.arm_mfe_ticks",
        cs_source="PantheonBotV2.cs:294",
    ),
    ParamMapping(
        nt_property="GivebackTicks",
        nt_type="int",
        role="exit",
        description="Distance below best price to park the trailing stop.",
        analysis_key="exits.GivebackAfterMfePolicy.giveback_ticks",
        cs_source="PantheonBotV2.cs:295",
    ),
    ParamMapping(
        nt_property="UseTrail",
        nt_type="bool",
        role="exit",
        description="Linear trailing stop once MFE >= start.",
        analysis_key="exits.TrailStopTargetPolicy",
        cs_source="PantheonBotV2.cs:297, 833-841",
    ),
    ParamMapping(
        nt_property="TrailStartTicks",
        nt_type="int",
        role="exit",
        description="MFE ticks required to arm the trail.",
        analysis_key="exits.TrailStopTargetPolicy.start_trail_ticks",
        cs_source="PantheonBotV2.cs:298",
    ),
    ParamMapping(
        nt_property="TrailDistanceTicks",
        nt_type="int",
        role="exit",
        description="Distance behind close for the trailing stop.",
        analysis_key="exits.TrailStopTargetPolicy.trail_amount",
        cs_source="PantheonBotV2.cs:299",
    ),

    # ── Time / session ──────────────────────────────────────────────────
    ParamMapping(
        nt_property="UseTimeFilter",
        nt_type="bool",
        role="time",
        description="Enable the daily entry window.",
        cs_source="PantheonBotV2.cs:281, 450",
    ),
    ParamMapping(
        nt_property="StartTimeH",
        nt_type="int",
        role="time",
        description="Entry-window start hour (workstation local time).",
        analysis_key="market_regime.session_window_optimizer.start_hour",
        cs_source="PantheonBotV2.cs:282",
    ),
    ParamMapping(
        nt_property="StartTimeM",
        nt_type="int",
        role="time",
        description="Entry-window start minute.",
        analysis_key="market_regime.session_window_optimizer.start_minute",
        cs_source="PantheonBotV2.cs:283",
    ),
    ParamMapping(
        nt_property="DurationTimeH",
        nt_type="int",
        role="time",
        description="Entry-window duration hours.",
        cs_source="PantheonBotV2.cs:284",
    ),
    ParamMapping(
        nt_property="DurationTimeM",
        nt_type="int",
        role="time",
        description="Entry-window duration minutes.",
        cs_source="PantheonBotV2.cs:285",
    ),

    # ── Regime classifier inputs (the feature recipe) ────────────────────
    ParamMapping(
        nt_property="TrendHigherTimeFrameMinutes",
        nt_type="int",
        role="classifier_input",
        description="HTF resolution for trend-regime classification (5/15/30/60).",
        analysis_key="market_regime.htf_minutes",
        cs_source="PantheonBotV2.cs:302",
    ),
    ParamMapping(
        nt_property="TrendEmaPeriod",
        nt_type="int",
        role="classifier_input",
        description="EMA period on the HTF series used for slope.",
        analysis_key="market_regime.trend_ema_period",
        cs_source="PantheonBotV2.cs:303",
    ),
    ParamMapping(
        nt_property="TrendSlopeLookbackBars",
        nt_type="int",
        role="classifier_input",
        description="HTF bars between EMA samples when computing slope.",
        cs_source="PantheonBotV2.cs:304, 610",
    ),
    ParamMapping(
        nt_property="TrendAtrPeriod",
        nt_type="int",
        role="classifier_input",
        description="ATR period on HTF used to normalise slope (when slope mode = ATR).",
        cs_source="PantheonBotV2.cs:305",
    ),
    ParamMapping(
        nt_property="TrendFlatThreshold",
        nt_type="float",
        role="classifier_input",
        description="Slope magnitude that separates flat from up/down.",
        analysis_key="market_regime.trend_flat_threshold",
        cs_source="PantheonBotV2.cs:306, 627",
    ),
    ParamMapping(
        nt_property="TrendSlopeModeInt",
        nt_type="enum",
        role="classifier_input",
        description="0 = raw point slope, 1 = ATR-normalized.",
        enum_values=_SLOPE_MODE,
        cs_source="PantheonBotV2.cs:307, 615-620",
    ),
    ParamMapping(
        nt_property="VwapAtrPeriod",
        nt_type="int",
        role="classifier_input",
        description="ATR period on the primary series for VWAP distance.",
        cs_source="PantheonBotV2.cs:309",
    ),
    ParamMapping(
        nt_property="VwapNearThresholdAtr",
        nt_type="float",
        role="classifier_input",
        description="distATR magnitude below which VWAP regime is 'near'.",
        analysis_key="market_regime.vwap_near_threshold_atr",
        cs_source="PantheonBotV2.cs:310",
    ),
    ParamMapping(
        nt_property="UseCloseForVwapPrice",
        nt_type="bool",
        role="classifier_input",
        description=(
            "When true, dist_atr = (close - vwap)/atr; when false, uses typical "
            "price (H+L+C)/3."
        ),
        cs_source="PantheonBotV2.cs:311, 653",
        notes=(
            "GOTCHA: this flag ONLY affects the dist_atr comparison in "
            "UpdateVwapRegimeFromPrimary. The VWAP accumulator in "
            "UpdateSessionVwap ALWAYS uses typical price regardless of this "
            "setting. Discovered 2026-05-19 during bar_regime parity work."
        ),
    ),
    ParamMapping(
        nt_property="VolatilityAtrPeriod",
        nt_type="int",
        role="classifier_input",
        description="ATR period for the volatility-percentile bucket.",
        cs_source="PantheonBotV2.cs:313",
    ),
    ParamMapping(
        nt_property="VolatilityLookbackWindow",
        nt_type="int",
        role="classifier_input",
        description="Rolling-window size for ATR percentile rank.",
        cs_source="PantheonBotV2.cs:314",
    ),
    ParamMapping(
        nt_property="VolatilityLowPercentile",
        nt_type="float",
        role="classifier_input",
        description="ATR percentile rank ≤ this threshold ⇒ vol_regime = 'low'.",
        cs_source="PantheonBotV2.cs:315",
    ),
    ParamMapping(
        nt_property="VolatilityHighPercentile",
        nt_type="float",
        role="classifier_input",
        description="ATR percentile rank ≥ this threshold ⇒ vol_regime = 'high'.",
        cs_source="PantheonBotV2.cs:316",
    ),

    # ── Regime filters (the actual operator-tunable knobs) ───────────────
    ParamMapping(
        nt_property="RequiredTrendRegimeFilter",
        nt_type="enum",
        role="filter",
        description="Only allow entries when trend regime matches this filter.",
        analysis_key="market_regime.trend_regime",
        enum_values=_TREND_FILTER,
        cs_source="PantheonBotV2.cs:318, 686-694",
    ),
    ParamMapping(
        nt_property="RequiredVwapRegimeFilter",
        nt_type="enum",
        role="filter",
        description="Only allow entries when VWAP regime matches this filter.",
        analysis_key="market_regime.vwap_regime",
        enum_values=_VWAP_FILTER,
        cs_source="PantheonBotV2.cs:319, 697-705",
    ),
    ParamMapping(
        nt_property="RequiredVolatilityRegimeFilter",
        nt_type="enum",
        role="filter",
        description="Only allow entries when volatility regime matches this filter.",
        analysis_key="market_regime.vol_regime",
        enum_values=_VOL_FILTER,
        cs_source="PantheonBotV2.cs:320, 708-716",
    ),
    ParamMapping(
        nt_property="BlockedVolatilityRegimeFilter",
        nt_type="enum",
        role="filter",
        description="Block entries when volatility regime matches this filter.",
        analysis_key="market_regime.vol_regime",
        enum_values=_VOL_FILTER,
        cs_source="PantheonBotV2.cs:321, 719-727",
    ),

    # ── Risk: daily caps and kill switch ─────────────────────────────────
    ParamMapping(
        nt_property="ProfitStop",
        nt_type="float",
        role="risk",
        description="Stop trading once cumulative session profit ≥ this USD.",
        cs_source="PantheonBotV2.cs:330, 440",
    ),
    ParamMapping(
        nt_property="LossStop",
        nt_type="float",
        role="risk",
        description="Stop trading once cumulative session loss ≥ this USD.",
        cs_source="PantheonBotV2.cs:331, 441",
    ),
    ParamMapping(
        nt_property="MaxTrades",
        nt_type="int",
        role="risk",
        description="Cap entries per session.",
        cs_source="PantheonBotV2.cs:332, 442",
    ),
    ParamMapping(
        nt_property="UseKill",
        nt_type="bool",
        role="risk",
        description="Enable the legacy kill-switch.",
        cs_source="PantheonBotV2.cs:326",
    ),
    ParamMapping(
        nt_property="KillProfitStop",
        nt_type="float",
        role="risk",
        description="Per-trade unrealized-profit ceiling (kill switch).",
        cs_source="PantheonBotV2.cs:327",
    ),
    ParamMapping(
        nt_property="KillLossStop",
        nt_type="float",
        role="risk",
        description="Per-trade unrealized-loss floor (kill switch).",
        cs_source="PantheonBotV2.cs:328",
    ),

    # ── Display / debug ──────────────────────────────────────────────────
    ParamMapping(
        nt_property="EnableDebugPrint",
        nt_type="bool",
        role="display",
        description=(
            "Emit one line per primary bar with regime labels and intermediate "
            "values used by ta_foundation bar_regime parity tests."
        ),
        cs_source="PantheonBotV2.cs:323, 416-432",
    ),
    ParamMapping(
        nt_property="EnableTradeMarkers",
        nt_type="bool",
        role="display",
        description="Draw vertical lines on the chart at each trade entry.",
        cs_source="PantheonBotV2.cs:324",
    ),
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

_BY_PROPERTY: dict[str, ParamMapping] = {p.nt_property: p for p in PANTHEON_BOT_V2_PARAMS}


def get_param(nt_property: str) -> ParamMapping:
    return _BY_PROPERTY[nt_property]


def params_by_role(role: str) -> tuple[ParamMapping, ...]:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; valid: {sorted(ROLES)}")
    return tuple(p for p in PANTHEON_BOT_V2_PARAMS if p.role == role)


def nt_enum_to_analysis_value(nt_property: str, nt_enum_value: str) -> str:
    param = get_param(nt_property)
    if param.nt_type != "enum":
        raise ValueError(f"{nt_property} is not an enum property")
    for em in param.enum_values:
        if em.nt_enum_value == nt_enum_value:
            return em.analysis_value
    raise KeyError(
        f"No enum value {nt_enum_value!r} for {nt_property}; "
        f"valid: {[em.nt_enum_value for em in param.enum_values]}"
    )


def analysis_value_to_nt_enum(nt_property: str, analysis_value: str) -> str:
    param = get_param(nt_property)
    if param.nt_type != "enum":
        raise ValueError(f"{nt_property} is not an enum property")
    for em in param.enum_values:
        if em.analysis_value == analysis_value:
            return em.nt_enum_value
    raise KeyError(
        f"No analysis value {analysis_value!r} for {nt_property}; "
        f"valid: {[em.analysis_value for em in param.enum_values]}"
    )


def registered_properties() -> tuple[str, ...]:
    return tuple(p.nt_property for p in PANTHEON_BOT_V2_PARAMS)
