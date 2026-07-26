"""Account-survival simulation — does a prop account survive trailing drawdown
while trading our backtest lineups? (Phase 2 validation, the ``❌`` link.)

The DD engine (``dd_engine.py``) is firm-agnostic math; the allocator
(``allocator.py``) sizes ONE static day. Neither plays a *sequence* of real trades
through the trailing threshold over time. This module does: it reconstructs a
conservative **intra-trade equity path** from each trade's realised P&L + MAE/MFE
and feeds it to ``DdEngine.on_value`` in time order, so an APEX-style intraday
trail can blow the account mid-trade exactly as it would live.

Intra-trade ordering (documented, deliberate): within each trade we test the
**adverse** excursion (``-mae``) FIRST, against the trailing threshold the account
carried *into* the trade, then ratchet the peak with the **favorable** excursion
(``+mfe``) so it trails/locks the threshold for *subsequent* trades, then book the
realized close. This models the dominant real path — drawdown-then-recovery, where
the dip is judged against the threshold in force when it happens. (Feeding MFE
first was rejected: it locks the threshold up and then flags the same trade's
*earlier* entry-level dip as a death — a false violation; e.g. a +$2,700 winner
with mae=0 would "die" at its own entry price.) The one case this is optimistic
about — a single trade spiking up to lock the threshold and then crashing below the
new locked floor before exit — is partly recaught by the realized-close check.

It does NOT model multiple *simultaneously open* positions summing their unrealized
drawdown — acceptable because a $2,500-DD APEX account can afford only a contract or
two (overlap is rare in the real plan); a future stress mode can sum concurrent
excursions. Nothing here is trusted live until it replays a real account history
exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from .account_state import AccountType, FirmProfile
from .dd_engine import DdEngine
from .trade_loader import Trade


@dataclass(frozen=True)
class SurvivalResult:
    """Outcome of playing a trade sequence through one account's trailing DD."""

    survived: bool                      # never touched the trailing threshold (incl. passed evals)
    passed: bool                        # realized balance reached the profit target (challenge cleared)
    n_trades: int
    n_trades_taken: int                 # trades actually played before a violation/pass halted trading
    starting_balance: float
    final_equity: float
    peak_equity: float
    min_cushion: float                  # smallest (equity - threshold) seen (>= 0 if survived)
    max_equity_drawdown: float          # deepest peak-to-trough of equity (<= 0)
    reached_target: bool
    locked: bool                        # trailing threshold reached its frozen floor
    violated_dt: Optional[datetime] = None
    reached_target_dt: Optional[datetime] = None
    dollar_scale: float = 1.0           # 1.0 = NQ as backtested; 0.1 = MNQ
    contracts: int = 1
    note: str = ""


