"""
Regime-Integrated Discovery Runner (Gap 6 Phase 3-4)

Extends unified discovery with regime classification and regime-specific discovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import yaml
import pandas as pd

from .unified_discovery_runner import (
    UnifiedDiscoveryRunner,
    run_unified_discovery_workflow,
)
from .regime_discovery_config import (
    RegimeDiscoveryConfig,
    create_default_regime_discovery_config,
)
from .regime_candidate_analyzer import RegimeCandidateAnalyzer


class RegimeIntegratedDiscoveryRunner(UnifiedDiscoveryRunner):
    """Extended discovery runner with automatic regime classification and adaptation."""

    def __init__(
        self,
        output_dir: str | Path = "./discovery_results",
        num_stages: int = 2,
        expansion_factor: float = 1.5,
        regime_discovery_config: Optional[RegimeDiscoveryConfig] = None,
    ):
        """Initialize with regime-aware configuration.

        Args:
            output_dir: Output directory for results
            num_stages: Number of discovery stages
            expansion_factor: Parameter expansion multiplier
            regime_discovery_config: RegimeDiscoveryConfig instance
        """
        super().__init__(
            output_dir=output_dir,
            num_stages=num_stages,
            expansion_factor=expansion_factor,
        )
        self.regime_config = regime_discovery_config or create_default_regime_discovery_config()
        self.regime_labels: List[str] = []
        self.regime_analysis_results: Dict[int, Dict[str, Any]] = {}

    def classify_bars_to_regimes(
        self,
        bars_1m: pd.DataFrame,
        classifier_fn: Any,
    ) -> List[str]:
        """Classify each bar to a regime using provided classifier.

        Args:
            bars_1m: 1-minute OHLCV DataFrame
            classifier_fn: Function that classifies a series of bars to regime_id

        Returns:
            List of regime IDs (same length as bars_1m)
        """
        if bars_1m.empty:
            return []

        # Classify all bars
        regime_ids = []
        for i in range(len(bars_1m)):
            # Get window of bars up to current point
            window = bars_1m.iloc[: i + 1]

            # Classify this window (classifier should return regime_id)
            try:
                regime_id = classifier_fn(window)
                regime_ids.append(regime_id)
            except Exception:
                # Default to range if classification fails
                regime_ids.append("range")

        return regime_ids

    def partition_discovery_results_by_regime(
        self,
        discovery_results: List[Dict[str, Any]],
        regime_labels: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Partition discovery results by regime.

        Args:
            discovery_results: List of trade/strategy dicts
            regime_labels: Regime ID for each result

        Returns:
            Dict mapping regime_id to list of results
        """
        by_regime = {}
        for result, regime in zip(discovery_results, regime_labels):
            if regime not in by_regime:
                by_regime[regime] = []
            by_regime[regime].append(result)

        return by_regime

    def process_stage_results_with_regime_analysis(
        self,
        stage: int,
        family_results: Dict[str, Dict[str, Any]],
        discovery_results: Optional[List[Dict[str, Any]]] = None,
        regime_labels: Optional[List[str]] = None,
        min_pf_threshold: float = 1.2,
    ) -> Dict[str, Any]:
        """Process stage results including regime analysis.

        Args:
            stage: Stage number
            family_results: Results from each discovery family
            discovery_results: Individual trades/strategies (optional)
            regime_labels: Regime ID for each result (optional)
            min_pf_threshold: PF threshold for focus

        Returns:
            Analysis dict with unified + regime breakdown
        """
        # Run standard unified analysis
        analysis = self.process_stage_results(stage, family_results, min_pf_threshold)

        # Add regime analysis if provided
        if discovery_results and regime_labels:
            analyzer = RegimeCandidateAnalyzer(discovery_results, regime_labels)
            regime_summary = analyzer.get_regime_summary(min_trades_per_regime=10)

            analysis["regime_analysis"] = regime_summary

            # Save regime analysis
            self._save_regime_analysis(stage, regime_summary)

            self.regime_analysis_results[stage] = regime_summary

        return analysis

    def apply_regime_specific_discovery(
        self,
        stage_config: Dict[str, Any],
        regime_id: str,
    ) -> Dict[str, Any]:
        """Apply regime-specific parameter overrides to discovery config.

        Args:
            stage_config: Base discovery configuration
            regime_id: Regime identifier

        Returns:
            Config with regime-specific overrides applied
        """
        if not self.regime_config.enabled:
            return stage_config

        return self.regime_config.apply_regime_to_discovery_config(
            stage_config,
            regime_id,
        )

    def _save_regime_analysis(self, stage: int, analysis: Dict[str, Any]) -> Path:
        """Save regime analysis results."""
        filepath = self.output_dir / f"stage_{stage:02d}_regime_analysis.json"
        with open(filepath, "w") as f:
            json.dump(analysis, f, indent=2, default=str)
        print(f"[regime_runner] Saved regime analysis to {filepath}")
        return filepath

    def save_regime_config(self) -> Path:
        """Save the regime discovery configuration."""
        filepath = self.output_dir / "regime_discovery_config.json"
        with open(filepath, "w") as f:
            json.dump(self.regime_config.to_dict(), f, indent=2)
        print(f"[regime_runner] Saved regime config to {filepath}")
        return filepath


