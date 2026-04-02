from __future__ import annotations

"""
PantheonBotV2 NinjaTrader Template Generator
==============================================
Generates a NinjaTrader 8 StrategyTemplate XML for PantheonBotV2, filling in
discovered parameters from the Strategy Discovery analysis results.

Key parameters derived from discovery data:
  MaxStop          — signal exit sweep overall_best.stop (or MAE p75 winners)
  MaxTPRatio       — target / MaxStop from exit sweep (so TP = MaxStop × ratio)
  Long / Short     — direction evaluation (PF-based, ≥ 10 trades)
  RequiredTrendRegimeFilter — Up / Down / Any based on direction enabled
  BlockedVolatilityRegimeFilter — High if high_vol_expansion PF < 1.0
  LockInTriggerTicks  — MFE winners p25 (protect early profits)
  GivebackStartTicks  — MFE winners p50 (arm giveback near median move)
  UseTimeFilter + start/duration — from best session window
  ProfitStop / LossStop  — from daily risk analysis

Entry point: generate_pantheon_v2_template(sd, run_id, options) -> GeneratedTemplate
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from .nt_template_generator import (
    GeneratedTemplate,
    _safe_float,
    _extract_direction_filters,
    _extract_daily_risk,
)


# ---------------------------------------------------------------------------
# PantheonBotV2-specific helpers
# ---------------------------------------------------------------------------

def _v2_stop_target(sd: Dict[str, Any], warnings: List[str]) -> Tuple[int, float, str, str]:
    """
    Extract MaxStop (ticks) and MaxTPRatio from (priority order):
      1. Signal exit sweep overall_best
      2. Trade MAE/MFE profile (mae_winners_p75 -> stop, mfe_all_p50 × ratio -> target)
      3. Hard-coded defaults (60, 1.5) with warning

    Returns (max_stop_ticks, max_tp_ratio, stop_reasoning, tp_reasoning).
    """
    # -- Priority 1: signal exit sweep --
    ses = sd.get("signal_exit_sweep") or {}
    ob = ses.get("overall_best") or {}
    sweep_stop   = ob.get("stop")
    sweep_target = ob.get("target")

    if sweep_stop is not None and sweep_target is not None:
        stop_t = int(sweep_stop)
        tgt_t  = int(sweep_target)
        ratio  = round(tgt_t / max(stop_t, 1), 2)
        n_v    = ob.get("n_rules_voting", 0)
        wr     = ob.get("avg_win_rate")
        pf     = ob.get("avg_profit_factor")
        detail = f"n_rules={n_v}"
        if wr is not None:
            detail += f", avg_WR={wr:.1%}"
        if pf is not None:
            detail += f", avg_PF={pf:.2f}"
        return (
            stop_t,
            ratio,
            f"Signal exit sweep best stop={stop_t}t ({detail})",
            f"Signal exit sweep best target={tgt_t}t -> MaxTPRatio={ratio} ({detail})",
        )

    # -- Priority 2: trade MAE/MFE profile --
    mf   = sd.get("mae_mfe_profile") or {}
    dist = mf.get("distributions") or {}
    tick_value = 5.0  # will be overridden by caller if available

    # Use raw tick values if dollar values not useful here
    mae_usd = _safe_float(dist.get("mae_winners_p75"))
    mfe_usd = _safe_float(dist.get("mfe_all_p50"))

    if mae_usd is not None and mfe_usd is not None and mae_usd > 0 and mfe_usd > 0:
        stop_t = max(4, round(mae_usd / tick_value))
        tgt_t  = max(4, round(mfe_usd / tick_value))
        ratio  = round(tgt_t / max(stop_t, 1), 2)
        return (
            stop_t,
            ratio,
            f"MAE winners p75=${mae_usd:.0f} -> {stop_t}t stop",
            f"MFE all p50=${mfe_usd:.0f} -> {tgt_t}t target -> MaxTPRatio={ratio}",
        )

    # -- Priority 3: hard-coded defaults --
    warnings.append("No exit sweep or MAE/MFE data — MaxStop=60, MaxTPRatio=1.5 (defaults)")
    return 60, 1.5, "Default (no data)", "Default (no data)"


def _v2_lock_giveback(
    sd: Dict[str, Any],
    max_stop: int,
    max_tp_ratio: float,
    warnings: List[str],
) -> Tuple[int, int, int, str]:
    """
    Extract LockInTriggerTicks, GivebackStartTicks, GivebackTicks from MFE data.

    Strategy:
      LockInTriggerTicks  ≈ mfe_winners_p25 (protect when 25% of winners in profit)
      GivebackStartTicks  ≈ mfe_winners_p50 (arm giveback near median winner move)
      GivebackTicks       ≈ max(12, max_stop // 4)   (small trailing offset)

    Falls back to fractions of MaxStop × MaxTPRatio.

    Returns (lock_in_ticks, giveback_start_ticks, giveback_ticks, reasoning).
    """
    mf   = sd.get("mae_mfe_profile") or {}
    dist = mf.get("distributions") or {}

    # MFE in tick units (may be stored as USD)
    tick_value = 5.0
    mfe_p25_usd = _safe_float(dist.get("mfe_winners_p25"))
    mfe_p50_usd = _safe_float(dist.get("mfe_winners_p50")) or _safe_float(dist.get("mfe_all_p50"))

    target_ticks = round(max_stop * max_tp_ratio)

    if mfe_p25_usd is not None and mfe_p50_usd is not None and mfe_p25_usd > 0:
        lock_in_t  = max(8, round(mfe_p25_usd / tick_value))
        giveback_s = max(lock_in_t + 4, round(mfe_p50_usd / tick_value))
        giveback_t = max(12, max_stop // 4)
        return (
            lock_in_t,
            giveback_s,
            giveback_t,
            f"LockIn={lock_in_t}t (MFE p25=${mfe_p25_usd:.0f}), "
            f"GivebackStart={giveback_s}t (MFE p50=${mfe_p50_usd:.0f}), "
            f"GivebackTicks={giveback_t}t",
        )

    # Signal corpus fallback
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    corpus_mfe = None
    for r in top_rules[:3]:
        v = _safe_float(r.get("mfe_p50"))
        if v and v > 0:
            corpus_mfe = v
            break

    if corpus_mfe is not None:
        lock_in_t  = max(8, round(corpus_mfe * 0.4))
        giveback_s = max(lock_in_t + 4, round(corpus_mfe * 0.7))
        giveback_t = max(12, max_stop // 4)
        return (
            lock_in_t,
            giveback_s,
            giveback_t,
            f"LockIn={lock_in_t}t (corpus MFE p50={corpus_mfe:.0f}t×0.4), "
            f"GivebackStart={giveback_s}t (×0.7), GivebackTicks={giveback_t}t",
        )

    # Hard defaults: fraction of target
    lock_in_t  = max(8,  round(target_ticks * 0.35))
    giveback_s = max(16, round(target_ticks * 0.65))
    giveback_t = max(12, max_stop // 4)
    warnings.append(
        f"No MFE data for lock-in/giveback — using fractions of target ({target_ticks}t)"
    )
    return (
        lock_in_t,
        giveback_s,
        giveback_t,
        f"Default fractions of target={target_ticks}t: "
        f"LockIn={lock_in_t}t (35%), GivebackStart={giveback_s}t (65%)",
    )


def _v2_time_filter(sd: Dict[str, Any], warnings: List[str]) -> Tuple[int, int, int, int, str]:
    """
    Extract time filter window from session evaluation.

    Returns (start_h, start_m, dur_h, dur_m, reasoning).
    Session hours are in America/Denver (MT) — adjust for NinjaTrader timezone.
    """
    ev = sd.get("evaluation") or {}
    by_session = ev.get("by_session") or {}

    # Find RTH sessions and their best time window
    # us_open typically ~07:30–08:00 MT, us_morning ~08:00–10:00 MT
    best_session = None
    best_pf = 0.0
    for label, data in by_session.items():
        pf = _safe_float((data or {}).get("profit_factor")) or 0.0
        n  = int((data or {}).get("n_trades") or 0)
        if n >= 5 and pf > best_pf:
            best_pf = pf
            best_session = label

    # Map session label -> NinjaTrader time window (in chart timezone, default ET)
    # These map Mountain Time sessions to Eastern Time equivalents
    SESSION_WINDOWS = {
        "us_open":      (9, 30, 1, 0),    # 09:30–10:30 ET
        "us_morning":   (9, 30, 2, 0),    # 09:30–11:30 ET
        "us_midday":    (11, 30, 1, 30),  # 11:30–13:00 ET
        "us_afternoon": (13, 0, 2, 0),    # 13:00–15:00 ET
        "pre_open":     (8, 0, 1, 30),    # 08:00–09:30 ET (pre-market)
    }

    if best_session and best_session in SESSION_WINDOWS:
        sh, sm, dh, dm = SESSION_WINDOWS[best_session]
        return (
            sh, sm, dh, dm,
            f"Best session={best_session} (PF={best_pf:.2f}) -> "
            f"{sh:02d}:{sm:02d}–{sh + dh:02d}:{sm + dm:02d} ET",
        )

    # Default: full RTH morning
    warnings.append("No session data — defaulting to 09:30–12:00 ET time filter")
    return 9, 30, 2, 30, "Default RTH morning window (09:30–12:00 ET)"


def _v2_regime_filters(
    sd: Dict[str, Any],
    allow_long: bool,
    allow_short: bool,
    warnings: List[str],
) -> Tuple[str, str, str, str, str]:
    """
    Extract regime filter enums for PantheonBotV2.

    Returns (trend_filter, vwap_filter, vol_filter_required, vol_filter_blocked, reasoning).
    Enum values must match the C# enum: Up/Down/Flat/Any, Below/Near/Above/Any, Low/Mid/High/Any.
    """
    # Trend filter from direction
    if allow_long and not allow_short:
        trend_filter = "Up"
        trend_reason = "Long-only -> TrendRegimeFilter=Up"
    elif allow_short and not allow_long:
        trend_filter = "Down"
        trend_reason = "Short-only -> TrendRegimeFilter=Down"
    else:
        trend_filter = "Any"
        trend_reason = "Both directions -> TrendRegimeFilter=Any"

    # VWAP filter — default Near (no VWAP regime analysis available)
    vwap_filter  = "Near"
    vwap_reason  = "Default Near (no VWAP regime analysis)"

    # Volatility filter — block High if high_vol has poor PF
    ev = sd.get("evaluation") or {}
    by_regime = ev.get("by_regime") or {}
    hv = by_regime.get("high_vol_expansion") or {}
    hv_pf = _safe_float(hv.get("profit_factor"))
    hv_n  = int(hv.get("n_trades") or 0)

    if hv_pf is not None and hv_pf < 1.0 and hv_n >= 10:
        vol_req = "Any"
        vol_blk = "High"
        vol_reason = (
            f"high_vol_expansion PF={hv_pf:.2f} (n={hv_n}) < 1.0 -> BlockedVol=High"
        )
    else:
        vol_req = "Any"
        vol_blk = "Any"
        hv_pf_str = f"{hv_pf:.2f}" if hv_pf is not None else "?"
        vol_reason = (
            f"high_vol PF={hv_pf_str} (n={hv_n}) — no vol block"
            if hv_pf is not None
            else "No vol regime data — BlockedVol=Any"
        )

    reasoning = f"{trend_reason}; VWAP={vwap_reason}; Vol={vol_reason}"
    return trend_filter, vwap_filter, vol_req, vol_blk, reasoning


# ---------------------------------------------------------------------------
# XML parameter builders
# ---------------------------------------------------------------------------

_ASSEMBLY = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
_STRATEGY_ASSEMBLY = "261b4b4062574da2ac23d883f2058f5a, Version=8.1.7.0, Culture=neutral, PublicKeyToken=null"


def _p_int(name: str, value: int) -> str:
    v = str(int(value))
    return (
        f"      <Parameter>\n"
        f"        <EnumValuesSerializable />\n"
        f"        <Increment>1</Increment>\n"
        f"        <Max xsi:type=\"xsd:int\">{v}</Max>\n"
        f"        <Min xsi:type=\"xsd:int\">{v}</Min>\n"
        f"        <Name>{name}</Name>\n"
        f"        <ParameterTypeSerializable>System.Int32, {_ASSEMBLY}</ParameterTypeSerializable>\n"
        f"        <ValueSerializable>{v}</ValueSerializable>\n"
        f"      </Parameter>"
    )


def _p_double(name: str, value: float, decimals: int = 4) -> str:
    # Format without unnecessary trailing zeros
    v = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if "." not in v:
        v = v  # integers stored as double still serialized without decimal in NT
    return (
        f"      <Parameter>\n"
        f"        <EnumValuesSerializable />\n"
        f"        <Increment>1</Increment>\n"
        f"        <Max xsi:type=\"xsd:double\">{v}</Max>\n"
        f"        <Min xsi:type=\"xsd:double\">{v}</Min>\n"
        f"        <Name>{name}</Name>\n"
        f"        <ParameterTypeSerializable>System.Double, {_ASSEMBLY}</ParameterTypeSerializable>\n"
        f"        <ValueSerializable>{v}</ValueSerializable>\n"
        f"      </Parameter>"
    )


def _p_bool(name: str, value: bool) -> str:
    v = "true" if value else "false"
    V = "True" if value else "False"
    return (
        f"      <Parameter>\n"
        f"        <EnumValuesSerializable />\n"
        f"        <Increment>1</Increment>\n"
        f"        <Max xsi:type=\"xsd:boolean\">{v}</Max>\n"
        f"        <Min xsi:type=\"xsd:boolean\">{v}</Min>\n"
        f"        <Name>{name}</Name>\n"
        f"        <ParameterTypeSerializable>System.Boolean, {_ASSEMBLY}</ParameterTypeSerializable>\n"
        f"        <ValueSerializable>{V}</ValueSerializable>\n"
        f"      </Parameter>"
    )


def _p_enum(name: str, value: str, enum_type: str) -> str:
    return (
        f"      <Parameter>\n"
        f"        <EnumValuesSerializable>\n"
        f"          <string>{value}</string>\n"
        f"        </EnumValuesSerializable>\n"
        f"        <Increment>1</Increment>\n"
        f"        <Max xsi:type=\"xsd:int\">0</Max>\n"
        f"        <Min xsi:type=\"xsd:int\">0</Min>\n"
        f"        <Name>{name}</Name>\n"
        f"        <ParameterTypeSerializable>NinjaTrader.NinjaScript.Strategies.{enum_type}, {_STRATEGY_ASSEMBLY}</ParameterTypeSerializable>\n"
        f"        <ValueSerializable>{value}</ValueSerializable>\n"
        f"      </Parameter>"
    )


# ---------------------------------------------------------------------------
# Main XML builder
# ---------------------------------------------------------------------------

def _build_v2_xml(p: Dict[str, Any], run_id: str, reasoning: Dict[str, str]) -> str:
    """Build full PantheonBotV2 StrategyTemplate XML from parameters dict."""

    notes_lines = ["      <!-- Auto-generated by ta_foundation Strategy Discovery"]
    notes_lines.append(f"           Source run: {run_id}")
    for key, reason in reasoning.items():
        notes_lines.append(f"           {key}: {str(reason)[:120]}")
    notes_lines.append("      -->")
    notes_block = "\n".join(notes_lines)

    # Strategy block direct values
    def b(tag: str, val: Any) -> str:
        if isinstance(val, bool):
            return f"      <{tag}>{'true' if val else 'false'}</{tag}>"
        return f"      <{tag}>{val}</{tag}>"

    max_tp_ratio_str = f"{p['MaxTPRatio']:.4f}".rstrip("0").rstrip(".")

    opt_params = "\n".join([
        _p_int("TrendHigherTimeFrameMinutes", p["TrendHigherTimeFrameMinutes"]),
        _p_int("TrendEmaPeriod",              p["TrendEmaPeriod"]),
        _p_int("TrendSlopeLookbackBars",      p["TrendSlopeLookbackBars"]),
        _p_int("TrendAtrPeriod",              p["TrendAtrPeriod"]),
        _p_double("TrendFlatThreshold",       p["TrendFlatThreshold"]),
        _p_int("TrendSlopeModeInt",           p["TrendSlopeModeInt"]),
        _p_int("VwapAtrPeriod",               p["VwapAtrPeriod"]),
        _p_double("VwapNearThresholdAtr",     p["VwapNearThresholdAtr"]),
        _p_bool("UseCloseForVwapPrice",       p["UseCloseForVwapPrice"]),
        _p_int("VolatilityAtrPeriod",         p["VolatilityAtrPeriod"]),
        _p_int("VolatilityLookbackWindow",    p["VolatilityLookbackWindow"]),
        _p_double("VolatilityLowPercentile",  p["VolatilityLowPercentile"]),
        _p_double("VolatilityHighPercentile", p["VolatilityHighPercentile"]),
        _p_enum("RequiredTrendRegimeFilter",       p["RequiredTrendRegimeFilter"],       "TrendRegimeFilter"),
        _p_enum("RequiredVwapRegimeFilter",        p["RequiredVwapRegimeFilter"],        "VwapRegimeFilter"),
        _p_enum("RequiredVolatilityRegimeFilter",  p["RequiredVolatilityRegimeFilter"],  "VolatilityRegimeFilter"),
        _p_enum("BlockedVolatilityRegimeFilter",   p["BlockedVolatilityRegimeFilter"],   "VolatilityRegimeFilter"),
        _p_bool("EnableDebugPrint",           p["EnableDebugPrint"]),
        _p_bool("EnableTradeMarkers",         p["EnableTradeMarkers"]),
        _p_bool("UseDynamicStop",             p["UseDynamicStop"]),
        _p_bool("UseLockIn",                  p["UseLockIn"]),
        _p_int("LockInTriggerTicks",          p["LockInTriggerTicks"]),
        _p_int("LockInPlusTicks",             p["LockInPlusTicks"]),
        _p_bool("UseGiveback",                p["UseGiveback"]),
        _p_int("GivebackStartTicks",          p["GivebackStartTicks"]),
        _p_int("GivebackTicks",               p["GivebackTicks"]),
        _p_bool("UseTrail",                   p["UseTrail"]),
        _p_int("TrailStartTicks",             p["TrailStartTicks"]),
        _p_int("TrailDistanceTicks",          p["TrailDistanceTicks"]),
        _p_bool("UseTimeFilter",              p["UseTimeFilter"]),
        _p_int("StartTimeH",                  p["StartTimeH"]),
        _p_int("StartTimeM",                  p["StartTimeM"]),
        _p_int("DurationTimeH",               p["DurationTimeH"]),
        _p_int("DurationTimeM",               p["DurationTimeM"]),
        _p_int("averageFast",                 p["averageFast"]),
        _p_int("averageSlow",                 p["averageSlow"]),
        _p_int("averageTrend",                p["averageTrend"]),
        _p_bool("UseTrend",                   p["UseTrend"]),
        _p_bool("UseTrendReverse",            p["UseTrendReverse"]),
        _p_double("ProfitStop",               p["ProfitStop"], 0),
        _p_double("LossStop",                 p["LossStop"], 0),
        _p_double("MaxTrades",                p["MaxTrades"], 0),
        _p_int("Contracts",                   p["Contracts"]),
        _p_int("MaxStop",                     p["MaxStop"]),
        _p_bool("UseMaxStop",                 p["UseMaxStop"]),
        _p_double("MaxTPRatio",               p["MaxTPRatio"]),
        _p_bool("UseMaxTP",                   p["UseMaxTP"]),
        _p_bool("Long",                       p["Long"]),
        _p_bool("Short",                      p["Short"]),
        _p_bool("Reverse",                    p["Reverse"]),
        _p_bool("UseKill",                    p["UseKill"]),
        _p_double("KillProfitStop",           p["KillProfitStop"], 0),
        _p_double("KillLossStop",             p["KillLossStop"], 0),
    ])

    return f"""<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonBotV2</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <ParameterWrapper>
        <DisplayName>IsStrategyGenerator</DisplayName>
        <Name>IsStrategyGenerator</Name>
        <Value xsi:type="xsd:boolean">false</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Keep best # results</DisplayName>
        <Name>KeepBestResults</Name>
        <Value xsi:type="xsd:int">10</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>LogTypeName</DisplayName>
        <Name>LogTypeName</Name>
        <Value xsi:type="xsd:string">Optimizer</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Visible</DisplayName>
        <Name>IsVisible</Name>
        <Value xsi:type="xsd:boolean">true</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Name</DisplayName>
        <Name>Name</Name>
        <Value xsi:type="xsd:string">Default</Value>
      </ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
{notes_block}
{opt_params}
    </ArrayOfParameter>
  </OptimizationParameters>
  <Strategy>
    <PantheonBotV2 xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <IsVisible>true</IsVisible>
      <calculate2>OnBarClose</calculate2>
      <AreLinesConfigurable>true</AreLinesConfigurable>
      <ArePlotsConfigurable>true</ArePlotsConfigurable>
      <BarsPeriodSerializable>
        <BarsPeriodTypeSerialize>4</BarsPeriodTypeSerialize>
        <BaseBarsPeriodType>Minute</BaseBarsPeriodType>
        <BaseBarsPeriodValue>1</BaseBarsPeriodValue>
        <VolumetricDeltaType>BidAsk</VolumetricDeltaType>
        <MarketDataType>Last</MarketDataType>
        <PointAndFigurePriceType>Close</PointAndFigurePriceType>
        <ReversalType>Tick</ReversalType>
        <Value>1</Value>
        <Value2>1</Value2>
      </BarsPeriodSerializable>
      <BarsToLoad>0</BarsToLoad>
      <Calculate>OnBarClose</Calculate>
      <Displacement>0</Displacement>
      <DisplayInDataBox>true</DisplayInDataBox>
      <From>2099-12-01T00:00:00</From>
      <IsAutoScale>true</IsAutoScale>
      <Lines />
      <MaximumBarsLookBack>TwoHundredFiftySix</MaximumBarsLookBack>
      <Name>PantheonBotV2</Name>
      <Panel>-1</Panel>
      <Plots>
        <Plot>
          <IsOpacityVisible>false</IsOpacityVisible>
          <BrushSerialize>&lt;SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"&gt;#FFFF0000&lt;/SolidColorBrush&gt;</BrushSerialize>
          <DashStyleHelper>Solid</DashStyleHelper>
          <Opacity>100</Opacity>
          <Width>1</Width>
          <AutoWidth>false</AutoWidth>
          <Max>1.7976931348623157E+308</Max>
          <Min>-1.7976931348623157E+308</Min>
          <Name>FAST</Name>
          <PlotStyle>Line</PlotStyle>
        </Plot>
        <Plot>
          <IsOpacityVisible>false</IsOpacityVisible>
          <BrushSerialize>&lt;SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"&gt;#FF008000&lt;/SolidColorBrush&gt;</BrushSerialize>
          <DashStyleHelper>Solid</DashStyleHelper>
          <Opacity>100</Opacity>
          <Width>1</Width>
          <AutoWidth>false</AutoWidth>
          <Max>1.7976931348623157E+308</Max>
          <Min>-1.7976931348623157E+308</Min>
          <Name>SLOW</Name>
          <PlotStyle>Line</PlotStyle>
        </Plot>
        <Plot>
          <IsOpacityVisible>false</IsOpacityVisible>
          <BrushSerialize>&lt;SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"&gt;#FFFFFFFF&lt;/SolidColorBrush&gt;</BrushSerialize>
          <DashStyleHelper>Solid</DashStyleHelper>
          <Opacity>100</Opacity>
          <Width>1</Width>
          <AutoWidth>false</AutoWidth>
          <Max>1.7976931348623157E+308</Max>
          <Min>-1.7976931348623157E+308</Min>
          <Name>TREND</Name>
          <PlotStyle>Line</PlotStyle>
        </Plot>
      </Plots>
      <ShowTransparentPlotsInDataBox>false</ShowTransparentPlotsInDataBox>
      <To>1800-01-01T00:00:00</To>
      <IsDataSeriesRequired>true</IsDataSeriesRequired>
      <IsOverlay>true</IsOverlay>
      <SelectedValueSeries>0</SelectedValueSeries>
      <Gtd>1800-01-01T00:00:00</Gtd>
      <Template />
      <TimeInForce>Gtc</TimeInForce>
      <BarsPeriodParameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">0</Max>
        <Min xsi:type="xsd:int">0</Min>
        <Name />
        <ParameterTypeSerializable>System.Int32, {_ASSEMBLY}</ParameterTypeSerializable>
        <ValueSerializable>0</ValueSerializable>
      </BarsPeriodParameter>
      <BarsRequiredToTrade>20</BarsRequiredToTrade>
      <Category>NinjaScript</Category>
      <ConnectionLossHandling>Recalculate</ConnectionLossHandling>
      <DaysToLoad>5</DaysToLoad>
      <DefaultQuantity>1</DefaultQuantity>
      <DisconnectDelaySeconds>10</DisconnectDelaySeconds>
      <EntriesPerDirection>1</EntriesPerDirection>
      <EntryHandling>AllEntries</EntryHandling>
      <ExitOnSessionCloseSeconds>30</ExitOnSessionCloseSeconds>
      <IncludeCommission>false</IncludeCommission>
      <IsAggregated>false</IsAggregated>
      <IsExitOnSessionCloseStrategy>true</IsExitOnSessionCloseStrategy>
      <IsFillLimitOnTouch>false</IsFillLimitOnTouch>
      <IsOptimizeDataSeries>false</IsOptimizeDataSeries>
      <IsStableSession>true</IsStableSession>
      <IsTickReplay>false</IsTickReplay>
      <IsTradingHoursBreakLineVisible>true</IsTradingHoursBreakLineVisible>
      <IsWaitUntilFlat>false</IsWaitUntilFlat>
      <NumberRestartAttempts>4</NumberRestartAttempts>
      <OptimizationPeriod>10</OptimizationPeriod>
      <OrderFillResolution>Standard</OrderFillResolution>
      <OrderFillResolutionType>Minute</OrderFillResolutionType>
      <OrderFillResolutionValue>1</OrderFillResolutionValue>
      <RestartsWithinMinutes>5</RestartsWithinMinutes>
      <SetOrderQuantity>Strategy</SetOrderQuantity>
      <Slippage>0</Slippage>
      <StartBehavior>WaitUntilFlat</StartBehavior>
      <StopTargetHandling>PerEntryExecution</StopTargetHandling>
      <SupportsOptimizationGraph>true</SupportsOptimizationGraph>
      <TestPeriod>28</TestPeriod>
      <TradingHoursSerializable />
      <DrawOnPricePanel>false</DrawOnPricePanel>
      <ZOrder>-2147483648</ZOrder>
      <TrendHigherTimeFrameMinutes>{p['TrendHigherTimeFrameMinutes']}</TrendHigherTimeFrameMinutes>
      <TrendEmaPeriod>{p['TrendEmaPeriod']}</TrendEmaPeriod>
      <TrendSlopeLookbackBars>{p['TrendSlopeLookbackBars']}</TrendSlopeLookbackBars>
      <TrendAtrPeriod>{p['TrendAtrPeriod']}</TrendAtrPeriod>
      <TrendFlatThreshold>{p['TrendFlatThreshold']}</TrendFlatThreshold>
      <TrendSlopeModeInt>{p['TrendSlopeModeInt']}</TrendSlopeModeInt>
      <VwapAtrPeriod>{p['VwapAtrPeriod']}</VwapAtrPeriod>
      <VwapNearThresholdAtr>{p['VwapNearThresholdAtr']}</VwapNearThresholdAtr>
      <UseCloseForVwapPrice>{'true' if p['UseCloseForVwapPrice'] else 'false'}</UseCloseForVwapPrice>
      <VolatilityAtrPeriod>{p['VolatilityAtrPeriod']}</VolatilityAtrPeriod>
      <VolatilityLookbackWindow>{p['VolatilityLookbackWindow']}</VolatilityLookbackWindow>
      <VolatilityLowPercentile>{p['VolatilityLowPercentile']}</VolatilityLowPercentile>
      <VolatilityHighPercentile>{p['VolatilityHighPercentile']}</VolatilityHighPercentile>
      <RequiredTrendRegimeFilter>{p['RequiredTrendRegimeFilter']}</RequiredTrendRegimeFilter>
      <RequiredVwapRegimeFilter>{p['RequiredVwapRegimeFilter']}</RequiredVwapRegimeFilter>
      <RequiredVolatilityRegimeFilter>{p['RequiredVolatilityRegimeFilter']}</RequiredVolatilityRegimeFilter>
      <BlockedVolatilityRegimeFilter>{p['BlockedVolatilityRegimeFilter']}</BlockedVolatilityRegimeFilter>
      <EnableDebugPrint>false</EnableDebugPrint>
      <EnableTradeMarkers>false</EnableTradeMarkers>
      <UseDynamicStop>{'true' if p['UseDynamicStop'] else 'false'}</UseDynamicStop>
      <UseLockIn>{'true' if p['UseLockIn'] else 'false'}</UseLockIn>
      <LockInTriggerTicks>{p['LockInTriggerTicks']}</LockInTriggerTicks>
      <LockInPlusTicks>{p['LockInPlusTicks']}</LockInPlusTicks>
      <UseGiveback>{'true' if p['UseGiveback'] else 'false'}</UseGiveback>
      <GivebackStartTicks>{p['GivebackStartTicks']}</GivebackStartTicks>
      <GivebackTicks>{p['GivebackTicks']}</GivebackTicks>
      <UseTrail>{'true' if p['UseTrail'] else 'false'}</UseTrail>
      <TrailStartTicks>{p['TrailStartTicks']}</TrailStartTicks>
      <TrailDistanceTicks>{p['TrailDistanceTicks']}</TrailDistanceTicks>
      <UseTimeFilter>{'true' if p['UseTimeFilter'] else 'false'}</UseTimeFilter>
      <StartTimeH>{p['StartTimeH']}</StartTimeH>
      <StartTimeM>{p['StartTimeM']}</StartTimeM>
      <DurationTimeH>{p['DurationTimeH']}</DurationTimeH>
      <DurationTimeM>{p['DurationTimeM']}</DurationTimeM>
      <averageFast>{p['averageFast']}</averageFast>
      <averageSlow>{p['averageSlow']}</averageSlow>
      <averageTrend>{p['averageTrend']}</averageTrend>
      <UseTrend>{'true' if p['UseTrend'] else 'false'}</UseTrend>
      <UseTrendReverse>{'true' if p['UseTrendReverse'] else 'false'}</UseTrendReverse>
      <ProfitStop>{p['ProfitStop']:.0f}</ProfitStop>
      <LossStop>{p['LossStop']:.0f}</LossStop>
      <MaxTrades>{int(p['MaxTrades'])}</MaxTrades>
      <Contracts>{p['Contracts']}</Contracts>
      <MaxStop>{p['MaxStop']}</MaxStop>
      <UseMaxStop>{'true' if p['UseMaxStop'] else 'false'}</UseMaxStop>
      <MaxTPRatio>{max_tp_ratio_str}</MaxTPRatio>
      <UseMaxTP>{'true' if p['UseMaxTP'] else 'false'}</UseMaxTP>
      <Long>{'true' if p['Long'] else 'false'}</Long>
      <Short>{'true' if p['Short'] else 'false'}</Short>
      <Reverse>false</Reverse>
      <UseKill>{'true' if p['UseKill'] else 'false'}</UseKill>
      <KillProfitStop>{p['KillProfitStop']:.0f}</KillProfitStop>
      <KillLossStop>{p['KillLossStop']:.0f}</KillLossStop>
    </PantheonBotV2>
  </Strategy>
</StrategyTemplate>"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_pantheon_v2_template(
    sd: Dict[str, Any],
    run_id: str = "unknown",
    options: Optional[Dict[str, Any]] = None,
) -> GeneratedTemplate:
    """
    Generate a PantheonBotV2 NinjaTrader template from Strategy Discovery results.

    Parameters
    ----------
    sd       : pkg.metadata["derived"]["strategy_discovery"] dict for one run
    run_id   : run identifier (used in XML comment)
    options  : optional overrides — tick_value, tick_size

    Returns
    -------
    GeneratedTemplate(xml_str, parameters, reasoning, warnings)
    """
    opts     = options or {}
    warnings: List[str] = []
    reasoning: Dict[str, str] = {}

    # --- Stop / target ---
    max_stop, max_tp_ratio, stop_r, tp_r = _v2_stop_target(sd, warnings)
    reasoning["MaxStop"]    = stop_r
    reasoning["MaxTPRatio"] = tp_r

    # --- Direction ---
    allow_long, allow_short, dir_r = _extract_direction_filters(sd, warnings)
    reasoning["Long/Short"] = dir_r

    # --- Regime filters ---
    trend_f, vwap_f, vol_req, vol_blk, regime_r = _v2_regime_filters(
        sd, allow_long, allow_short, warnings
    )
    reasoning["RegimeFilters"] = regime_r

    # --- Dynamic stop params ---
    lock_in_t, giveback_s, giveback_t, dyn_r = _v2_lock_giveback(
        sd, max_stop, max_tp_ratio, warnings
    )
    reasoning["DynamicStop"] = dyn_r

    # --- Time filter ---
    start_h, start_m, dur_h, dur_m, time_r = _v2_time_filter(sd, warnings)
    reasoning["TimeFilter"] = time_r

    # --- Daily risk ---
    max_daily_loss, max_daily_trades, max_daily_profit, risk_r = _extract_daily_risk(
        sd, warnings, tick_value=float(opts.get("tick_value", 5.0))
    )
    reasoning["DailyRisk"] = risk_r

    # Derive target ticks for LockIn validation
    target_ticks = math.floor(max_stop * max_tp_ratio)

    # Clamp LockIn to be < target
    if lock_in_t >= target_ticks:
        lock_in_t = max(4, target_ticks - 4)
    if giveback_s >= target_ticks:
        giveback_s = max(lock_in_t + 4, target_ticks - 4)
    if giveback_t >= giveback_s:
        giveback_t = max(4, giveback_s // 3)

    # Build params dict
    params: Dict[str, Any] = {
        # Regime / HTF settings (fixed defaults)
        "TrendHigherTimeFrameMinutes": 15,
        "TrendEmaPeriod":              34,
        "TrendSlopeLookbackBars":       3,
        "TrendAtrPeriod":              14,
        "TrendFlatThreshold":        0.03,
        "TrendSlopeModeInt":            1,
        "VwapAtrPeriod":               14,
        "VwapNearThresholdAtr":       0.2,
        "UseCloseForVwapPrice":       True,
        "VolatilityAtrPeriod":         14,
        "VolatilityLookbackWindow":   100,
        "VolatilityLowPercentile":   0.33,
        "VolatilityHighPercentile":  0.67,
        # Discovered regime filters
        "RequiredTrendRegimeFilter":      trend_f,
        "RequiredVwapRegimeFilter":       vwap_f,
        "RequiredVolatilityRegimeFilter": vol_req,
        "BlockedVolatilityRegimeFilter":  vol_blk,
        # Debug (off)
        "EnableDebugPrint":   False,
        "EnableTradeMarkers": False,
        # Dynamic stop
        "UseDynamicStop":    True,
        "UseLockIn":         True,
        "LockInTriggerTicks": lock_in_t,
        "LockInPlusTicks":    6,
        "UseGiveback":       True,
        "GivebackStartTicks": giveback_s,
        "GivebackTicks":      giveback_t,
        "UseTrail":          False,
        "TrailStartTicks":   30,
        "TrailDistanceTicks": 12,
        # Time filter
        "UseTimeFilter": True,
        "StartTimeH":    start_h,
        "StartTimeM":    start_m,
        "DurationTimeH": dur_h,
        "DurationTimeM": dur_m,
        # MA periods (defaults — not derived from discovery)
        "averageFast":  50,
        "averageSlow":  200,
        "averageTrend": 300,
        "UseTrend":         True,
        "UseTrendReverse":  False,
        # Daily risk
        "ProfitStop": float(max_daily_profit),
        "LossStop":   float(max_daily_loss),
        "MaxTrades":  float(max_daily_trades),
        # Position / stop / target
        "Contracts":    1,
        "MaxStop":      max_stop,
        "UseMaxStop":   True,
        "MaxTPRatio":   max_tp_ratio,
        "UseMaxTP":     True,
        "Long":         allow_long,
        "Short":        allow_short,
        "Reverse":      False,
        # Kill switch (off)
        "UseKill":       False,
        "KillProfitStop": 800.0,
        "KillLossStop":   400.0,
    }

    xml_str = _build_v2_xml(params, run_id, reasoning)

    return GeneratedTemplate(
        xml_str=xml_str,
        parameters=params,
        reasoning=reasoning,
        warnings=warnings,
    )
