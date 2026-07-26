"""Regime-Aware Discovery Configuration (Gap 6 Phase 1)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class RegimeParamSet:
    """Parameter overrides for a specific regime."""

    regime_id: str
    """Primary regime identifier (e.g., 'trend_up', 'trend_down', 'range')."""

    enabled_families: Optional[List[str]] = None
    """List of discovery families to enable (e.g., ['candle', 'ma']). If None, all families enabled."""

    disabled_families: Optional[List[str]] = None
    """List of discovery families to disable."""

    param_overrides: Dict[str, Any] = field(default_factory=dict)
    """Parameter overrides per family (e.g., candle_discovery: {fast_range: [5, 15]})."""

    enabled_patterns: Optional[Dict[str, List[str]]] = None
    """Enable specific patterns per family (e.g., candle: [large_body, pin_bar])."""

    disabled_patterns: Optional[Dict[str, List[str]]] = None
    """Disable specific patterns per family."""

    min_trades_per_regime: int = 20
    """Minimum trades required to validate strategy in this regime."""

    def apply_to_family_config(self, family_name: str, base_config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply regime-specific overrides to a family's base config."""
        if self.disabled_families and family_name in self.disabled_families:
            return {**base_config, "enabled": False}

        config = {**base_config, "enabled": True}

        # Apply parameter overrides for this family
        if self.param_overrides and family_name in self.param_overrides:
            family_overrides = self.param_overrides[family_name]
            if isinstance(family_overrides, dict):
                config.update(family_overrides)

        # Apply pattern filtering
        if self.enabled_patterns and family_name in self.enabled_patterns:
            patterns = self.enabled_patterns[family_name]
            if "patterns" in config and isinstance(config["patterns"], list):
                # Keep only enabled patterns
                config["patterns"] = [p for p in config["patterns"] if p in patterns]

        if self.disabled_patterns and family_name in self.disabled_patterns:
            patterns = self.disabled_patterns[family_name]
            if "patterns" in config and isinstance(config["patterns"], list):
                # Remove disabled patterns
                config["patterns"] = [p for p in config["patterns"] if p not in patterns]

        return config


