from __future__ import annotations

"""
Instrument registry for the Discovery web UI.

Lookups in this module are the single source of truth for:
- tick_size and tick_value (dollar value per tick, per contract)
- regular trading hours (RTH) in America/Denver, used as the default
  session_filter when generating discovery YAML
- ATR period and default contract format hints used in form defaults

NOTE: The hand-written YAMLs in discovery/*.yaml currently set
`tick_value: 12.50` for NQ. That is the ES tick value — NQ is $5.00
per tick (1 point = $20 / 4 ticks). The Discovery UI uses the values
defined here, so generated YAML will be correct. The legacy YAMLs are
not modified by this module; fixing them is a separate, user-confirmed
step (see docs/designs/discovery_web_ui.md).
"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TradingSession:
    """Regular trading hours window in America/Denver (Mountain) time.

    Mirrors the `session_filter` shape used by every discovery YAML:
        hour_from / minute_from = inclusive start
        hour_to                 = exclusive end (top of the hour)
    """

    hour_from: int
    minute_from: int
    hour_to: int
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    exchange: str
    tick_size: float
    tick_value: float          # USD per tick, per contract
    point_value: float         # USD per 1.0 point
    rth: TradingSession
    atr_period: int = 14
    default_contract_hint: str = ""   # e.g. "H25" — for UI placeholder only
    notes: str = ""
    is_custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rth"] = self.rth.to_dict()
        return d


# ---------------------------------------------------------------------------
# Canonical session presets
# ---------------------------------------------------------------------------

# CME equity-index RTH (08:30 Chicago = 07:30 Denver MDT, ends 15:00 Chicago)
_RTH_EQUITY_INDEX = TradingSession(
    hour_from=7, minute_from=30, hour_to=14,
    label="RTH 07:30–14:15 Denver (CME equity index)",
)

# CME crude oil pit hours (mapped to Denver MDT)
_RTH_CRUDE = TradingSession(
    hour_from=7, minute_from=0, hour_to=12,
    label="RTH 07:00–12:30 Denver (CME crude)",
)

# COMEX gold pit hours (Denver MDT)
_RTH_GOLD = TradingSession(
    hour_from=6, minute_from=20, hour_to=11,
    label="RTH 06:20–11:30 Denver (COMEX metals)",
)

# CME FX pit (Denver MDT)
_RTH_FX = TradingSession(
    hour_from=6, minute_from=0, hour_to=14,
    label="RTH 06:00–14:00 Denver (CME FX)",
)

# Henry Hub natural gas (NYMEX, Denver MDT)
_RTH_NATGAS = TradingSession(
    hour_from=7, minute_from=0, hour_to=12,
    label="RTH 07:00–12:30 Denver (NYMEX nat gas)",
)


# ---------------------------------------------------------------------------
# Canonical instrument registry
#
# Tick values cross-checked against
# src/ta_foundation/reports/html/sections/apex_drawdown_survival_profile.py,
# which is the existing source of truth in this codebase.
# ---------------------------------------------------------------------------

_CANONICAL: tuple[Instrument, ...] = (
    Instrument(
        symbol="NQ",
        name="E-mini Nasdaq-100",
        exchange="CME",
        tick_size=0.25,
        tick_value=5.00,
        point_value=20.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1 point = $20. Most actively traded equity-index contract.",
    ),
    Instrument(
        symbol="MNQ",
        name="Micro E-mini Nasdaq-100",
        exchange="CME",
        tick_size=0.25,
        tick_value=0.50,
        point_value=2.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1/10 the size of NQ. Useful for small accounts and prop evals.",
    ),
    Instrument(
        symbol="ES",
        name="E-mini S&P 500",
        exchange="CME",
        tick_size=0.25,
        tick_value=12.50,
        point_value=50.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1 point = $50. Highest volume of any equity-index future.",
    ),
    Instrument(
        symbol="MES",
        name="Micro E-mini S&P 500",
        exchange="CME",
        tick_size=0.25,
        tick_value=1.25,
        point_value=5.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1/10 the size of ES.",
    ),
    Instrument(
        symbol="YM",
        name="E-mini Dow",
        exchange="CBOT",
        tick_size=1.0,
        tick_value=5.00,
        point_value=5.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1 tick = 1 point = $5.",
    ),
    Instrument(
        symbol="RTY",
        name="E-mini Russell 2000",
        exchange="CME",
        tick_size=0.10,
        tick_value=5.00,
        point_value=50.00,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
        notes="1 point = $50.",
    ),
    Instrument(
        symbol="M2K",
        name="Micro E-mini Russell 2000",
        exchange="CME",
        tick_size=0.10,
        tick_value=1.25,
        point_value=12.50,
        rth=_RTH_EQUITY_INDEX,
        default_contract_hint="H25",
    ),
    Instrument(
        symbol="CL",
        name="Crude Oil",
        exchange="NYMEX",
        tick_size=0.01,
        tick_value=10.00,
        point_value=1000.00,
        rth=_RTH_CRUDE,
        default_contract_hint="K25",
        notes="1 tick = 1¢ = $10.",
    ),
    Instrument(
        symbol="GC",
        name="Gold",
        exchange="COMEX",
        tick_size=0.10,
        tick_value=10.00,
        point_value=100.00,
        rth=_RTH_GOLD,
        default_contract_hint="J25",
    ),
    Instrument(
        symbol="MGC",
        name="Micro Gold",
        exchange="COMEX",
        tick_size=0.10,
        tick_value=1.00,
        point_value=10.00,
        rth=_RTH_GOLD,
        default_contract_hint="J25",
    ),
    Instrument(
        symbol="NG",
        name="Henry Hub Natural Gas",
        exchange="NYMEX",
        tick_size=0.001,
        tick_value=10.00,
        point_value=10000.00,
        rth=_RTH_NATGAS,
        default_contract_hint="K25",
    ),
    Instrument(
        symbol="6E",
        name="Euro FX",
        exchange="CME",
        tick_size=0.00005,
        tick_value=6.25,
        point_value=125000.00,
        rth=_RTH_FX,
        default_contract_hint="H25",
    ),
)


_BY_SYMBOL: dict[str, Instrument] = {inst.symbol.upper(): inst for inst in _CANONICAL}
_CUSTOM: dict[str, Instrument] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_instruments() -> list[dict[str, Any]]:
    """Return all known instruments (canonical first, then registered customs).

    Each entry is JSON-safe and shaped for direct consumption by the
    Discovery UI's instrument picker.
    """
    out: list[dict[str, Any]] = [inst.to_dict() for inst in _CANONICAL]
    for inst in _CUSTOM.values():
        out.append(inst.to_dict())
    return out


def get_instrument(symbol: str) -> Instrument | None:
    """Look up an instrument by symbol. Case-insensitive. Returns None if unknown."""
    if not symbol:
        return None
    key = str(symbol).strip().upper()
    if not key:
        return None
    if key in _BY_SYMBOL:
        return _BY_SYMBOL[key]
    return _CUSTOM.get(key)


def register_custom_instrument(
    symbol: str,
    name: str,
    *,
    tick_size: float,
    tick_value: float,
    point_value: float | None = None,
    rth: TradingSession | None = None,
    exchange: str = "custom",
    atr_period: int = 14,
    default_contract_hint: str = "",
    notes: str = "",
) -> Instrument:
    """Register a user-defined instrument. Overwrites if the symbol is already custom.

    Cannot overwrite a canonical entry — raises ValueError to keep canonical
    values authoritative.
    """
    if not symbol or not str(symbol).strip():
        raise ValueError("symbol is required")
    key = str(symbol).strip().upper()
    if key in _BY_SYMBOL:
        raise ValueError(
            f"{key} is a canonical instrument; cannot overwrite via custom registration"
        )
    if tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    if tick_value <= 0:
        raise ValueError("tick_value must be > 0")
    if point_value is None:
        # Derive: how many ticks make up a point of 1.0
        point_value = tick_value / tick_size
    if rth is None:
        rth = _RTH_EQUITY_INDEX
    inst = Instrument(
        symbol=key,
        name=name or key,
        exchange=exchange,
        tick_size=float(tick_size),
        tick_value=float(tick_value),
        point_value=float(point_value),
        rth=rth,
        atr_period=int(atr_period),
        default_contract_hint=str(default_contract_hint or ""),
        notes=str(notes or ""),
        is_custom=True,
    )
    _CUSTOM[key] = inst
    return inst


def clear_custom_instruments() -> None:
    """Remove all registered custom instruments. Mostly for tests."""
    _CUSTOM.clear()


def default_instrument() -> Instrument:
    """Return the UI's default instrument. NQ — most heavily used in this codebase."""
    return _BY_SYMBOL["NQ"]
