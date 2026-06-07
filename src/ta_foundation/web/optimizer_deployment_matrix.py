from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_NAMING_RULES_PATH = Path(r"D:\templateNaming\naming_rules.json")


def load_naming_rules(path: str | Path | None = None) -> dict:
    rules_path = Path(
        path
        if path is not None
        else os.environ.get("TA_NAMING_RULES_PATH", DEFAULT_NAMING_RULES_PATH)
    )
    if not rules_path.exists():
        raise FileNotFoundError(f"Naming rules file not found: {rules_path}")

    try:
        with rules_path.open("r", encoding="utf-8") as handle:
            rules = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Naming rules file is not valid JSON: {rules_path}: {exc}") from exc

    _validate_rules(rules, source=rules_path)
    return rules


def session_timeboxes(rules: dict) -> list[dict]:
    _validate_rules(rules)
    timeboxes: list[dict[str, Any]] = []
    for window in rules["session_windows"]:
        start_minute = int(window["start_minute"])
        end_minute = int(window["end_minute"])
        duration_minutes = end_minute - start_minute + 1
        timeboxes.append(
            {
                "session": window["session"],
                "single": window["single"],
                "multi": window["multi"],
                "start_minute": start_minute,
                "end_minute": end_minute,
                "start_h": start_minute // 60,
                "start_m": start_minute % 60,
                "dur_h": duration_minutes // 60,
                "dur_m": duration_minutes % 60,
            }
        )
    return timeboxes


def tier_slow_values(rules: dict) -> list[int]:
    _validate_rules(rules)
    values: list[int] = []
    for tier in rules["ma_tiers"]:
        minimum = int(tier["minimum"])
        maximum = int(tier["maximum"])
        if maximum == 500:
            values.append(450)
            continue

        midpoint = (minimum + maximum) / 2
        value = int(round(midpoint / 10) * 10)
        if value <= minimum:
            value = minimum + 1
        if value >= maximum:
            value = maximum - 1
        values.append(value)
    return values


def classify_session(start_minute: int, rules: dict) -> str:
    _validate_rules(rules)
    for window in rules["session_windows"]:
        if int(window["start_minute"]) <= start_minute <= int(window["end_minute"]):
            return str(window["session"])
    raise ValueError(f"Start minute does not fall within any session window: {start_minute}")


def classify_tier(average_fast: float, average_slow: float, rules: dict) -> dict:
    _validate_rules(rules)
    ma_value = max(average_fast, average_slow)
    for tier_index, tier in enumerate(rules["ma_tiers"], start=1):
        minimum = tier["minimum"]
        maximum = tier["maximum"]
        if minimum <= ma_value <= maximum:
            return {
                "tier_index": tier_index,
                "minimum": minimum,
                "maximum": maximum,
                "god": tier["god"],
                "monster": tier["monster"],
            }
    raise ValueError(f"MA value does not fall within any tier: {ma_value}")


def classify_single_multi(
    max_trades: int,
    profit_stop: float,
    loss_stop: float,
    *,
    max_stop: float | None = None,
    max_tp_ratio: float | None = None,
    tick_value: float = 5.0,
) -> str:
    """single vs multi for the 252-grid axis.

    Must agree with the canonical name's single/multi: a template is ``single``
    when it can take at most one *effective* trade per session after the
    ProfitStop/LossStop guardrails. When the per-trade bracket inputs
    (``max_stop`` + ``max_tp_ratio``) are available, defer to the same
    effective-trades logic the namer uses (``template_naming``); otherwise fall
    back to the raw-cap rule (MaxTrades==1, or both stops pinned to 1).
    """
    if max_stop is not None and max_tp_ratio is not None and tick_value:
        try:
            import math

            from template_naming.core import compute_effective_trades

            per_trade_loss = float(max_stop) * float(tick_value)
            per_trade_profit = math.floor(float(max_stop) * float(max_tp_ratio)) * float(tick_value)
            effective = compute_effective_trades(
                max_trades=int(max_trades),
                per_trade_profit=per_trade_profit,
                per_trade_loss=per_trade_loss,
                profit_stop=float(profit_stop),
                loss_stop=float(loss_stop),
            )
            return "single" if effective <= 1 else "multi"
        except Exception:
            pass
    if max_trades == 1 or (profit_stop == 1 and loss_stop == 1):
        return "single"
    return "multi"