class RegimeDiscoveryConfig:
    """Configuration for regime-specific discovery parameters."""

    def __init__(
        self,
        enabled: bool = True,
        regime_types: Optional[List[str]] = None,
        regime_params: Optional[Dict[str, RegimeParamSet]] = None,
        default_min_trades: int = 20,
    ):
        """Initialize regime discovery configuration.

        Args:
            enabled: Whether to use regime-specific discovery
            regime_types: List of regime IDs to handle (e.g., ['trend_up', 'trend_down', 'range'])
            regime_params: Dict mapping regime_id to RegimeParamSet
            default_min_trades: Default minimum trades per regime
        """
        self.enabled = enabled
        self.regime_types = regime_types or ["trend_up", "trend_down", "range"]
        self.regime_params = regime_params or {}
        self.default_min_trades = default_min_trades

        # Initialize default empty param sets for all regime types
        for regime_id in self.regime_types:
            if regime_id not in self.regime_params:
                self.regime_params[regime_id] = RegimeParamSet(
                    regime_id=regime_id,
                    min_trades_per_regime=default_min_trades,
                )

    def get_regime_config(self, regime_id: str) -> RegimeParamSet:
        """Get configuration for a specific regime.

        Args:
            regime_id: Regime identifier

        Returns:
            RegimeParamSet for the regime (or default if not found)
        """
        if regime_id not in self.regime_params:
            return RegimeParamSet(
                regime_id=regime_id,
                min_trades_per_regime=self.default_min_trades,
            )
        return self.regime_params[regime_id]

    def set_regime_config(self, regime_id: str, config: RegimeParamSet) -> None:
        """Set configuration for a specific regime."""
        self.regime_params[regime_id] = config

    def apply_regime_to_discovery_config(
        self,
        base_config: Dict[str, Any],
        regime_id: str,
    ) -> Dict[str, Any]:
        """Apply regime-specific overrides to discovery config.

        Args:
            base_config: Base discovery configuration
            regime_id: Regime identifier

        Returns:
            Modified discovery configuration with regime overrides applied
        """
        if not self.enabled:
            return base_config

        regime_cfg = self.get_regime_config(regime_id)

        # Start with base config
        config = {**base_config}

        # Apply enabled/disabled families
        if "discovery_families" in config and isinstance(config["discovery_families"], dict):
            families = config["discovery_families"]

            if regime_cfg.disabled_families:
                for family in regime_cfg.disabled_families:
                    if family in families:
                        families[family] = {**families[family], "enabled": False}

            if regime_cfg.enabled_families:
                for family in families:
                    enabled = family in regime_cfg.enabled_families
                    families[family] = {**families[family], "enabled": enabled}

        # Apply parameter overrides per family
        if "discovery_families" in config and isinstance(config["discovery_families"], dict):
            for family_name, family_config in config["discovery_families"].items():
                config["discovery_families"][family_name] = regime_cfg.apply_to_family_config(
                    family_name, family_config
                )

        return config

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-safe dict for serialization."""
        return {
            "enabled": self.enabled,
            "regime_types": self.regime_types,
            "default_min_trades": self.default_min_trades,
            "regime_params": {
                regime_id: {
                    "regime_id": regime_id,
                    "enabled_families": config.enabled_families,
                    "disabled_families": config.disabled_families,
                    "param_overrides": config.param_overrides,
                    "enabled_patterns": config.enabled_patterns,
                    "disabled_patterns": config.disabled_patterns,
                    "min_trades_per_regime": config.min_trades_per_regime,
                }
                for regime_id, config in self.regime_params.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RegimeDiscoveryConfig:
        """Create from JSON-safe dict."""
        regime_params = {}
        for regime_id, param_data in data.get("regime_params", {}).items():
            regime_params[regime_id] = RegimeParamSet(
                regime_id=param_data.get("regime_id", regime_id),
                enabled_families=param_data.get("enabled_families"),
                disabled_families=param_data.get("disabled_families"),
                param_overrides=param_data.get("param_overrides", {}),
                enabled_patterns=param_data.get("enabled_patterns"),
                disabled_patterns=param_data.get("disabled_patterns"),
                min_trades_per_regime=param_data.get("min_trades_per_regime", 20),
            )

        return cls(
            enabled=data.get("enabled", True),
            regime_types=data.get("regime_types", ["trend_up", "trend_down", "range"]),
            regime_params=regime_params,
            default_min_trades=data.get("default_min_trades", 20),
        )


def create_default_regime_discovery_config() -> RegimeDiscoveryConfig:
    """Create a default regime discovery configuration with sensible overrides.

    This provides an example of how to configure regime-specific discovery.
    """
    config = RegimeDiscoveryConfig(enabled=True)

    # Trend up: focus on candle patterns and moving averages
    trend_up = RegimeParamSet(
        regime_id="trend_up",
        enabled_families=["candle", "ma", "orb"],
        disabled_families=["level", "lcr"],
        param_overrides={
            "candle": {
                "patterns": ["large_body", "pin_bar", "engulfing"],
            },
            "ma": {
                "fast_range": [5, 20],
                "slow_range": [30, 100],
            },
        },
        min_trades_per_regime=20,
    )
    config.set_regime_config("trend_up", trend_up)

    # Trend down: similar to trend up but for downtrends
    trend_down = RegimeParamSet(
        regime_id="trend_down",
        enabled_families=["candle", "ma", "orb"],
        disabled_families=["level", "lcr"],
        param_overrides={
            "candle": {
                "patterns": ["large_body", "pin_bar", "engulfing"],
            },
            "ma": {
                "fast_range": [5, 20],
                "slow_range": [30, 100],
            },
        },
        min_trades_per_regime=20,
    )
    config.set_regime_config("trend_down", trend_down)

    # Range: focus on levels and support/resistance
    range_regime = RegimeParamSet(
        regime_id="range",
        enabled_families=["level", "lcr", "ma", "bb"],
        disabled_families=["orb"],
        param_overrides={
            "level": {
                "touch_tolerance": [3, 8],
                "lookback_periods": [50, 100],
            },
            "ma": {
                "fast_range": [5, 15],
                "slow_range": [20, 50],
            },
        },
        min_trades_per_regime=20,
    )
    config.set_regime_config("range", range_regime)

    return config
