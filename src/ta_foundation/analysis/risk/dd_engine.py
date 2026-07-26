"""Trailing-drawdown state machine — the core, firm-agnostic risk math (Phase 2b).

Implements both APEX-style drawdown models (``docs/designs/account_risk_engine.md``)
selected by the firm profile's ``drawdown_type``:

* **intraday_trailing** — ``peak_balance`` tracks the highest intraday *equity*
  (incl. open PnL); ``threshold = peak - max_drawdown`` trails up; once
  ``peak >= start + max_drawdown + lock_buffer`` the threshold **locks** at
  ``start + lock_buffer`` forever. Violation = equity touches the threshold.
* **eod_trailing** — threshold recalculated once per session close from the
  *closing realized* balance (``includes_unrealized = false``); enforced intraday
  next session; carries a separate **daily-loss limit**.

The engine only mutates ``AccountState`` (``on_value`` per intraday update,
``on_session_close`` once per day). All derived numbers — cushion, distance to
lock, daily-loss-limit remaining, progress to target, and the **daily risk
budget** the allocator consumes — come from the pure ``readout`` function so the
whole thing is table-testable. Nothing here is trusted live until it replays a
real account history exactly (the Phase-2 gate).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .account_state import AccountState, AccountType, FirmProfile

_EPS = 1e-9


@dataclass(frozen=True)
class RiskReadout:
    """Everything the allocator / UI needs for one account at one instant."""

    threshold: float
    peak_balance: float
    locked: bool
    violated: bool
    remaining_cushion: float          # current equity - threshold (room to lose)
    daily_risk_budget: float          # cushion - safety margin (>= 0), capped by DLL if any
    distance_to_lock: Optional[float] = None          # intraday only
    daily_loss_limit_remaining: Optional[float] = None  # eod (DLL) only
    progress_to_target: Optional[float] = None        # evaluation accounts only


class DdEngine:
    """Advances ``AccountState`` per a ``FirmProfile`` and reports risk readouts."""

    def __init__(self, profile: FirmProfile) -> None:
        self.profile = profile

    # ---- lifecycle -----------------------------------------------------
    def init_account(
        self,
        *,
        starting_balance: float,
        account_type: AccountType,
        account_size: float | int | str,
    ) -> AccountState:
        rules = self.profile.size_rules(account_size)
        return AccountState(
            starting_balance=float(starting_balance),
            account_type=account_type,
            account_size=str(int(float(account_size))),
            peak_balance=float(starting_balance),
            current_threshold=float(starting_balance) - rules.max_drawdown,
            last_value=float(starting_balance),
        )

    # ---- internals -----------------------------------------------------
    def _max_dd(self, state: AccountState) -> float:
        return self.profile.size_rules(state.account_size).max_drawdown

    def _apply_trail(self, state: AccountState) -> None:
        """Trail the threshold up from ``peak_balance``; lock at start+buffer."""
        max_dd = self._max_dd(state)
        lock_level = state.starting_balance + self.profile.lock_buffer
        if state.peak_balance + _EPS >= state.starting_balance + max_dd + self.profile.lock_buffer:
            state.locked = True
        if state.locked:
            state.current_threshold = lock_level
        else:
            # monotonic up (peak only rises, but max() is defensive)
            state.current_threshold = max(state.current_threshold, state.peak_balance - max_dd)

    # ---- updates -------------------------------------------------------
    def on_value(self, state: AccountState, account_value: float) -> RiskReadout:
        """Intraday equity update. For intraday_trailing this raises the peak and
        trails/locks the threshold; for eod_trailing the threshold is fixed
        (set at the prior close) and this only checks violation. Both enforce
        violation against ``current_threshold`` on live equity."""
        v = float(account_value)
        state.last_value = v
        if self.profile.drawdown_type == "intraday_trailing":
            state.peak_balance = max(state.peak_balance, v)
            self._apply_trail(state)
        if v <= state.current_threshold + _EPS:
            state.violated = True
        return self.readout(state)

    def add_realized(self, state: AccountState, pnl: float) -> None:
        """Accumulate realized P&L for the current session (drives the DLL)."""
        state.realized_today += float(pnl)

    def on_session_close(self, state: AccountState, closing_balance: float) -> RiskReadout:
        """End-of-session recalculation. For eod_trailing the threshold trails off
        the highest *closing* balance (and locks the same way). Resets the daily
        realized tally. For intraday_trailing the threshold already trailed live;
        this just books the close and resets the day."""
        c = float(closing_balance)
        state.last_value = c
        if self.profile.drawdown_type == "eod_trailing":
            state.peak_balance = max(state.peak_balance, c)
            self._apply_trail(state)
        state.realized_today = 0.0
        return self.readout(state)

    # ---- derived numbers (pure) ---------------------------------------
    def readout(self, state: AccountState, *, safety_margin: float = 0.0) -> RiskReadout:
        rules = self.profile.size_rules(state.account_size)
        max_dd = rules.max_drawdown
        cushion = state.last_value - state.current_threshold

        distance_to_lock = None
        if self.profile.drawdown_type == "intraday_trailing" and not state.locked:
            lock_peak = state.starting_balance + max_dd + self.profile.lock_buffer
            distance_to_lock = max(0.0, lock_peak - state.peak_balance)

        dll_remaining = None
        if self.profile.daily_loss_limit is not None:
            # realized_today is negative when losing; remaining room = limit + losses
            dll_remaining = max(0.0, self.profile.daily_loss_limit + min(0.0, state.realized_today))

        progress = None
        if state.account_type == "evaluation" and rules.profit_target > 0:
            progress = (state.last_value - state.starting_balance) / rules.profit_target

        budget = max(0.0, cushion - safety_margin)
        if dll_remaining is not None:
            budget = min(budget, dll_remaining)

        return RiskReadout(
            threshold=state.current_threshold,
            peak_balance=state.peak_balance,
            locked=state.locked,
            violated=state.violated,
            remaining_cushion=cushion,
            daily_risk_budget=budget,
            distance_to_lock=distance_to_lock,
            daily_loss_limit_remaining=dll_remaining,
            progress_to_target=progress,
        )
