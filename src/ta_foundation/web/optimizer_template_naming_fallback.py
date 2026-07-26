from __future__ import annotations

"""Fallback template-name decoder for Pantheon final XML templates.

This keeps report naming and portrait lookup working when the external
``template_naming`` package is unavailable. The logic mirrors the
documented naming-guide rules used by the deployment-matrix capability.
"""

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree as ET

from ta_foundation.core.run_name_parser import TICK_VALUE_USD
from ta_foundation.web.optimizer_deployment_matrix import (
    build_name,
    classify_session,
    classify_tier,
    direction_letter,
    phase_word,
)


FALLBACK_NAMING_RULES: dict[str, Any] = {
    "tick_value": 5.0,
    "session_windows": [
        {"session": "London Early", "single": "Rise", "multi": "Rising", "start_minute": 0, "end_minute": 239},
        {"session": "London Late", "single": "Prime", "multi": "Priming", "start_minute": 240, "end_minute": 419},
        {"session": "Pre-Market", "single": "Coil", "multi": "Coiling", "start_minute": 420, "end_minute": 449},
        {"session": "NY Open", "single": "Rage", "multi": "Raging", "start_minute": 450, "end_minute": 479},
        {"session": "Midday", "single": "Drift", "multi": "Drifting", "start_minute": 480, "end_minute": 719},
        {"session": "Power Hour", "single": "Close", "multi": "Closing", "start_minute": 720, "end_minute": 959},
        {"session": "Asia", "single": "Dawn", "multi": "Dawning", "start_minute": 960, "end_minute": 1439},
    ],
    "ma_tiers": [
        {"minimum": 2, "maximum": 75, "god": "Hermes", "monster": "Harpy"},
        {"minimum": 76, "maximum": 125, "god": "Artemis", "monster": "Griffin"},
        {"minimum": 126, "maximum": 175, "god": "Poseidon", "monster": "Medusa"},
        {"minimum": 176, "maximum": 225, "god": "Apollo", "monster": "Hydra"},
        {"minimum": 226, "maximum": 275, "god": "Zeus", "monster": "Chimera"},
        {"minimum": 276, "maximum": 325, "god": "Ares", "monster": "Cerberus"},
        {"minimum": 326, "maximum": 375, "god": "Athena", "monster": "Sphinx"},
        {"minimum": 376, "maximum": 425, "god": "Aphrodite", "monster": "Siren"},
        {"minimum": 426, "maximum": 500, "god": "Dionysus", "monster": "Typhon"},
    ],
    "descriptor_tiers": [
        {"minimum": 100, "maximum": 300, "rr_leq_1": "Serenity", "rr_gt_1": "Balance"},
        {"minimum": 301, "maximum": 600, "rr_leq_1": "Ember", "rr_gt_1": "Bolt"},
        {"minimum": 601, "maximum": 1000, "rr_leq_1": "Cosmos", "rr_gt_1": "Hunter"},
        {"minimum": 1001, "maximum": None, "rr_leq_1": "Fire", "rr_gt_1": "Inferno"},
    ],
}


@dataclass(frozen=True)
class FallbackNamingDecision:
    phase: str
    ma_name: str
    descriptor: str
    direction: str
    compact_name: str
    spaced_name: str
    output_file_name: str
    ma_value: float
    rr_value: float
    per_trade_max_loss: float
    true_max_loss: float
    effective_trades: int
    market: str


