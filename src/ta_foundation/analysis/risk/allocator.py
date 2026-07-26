"""Allocator (Phase 2c) — selector lineup × per-account risk budget → daily plan.

This is the piece that makes the system *account-aware*: it takes today's selector
lineup (which edges look good) and converts it into a per-account plan (how much
THIS account may trade) given its trailing-drawdown budget and objective. Risk and
selection stay separate (``daily_lineup_selector.md``): the selector ranks edges;
the allocator sizes them per account.

Objective branching (``account_risk_engine.md``):
* **evaluation/challenge** — trade *to pass*: use more of the daily risk budget,
  prefer the highest-expectancy picks (reach the profit target before violation).
* **PA/funded** — trade *to protect*: use a smaller fraction of budget, allocate to
  the *lowest-risk* picks first, tighter daily-loss cap.

Sizing is honest and explicit, not magic: given each pick's *per-contract daily
downside* (estimated from its realised daily P&L — see ``estimate_per_contract_risk``),
the allocator greedily buys contracts until the daily-loss cap is exhausted or
``max_contracts`` is hit. A locked-out / no-cushion account gets an empty plan
("do not trade"). v1 heuristics are config and tunable; this never sizes blind.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .account_state import AccountType, FirmProfile, load_firm_profile
from .dd_engine import DdEngine, RiskReadout


@dataclass(frozen=True)
class AllocatorConfig:
    """Tunable v1 heuristics. Utilization = fraction of the daily risk budget an
    account is willing to put at risk today, by objective."""

    eval_utilization: float = 0.80     # challenge: press the edge to reach target
    pa_utilization: float = 0.40       # funded: protect the cushion
    min_contracts: int = 1             # a pick gets dropped if it can't afford this many


@dataclass(frozen=True)
class Allocation:
    slice_key: str
    template_id: str
    contracts: int
    est_daily_risk: float              # contracts × per-contract downside


@dataclass(frozen=True)
class DailyPlan:
    account_size: str
    account_type: AccountType
    daily_loss_cap: float              # the hard $ cap we impose for the day
    allocations: list[Allocation]
    total_est_risk: float              # sum of allocated est_daily_risk
    skipped: list[str] = field(default_factory=list)   # picks we couldn't afford
    note: str = ""

    @property
    def total_contracts(self) -> int:
        return sum(a.contracts for a in self.allocations)


def estimate_per_contract_risk(daily_pnl_per_contract: Sequence[float], *, floor: float = 1.0) -> float:
    """A robust per-contract daily downside estimate from a template's realised
    daily P&L (already normalised to ONE contract by the caller). Uses the deeper
    of |worst day| and |2× the mean of losing days| so a single fluke day doesn't
    dominate but a fat left tail isn't ignored. Floored so risk is never zero."""
    if not daily_pnl_per_contract:
        return floor
    worst = abs(min(daily_pnl_per_contract))
    losers = [p for p in daily_pnl_per_contract if p < 0]
    typ_loss = 2.0 * abs(sum(losers) / len(losers)) if losers else 0.0
    return max(floor, min(worst, typ_loss) if typ_loss > 0 else worst)


def allocate_account(
    picks: Sequence[tuple[str, str]],          # (slice_key, template_id)
    *,
    readout: RiskReadout,
    account_type: AccountType,
    account_size: str,
    per_contract_risk: dict[str, float],       # template_id -> $ downside per 1 contract
    expected_return: Optional[dict[str, float]] = None,  # template_id -> $/day per contract
    max_contracts: Optional[int] = None,
    config: Optional[AllocatorConfig] = None,
) -> DailyPlan:
    """Size today's ``picks`` for one account. Pure/testable: all account state
    enters via ``readout`` (which carries ``daily_risk_budget`` from the DD engine)."""
    cfg = config or AllocatorConfig()
    expected_return = expected_return or {}

    budget = max(0.0, readout.daily_risk_budget)
    if readout.violated or budget <= 0.0:
        return DailyPlan(account_size, account_type, 0.0, [], 0.0,
                         skipped=[t for _s, t in picks],
                         note="no risk budget (locked-out / violated) — do not trade")

    utilization = cfg.eval_utilization if account_type == "evaluation" else cfg.pa_utilization
    daily_loss_cap = budget * utilization

    # Objective ordering: eval presses highest expectancy first; PA fills safest first.
    if account_type == "evaluation":
        ordered = sorted(picks, key=lambda p: expected_return.get(p[1], 0.0), reverse=True)
    else:
        ordered = sorted(picks, key=lambda p: per_contract_risk.get(p[1], float("inf")))

    remaining = daily_loss_cap
    allocations: list[Allocation] = []
    skipped: list[str] = []
    for slice_key, tid in ordered:
        unit = per_contract_risk.get(tid)
        if not unit or unit <= 0:
            skipped.append(tid)
            continue
        affordable = int(math.floor(remaining / unit))
        if max_contracts is not None:
            affordable = min(affordable, max_contracts)
        if affordable < cfg.min_contracts:
            skipped.append(tid)
            continue
        risk = affordable * unit
        allocations.append(Allocation(slice_key, tid, affordable, risk))
        remaining -= risk

    total = sum(a.est_daily_risk for a in allocations)
    note = "" if allocations else "budget too small for any pick at 1 contract"
    return DailyPlan(account_size, account_type, daily_loss_cap, allocations, total,
                     skipped=skipped, note=note)


@dataclass(frozen=True)
class AccountSpec:
    """One client account for the roster allocator. ``current_value`` is the live
    equity (incl. unrealized) the DD engine trails against today; omit it to start
    flat at ``starting_balance``."""

    name: str
    firm: str
    account_size: str
    account_type: AccountType
    starting_balance: float
    current_value: Optional[float] = None


def allocate_roster(
    picks: Sequence[tuple[str, str]],
    accounts: Sequence[AccountSpec],
    *,
    per_contract_risk: dict[str, float],
    expected_return: Optional[dict[str, float]] = None,
    config: Optional[AllocatorConfig] = None,
    profiles: Optional[dict[str, FirmProfile]] = None,
) -> dict[str, DailyPlan]:
    """Size the SAME selector lineup for every account in a roster, each per its
    own firm profile, drawdown budget, and objective — the 20+-accounts reality.
    Returns ``{account_name: DailyPlan}``. Firm profiles are cached/loaded once.

    Each account's plan differs because the DD engine produces a different
    ``daily_risk_budget`` (size, current cushion, lock state) and the allocator
    branches on eval-vs-PA — so a challenge account presses the edge while a funded
    account of the same size protects, from one shared lineup."""
    cache: dict[str, FirmProfile] = dict(profiles or {})
    out: dict[str, DailyPlan] = {}
    for spec in accounts:
        prof = cache.get(spec.firm) or load_firm_profile(spec.firm)
        cache[spec.firm] = prof
        eng = DdEngine(prof)
        state = eng.init_account(
            starting_balance=spec.starting_balance,
            account_type=spec.account_type, account_size=spec.account_size)
        value = spec.current_value if spec.current_value is not None else spec.starting_balance
        readout = eng.on_value(state, value)
        out[spec.name] = allocate_account(
            picks, readout=readout, account_type=spec.account_type,
            account_size=spec.account_size, per_contract_risk=per_contract_risk,
            expected_return=expected_return,
            max_contracts=prof.size_rules(spec.account_size).max_contracts, config=config)
    return out
