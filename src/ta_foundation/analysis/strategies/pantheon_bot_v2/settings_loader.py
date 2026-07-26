"""
Load PantheonBotV2 settings from an NT Strategy Analyzer export (.csv) or
a hand-authored .json file.

The NT export is a two-column key/value CSV where the keys are display
names from the strategy property grid (e.g. "Trend EMA Period"), not the
C# property names. This module normalises both to canonical property keys
matching PantheonBotV2.cs so test code can refer to one schema.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Union


_DISPLAY_TO_PROP: dict[str, str] = {
    "Trend HTF Minutes": "TrendHigherTimeFrameMinutes",
    "Trend EMA Period": "TrendEmaPeriod",
    "Trend Slope Lookback Bars": "TrendSlopeLookbackBars",
    "Trend ATR Period": "TrendAtrPeriod",
    "Trend Flat Threshold": "TrendFlatThreshold",
    "Trend Slope Mode (0=RawPoints1=AtrNormalized)": "TrendSlopeModeInt",
    "VWAP ATR Period": "VwapAtrPeriod",
    "VWAP Near Threshold ATR": "VwapNearThresholdAtr",
    "Use Close for VWAP Regime": "UseCloseForVwapPrice",
    "Volatility ATR Period": "VolatilityAtrPeriod",
    "Volatility Lookback Window": "VolatilityLookbackWindow",
    "Volatility Low Percentile": "VolatilityLowPercentile",
    "Volatility High Percentile": "VolatilityHighPercentile",
    "Required Trend Regime": "RequiredTrendRegimeFilter",
    "Required VWAP Regime": "RequiredVwapRegimeFilter",
    "Required Volatility Regime": "RequiredVolatilityRegimeFilter",
    "Blocked Volatility Regime": "BlockedVolatilityRegimeFilter",
    "Enable Debug Print": "EnableDebugPrint",
    "Enable Trade Markers": "EnableTradeMarkers",
    "Use_DynamicStop": "UseDynamicStop",
    "Use_LockIn": "UseLockIn",
    "LockIn_TriggerTicks": "LockInTriggerTicks",
    "LockIn_PlusTicks": "LockInPlusTicks",
    "Use_Giveback": "UseGiveback",
    "Giveback_StartTicks": "GivebackStartTicks",
    "Giveback_Ticks": "GivebackTicks",
    "Use_Trail": "UseTrail",
    "Trail_StartTicks": "TrailStartTicks",
    "Trail_DistanceTicks": "TrailDistanceTicks",
    "Use_Time_Filter": "UseTimeFilter",
    "Start_Time_(HH)": "StartTimeH",
    "Start_Time_(mm)": "StartTimeM",
    "Duration_Time_(HH)": "DurationTimeH",
    "Duration_Time_(mm)": "DurationTimeM",
    "averageFast": "averageFast",
    "averageSlow": "averageSlow",
    "averageTrend": "averageTrend",
    "UseTrend": "UseTrend",
    "UseTrendReverse": "UseTrendReverse",
    "ProfitStop": "ProfitStop",
    "LossStop": "LossStop",
    "MaxTrades": "MaxTrades",
    "Contracts": "Contracts",
    "MaxStop": "MaxStop",
    "Use_MaxStop": "UseMaxStop",
    "MaxTPRatio": "MaxTPRatio",
    "Use_MaxTP": "UseMaxTP",
    "Long": "Long",
    "Short": "Short",
    "Reverse": "Reverse",
    "Use_Kill": "UseKill",
    "Kill_Profit_Stop": "KillProfitStop",
    "Kill_Loss_Stop": "KillLossStop",
}


def _coerce(value: str) -> Any:
    s = value.strip()
    if s == "":
        return None
    lower = s.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        if "." in s or "e" in lower:
            return float(s)
        return int(s)
    except ValueError:
        return s


def load_settings(path: Union[str, Path]) -> dict[str, Any]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    if suffix == ".csv":
        return _load_nt_csv(p)
    raise ValueError(f"Unsupported settings file extension: {p.suffix}")


def _load_nt_csv(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            key = row[0].strip()
            val = row[1].strip() if len(row) > 1 else ""
            if not key or not val:
                continue
            prop = _DISPLAY_TO_PROP.get(key)
            if prop is None:
                continue
            out[prop] = _coerce(val)
    return out
