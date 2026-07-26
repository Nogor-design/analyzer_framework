from __future__ import annotations

"""
Conditional-rule promotion for Discovery sidecars.

Entry discovery can find interpretable pockets inside a broader sweep row. This
module turns those nested ``entry_discovery.top_rules`` into concrete follow-up
YAML probes so promising structure gets retested as a first-class candidate.
"""

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

import yaml


SESSION_FILTERS: dict[str, dict[str, Any]] = {
    "London": {
        "hour_from": 0,
        "minute_from": 0,
        "hour_to": 6,
        "minute_to": 0,
        "timezone": "America/Denver",
    },
}

LEVEL_TYPE_TO_SIGNAL: dict[str, tuple[str, int]] = {
    "vwap_reject": ("vwap_reclaim_reject", -1),
    "vwap_reclaim": ("vwap_reclaim_reject", 1),
}

_DISCOVERY_BLOCKS = {
    "candle": "candle_discovery",
    "ma": "ma_discovery",
    "orb": "orb_discovery",
    "bb": "bb_discovery",
    "lcr": "lcr_discovery",
    "breakout": "breakout_discovery",
    "pullback": "pullback_discovery",
    "level": "level_discovery",
}

_NUMERIC_COL_TO_PARAM = {
    "signal_atr": "min_atr_ticks",
    "level_dist_ticks": ("min_dist_ticks", "max_dist_ticks"),
    "breakout_ticks": "min_breakout_ticks",
    "fill_bars": "max_fill_bars",
}


def generate_conditional_probe_yamls(
    *,
    parent_config_path: str | Path,
    sidecar_path: str | Path,
    generated_dir: str | Path = "discovery/generated",
    max_parent_ranks: int = 10,
    max_rules_per_parent: int = 3,
    min_rule_profit_factor: float = 1.0,
    min_rule_trades: int = 20,
) -> list[Path]:
    """Read a parent YAML and sidecar, then write focused probe YAMLs.

    Returns the list of written paths. Invalid/unconvertible rules are skipped.
    """
    parent_config_path = Path(parent_config_path)
    sidecar_path = Path(sidecar_path)
    parent_cfg = _read_yaml(parent_config_path)
    sidecar = _read_yaml(sidecar_path)
    return write_conditional_probe_yamls(
        parent_config=parent_cfg,
        sidecar=sidecar,
        generated_dir=generated_dir,
        parent_config_path=parent_config_path,
        sidecar_path=sidecar_path,
        max_parent_ranks=max_parent_ranks,
        max_rules_per_parent=max_rules_per_parent,
        min_rule_profit_factor=min_rule_profit_factor,
        min_rule_trades=min_rule_trades,
    )


