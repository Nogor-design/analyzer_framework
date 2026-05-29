"""
Conditional Entry Rules Engine

Allows YAML to specify logic like:
  - "Use candles if regime=bull, else ORB"
  - "Only enable patterns with > 50 trades"
  - "Expand TP/SL only if PF > 1.3"

This eliminates manual config tweaking and makes discovery more automated.

YAML Syntax:
  conditional_rules:
    - name: "Bull regime candles"
      condition: "regime == 'bull'"
      action: "enable_family"
      family: "candle_discovery"

    - name: "Disable weak patterns"
      condition: "n_trades < 50"
      action: "disable_pattern"
      pattern: "inside_bar"

    - name: "Expand TP/SL for winners"
      condition: "profit_factor > 1.3"
      action: "expand_params"
      family: "candle_discovery"
      expansion_factor: 2.0
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable
import re


class ConditionalRule:
    """A single conditional rule."""

    def __init__(
        self,
        name: str,
        condition: str,
        action: str,
        **kwargs,
    ):
        self.name = name
        self.condition = condition  # e.g., "regime == 'bull'"
        self.action = action  # e.g., "enable_family", "disable_pattern"
        self.params = kwargs

    def __repr__(self) -> str:
        return f"Rule({self.name}): IF {self.condition} THEN {self.action}"


def parse_condition(condition_str: str) -> Callable:
    """
    Parse a condition string into a callable.

    Examples:
      - "regime == 'bull'" → lambda ctx: ctx.get('regime') == 'bull'
      - "n_trades > 50" → lambda ctx: ctx.get('n_trades', 0) > 50
      - "profit_factor > 1.3 and win_rate > 0.5"

    Parameters
    ----------
    condition_str : condition expression

    Returns
    -------
    Callable that takes a context dict and returns bool
    """

    def eval_condition(context: Dict[str, Any]) -> bool:
        """Safely evaluate condition with context."""
        try:
            # Allow safe comparison operations
            # Replace context variables with their values
            expr = condition_str
            for key, val in context.items():
                if isinstance(val, str):
                    expr = re.sub(rf"\b{key}\b", f"'{val}'", expr)
                else:
                    expr = re.sub(rf"\b{key}\b", str(val), expr)

            # Evaluate with restricted namespace
            result = eval(expr, {"__builtins__": {}}, {})
            return bool(result)
        except Exception as e:
            print(f"[conditional_rules] Error evaluating '{condition_str}': {e}")
            return False

    return eval_condition


def load_rules_from_yaml(rules_config: List[Dict[str, Any]]) -> List[ConditionalRule]:
    """
    Load conditional rules from YAML config.

    Parameters
    ----------
    rules_config : list of rule dicts from YAML

    Returns
    -------
    List of ConditionalRule objects
    """
    rules = []

    for rule_dict in rules_config:
        if not isinstance(rule_dict, dict):
            continue

        name = rule_dict.get("name", "unnamed")
        condition = rule_dict.get("condition")
        action = rule_dict.get("action")

        if not condition or not action:
            print(f"[conditional_rules] Skipping incomplete rule: {rule_dict}")
            continue

        rule = ConditionalRule(name, condition, action, **rule_dict)
        rules.append(rule)

    return rules


def apply_family_enable_rule(
    config: Dict[str, Any],
    family: str,
    enabled: bool,
) -> None:
    """
    Enable/disable a family in config.

    Parameters
    ----------
    config   : configuration dict (mutated)
    family   : family name (e.g., "candle_discovery")
    enabled  : True to enable, False to disable
    """
    if family in config:
        config[family]["enabled"] = enabled


def apply_pattern_enable_rule(
    config: Dict[str, Any],
    family: str,
    pattern: str,
    enabled: bool,
) -> None:
    """
    Enable/disable a specific pattern in a family.

    Parameters
    ----------
    config   : configuration dict (mutated)
    family   : family name (e.g., "candle_discovery")
    pattern  : pattern name (e.g., "large_body")
    enabled  : True to enable, False to disable
    """
    if family in config:
        patterns = config[family].get("patterns", {})
        if pattern in patterns:
            patterns[pattern]["enabled"] = enabled


def apply_expand_params_rule(
    config: Dict[str, Any],
    family: str,
    expansion_factor: float = 1.5,
) -> None:
    """
    Expand parameters for a family.

    Parameters
    ----------
    config             : configuration dict (mutated)
    family             : family name
    expansion_factor   : how much to expand (1.5 = 50%)
    """
    from .dynamic_params import expand_numeric_list

    if family not in config:
        return

    # Expand parameters in patterns or signals
    patterns = config[family].get("patterns", {})
    for pattern_name, pattern_cfg in patterns.items():
        for param_name, val in pattern_cfg.items():
            if isinstance(val, list) and param_name != "enabled":
                pattern_cfg[param_name] = expand_numeric_list(val, factor=expansion_factor)

    signals = config[family].get("signals", {})
    for signal_name, signal_cfg in signals.items():
        for param_name, val in signal_cfg.items():
            if isinstance(val, list) and param_name != "enabled":
                signal_cfg[param_name] = expand_numeric_list(val, factor=expansion_factor)

    # Expand outcome params
    outcome = config[family].get("outcome", {})
    if "ticks" in outcome:
        ticks_cfg = outcome["ticks"]
        if "take_profit" in ticks_cfg and isinstance(ticks_cfg["take_profit"], list):
            ticks_cfg["take_profit"] = expand_numeric_list(
                ticks_cfg["take_profit"], factor=expansion_factor
            )
        if "stop" in ticks_cfg and isinstance(ticks_cfg["stop"], list):
            ticks_cfg["stop"] = expand_numeric_list(
                ticks_cfg["stop"], factor=expansion_factor
            )


def apply_rules_to_config(
    config: Dict[str, Any],
    rules: List[ConditionalRule],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply conditional rules to config based on context.

    Parameters
    ----------
    config   : configuration dict (mutated)
    rules    : list of ConditionalRule objects
    context  : evaluation context (e.g., {"regime": "bull", "n_trades": 150})

    Returns
    -------
    Modified config
    """
    import copy

    config = copy.deepcopy(config)

    for rule in rules:
        condition_fn = parse_condition(rule.condition)

        if not condition_fn(context):
            continue

        print(f"[conditional_rules] Applying: {rule.name}")

        # Execute action
        if rule.action == "enable_family":
            family = rule.params.get("family")
            if family:
                apply_family_enable_rule(config, family, True)

        elif rule.action == "disable_family":
            family = rule.params.get("family")
            if family:
                apply_family_enable_rule(config, family, False)

        elif rule.action == "enable_pattern":
            family = rule.params.get("family")
            pattern = rule.params.get("pattern")
            if family and pattern:
                apply_pattern_enable_rule(config, family, pattern, True)

        elif rule.action == "disable_pattern":
            family = rule.params.get("family")
            pattern = rule.params.get("pattern")
            if family and pattern:
                apply_pattern_enable_rule(config, family, pattern, False)

        elif rule.action == "expand_params":
            family = rule.params.get("family")
            expansion_factor = rule.params.get("expansion_factor", 1.5)
            if family:
                apply_expand_params_rule(config, family, expansion_factor)

        elif rule.action == "disable_family_if_weak":
            family = rule.params.get("family")
            threshold = rule.params.get("pf_threshold", 1.2)
            pf = context.get("profit_factor", 0.0)
            if family and pf < threshold:
                apply_family_enable_rule(config, family, False)
                print(f"[conditional_rules]   Disabled {family} (PF {pf} < {threshold})")

    return config


