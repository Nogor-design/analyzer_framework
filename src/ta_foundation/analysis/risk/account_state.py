"""Account-risk models — firm profile (config) + live per-account DD state.

Phase 2b of the account-risk layer (``docs/designs/account_risk_engine.md``). The
*math* (``dd_engine.py``) is firm-agnostic; the per-firm numbers live here as
**verified, versioned config** loaded from ``firm_profiles/*.yaml`` so a rule
change is a config bump, never a code change. Nothing here is trusted live until
the engine replays a real account history exactly (the Phase-2 gate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

DrawdownType = Literal["intraday_trailing", "eod_trailing"]
AccountType = Literal["evaluation", "PA"]

_PROFILE_DIR = Path(__file__).parent / "firm_profiles"


@dataclass(frozen=True)
class SizeRules:
    max_drawdown: float
    profit_target: float
    max_contracts: Optional[int] = None


@dataclass(frozen=True)
class FirmProfile:
    """Verified, versioned per-firm rule set (loaded from yaml)."""

    firm: str
    version: str
    drawdown_type: DrawdownType
    includes_unrealized: bool
    lock_buffer: float
    account_sizes: dict[str, SizeRules]
    daily_loss_limit: Optional[float] = None
    eod_recalc_time_et: Optional[str] = None
    consistency_rules: dict[str, Any] = field(default_factory=dict)

    def size_rules(self, account_size: float | int | str) -> SizeRules:
        key = str(int(float(account_size)))
        if key not in self.account_sizes:
            raise KeyError(
                f"{self.firm} {self.version} has no rules for account size {key}; "
                f"known sizes: {sorted(self.account_sizes)}"
            )
        return self.account_sizes[key]


def load_firm_profile(firm: str, *, profile_dir: Path | str | None = None) -> FirmProfile:
    """Load ``<firm>.yaml`` (case-insensitive) into a ``FirmProfile``."""
    d = Path(profile_dir) if profile_dir else _PROFILE_DIR
    path = d / f"{firm.lower()}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no firm profile at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sizes = {
        str(k): SizeRules(
            max_drawdown=float(v["max_drawdown"]),
            profit_target=float(v["profit_target"]),
            max_contracts=(int(v["max_contracts"]) if v.get("max_contracts") is not None else None),
        )
        for k, v in (raw.get("account_sizes") or {}).items()
    }
    dll = raw.get("daily_loss_limit")
    return FirmProfile(
        firm=raw["firm"],
        version=str(raw["version"]),
        drawdown_type=raw["drawdown_type"],
        includes_unrealized=bool(raw["includes_unrealized"]),
        lock_buffer=float(raw["lock_buffer"]),
        account_sizes=sizes,
        daily_loss_limit=(float(dll) if dll is not None else None),
        eod_recalc_time_et=raw.get("eod_recalc_time_et"),
        consistency_rules=raw.get("consistency_rules") or {},
    )


@dataclass
class AccountState:
    """Mutable per-account drawdown state the engine advances.

    ``peak_balance`` is the high-water mark (intraday: account *equity* incl. open
    PnL; eod: highest *closing realized* balance). ``current_threshold`` is the
    live blow-up level; ``locked`` means the trailing threshold has frozen at
    ``starting_balance + lock_buffer`` and never moves again.
    """

    starting_balance: float
    account_type: AccountType
    account_size: str
    peak_balance: float
    current_threshold: float
    last_value: float
    realized_today: float = 0.0
    locked: bool = False
    violated: bool = False
