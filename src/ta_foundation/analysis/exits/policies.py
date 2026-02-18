from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal


ExitReason = Literal["stop", "target", "time", "eod", "unknown"]


@dataclass(frozen=True)
class FixedStopTargetPolicy:
    name: str
    stop_ticks: Optional[float] = None
    target_ticks: Optional[float] = None
    stop_atr_mult: Optional[float] = None
    target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class AtrTrailPolicy:
    name: str
    stop_atr_mult: float
    trail_atr_mult: float
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class TrailStopTargetPolicy:
    name: str
    start_trail_ticks: float
    trail_amount: float
    stop_ticks: float


@dataclass(frozen=True)
class BreakEvenAtrTrailPolicy:
    """
    Initial fixed ATR stop, optional profit target, plus:
      - arm break-even after a profit threshold (either ATR-based or ticks-based)
      - move stop to entry +/- offset ticks (conservative snap handled in simulator)
      - then keep ATR trailing
    """
    name: str
    stop_atr_mult: float
    trail_atr_mult: float
    be_trigger_atr_mult: float
    be_offset_ticks: float = 0.0
    be_trigger_ticks: Optional[float] = None
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class ChandelierAtrTrailPolicy:
    name: str
    stop_atr_mult: float
    trail_atr_mult: float
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class TimeStopNoProgressPolicy:
    """
    Time stop if trade doesn't show enough progress.
      - If minutes_in_trade >= max_minutes AND best_favorable_ticks < min_mfe_ticks => exit (market)
    """
    name: str
    max_minutes: float
    min_mfe_ticks: float


@dataclass(frozen=True)
class GivebackAfterMfePolicy:
    name: str
    arm_mfe_ticks: float
    giveback_ticks: float


@dataclass(frozen=True)
class FixedAtrThenAtrTrailPolicy:
    """
    Fixed ATR stop until MFE >= arm_mfe_ticks, then ATR trail starts.

    - Stop starts at entry +/- stop_atr_mult * ATR
    - Once armed, stop becomes max/min(current_stop, best_price -/+ trail_atr_mult * ATR)
    - Optional profit target can be added (constant)
    """
    name: str
    stop_atr_mult: float
    trail_atr_mult: float
    arm_mfe_ticks: float = 40.0
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class FixedAtrThenChandelierPolicy:
    """
    Fixed ATR stop until MFE >= arm_mfe_ticks, then "chandelier" behavior after arming.

    In this simulator, chandelier behavior is implemented as:
      - watermark best_price (LAST-based)
      - stop = best_price -/+ trail_atr_mult * ATR  (long/short)
      - stop is monotone in the favorable direction

    This is intentionally aligned with ChandelierAtrTrailPolicy mechanics, but gated by arm_mfe_ticks.
    """
    name: str
    stop_atr_mult: float
    trail_atr_mult: float
    arm_mfe_ticks: float = 40.0
    profit_target_atr_mult: Optional[float] = None