def classify_descriptor(max_loss: float, rr: float, rules: dict) -> str:
    _validate_rules(rules)
    for tier in rules["descriptor_tiers"]:
        minimum = tier["minimum"]
        maximum = tier["maximum"]
        if max_loss >= minimum and (maximum is None or max_loss <= maximum):
            return str(tier["rr_gt_1"] if rr > 1 else tier["rr_leq_1"])
    raise ValueError(f"Max loss does not fall within any descriptor tier: {max_loss}")


def phase_word(session: str, single_multi: str, rules: dict) -> str:
    _validate_rules(rules)
    if single_multi not in {"single", "multi"}:
        raise ValueError(f"single_multi must be 'single' or 'multi': {single_multi}")
    for window in rules["session_windows"]:
        if window["session"] == session:
            return str(window[single_multi])
    raise ValueError(f"Unknown session: {session}")


def direction_letter(long_enabled: bool, short_enabled: bool) -> str:
    if long_enabled and short_enabled:
        return "B"
    if long_enabled:
        return "L"
    if short_enabled:
        return "S"
    raise ValueError("At least one of long_enabled or short_enabled must be true")


def enumerate_cells(rules: dict) -> list[dict]:
    _validate_rules(rules)
    cells: list[dict[str, Any]] = []
    for window in rules["session_windows"]:
        for single_multi in ("single", "multi"):
            for tier_index, _tier in enumerate(rules["ma_tiers"], start=1):
                for side in ("god", "monster"):
                    cells.append(
                        {
                            "session": window["session"],
                            "single_multi": single_multi,
                            "tier_index": tier_index,
                            "side": side,
                        }
                )
    return cells