def write_conditional_probe_yamls(
    *,
    parent_config: dict[str, Any],
    sidecar: dict[str, Any],
    generated_dir: str | Path,
    parent_config_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
    max_parent_ranks: int = 10,
    max_rules_per_parent: int = 3,
    min_rule_profit_factor: float = 1.0,
    min_rule_trades: int = 20,
) -> list[Path]:
    """Write one focused YAML per convertible nested conditional rule."""
    if not isinstance(parent_config, dict) or not isinstance(sidecar, dict):
        return []
    if isinstance(parent_config.get("conditional_promotion"), dict):
        return []

    out_dir = Path(generated_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    seen_effective_configs: set[str] = set()
    rankings = sidecar.get("rankings") or []
    if not isinstance(rankings, list):
        return []

    parent_stem = Path(parent_config_path).stem if parent_config_path else "parent"
    for ranking in rankings[: max(0, int(max_parent_ranks))]:
        if not isinstance(ranking, dict):
            continue
        parent_rank = _as_int(ranking.get("rank"))
        rules = ranking.get("conditional_rules") or []
        if not isinstance(rules, list):
            continue
        for rule in rules[: max(0, int(max_rules_per_parent))]:
            if not isinstance(rule, dict):
                continue
            if not _rule_meets_quality_floor(
                rule,
                min_profit_factor=min_rule_profit_factor,
                min_trades=min_rule_trades,
            ):
                continue
            promoted = promote_rule_to_config(
                parent_config,
                sidecar=sidecar,
                ranking=ranking,
                rule=rule,
                parent_config_path=parent_config_path,
                sidecar_path=sidecar_path,
            )
            if promoted is None:
                continue
            fingerprint = _effective_config_fingerprint(promoted)
            if fingerprint in seen_effective_configs:
                continue
            seen_effective_configs.add(fingerprint)
            rule_rank = _as_int(rule.get("rank")) or len(written) + 1
            fname = f"{_slug(parent_stem)}__rank{parent_rank or 0:02d}_rule{rule_rank:02d}.yaml"
            path = out_dir / fname
            path.write_text(yaml.safe_dump(promoted, sort_keys=False), encoding="utf-8")
            written.append(path)
    return written


def promote_rule_to_config(
    parent_config: dict[str, Any],
    *,
    sidecar: dict[str, Any],
    ranking: dict[str, Any],
    rule: dict[str, Any],
    parent_config_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Convert one sidecar conditional rule into a focused follow-up config."""
    cfg = copy.deepcopy(parent_config)
    conversions: list[dict[str, Any]] = []
    changed = False

    conditions = rule.get("conditions") or []
    if not isinstance(conditions, list):
        return None

    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        applied = _apply_condition(cfg, cond)
        if applied:
            changed = True
            conversions.append(applied)

    if not changed:
        return None

    _set_report_identity(cfg, ranking, rule)
    cfg["conditional_promotion"] = _build_provenance(
        sidecar=sidecar,
        ranking=ranking,
        rule=rule,
        conversions=conversions,
        parent_config_path=parent_config_path,
        sidecar_path=sidecar_path,
    )
    return cfg


def _rule_meets_quality_floor(
    rule: dict[str, Any],
    *,
    min_profit_factor: float,
    min_trades: int,
) -> bool:
    n_trades = _as_int(rule.get("n_trades"))
    if n_trades is not None and n_trades < int(min_trades):
        return False
    pf = rule.get("profit_factor")
    if pf is None:
        return True
    try:
        return float(pf) >= float(min_profit_factor)
    except (TypeError, ValueError):
        return False


def _apply_condition(cfg: dict[str, Any], cond: dict[str, Any]) -> dict[str, Any] | None:
    col = str(cond.get("column") or "")
    op = str(cond.get("op") or "")
    value = cond.get("value")

    # Resolve the active discovery family and its configuration block
    family = _resolve_active_family(cfg)
    if not family:
        return None
    block_name = _DISCOVERY_BLOCKS[family]
    block = cfg.setdefault(block_name, {})

    if col == "session_label" and op == "eq":
        label = str(value)
        session_filter = SESSION_FILTERS.get(label)
        if session_filter:
            block["session_filter"] = dict(session_filter)
            return {"column": col, "op": op, "value": label, "yaml_path": f"{block_name}.session_filter"}

    REGIME_COLS = {
        "regime", "vol_regime", "vol_regime_tertile", "vol_regime_quartile",
        "trend_direction", "trend_strength"
    }
    if col in REGIME_COLS and op == "eq":
        val_str = str(value)
        regime_filter = block.setdefault("regime_filter", {})
        if not isinstance(regime_filter, dict):
            block["regime_filter"] = {}
            regime_filter = block["regime_filter"]
        regime_filter[col] = [val_str]
        return {
            "column": col,
            "op": op,
            "value": val_str,
            "yaml_path": f"{block_name}.regime_filter.{col}"
        }

    if col == "level_type" and op == "eq" and family == "level":
        mapped = LEVEL_TYPE_TO_SIGNAL.get(str(value))
        if mapped:
            signal_id, direction = mapped
            _focus_signal(cfg, family, signal_id)
            sig_block = _signal_block(cfg, family, signal_id)
            sig_block["direction"] = [direction]
            return {
                "column": col,
                "op": op,
                "value": str(value),
                "yaml_path": f"{block_name}.signals.{signal_id}.direction",
            }

    if col in _NUMERIC_COL_TO_PARAM and op in {"gte", "lte"}:
        param = _NUMERIC_COL_TO_PARAM[col]
        # Some columns map to different params based on the operator (e.g. dist_ticks)
        if isinstance(param, tuple):
            param = param[0] if op == "gte" else param[1]

        signal_id = _active_signal(cfg, family)
        if signal_id:
            threshold = _num_for_yaml(str(value))
            if col in {"level_dist_ticks", "breakout_ticks"}:
                threshold = int(math.ceil(float(value)))
            
            sig_block = _signal_block(cfg, family, signal_id)
            sig_block[param] = [threshold]
            return {
                "column": col,
                "op": op,
                "value": value,
                "yaml_path": f"{block_name}.signals.{signal_id}.{param}",
            }

    if col == "market_pos" and op == "eq":
        direction = {"Long": 1, "Short": -1}.get(str(value))
        if direction is not None:
            signal_id = _active_signal(cfg, family)
            if signal_id:
                raw_direction = _raw_direction_for_effective_direction(cfg, family, signal_id, direction)
                sig_block = _signal_block(cfg, family, signal_id)
                sig_block["direction"] = [raw_direction]
                return {
                    "column": col,
                    "op": op,
                    "value": str(value),
                    "yaml_path": f"{block_name}.signals.{signal_id}.direction",
                }

    if col == "direction" and op == "eq":
        direction = _direction_value(value)
        if direction is not None:
            signal_id = _active_signal(cfg, family)
            if signal_id:
                raw_direction = _raw_direction_for_effective_direction(cfg, family, signal_id, direction)
                sig_block = _signal_block(cfg, family, signal_id)
                sig_block["direction"] = [raw_direction]
                return {
                    "column": col,
                    "op": op,
                    "value": value,
                    "yaml_path": f"{block_name}.signals.{signal_id}.direction",
                }

    if col == "timing_mode" and op == "eq":
        timing_val = str(value)
        if _only_timing_enabled(cfg, family, timing_val):
            return None
        _enable_only_timing(cfg, family, timing_val)
        return {"column": col, "op": op, "value": timing_val, "yaml_path": f"{block_name}.entry_timing"}

    if col == "outcome_mode" and op == "eq":
        converted = _apply_outcome_mode(cfg, family, str(value))
        if converted:
            return {"column": col, "op": op, "value": str(value), "yaml_path": converted}

    return None


def _resolve_active_family(cfg: dict[str, Any]) -> str | None:
    # 1. Check if a family is explicitly marked as enabled
    for family, block_name in _DISCOVERY_BLOCKS.items():
        if cfg.get(block_name, {}).get("enabled"):
            return family
    # 2. Fallback: find the first block present
    for family, block_name in _DISCOVERY_BLOCKS.items():
        if block_name in cfg:
            return family
    return None


def _discovery_block(cfg: dict[str, Any], family: str) -> dict[str, Any]:
    block_name = _DISCOVERY_BLOCKS.get(family, "level_discovery")
    block = cfg.setdefault(block_name, {})
    if not isinstance(block, dict):
        cfg[block_name] = {}
    return cfg[block_name]


def _signal_block(cfg: dict[str, Any], family: str, signal_id: str) -> dict[str, Any]:
    block = _discovery_block(cfg, family)
    signals = block.setdefault("signals", {})
    if not isinstance(signals, dict):
        block["signals"] = {}
    sig = block["signals"].setdefault(signal_id, {"enabled": True})
    if not isinstance(sig, dict):
        block["signals"][signal_id] = {"enabled": True}
    block["signals"][signal_id]["enabled"] = True
    return block["signals"][signal_id]


def _focus_signal(cfg: dict[str, Any], family: str, signal_id: str) -> None:
    signals = _discovery_block(cfg, family).setdefault("signals", {})
    if not isinstance(signals, dict):
        _discovery_block(cfg, family)["signals"] = {}
        signals = _discovery_block(cfg, family)["signals"]
    for key, block in list(signals.items()):
        if isinstance(block, dict):
            block["enabled"] = (key == signal_id)
    _signal_block(cfg, family, signal_id)["enabled"] = True


def _active_signal(cfg: dict[str, Any], family: str) -> str | None:
    signals = (_discovery_block(cfg, family).get("signals") or {})
    if not isinstance(signals, dict):
        return None
    enabled = [
        str(k)
        for k, v in signals.items()
        if isinstance(v, dict) and bool(v.get("enabled", True))
    ]
    if len(enabled) == 1:
        return enabled[0]
    
    # Heuristic fallbacks for common families
    if family == "level" and "vwap_reclaim_reject" in signals:
        return "vwap_reclaim_reject"
    if family == "bb" and "bb_mean_reversion" in signals:
        return "bb_mean_reversion"
    if family == "orb" and "orb_breakout" in signals:
        return "orb_breakout"
    
    return enabled[0] if enabled else None


def _enable_only_timing(cfg: dict[str, Any], family: str, timing_mode: str) -> None:
    timing = _discovery_block(cfg, family).setdefault("entry_timing", {})
    if not isinstance(timing, dict):
        _discovery_block(cfg, family)["entry_timing"] = {}
        timing = _discovery_block(cfg, family)["entry_timing"]
    
    known = {"next_open", "break_extreme", "body_midpoint"} | set(timing.keys())
    for key in known:
        existing = timing.get(key)
        timing[key] = dict(existing) if isinstance(existing, dict) else {}
        timing[key]["enabled"] = (key == timing_mode)


def _only_timing_enabled(cfg: dict[str, Any], family: str, timing_mode: str) -> bool:
    timing = _discovery_block(cfg, family).get("entry_timing") or {}
    if not isinstance(timing, dict) or not timing:
        return False
    enabled = [
        str(key)
        for key, value in timing.items()
        if isinstance(value, dict) and bool(value.get("enabled", True))
    ]
    return enabled == [timing_mode]


def _raw_direction_for_effective_direction(cfg: dict[str, Any], family: str, signal_id: str, direction: int) -> int:
    sig_block = _signal_block(cfg, family, signal_id)
    invert = sig_block.get("invert_direction", False)
    if isinstance(invert, list):
        invert = bool(invert[0]) if invert else False
    return -direction if bool(invert) else direction


def _direction_value(value: Any) -> int | None:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text in {"long", "buy"}:
            return 1
        if text in {"short", "sell"}:
            return -1
        return None
    return v if v in {-1, 1} else None


def _apply_outcome_mode(cfg: dict[str, Any], family: str, value: str) -> str | None:
    block_name = _DISCOVERY_BLOCKS.get(family, "level_discovery")
    block = _discovery_block(cfg, family)

    ticks = re.fullmatch(r"ticks_([0-9]+(?:\.[0-9]+)?)_([0-9]+(?:\.[0-9]+)?)", value)
    if ticks:
        tp = _num_for_yaml(ticks.group(1))
        sl = _num_for_yaml(ticks.group(2))
        outcome = block.setdefault("outcome", {})
        if not isinstance(outcome, dict):
            block["outcome"] = {}
            outcome = block["outcome"]
        
        # Disable ATR, enable ticks
        outcome["atr"] = {**(outcome.get("atr") if isinstance(outcome.get("atr"), dict) else {}), "enabled": False}
        outcome["ticks"] = {
            **(outcome.get("ticks") if isinstance(outcome.get("ticks"), dict) else {}),
            "enabled": True,
            "take_profit": [tp],
            "stop": [sl],
        }
        return f"{block_name}.outcome.ticks"

    atr = re.fullmatch(r"atr_([0-9]+(?:\.[0-9]+)?)x([0-9]+(?:\.[0-9]+)?)", value)
    if atr:
        target = _num_for_yaml(atr.group(1))
        stop = _num_for_yaml(atr.group(2))
        outcome = block.setdefault("outcome", {})
        if not isinstance(outcome, dict):
            block["outcome"] = {}
            outcome = block["outcome"]
        
        # Disable ticks, enable ATR
        outcome["ticks"] = {**(outcome.get("ticks") if isinstance(outcome.get("ticks"), dict) else {}), "enabled": False}
        outcome["atr"] = {
            **(outcome.get("atr") if isinstance(outcome.get("atr"), dict) else {}),
            "enabled": True,
            "target_mult": target,
            "stop_mult": stop,
        }
        return f"{block_name}.outcome.atr"

    return None



def _set_report_identity(cfg: dict[str, Any], ranking: dict[str, Any], rule: dict[str, Any]) -> None:
    report = cfg.setdefault("report", {})
    if not isinstance(report, dict):
        cfg["report"] = {}
        report = cfg["report"]
    parent_rank = _as_int(ranking.get("rank")) or 0
    rule_rank = _as_int(rule.get("rank")) or 0
    report["title"] = f"Conditional Probe R{parent_rank} Rule {rule_rank}"
    base = _slug(str(report.get("output") or "conditional_probe.html").removesuffix(".html"))
    report["output"] = f"{base}__rank{parent_rank:02d}_rule{rule_rank:02d}.html"


def _build_provenance(
    *,
    sidecar: dict[str, Any],
    ranking: dict[str, Any],
    rule: dict[str, Any],
    conversions: list[dict[str, Any]],
    parent_config_path: str | Path | None,
    sidecar_path: str | Path | None,
) -> dict[str, Any]:
    return {
        "parent_config": str(parent_config_path) if parent_config_path else "",
        "parent_sidecar": str(sidecar_path) if sidecar_path else "",
        "parent_report": sidecar.get("report_html", ""),
        "parent_stage": (sidecar.get("stage") or {}).get("id", ""),
        "parent_rank": ranking.get("rank"),
        "parent_family": ranking.get("family"),
        "parent_signal": ranking.get("signal"),
        "parent_params": copy.deepcopy(ranking.get("params") or {}),
        "parent_outcome": copy.deepcopy(ranking.get("outcome") or {}),
        "rule_rank": rule.get("rank"),
        "rule_string": rule.get("rule_str") or "",
        "rule_conditions": copy.deepcopy(rule.get("conditions") or []),
        "rule_stats": {
            k: rule.get(k)
            for k in ("n_trades", "win_rate", "profit_factor", "avg_profit", "net_profit", "score")
            if k in rule
        },
        "conversions": conversions,
        "status": "research_candidate",
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num_for_yaml(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() else value


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return slug or "conditional_probe"


def _effective_config_fingerprint(cfg: dict[str, Any]) -> str:
    effective = copy.deepcopy(cfg)
    effective.pop("report", None)
    effective.pop("conditional_promotion", None)
    return json.dumps(effective, sort_keys=True, default=str)
