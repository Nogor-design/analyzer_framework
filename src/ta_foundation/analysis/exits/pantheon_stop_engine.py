"""Python mirror of ``strategies/shared/PantheonStopEngine.cs``.

This is a 1:1 port of the pure C# stop/trail math so the live NinjaTrader path
(PantheonMaster + the AddOn harness) and the fast Python validation battery
exercise the *same* logic. Every function maps to a static method in
``PantheonStopEngine``; keep the two in lockstep — if you change a branch here,
change it there (and vice versa).

The companion driver/battery lives in ``pantheon_trail_battery.py``; the original
AtrTrail-only vectorized replica in ``nt_atr_trail_parity.py`` remains the
backtest-parity reference and is cross-checked against this engine in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

LONG = 1
SHORT = -1


class PantheonStopPolicy(str, Enum):
    FIXED_RR = "FixedRR"
    ATR_TRAIL = "AtrTrail"
    CHANDELIER = "Chandelier"
    GIVEBACK = "Giveback"
    FIXED_STOP = "FixedStop"
    BREAK_EVEN_ONLY = "BreakEvenOnly"


@dataclass(frozen=True)
class PantheonStopConfig:
    """Mirror of the C# ``PantheonStopConfig`` (= the strategy's Discovery Exit params)."""
    policy: PantheonStopPolicy
    tick_size: float = 0.25
    point_value: float = 20.0          # NQ
    stop_ticks: int = 60
    target_ticks: int = 90
    atr_trail_multiple: float = 2.0
    chandelier_lookback: int = 20
    giveback_pct: float = 0.40
    breakeven_trigger_ticks: int = 30
    breakeven_plus_ticks: int = 4


@dataclass(frozen=True)
class TrailResult:
    has_proposal: bool = False
    proposed_stop: float = 0.0
    breakeven_activated: bool = False


def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0.0:
        return price
    return round(price / tick_size) * tick_size


def initial_stop(direction: int, entry_price: float, cfg: PantheonStopConfig) -> float:
    """Mirror of ``InitialStop`` — StopTicks below entry (long) / above (short)."""
    return round_to_tick(entry_price - direction * cfg.stop_ticks * cfg.tick_size, cfg.tick_size)


def uses_target(cfg: PantheonStopConfig) -> bool:
    """Only FixedRR carries a target (``UsesTarget`` / ShouldUseTarget)."""
    return cfg.policy == PantheonStopPolicy.FIXED_RR


def initial_target(direction: int, entry_price: float, cfg: PantheonStopConfig) -> float:
    return round_to_tick(entry_price + direction * cfg.target_ticks * cfg.tick_size, cfg.tick_size)


def improves(direction: int, proposed_stop: float, last_stop: float, tick_size: float) -> bool:
    """Ratchet test — long must rise > 0.5 tick, short must fall the same (``Improves``)."""
    if direction == LONG:
        return proposed_stop > last_stop + tick_size * 0.5
    return proposed_stop < last_stop - tick_size * 0.5


def open_profit_currency(direction: int, price: float, entry_price: float,
                         point_value: float, quantity: int) -> float:
    if quantity <= 0:
        return 0.0
    points = price - entry_price if direction == LONG else entry_price - price
    return points * point_value * quantity


def stop_price_from_protected_profit(direction: int, entry_price: float,
                                     protected_profit_currency: float,
                                     point_value: float, quantity: int) -> float:
    """Mirror of ``StopPriceFromProtectedProfit``."""
    denom = point_value * max(1, quantity)
    protected_points = 0.0 if denom == 0.0 else protected_profit_currency / denom
    return entry_price + protected_points if direction == LONG else entry_price - protected_points


def compute_trail_stop(
    direction: int,
    entry_price: float,
    trigger_price: float,
    favorable_extreme: float,
    current_atr: float,
    peak_open_profit_currency: float,
    quantity: int,
    chandelier_ref_price: float,
    breakeven_active: bool,
    cfg: PantheonStopConfig,
) -> TrailResult:
    """1:1 port of ``PantheonStopEngine.ComputeTrailStop`` — the per-policy switch
    from PantheonMaster.ManageLiveDynamicStop. Pure: the caller threads the running
    state and applies the ratchet + side-of-market guard before submitting."""
    tick = cfg.tick_size
    policy = cfg.policy

    if policy == PantheonStopPolicy.BREAK_EVEN_ONLY:
        if breakeven_active:
            return TrailResult()
        trigger_level = entry_price + direction * cfg.breakeven_trigger_ticks * tick
        armed = trigger_price >= trigger_level if direction == LONG else trigger_price <= trigger_level
        if armed:
            return TrailResult(
                has_proposal=True,
                proposed_stop=round_to_tick(entry_price + direction * cfg.breakeven_plus_ticks * tick, tick),
                breakeven_activated=True,
            )
        return TrailResult()

    if policy == PantheonStopPolicy.ATR_TRAIL:
        proposed = favorable_extreme - direction * cfg.atr_trail_multiple * current_atr
        return TrailResult(has_proposal=True, proposed_stop=round_to_tick(proposed, tick))

    if policy == PantheonStopPolicy.CHANDELIER:
        ref_price = (max(chandelier_ref_price, favorable_extreme) if direction == LONG
                     else min(chandelier_ref_price, favorable_extreme))
        proposed = ref_price - direction * cfg.atr_trail_multiple * current_atr
        return TrailResult(has_proposal=True, proposed_stop=round_to_tick(proposed, tick))

    if policy == PantheonStopPolicy.GIVEBACK:
        if peak_open_profit_currency > 0.0:
            protected_profit = peak_open_profit_currency * (1.0 - cfg.giveback_pct)
            proposed = stop_price_from_protected_profit(
                direction, entry_price, protected_profit, cfg.point_value, quantity)
            return TrailResult(has_proposal=True, proposed_stop=round_to_tick(proposed, tick))
        return TrailResult()

    # FixedRR / FixedStop: the initial stop never trails.
    return TrailResult()