class ConditionalRuleEngine:
    """Engine for evaluating and applying conditional rules."""

    def __init__(self):
        self.rules: List[ConditionalRule] = []

    def load_from_yaml(self, rules_config: List[Dict[str, Any]]) -> None:
        """Load rules from YAML config."""
        self.rules = load_rules_from_yaml(rules_config)
        print(f"[conditional_rules] Loaded {len(self.rules)} rules")

    def apply(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Apply all rules to config with given context.

        Parameters
        ----------
        config   : configuration dict
        context  : evaluation context

        Returns
        -------
        Modified config
        """
        return apply_rules_to_config(config, self.rules, context)

    def add_rule(self, rule: ConditionalRule) -> None:
        """Add a single rule."""
        self.rules.append(rule)

    def __repr__(self) -> str:
        return f"ConditionalRuleEngine({len(self.rules)} rules)"


# Example YAML for conditional rules:
EXAMPLE_YAML = """
conditional_rules:
  - name: "Bull regime → use candles"
    condition: "regime == 'bull'"
    action: "enable_family"
    family: "candle_discovery"

  - name: "Bear regime → use ORB breakdowns"
    condition: "regime == 'bear'"
    action: "enable_family"
    family: "orb_discovery"

  - name: "Skip weak patterns"
    condition: "n_trades < 30"
    action: "disable_pattern"
    family: "candle_discovery"
    pattern: "inside_bar"

  - name: "Expand on winners"
    condition: "profit_factor > 1.4"
    action: "expand_params"
    family: "candle_discovery"
    expansion_factor: 2.0

  - name: "Disable underperformers"
    condition: "profit_factor < 1.1"
    action: "disable_family"
    family: "level_discovery"
"""