def build_deployment_matrix_recipe(
    *,
    strategy_id: str,
    recipe_name: str,
    rules: dict,
    average_fast: int | float = 5,
    max_stop: tuple[int | float, int | float, int | float] = (50, 350, 50),
    max_tp_ratio: tuple[int | float, int | float, int | float] = (0.5, 2.0, 0.5),
    profit_stop: tuple[int | float, int | float, int | float] = (1, 1001, 500),
    loss_stop: tuple[int | float, int | float, int | float] = (1, 1001, 500),
    max_trades: tuple[int | float, ...] = (1, 3, 5, 10),
    refine_selection_min_trades: int = 0,
    fast_param: str = "averageFast",
    slow_param: str = "averageSlow",
    pinned_strategy_params: dict[str, Any] | None = None,
) -> dict:
    _validate_rules(rules)
    safe_name = "".join(ch.lower() if ch.isalnum() else "_" for ch in recipe_name).strip("_")
    max_trades_values = sorted({1, *(int(value) for value in max_trades)})
    max_trades_min = max_trades_values[0] if max_trades_values else 1
    max_trades_max = max_trades_values[-1] if max_trades_values else 1
    max_trades_step = (
        max(1, max_trades_values[1] - max_trades_values[0])
        if len(max_trades_values) > 1
        else 1
    )

    session_values = [
        {
            "StartTimeH": timebox["start_h"],
            "StartTimeM": timebox["start_m"],
            "DurationTimeH": timebox["dur_h"],
            "DurationTimeM": timebox["dur_m"],
        }
        for timebox in session_timeboxes(rules)
    ]
    slow_values = tier_slow_values(rules)
    # Promote, per cell, the best-PF candidate that ALSO clears a trade floor.
    # Top-PF-only selection promotes high-PF/low-trade candidates that then fail
    # the final min_trades / %-days gates -> empty pool. Measured on a real run
    # (opt_91711cf3671c): PF vs trade-count correlate -0.74, so PF-first selection
    # is effectively a fewest-trades selector. A min_trades floor at selection time
    # mirrors the operator's "make sure it has enough trades before promoting"
    # heuristic. Applied to BOTH stage_1 (structural) and refine_risk selection so
    # a noise winner cannot survive either round. Off by default (0); the launcher
    # ships a default of 10. See docs/designs/deployment_matrix_optimization_redesign.md.
    selection_hard_filters = (
        {"hard_filters": {"min_trades": int(refine_selection_min_trades)}}
        if int(refine_selection_min_trades) > 0
        else {}
    )
    structural_pins = [
        "StartTimeH",
        "StartTimeM",
        "DurationTimeH",
        "DurationTimeM",
        slow_param,
        fast_param,
        "MaxStop",
        "MaxTPRatio",
        "Long",
        "Short",
        "Reverse",
    ]
    # Extra strategy params pinned (never swept) — e.g. PantheonMaster's regime
    # filter + exit policy. Enums/strings land in the <Strategy> section; numerics
    # also pin their OptimizationParameter. Pinning (not sweeping) keeps Stage 1
    # combinatorics bounded for the 59-param advanced strategy.
    extra_pins = dict(pinned_strategy_params or {})
    extra_pin_entries = [
        {"param": name, "role": "fixed", "value": value}
        for name, value in extra_pins.items()
    ]
    structural_pins.extend(extra_pins.keys())

    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": f"rec_{safe_name or 'deployment_matrix'}",
        "recipe_name": recipe_name,
        "strategy_id": strategy_id,
        "entries_per_direction": 1,
        "target_final_candidates": 252,
        "safety_caps": {
            "max_total_combinations": 250000,
            "max_templates_per_stage": 250,
        },
        "base_matrix": [
            {"param": "Session", "role": "matrix_bundle_axis", "values": session_values},
            {"param": "Reverse", "role": "matrix_axis", "values": [False, True]},
            {"param": slow_param, "role": "matrix_axis", "values": slow_values},
            {"param": fast_param, "role": "fixed", "value": average_fast},
            # Pin the trend filter OFF. The Pantheon seed defaults UseTrend=true,
            # which silently runs the whole grid trend-on -> most lanes take too
            # few trades and every survivor violates the UseTrend=false settings
            # contract. (See the 2026-06-04 full run: 4/252 covered, all rejected.)
            {"param": "UseTrend", "role": "fixed", "value": False},
            {"param": "UseTrendReverse", "role": "fixed", "value": False},
            *extra_pin_entries,
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Deployment matrix broad structural search",
                "optimize_inside_template": {
                    "MaxStop": _range_payload(max_stop),
                    "MaxTPRatio": _range_payload(max_tp_ratio),
                },
                "add_optimize": {
                    "Long": [False, True],
                    "Short": [False, True],
                },
                "selection": {
                    "mode": "coverage_matrix_sequence",
                    "group_by": ["StartTimeH", "StartTimeM", "Reverse", slow_param],
                    "coverage_grid": {
                        "StartTimeH": [value["StartTimeH"] for value in session_values],
                        "StartTimeM": [value["StartTimeM"] for value in session_values],
                        "Reverse": [False, True],
                        slow_param: slow_values,
                    },
                    "keep_per_group": 1,
                    "fitness_metrics": ["profit_factor", "total_net_profit"],
                    **selection_hard_filters,
                },
            },
            {
                "stage_id": "refine_risk",
                "stage_type": "optimizer",
                "from": "stage_1.selected_rows",
                "description": "Refine deployment risk knobs per structural winner",
                "pin": structural_pins,
                "optimize_inside_template": {
                    "ProfitStop": _range_payload(profit_stop),
                    "LossStop": _range_payload(loss_stop),
                    "MaxTrades": {
                        "min": max_trades_min,
                        "max": max_trades_max,
                        "step": max_trades_step,
                    },
                },
                "selection": {
                    "group_by": ["parent_candidate_id", "single_multi"],
                    "keep_per_group": 1,
                    "fitness_metrics": ["profit_factor", "total_net_profit"],
                    "retain_parent_if_child_worse": True,
                    **selection_hard_filters,
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "refine_risk.selected_rows",
                "finalists_per_bucket": 1,
                "description": "Final fixed Backtest validation",
            },
        ],
        "optimizer_type": "Default",
        "keep_best_results": 1000,
        "active_targets": ["MaxProfitFactor", "MaxNetProfit"],
    }


def pantheonmaster_recipe_overrides(
    *,
    exit_policy: str = "AtrTrail",
    atr_trail_multiple: float = 2.0,
) -> dict[str, Any]:
    """``build_deployment_matrix_recipe`` kwargs to drive the advanced
    PantheonMaster strategy through the deployment matrix.

    PantheonMaster shares the risk + session param names the pipeline keys on, so
    only the MA params differ (``FastPeriod``/``SlowPeriod``) and its two new
    dimensions — regime filter + selectable exit — must be **pinned, never swept**
    (59 params would explode Stage 1).

    **Relaxed regime pin (2026-06-07).** The first pass pinned
    ``RegimeMode=TrendingOnly`` + ``UseTrendAlignment=True``; the head-to-head
    (opt_3d12659e4be8) showed that pin is too restrictive — it filtered out most
    session×tier cells (only 16 finals / 5 passed vs the MA-cross 183/116). Worse,
    it left *two* variables between PantheonMaster and the MA-cross pool (regime
    AND exit), so a result could not be attributed to the exit alone.

    This now pins ``EnableDiscoveryFilters=False``, which short-circuits the whole
    entry-filter block (regime + named-session double-gate + trend-alignment +
    atr-pct + ema-confirm — see ``DiscoveryEntryAllowed``), giving **plain
    MA-cross-parity entry and full 252-cell coverage**, while keeping
    ``UseDiscoveryExitPolicy`` on. That isolates **the exit policy as the single
    variable** vs the MA-cross bracket pool — the clean "does a smart exit beat
    the plain bracket" experiment. Regime specialization is a *separate* later
    experiment (run per-regime matrices), not mixed into the coverage pool.
    ``RegimeMode=Any`` is pinned too for clarity (inert while filters are off).
    """
    return {
        "fast_param": "FastPeriod",
        "slow_param": "SlowPeriod",
        "pinned_strategy_params": {
            "EnableDiscoveryFilters": False,
            "RegimeMode": "Any",
            "UseDiscoveryExitPolicy": True,
            "DiscoveryExitPolicy": exit_policy,
            "AtrTrailMultiple": atr_trail_multiple,
        },
    }