def run_regime_integrated_discovery_workflow(
    bars_1m: pd.DataFrame,
    discovery_modules: Dict[str, Any],
    initial_config: Dict[str, Any],
    classifier_fn: Optional[Any] = None,
    regime_config: Optional[RegimeDiscoveryConfig] = None,
    output_dir: Path = Path("./discovery_results"),
    num_stages: int = 2,
    min_pf_threshold: float = 1.2,
) -> Dict[int, Dict[str, Any]]:
    """Run regime-integrated discovery workflow.

    Extends unified discovery with:
    1. Regime classification of all bars
    2. Regime-specific parameter adaptation
    3. Per-regime performance analysis
    4. Robustness scoring across regimes

    Args:
        bars_1m: 1-minute OHLCV DataFrame
        discovery_modules: {module_name: run_fn} discovery functions
        initial_config: Base discovery configuration
        classifier_fn: Function to classify bars to regime_id (optional)
        regime_config: RegimeDiscoveryConfig (optional, uses default if None)
        output_dir: Output directory
        num_stages: Number of discovery stages
        min_pf_threshold: PF threshold for focus recommendation

    Returns:
        Dict with stage results, analyses, and regime breakdowns
    """
    runner = RegimeIntegratedDiscoveryRunner(
        output_dir=output_dir,
        num_stages=num_stages,
        expansion_factor=1.5,
        regime_discovery_config=regime_config or create_default_regime_discovery_config(),
    )

    # Save regime configuration
    runner.save_regime_config()

    # Classify bars to regimes if classifier provided
    regime_labels = None
    if classifier_fn:
        print("\n[regime_runner] Classifying bars to regimes...")
        regime_labels = runner.classify_bars_to_regimes(bars_1m, classifier_fn)
        print(f"[regime_runner] Classified {len(regime_labels)} bars to regimes")

    all_results: Dict[int, Dict[str, Any]] = {}

    for stage in range(1, num_stages + 1):
        print(f"\n{'='*80}")
        print(f"STAGE {stage} — Regime-Integrated Discovery")
        print(f"{'='*80}\n")

        # Prepare config for this stage
        if stage == 1:
            stage_config = initial_config
        else:
            config_path = output_dir / f"{stage:02d}_quick_scan_expanded.yaml"
            if config_path.exists():
                with open(config_path, "r") as f:
                    stage_config = yaml.safe_load(f)
            else:
                stage_config = initial_config

        # Run discovery families with regime-specific configs
        family_results = {}

        if regime_labels and runner.regime_config.enabled:
            # Run regime-specific discovery
            print("[regime_runner] Running regime-specific discovery sweeps...")

            for regime_id in set(regime_labels):
                print(f"\n  [regime_runner] Running discovery for regime: {regime_id}")

                # Apply regime-specific config overrides
                regime_config_adjusted = runner.apply_regime_specific_discovery(
                    stage_config,
                    regime_id,
                )

                # Run families with regime config
                for family, run_fn in discovery_modules.items():
                    try:
                        result = run_fn(bars_1m, regime_config_adjusted)
                        if family not in family_results:
                            family_results[family] = {
                                "regime_results": {},
                                "sweep_results": result.get("sweep_results", []),
                            }
                        family_results[family]["regime_results"][regime_id] = result

                    except Exception as e:
                        print(f"    [ERROR] {family} in {regime_id}: {e}")
        else:
            # Standard discovery (no regime classification)
            for family, run_fn in discovery_modules.items():
                try:
                    print(f"[regime_runner] Running {family}...")
                    result = run_fn(bars_1m, stage_config)
                    family_results[family] = result
                except Exception as e:
                    print(f"[regime_runner] ERROR {family}: {e}")

        # Analyze stage results with regime breakdown
        analysis = runner.process_stage_results_with_regime_analysis(
            stage,
            family_results,
            discovery_results=None,  # Could be populated if individual trades available
            regime_labels=regime_labels,
            min_pf_threshold=min_pf_threshold,
        )

        all_results[stage] = {
            "family_results": family_results,
            "analysis": analysis,
            "regime_analysis": analysis.get("regime_analysis"),
        }

        if stage < num_stages:
            focus_families = analysis["focus_families"]
            print(f"\n[regime_runner] Focus families for next stage: {focus_families}")

    print(f"\n{'='*80}")
    print("REGIME-INTEGRATED DISCOVERY WORKFLOW COMPLETE")
    print(f"{'='*80}")

    return all_results
