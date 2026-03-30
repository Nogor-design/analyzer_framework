from __future__ import annotations

"""
PantheonMaster Template Generator
===================================
Reads Strategy Discovery analysis results and produces a NinjaTrader 8 XML
parameter template pre-configured for the PantheonMaster strategy.

Session labels (America/Denver) produced by the analysis pipeline:
  London   00:00-05:59  → AllowLondon
  NyPre    06:00-06:59  → AllowNyPre
  NyOpen   07:00-08:59  → AllowNyOpen
  NyMid    09:00-12:59  → AllowNyMid
  PowerHr  13:00-13:59  → AllowMyPowerHr   (note: strategy property name is MyPowerHr)
  Asia     18:00-23:59  → AllowAsia
  other    14:00-17:59  → blocked by UseNamedSessions

Regime labels (from analysis):
  trending_up / trending_down → PantheonRegimeMode
  ranging                     → RangingOnly
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class PantheonMasterTemplate:
    xml_str: str
    parameters: Dict[str, Any]
    reasoning: Dict[str, str]
    warnings: List[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return None if (f != f) else f  # NaN guard
    except Exception:
        return None


def _fmt_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s
    return str(v)


# ---------------------------------------------------------------------------
# Session filter extraction → Allow* booleans
# ---------------------------------------------------------------------------

# session_label → PantheonMaster Allow* property name
_SESSION_TO_PARAM = {
    "London":  "AllowLondon",
    "NyPre":   "AllowNyPre",
    "NyOpen":  "AllowNyOpen",
    "NyMid":   "AllowNyMid",
    "PowerHr": "AllowMyPowerHr",   # strategy property is AllowMyPowerHr
    "Asia":    "AllowAsia",
}

_ALL_SESSIONS = list(_SESSION_TO_PARAM.keys())


def _extract_session_allow(
    sd: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, bool]:
    """
    Return {Allow* param: bool} by reading evaluation.by_session profit_factor.
    A session is allowed when its profit_factor >= 1.0, or when there is no data.
    Falls back to scanning top signal rules for session_label conditions.
    """
    ev = sd.get("evaluation") or {}
    by_session = ev.get("by_session") or {}

    session_pf: Dict[str, Optional[float]] = {}
    for label in _ALL_SESSIONS:
        data = by_session.get(label) or {}
        session_pf[label] = _safe_float(data.get("profit_factor"))

    has_data = any(v is not None for v in session_pf.values())

    if has_data:
        result: Dict[str, bool] = {}
        for label, param in _SESSION_TO_PARAM.items():
            pf = session_pf.get(label)
            # Allow if PF >= 1.0 or if no data for this session (default open)
            result[param] = (pf is None) or (pf >= 1.0)
        return result

    # -- Fallback: scan top signal rules for session_label conditions --
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    good_sessions = set()
    for rule in top_rules[:15]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "session_label":
                val = str(cond.get("value") or "")
                if val in _SESSION_TO_PARAM:
                    good_sessions.add(val)

    if good_sessions:
        result = {}
        for label, param in _SESSION_TO_PARAM.items():
            result[param] = label in good_sessions
        warnings.append(
            f"Session filters from signal rule conditions: {sorted(good_sessions)}"
        )
        return result

    # No data at all — allow everything
    warnings.append("No session breakdown data — all sessions enabled")
    return {param: True for param in _SESSION_TO_PARAM.values()}


# ---------------------------------------------------------------------------
# Regime extraction → RegimeMode string
# ---------------------------------------------------------------------------

def _extract_regime_mode(sd: Dict[str, Any], warnings: List[str]) -> str:
    """
    Map regime analysis to PantheonRegimeMode enum string.

    Priority:
      1. Signal corpus top rules: count trending_up / trending_down / ranging conditions
      2. evaluation.by_regime profit_factor
      3. Default: TrendingOnly
    """
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []

    up_count = down_count = ranging_count = 0
    for rule in top_rules[:15]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "regime":
                val = str(cond.get("value") or "").lower()
                if val == "trending_up":   up_count += 1
                elif val == "trending_down": down_count += 1
                elif val == "ranging":     ranging_count += 1

    if up_count or down_count or ranging_count:
        if ranging_count > up_count + down_count:
            return "RangingOnly"
        if up_count > 0 and down_count == 0 and ranging_count == 0:
            return "TrendingUp"
        if down_count > 0 and up_count == 0 and ranging_count == 0:
            return "TrendingDown"
        if up_count + down_count > 0:
            return "TrendingOnly"

    # Fallback: evaluation.by_regime
    ev = sd.get("evaluation") or {}
    by_regime = ev.get("by_regime") or {}
    trending_pf = _safe_float((by_regime.get("trending_up") or {}).get("profit_factor"))
    down_pf     = _safe_float((by_regime.get("trending_down") or {}).get("profit_factor"))
    ranging_pf  = _safe_float((by_regime.get("ranging") or {}).get("profit_factor"))

    if trending_pf is not None or down_pf is not None or ranging_pf is not None:
        trending_good = (trending_pf or 0) >= 1.0 or (down_pf or 0) >= 1.0
        ranging_good  = (ranging_pf or 0) >= 1.0
        if trending_good and not ranging_good:
            return "TrendingOnly"
        if ranging_good and not trending_good:
            return "RangingOnly"

    warnings.append("No clear regime preference — defaulting to TrendingOnly")
    return "TrendingOnly"


# ---------------------------------------------------------------------------
# ADX threshold extraction
# ---------------------------------------------------------------------------

def _extract_adx_threshold(sd: Dict[str, Any]) -> float:
    """
    Find the most common ADX threshold in top signal rules.
    Falls back to 25.0.
    """
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    from collections import Counter
    adx_vals: List[float] = []
    for rule in top_rules[:15]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "adx" and str(cond.get("op") or "") == "gte":
                v = _safe_float(cond.get("value"))
                if v is not None:
                    adx_vals.append(v)
    if adx_vals:
        counts = Counter(adx_vals)
        return counts.most_common(1)[0][0]
    return 25.0


# ---------------------------------------------------------------------------
# Direction extraction → DiscoveryAllowLong / DiscoveryAllowShort
# ---------------------------------------------------------------------------

def _extract_direction(sd: Dict[str, Any], warnings: List[str]):
    """Returns (allow_long, allow_short)."""
    ev = sd.get("evaluation") or {}
    by_dir = ev.get("by_direction") or {}
    long_pf  = _safe_float((by_dir.get("Long") or {}).get("profit_factor"))
    short_pf = _safe_float((by_dir.get("Short") or {}).get("profit_factor"))

    if long_pf is not None or short_pf is not None:
        allow_long  = (long_pf  is None) or (long_pf  >= 1.0)
        allow_short = (short_pf is None) or (short_pf >= 1.0)
        # Never disable both
        if not allow_long and not allow_short:
            best = "long" if (long_pf or 0) >= (short_pf or 0) else "short"
            allow_long  = best == "long"
            allow_short = best == "short"
            warnings.append("Both directions PF < 1.0 — re-enabling better direction")
        return allow_long, allow_short

    # Fallback: signal corpus direction conditions
    sed = sd.get("signal_entry_discovery") or {}
    for rule in (sed.get("top_signal_rules") or [])[:15]:
        for cond in (rule.get("conditions") or []):
            if str(cond.get("column") or "") == "direction":
                try:
                    v = int(float(cond.get("value") or 0))
                    if v == 1:  return True, False
                    if v == -1: return False, True
                except Exception:
                    pass

    return True, True


# ---------------------------------------------------------------------------
# Stop / target tick extraction
# ---------------------------------------------------------------------------

def _extract_stop_target(
    sd: Dict[str, Any],
    warnings: List[str],
    tick_value: float = 5.0,
    tick_size:  float = 0.25,
) -> tuple:
    """Returns (stop_ticks, target_ticks)."""
    # Try signal_exit_sweep best policy
    ses = sd.get("signal_exit_sweep") or {}
    best_policy = ses.get("best_policy") or {}
    best_name   = str(best_policy.get("policy_name") or "")

    # Parse fixed_rr_s{S}_t{T}
    import re
    m = re.match(r"fixed_rr_s(\d+)_t(\d+)", best_name)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Try corpus MFE/MAE p50 from top signal rule
    sed = sd.get("signal_entry_discovery") or {}
    top_rules = sed.get("top_signal_rules") or []
    if top_rules:
        top = top_rules[0]
        mae_p50 = _safe_float(top.get("mae_p50"))
        mfe_p50 = _safe_float(top.get("mfe_p50"))
        if mae_p50 and mae_p50 > 0:
            stop_ticks   = max(8, int(round(mae_p50 * 1.2)))
            target_ticks = max(stop_ticks + 4, int(round(mfe_p50 * 0.8))) if mfe_p50 else stop_ticks * 2
            return stop_ticks, target_ticks

    # Fallback
    warnings.append("No exit sweep data — using default 60/90 ticks")
    return 60, 90


# ---------------------------------------------------------------------------
# Daily risk extraction
# ---------------------------------------------------------------------------

def _extract_daily_risk(
    sd: Dict[str, Any],
    warnings: List[str],
    tick_value: float = 5.0,
) -> tuple:
    """Returns (max_daily_loss_usd, max_daily_profit_usd, max_daily_trades)."""
    dd = sd.get("drawdown_analysis") or {}
    streaks = dd.get("streaks") or {}
    max_consec_losses = int(streaks.get("max_consecutive_losses") or 3)
    max_daily_trades  = max_consec_losses + 2

    ev = sd.get("evaluation") or {}
    avg_trade = _safe_float(ev.get("avg_trade"))

    if avg_trade is not None and avg_trade > 0:
        max_daily_loss   = round(avg_trade * 1.0, -1)
        max_daily_profit = max_daily_loss * 3.0
        return max(100.0, max_daily_loss), max_daily_profit, max_daily_trades

    # Corpus fallback
    sed_d = sd.get("signal_entry_discovery") or {}
    baseline = sed_d.get("baseline") or {}
    avg_ticks = _safe_float(baseline.get("avg_ticks"))
    if avg_ticks and avg_ticks > 0:
        est = avg_ticks * tick_value
        max_daily_loss   = max(100.0, round(est * 2.0, -1))
        max_daily_profit = max_daily_loss * 3.0
        return max_daily_loss, max_daily_profit, max_daily_trades

    warnings.append("No avg_trade data — MaxDailyLossUsd defaulted to $500")
    return 500.0, 1500.0, max_daily_trades


# ---------------------------------------------------------------------------
# XML builder
# ---------------------------------------------------------------------------

def _build_xml(params: Dict[str, Any], run_id: str, reasoning: Dict[str, str]) -> str:
    def e(tag: str, value: Any) -> str:
        return f"      <{tag}>{_fmt_val(value)}</{tag}>"

    notes_lines = ["      <!-- Auto-generated by ta_foundation PantheonMaster Template Generator"]
    notes_lines.append(f"           Source run: {run_id}")
    for key, reason in reasoning.items():
        notes_lines.append(f"           {key}: {reason[:120]}")
    notes_lines.append("      -->")
    notes_block = "\n".join(notes_lines)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMaster</StrategyType>
  <Strategy>
    <PantheonMaster xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
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
      <From>2026-01-26T00:00:00</From>
      <IsAutoScale>true</IsAutoScale>
      <Lines />
      <MaximumBarsLookBack>TwoHundredFiftySix</MaximumBarsLookBack>
      <Name>PantheonMaster</Name>
      <Panel>-1</Panel>
      <Plots />
      <ScaleJustification>Right</ScaleJustification>
      <ShowTransparentPlotsInDataBox>false</ShowTransparentPlotsInDataBox>
      <To>2026-03-31T00:00:00</To>
      <IsDataSeriesRequired>true</IsDataSeriesRequired>
      <IsOverlay>true</IsOverlay>
      <SelectedValueSeries>0</SelectedValueSeries>
      <Gtd>1800-01-01T00:00:00</Gtd>
      <Template />
      <TimeInForce>Gtc</TimeInForce>
      <BarsRequiredToTrade>20</BarsRequiredToTrade>
      <Category>Backtest</Category>
      <ConnectionLossHandling>Recalculate</ConnectionLossHandling>
      <DaysToLoad>5</DaysToLoad>
      <DefaultQuantity>1</DefaultQuantity>
      <DisconnectDelaySeconds>10</DisconnectDelaySeconds>
      <EntriesPerDirection>1</EntriesPerDirection>
      <EntryHandling>AllEntries</EntryHandling>
      <ExitOnSessionCloseSeconds>30</ExitOnSessionCloseSeconds>
      <IncludeCommission>false</IncludeCommission>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
      <IsExitOnSessionCloseStrategy>true</IsExitOnSessionCloseStrategy>
      <IsFillLimitOnTouch>false</IsFillLimitOnTouch>
      <IsOptimizeDataSeries>false</IsOptimizeDataSeries>
      <IsStableSession>true</IsStableSession>
      <IsTickReplay>false</IsTickReplay>
      <IsWaitUntilFlat>false</IsWaitUntilFlat>
      <StartBehavior>WaitUntilFlat</StartBehavior>
      <StopTargetHandling>PerEntryExecution</StopTargetHandling>
{notes_block}
{e("FastPeriod", params["FastPeriod"])}
{e("SlowPeriod", params["SlowPeriod"])}
{e("TrendPeriod", params["TrendPeriod"])}
{e("UseTrend", params["UseTrend"])}
{e("UseTrendReverse", params["UseTrendReverse"])}
{e("Long", params["Long"])}
{e("Short", params["Short"])}
{e("Reverse", params["Reverse"])}
{e("UseTimeFilter", params["UseTimeFilter"])}
{e("StartTimeH", params["StartTimeH"])}
{e("StartTimeM", params["StartTimeM"])}
{e("DurationTimeH", params["DurationTimeH"])}
{e("DurationTimeM", params["DurationTimeM"])}
{e("Show_Current_PNL", params["Show_Current_PNL"])}
{e("Show_Stats_Box", params["Show_Stats_Box"])}
{e("BotName", params["BotName"])}
{e("UseKill", params["UseKill"])}
{e("KillProfitStop", params["KillProfitStop"])}
{e("KillLossStop", params["KillLossStop"])}
{e("Contracts", params["Contracts"])}
{e("MaxStop", params["MaxStop"])}
{e("UseMaxStop", params["UseMaxStop"])}
{e("MaxTPRatio", params["MaxTPRatio"])}
{e("UseMaxTP", params["UseMaxTP"])}
{e("UseLegacyDailyRisk", params["UseLegacyDailyRisk"])}
{e("ProfitStop", params["ProfitStop"])}
{e("LossStop", params["LossStop"])}
{e("MaxTrades", params["MaxTrades"])}
{e("EnableDiscoveryFilters", params["EnableDiscoveryFilters"])}
{e("RegimeMode", params["RegimeMode"])}
{e("AdxPeriod", params["AdxPeriod"])}
{e("AdxThreshold", params["AdxThreshold"])}
{e("RegimeEmaPeriod", params["RegimeEmaPeriod"])}
{e("AtrPeriod", params["AtrPeriod"])}
{e("AtrLookbackBars", params["AtrLookbackBars"])}
{e("VolLowPercentile", params["VolLowPercentile"])}
{e("VolHighPercentile", params["VolHighPercentile"])}
{e("UseNamedSessions", params["UseNamedSessions"])}
{e("SessionTimeZoneId", params["SessionTimeZoneId"])}
{e("AllowLondon", params["AllowLondon"])}
{e("AllowNyPre", params["AllowNyPre"])}
{e("AllowNyOpen", params["AllowNyOpen"])}
{e("AllowNyMid", params["AllowNyMid"])}
{e("AllowMyPowerHr", params["AllowMyPowerHr"])}
{e("AllowAsia", params["AllowAsia"])}
      <LondonStart>00:00</LondonStart>
      <LondonEnd>06:00</LondonEnd>
      <NyPreStart>06:00</NyPreStart>
      <NyPreEnd>07:29</NyPreEnd>
      <NyOpenStart>07:30</NyOpenStart>
      <NyOpenEnd>09:00</NyOpenEnd>
      <NyMidStart>09:01</NyMidStart>
      <NyMidEnd>13:00</NyMidEnd>
      <MyPowerHrStart>13:01</MyPowerHrStart>
      <MyPowerHrEnd>14:00</MyPowerHrEnd>
      <AsiaStart>18:00</AsiaStart>
      <AsiaEnd>23:59</AsiaEnd>
{e("DiscoveryAllowLong", params["DiscoveryAllowLong"])}
{e("DiscoveryAllowShort", params["DiscoveryAllowShort"])}
{e("UseTrendAlignment", params["UseTrendAlignment"])}
{e("RequireEmaConfirmation", params["RequireEmaConfirmation"])}
{e("RequireMinAtrPercentile", params["RequireMinAtrPercentile"])}
{e("UseDiscoveryExitPolicy", params["UseDiscoveryExitPolicy"])}
{e("DiscoveryExitPolicy", params["DiscoveryExitPolicy"])}
{e("StopTicks", params["StopTicks"])}
{e("TargetTicks", params["TargetTicks"])}
{e("AtrTrailMultiple", params["AtrTrailMultiple"])}
{e("ChandelierLookback", params["ChandelierLookback"])}
{e("GivebackPct", params["GivebackPct"])}
{e("BreakEvenTriggerTicks", params["BreakEvenTriggerTicks"])}
{e("BreakEvenPlusTicks", params["BreakEvenPlusTicks"])}
{e("UseDiscoveryDailyRisk", params["UseDiscoveryDailyRisk"])}
{e("MaxDailyLossUsd", params["MaxDailyLossUsd"])}
{e("MaxDailyProfitUsd", params["MaxDailyProfitUsd"])}
{e("MaxDailyTrades", params["MaxDailyTrades"])}
{e("UseLiveStopManagement", params["UseLiveStopManagement"])}
{e("EnableDebugPrint", params["EnableDebugPrint"])}
    </PantheonMaster>
  </Strategy>
</StrategyTemplate>
"""
    return xml


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_pantheon_master_template(
    sd: Dict[str, Any],
    run_id: str = "unknown",
    options: Optional[Dict[str, Any]] = None,
) -> PantheonMasterTemplate:
    """
    Generate a PantheonMaster NinjaTrader XML template from strategy discovery results.

    Parameters
    ----------
    sd      : pkg.metadata["derived"]["strategy_discovery"]
    run_id  : used for XML comment attribution
    options : {"tick_value": float, "tick_size": float}

    Returns
    -------
    PantheonMasterTemplate with xml_str, parameters, reasoning, warnings
    """
    options    = options or {}
    tick_value = float(options.get("tick_value") or 5.0)
    tick_size  = float(options.get("tick_size")  or 0.25)

    warnings: List[str] = []
    reasoning: Dict[str, str] = {}

    # A: Session filters
    session_allow = _extract_session_allow(sd, warnings)
    enabled_sessions = [s for s, p in _SESSION_TO_PARAM.items() if session_allow.get(p, True)]
    reasoning["Sessions"] = f"Enabled: {', '.join(enabled_sessions) or 'all'}"

    # B: Regime
    regime_mode = _extract_regime_mode(sd, warnings)
    reasoning["RegimeMode"] = regime_mode

    # C: ADX threshold
    adx_threshold = _extract_adx_threshold(sd)
    reasoning["AdxThreshold"] = str(adx_threshold)

    # D: Direction
    allow_long, allow_short = _extract_direction(sd, warnings)
    reasoning["Direction"] = f"Long={allow_long}, Short={allow_short}"

    # E: Stop / Target
    stop_ticks, target_ticks = _extract_stop_target(sd, warnings, tick_value, tick_size)
    reasoning["StopTicks/TargetTicks"] = f"{stop_ticks}/{target_ticks}"

    # F: Daily risk
    max_loss, max_profit, max_trades = _extract_daily_risk(sd, warnings, tick_value)
    reasoning["DailyRisk"] = f"Loss=${max_loss:.0f}, Profit=${max_profit:.0f}, Trades={max_trades}"

    # G: Trend alignment — enable when regime is trending
    use_trend_alignment = regime_mode in ("TrendingOnly", "TrendingUp", "TrendingDown")

    params: Dict[str, Any] = {
        # MA periods (reasonable futures defaults, not from analysis)
        "FastPeriod":    5,
        "SlowPeriod":    200,
        "TrendPeriod":   300,
        "UseTrend":      False,
        "UseTrendReverse": False,
        # Direction
        "Long":          allow_long,
        "Short":         allow_short,
        "Reverse":       False,
        # Legacy time filter — disabled when UseNamedSessions is on
        "UseTimeFilter": False,
        "StartTimeH":    8,
        "StartTimeM":    0,
        "DurationTimeH": 8,
        "DurationTimeM": 0,
        # Display
        "Show_Current_PNL": False,
        "Show_Stats_Box":   False,
        "BotName":       "PantheonMaster",
        # Kill switch (disabled — let discovery daily risk manage)
        "UseKill":       False,
        "KillProfitStop": 800,
        "KillLossStop":   400,
        "Contracts":     1,
        # Cross profit caps
        "MaxStop":       max(int(stop_ticks * tick_value * 1.5), 200),
        "UseMaxStop":    True,
        "MaxTPRatio":    round(target_ticks / max(1, stop_ticks), 2),
        "UseMaxTP":      True,
        # Legacy daily risk (disabled — use discovery risk instead)
        "UseLegacyDailyRisk": False,
        "ProfitStop":    10000,
        "LossStop":      10000,
        "MaxTrades":     500,
        # Discovery regime
        "EnableDiscoveryFilters": True,
        "RegimeMode":    regime_mode,
        "AdxPeriod":     14,
        "AdxThreshold":  adx_threshold,
        "RegimeEmaPeriod": 50,
        "AtrPeriod":     14,
        "AtrLookbackBars": 100,
        "VolLowPercentile":  0.3,
        "VolHighPercentile": 0.7,
        # Discovery sessions
        "UseNamedSessions": True,
        "SessionTimeZoneId": "America/Denver",
        "AllowLondon":    session_allow.get("AllowLondon",    True),
        "AllowNyPre":     session_allow.get("AllowNyPre",     True),
        "AllowNyOpen":    session_allow.get("AllowNyOpen",    True),
        "AllowNyMid":     session_allow.get("AllowNyMid",     True),
        "AllowMyPowerHr": session_allow.get("AllowMyPowerHr", True),
        "AllowAsia":      session_allow.get("AllowAsia",      True),
        # Discovery entry
        "DiscoveryAllowLong":       allow_long,
        "DiscoveryAllowShort":      allow_short,
        "UseTrendAlignment":        use_trend_alignment,
        "RequireEmaConfirmation":   False,
        "RequireMinAtrPercentile":  0.0,
        # Discovery exit
        "UseDiscoveryExitPolicy":   True,
        "DiscoveryExitPolicy":      "FixedRR",
        "StopTicks":                stop_ticks,
        "TargetTicks":              target_ticks,
        "AtrTrailMultiple":         2.0,
        "ChandelierLookback":       20,
        "GivebackPct":              0.4,
        "BreakEvenTriggerTicks":    max(8, stop_ticks // 2),
        "BreakEvenPlusTicks":       4,
        # Discovery daily risk
        "UseDiscoveryDailyRisk":    True,
        "MaxDailyLossUsd":          max_loss,
        "MaxDailyProfitUsd":        max_profit,
        "MaxDailyTrades":           max_trades,
        # Live / debug
        "UseLiveStopManagement": False,
        "EnableDebugPrint":       False,
    }

    xml_str = _build_xml(params, run_id, reasoning)

    return PantheonMasterTemplate(
        xml_str=xml_str,
        parameters=params,
        reasoning=reasoning,
        warnings=warnings,
    )
