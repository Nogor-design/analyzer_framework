from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal


ExitReason = Literal["stop", "target", "time", "eod", "unknown"]


@dataclass(frozen=True)
class FixedStopTargetPolicy:
    """
    Stop/Target can be ATR-based (preferred) and/or tick-based.
    If both are provided, ATR-based distances are used.
    """
    name: str = "fixed_atr"
    stop_atr_mult: float = 1.0
    target_atr_mult: float = 1.5
    stop_ticks: Optional[float] = None
    target_ticks: Optional[float] = None


@dataclass(frozen=True)
class AtrTrailPolicy:
    """
    ATR trailing stop:
      - Initial stop at entry +/- stop_atr_mult * ATR(entry)
      - Trail behind watermark by trail_atr_mult * ATR(entry)
      - Optional profit target (ATR-based)
    """
    name: str = "atr_trail"
    stop_atr_mult: float = 1.0
    trail_atr_mult: float = 1.0
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class BreakEvenAtrTrailPolicy:
    """
    Break-even kick + ATR trail:
      - Initial stop at entry +/- stop_atr_mult * ATR(entry)
      - Once move >= be_trigger_atr_mult * ATR(entry), stop moves to entry + be_offset_ticks
      - Then trail behind watermark by trail_atr_mult * ATR(entry)
      - Optional profit target (ATR-based)
    """
    name: str = "be_atr_trail"
    stop_atr_mult: float = 1.0
    be_trigger_atr_mult: float = 1.0
    be_offset_ticks: float = 0.0
    trail_atr_mult: float = 1.0
    profit_target_atr_mult: Optional[float] = None