def build_name(
    *,
    start_minute: int,
    average_fast: float,
    average_slow: float,
    reverse: bool,
    max_trades: int,
    profit_stop: float,
    loss_stop: float,
    max_loss: float,
    rr: float,
    long_enabled: bool,
    short_enabled: bool,
    rules: dict,
    market: str | None = None,
    version: int | None = None,
) -> str:
    session = classify_session(start_minute, rules)
    single_multi = classify_single_multi(max_trades, profit_stop, loss_stop)
    tier = classify_tier(average_fast, average_slow, rules)

    name = "".join(
        [
            phase_word(session, single_multi, rules),
            str(tier["monster"] if reverse else tier["god"]),
            classify_descriptor(max_loss, rr, rules),
            direction_letter(long_enabled, short_enabled),
        ]
    )
    if version is not None:
        name += f"V{version}"
    if market:
        name += f"-{market}"
    return name


def _range_payload(values: tuple[int | float, int | float, int | float]) -> dict[str, int | float]:
    minimum, maximum, step = values
    return {"min": minimum, "max": maximum, "step": step}


def _validate_rules(rules: Any, source: Path | None = None) -> None:
    label = f" in {source}" if source is not None else ""
    if not isinstance(rules, dict):
        raise ValueError(f"Naming rules{label} must be a JSON object")

    required_lists = ("session_windows", "ma_tiers", "descriptor_tiers")
    for key in required_lists:
        if key not in rules or not isinstance(rules[key], list) or not rules[key]:
            raise ValueError(f"Naming rules{label} must include a non-empty '{key}' list")

    for index, window in enumerate(rules["session_windows"]):
        _require_keys(
            window,
            ("session", "single", "multi", "start_minute", "end_minute"),
            f"session_windows[{index}]{label}",
        )
        start_minute = window["start_minute"]
        end_minute = window["end_minute"]
        if not isinstance(start_minute, int) or not isinstance(end_minute, int):
            raise ValueError(f"session_windows[{index}]{label} minutes must be integers")
        if start_minute > end_minute:
            raise ValueError(f"session_windows[{index}]{label} start_minute exceeds end_minute")

    for index, tier in enumerate(rules["ma_tiers"]):
        _require_keys(tier, ("minimum", "maximum", "god", "monster"), f"ma_tiers[{index}]{label}")
        if not isinstance(tier["minimum"], (int, float)) or not isinstance(
            tier["maximum"], (int, float)
        ):
            raise ValueError(f"ma_tiers[{index}]{label} bounds must be numeric")
        if tier["minimum"] > tier["maximum"]:
            raise ValueError(f"ma_tiers[{index}]{label} minimum exceeds maximum")

    for index, tier in enumerate(rules["descriptor_tiers"]):
        _require_keys(
            tier,
            ("minimum", "maximum", "rr_leq_1", "rr_gt_1"),
            f"descriptor_tiers[{index}]{label}",
        )
        if not isinstance(tier["minimum"], (int, float)):
            raise ValueError(f"descriptor_tiers[{index}]{label} minimum must be numeric")
        if tier["maximum"] is not None and not isinstance(tier["maximum"], (int, float)):
            raise ValueError(
                f"descriptor_tiers[{index}]{label} maximum must be numeric or null"
            )


def _require_keys(value: Any, keys: tuple[str, ...], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    missing = [key for key in keys if key not in value]
    if missing:
        raise ValueError(f"{label} missing required keys: {', '.join(missing)}")