def analyze_template_with_fallback(path: Path | str) -> FallbackNamingDecision:
    template = Path(path)
    values = _read_template_values(template)
    market = _market_from_values(values)
    tick_value = TICK_VALUE_USD.get(market, float(FALLBACK_NAMING_RULES["tick_value"]))

    start_h = _int_value(values.get("StartTimeH"), 0)
    start_m = _int_value(values.get("StartTimeM"), 0)
    start_minute = ((start_h * 60) + start_m) % 1440

    average_fast = _float_value(values.get("averageFast"), 5.0)
    average_slow = _float_value(values.get("averageSlow"), average_fast)
    reverse = _bool_value(values.get("Reverse"))
    long_enabled = _bool_value(values.get("Long"), True)
    short_enabled = _bool_value(values.get("Short"), True)
    max_trades = max(1, _int_value(values.get("MaxTrades"), 1))
    profit_stop = _float_value(values.get("ProfitStop"), 0.0)
    loss_stop = _float_value(values.get("LossStop"), 0.0)
    max_stop = _float_value(values.get("MaxStop"), 0.0)
    max_tp_ratio = _float_value(values.get("MaxTPRatio"), 1.0)
    contracts = max(1.0, _float_value(values.get("Contracts"), 1.0))
    use_max_tp = _bool_value(values.get("UseMaxTP"), True)

    per_trade_max_loss = max_stop * tick_value * contracts
    per_trade_profit = 0.0
    if use_max_tp:
        per_trade_profit = int(max_stop * max_tp_ratio) * tick_value * contracts
    effective_trades = _compute_effective_trades(
        max_trades=max_trades,
        profit_stop=profit_stop,
        loss_stop=loss_stop,
        per_trade_profit=per_trade_profit,
        per_trade_loss=per_trade_max_loss,
    )
    true_max_loss = min(loss_stop, per_trade_max_loss * effective_trades) if loss_stop > 0 else (per_trade_max_loss * effective_trades)

    compact_name = build_name(
        start_minute=start_minute,
        average_fast=average_fast,
        average_slow=average_slow,
        reverse=reverse,
        max_trades=max_trades,
        profit_stop=profit_stop,
        loss_stop=loss_stop,
        max_loss=per_trade_max_loss,
        rr=max_tp_ratio,
        long_enabled=long_enabled,
        short_enabled=short_enabled,
        rules=FALLBACK_NAMING_RULES,
    )
    session = classify_session(start_minute, FALLBACK_NAMING_RULES)
    phase = phase_word(
        session,
        "single" if effective_trades <= 1 else "multi",
        FALLBACK_NAMING_RULES,
    )
    tier = classify_tier(average_fast, average_slow, FALLBACK_NAMING_RULES)
    ma_name = str(tier["monster"] if reverse else tier["god"])
    direction = direction_letter(long_enabled, short_enabled)
    descriptor = compact_name.removeprefix(phase).removeprefix(ma_name).removesuffix(direction)
    spaced_name = f"{phase} {ma_name} {descriptor} {direction}".strip()
    output_file_name = f"{compact_name}-{market}.xml" if market else f"{compact_name}.xml"

    return FallbackNamingDecision(
        phase=phase,
        ma_name=ma_name,
        descriptor=descriptor,
        direction=direction,
        compact_name=compact_name,
        spaced_name=spaced_name,
        output_file_name=output_file_name,
        ma_value=max(average_fast, average_slow),
        rr_value=max_tp_ratio,
        per_trade_max_loss=per_trade_max_loss,
        true_max_loss=true_max_loss,
        effective_trades=effective_trades,
        market=market,
    )


def analyze_template_any(path: Path | str) -> Any:
    try:
        from template_naming import analyze_template

        return analyze_template(Path(path))
    except Exception:
        return analyze_template_with_fallback(path)


def analyze_template_dict(path: Path | str) -> dict[str, Any]:
    decision = analyze_template_any(path)
    return {
        "phase": getattr(decision, "phase", None),
        "ma_name": getattr(decision, "ma_name", None),
        "descriptor": getattr(decision, "descriptor", None),
        "direction": getattr(decision, "direction", None),
        "compact_name": getattr(decision, "compact_name", None),
        "spaced_name": getattr(decision, "spaced_name", None),
        "output_file_name": getattr(decision, "output_file_name", None),
        "ma_value": _maybe_float(getattr(decision, "ma_value", None)),
        "rr_value": _maybe_float(getattr(decision, "rr_value", None)),
        "per_trade_max_loss": _maybe_float(getattr(decision, "per_trade_max_loss", None)),
        "true_max_loss": _maybe_float(getattr(decision, "true_max_loss", None)),
        "effective_trades": _int_value(getattr(decision, "effective_trades", None), 0),
    }


def _read_template_values(path: Path) -> dict[str, str]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        text = (element.text or "").strip()
        if tag and text:
            out[tag] = text
    return out


def _market_from_values(values: dict[str, str]) -> str:
    instrument = (
        values.get("InstrumentOrInstrumentList")
        or values.get("Instrument")
        or ""
    ).strip()
    market = instrument.split()[0].upper() if instrument else ""
    return market or "NQ"


def _compute_effective_trades(
    *,
    max_trades: int,
    profit_stop: float,
    loss_stop: float,
    per_trade_profit: float,
    per_trade_loss: float,
) -> int:
    counts = [max(1, int(max_trades))]
    if profit_stop > 0 and per_trade_profit > 0:
        counts.append(max(1, int(-(-profit_stop // per_trade_profit))))
    if loss_stop > 0 and per_trade_loss > 0:
        counts.append(max(1, int(-(-loss_stop // per_trade_loss))))
    return min(counts) if counts else 1


def _int_value(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _maybe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
