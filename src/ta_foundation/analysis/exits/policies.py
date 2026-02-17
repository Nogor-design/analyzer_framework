from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal


ExitReason = Literal["stop", "target", "time", "eod", "unknown"]

@dataclass(frozen=True)
class TrailStopTargetPolicy:
    """
    Standard trail
    """
    name: str = "trail"
    start_trail_ticks: float = 20.0
    trail_amount: float = 10.0
    stop_ticks: float = 20.0


@dataclass(frozen=True)
class FixedStopTargetPolicy:
    """
    Stop/Target can be ATR-based (preferred) and/or tick-based.
    If both are provided, ATR-based distances are used.
    """
    name: str = "fixed_atr"
    stop_atr_mult: float = None#1.0
    target_atr_mult: float = None#1.5
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
    trail_atr_mult: float = 1.25
    profit_target_atr_mult: Optional[float] = None


@dataclass(frozen=True)
class BreakEvenAtrTrailPolicy:
    name: str = "be_atr_trail"

    # Base stop and trailing behavior
    stop_atr_mult: float = 1.0
    trail_atr_mult: float = 1.25
    profit_target_atr_mult: Optional[float] = None

    # Break-even / stop-in-profit behavior
    # Existing: arm after move of ATR * be_trigger_atr_mult
    be_trigger_atr_mult: float = 0.8

    # ✅ New: arm after move of N ticks (if set, overrides ATR trigger)
    be_trigger_ticks: Optional[float] = None

    # Where to set stop once armed:
    # 0 => break-even, positive => stop in profit, negative => stop still in loss (rare)
    be_offset_ticks: float = 0.0

@dataclass(frozen=True)
class ChandelierAtrTrailPolicy:
    """
    Chandelier ATR trail:
      - Initial stop at entry +/- stop_atr_mult * ATR(entry)
      - Trail stop = watermark -/+ trail_atr_mult * ATR(entry) (watermark = best favorable price)
      - Optional profit target (ATR-based)
    """
    name: str = "chandelier_atr_trail"
    stop_atr_mult: float = 1.0
    trail_atr_mult: float = 1.75
    profit_target_atr_mult: Optional[float] = None

@dataclass(frozen=True)
class TimeStopNoProgressPolicy:
    """
    Time stop if trade doesn't show enough progress.
      - If minutes_in_trade >= max_minutes AND best_favorable_ticks < min_mfe_ticks => exit (market)
    """
    name: str = "time_stop_no_progress"
    max_minutes: int = 8
    min_mfe_ticks: float = 6.0

@dataclass(frozen=True)
class GivebackAfterMfePolicy:
    """
    Giveback stop:
      - Once MFE >= arm_mfe_ticks, exit if giveback >= giveback_ticks from the peak.
    This directly attacks 'winner turns into loser' behavior.
    """
    name: str = "giveback_after_mfe"
    arm_mfe_ticks: float = 18.0
    giveback_ticks: float = 10.0