def simulate_survival(
    trades: Sequence[Trade],
    *,
    profile: FirmProfile,
    account_type: AccountType,
    account_size: str,
    starting_balance: float,
    contracts: int = 1,
    dollar_scale: float = 1.0,
    halt_on_violation: bool = True,
    stop_at_target: bool = True,
) -> SurvivalResult:
    """Play ``trades`` (per-1-contract $) through one account's trailing DD.

    ``dollar_scale`` rescales each trade's $ figures for a different instrument
    multiplier than the backtest (1.0 = full NQ as backtested, 0.1 = MNQ micro).
    ``contracts`` then multiplies on top. ``halt_on_violation`` stops trading the
    moment the threshold is breached (a blown APEX account can't keep trading) —
    set False to measure how deep the whole sequence would have gone regardless.
    ``stop_at_target`` (default on) stops the moment the *realized* balance reaches
    the profit target: an evaluation account that hits the target has PASSED the
    challenge and stops trading, so a later drawdown is not a death (the funded
    account that follows has its own fresh state). A passed account counts as
    ``survived``.
    """
    eng = DdEngine(profile)
    state = eng.init_account(
        starting_balance=starting_balance,
        account_type=account_type,
        account_size=account_size,
    )
    rules = profile.size_rules(account_size)
    target_equity = starting_balance + rules.profit_target

    mult = float(dollar_scale) * int(contracts)
    ordered = sorted(trades, key=lambda t: (t.entry_dt, t.exit_dt))

    running = float(starting_balance)
    peak_equity = float(starting_balance)
    min_cushion = float("inf")
    max_dd = 0.0
    reached_target = False
    reached_target_dt: Optional[datetime] = None
    violated_dt: Optional[datetime] = None
    passed = False
    n_taken = 0

    def _observe(equity: float, dt: datetime) -> bool:
        """Feed one equity sample; update trackers. Returns True if violated."""
        nonlocal peak_equity, min_cushion, max_dd, reached_target, reached_target_dt
        ro = eng.on_value(state, equity)
        peak_equity = max(peak_equity, equity)
        min_cushion = min(min_cushion, ro.remaining_cushion)
        max_dd = min(max_dd, equity - peak_equity)
        if not reached_target and equity >= target_equity:
            reached_target = True
            reached_target_dt = dt
        return ro.violated

    for t in ordered:
        n_taken += 1
        # Adverse excursion first, judged against the threshold trailed in from prior
        # trades; then the favorable excursion ratchets/locks the peak for the next
        # trades; then book the realized close.
        if _observe(running - t.mae * mult, t.entry_dt):
            violated_dt = t.entry_dt
            if halt_on_violation:
                break
        if _observe(running + t.mfe * mult, t.entry_dt):
            violated_dt = violated_dt or t.entry_dt
            if halt_on_violation:
                break
        running += t.profit * mult
        if _observe(running, t.exit_dt):
            violated_dt = violated_dt or t.exit_dt
            if halt_on_violation:
                break
        # Realized balance cleared the profit target -> challenge passed, stop trading.
        if running >= target_equity:
            passed = True
            if stop_at_target:
                break

    survived = violated_dt is None
    if min_cushion == float("inf"):
        min_cushion = starting_balance - state.current_threshold

    return SurvivalResult(
        survived=survived,
        passed=passed,
        n_trades=len(ordered),
        n_trades_taken=n_taken,
        starting_balance=float(starting_balance),
        final_equity=running,
        peak_equity=peak_equity,
        min_cushion=float(min_cushion),
        max_equity_drawdown=float(max_dd),
        reached_target=reached_target,
        locked=state.locked,
        violated_dt=violated_dt,
        reached_target_dt=reached_target_dt,
        dollar_scale=float(dollar_scale),
        contracts=int(contracts),
        note="" if survived else "trailing drawdown breached",
    )


@dataclass(frozen=True)
class SurvivalSweep:
    """Per-template survival across a candidate pool at a fixed account/size."""

    account_size: str
    dollar_scale: float
    contracts: int
    n_templates: int
    n_survived: int
    n_reached_target: int
    n_passed: int = 0                   # survived AND cleared the profit target
    results: dict[str, SurvivalResult] = field(default_factory=dict)

    @property
    def survival_rate(self) -> float:
        return self.n_survived / self.n_templates if self.n_templates else 0.0


def per_template_survival(
    template_trades: dict[str, list[Trade]],
    *,
    profile: FirmProfile,
    account_type: AccountType,
    account_size: str,
    starting_balance: float,
    contracts: int = 1,
    dollar_scale: float = 1.0,
) -> SurvivalSweep:
    """Simulate EACH template alone (its own fresh account) — which templates are
    even survivable on this firm/size/instrument-scale at ``contracts``?"""
    results: dict[str, SurvivalResult] = {}
    for tid, trades in template_trades.items():
        results[tid] = simulate_survival(
            trades, profile=profile, account_type=account_type,
            account_size=account_size, starting_balance=starting_balance,
            contracts=contracts, dollar_scale=dollar_scale,
        )
    survived = sum(1 for r in results.values() if r.survived)
    hit = sum(1 for r in results.values() if r.reached_target)
    passed = sum(1 for r in results.values() if r.passed and r.survived)
    return SurvivalSweep(
        account_size=account_size, dollar_scale=dollar_scale, contracts=contracts,
        n_templates=len(results), n_survived=survived, n_reached_target=hit,
        n_passed=passed, results=results,
    )


def lineup_survival(
    lineup_trades: Sequence[Trade],
    *,
    profile: FirmProfile,
    account_type: AccountType,
    account_size: str,
    starting_balance: float,
    contracts: int = 1,
    dollar_scale: float = 1.0,
) -> SurvivalResult:
    """Survival of ONE account trading a combined daily lineup (trades from multiple
    chosen templates merged chronologically). The realistic product question."""
    return simulate_survival(
        lineup_trades, profile=profile, account_type=account_type,
        account_size=account_size, starting_balance=starting_balance,
        contracts=contracts, dollar_scale=dollar_scale,
    )
