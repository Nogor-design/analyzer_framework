"""
Stage Coordinator — Manages multi-stage discovery workflows with dynamic expansion.

Workflow:
  1. Run stage N with current config
  2. Analyze results for winners (PF >= threshold)
  3. Auto-expand parameters for winners
  4. Generate stage N+1 config
  5. Save to YAML for next run (or auto-continue if in-memory)

Example usage:
    coordinator = DiscoveryStageCoordinator(output_dir="./discovery_results")

    # Run stage 1
    stage1_cfg = load_yaml("01_quick_scan.yaml")
    stage1_results = run_candle_discovery(bars_1m, stage1_cfg)

    # Auto-generate and save stage 2
    stage2_cfg = coordinator.expand_for_next_stage(
        current_stage=1,
        current_config=stage1_cfg,
        current_results=stage1_results,
    )
    coordinator.save_stage_config(stage2_cfg, stage=2)
"""

from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dynamic_params import (
    recommend_param_expansion,
    generate_expanded_config,
    print_recommendation_summary,
    rank_results,
)


class DiscoveryStageCoordinator:
    """Orchestrates multi-stage discovery with dynamic parameter expansion."""

    def __init__(
        self,
        output_dir: str | Path = "./discovery_results",
        expansion_factor: float = 1.5,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.expansion_factor = expansion_factor
        self.stage_history: Dict[int, Dict[str, Any]] = {}

    def expand_for_next_stage(
        self,
        current_stage: int,
        current_config: Dict[str, Any],
        current_results: List[Dict[str, Any]] | Dict[str, Any],
        min_pf_threshold: float = 1.2,
    ) -> Dict[str, Any]:
        """
        Analyze current stage results and generate next stage config.

        Parameters
        ----------
        current_stage          : e.g., 1 for 01_quick_scan
        current_config         : the YAML config used for current stage
        current_results        : output dict (sweep_results list inside)
        min_pf_threshold       : only expand families with PF >= this

        Returns
        -------
        New config dict for next stage
        """
        # Extract sweep_results from either format
        if isinstance(current_results, dict):
            sweep_results = current_results.get("sweep_results", [])
        else:
            sweep_results = current_results

        if not sweep_results:
            print(f"[stage_coordinator] No results to expand for stage {current_stage}")
            return current_config

        # Analyze and recommend
        rec = recommend_param_expansion(
            sweep_results,
            expansion_factor=self.expansion_factor,
            min_pf_threshold=min_pf_threshold,
        )

        print(print_recommendation_summary(rec))

        # Generate next config
        next_config = generate_expanded_config(
            current_config,
            sweep_results,
            expansion_factor=self.expansion_factor,
        )

        # Store history
        self.stage_history[current_stage] = {
            "config": current_config,
            "results_count": len(sweep_results),
            "recommendations": rec,
            "next_config": next_config,
        }

        return next_config

    def save_stage_config(
        self,
        config: Dict[str, Any],
        stage: int,
        suffix: str = "",
    ) -> Path:
        """
        Save a stage config to YAML.

        Parameters
        ----------
        config   : config dict
        stage    : stage number (e.g., 2)
        suffix   : optional suffix (e.g., "_expanded")

        Returns
        -------
        Path to saved file
        """
        stage_filename = f"{stage:02d}_scan_expanded{suffix}.yaml"
        filepath = self.output_dir / stage_filename

        with open(filepath, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        print(f"[stage_coordinator] Saved stage {stage} config to {filepath}")
        return filepath

    def save_analysis(
        self,
        stage: int,
        results: List[Dict[str, Any]],
    ) -> Path:
        """
        Save stage results and recommendations to JSON.

        Parameters
        ----------
        stage    : stage number
        results  : sweep_results list

        Returns
        -------
        Path to saved file
        """
        if stage not in self.stage_history:
            return Path()

        history = self.stage_history[stage]
        rec = history.get("recommendations", {})

        output = {
            "stage": stage,
            "results_count": len(results),
            "top_results": rank_results(results)[:10],
            "recommendations": rec.get("recommendations", {}),
            "summary": rec.get("summary", ""),
        }

        filepath = self.output_dir / f"stage_{stage:02d}_analysis.json"

        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, default=str)

        print(f"[stage_coordinator] Saved stage {stage} analysis to {filepath}")
        return filepath

    def get_top_signals(
        self,
        stage: int,
        top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get top N results from a stored stage.

        Parameters
        ----------
        stage   : stage number
        top_n   : how many to return

        Returns
        -------
        List of top results
        """
        if stage not in self.stage_history:
            return []

        history = self.stage_history[stage]
        # Note: would need to store full results to return here
        # For now just return summary
        return []


def interactive_discovery_flow(
    bars_1m,
    discovery_modules: Dict[str, Any],
    stage_configs_dir: Path,
    output_dir: Path = "./discovery_results",
    num_stages: int = 2,
    min_pf_threshold: float = 1.2,
) -> Dict[str, Any]:
    """
    Run a multi-stage discovery workflow interactively.

    Parameters
    ----------
    bars_1m             : 1-minute OHLCV DataFrame
    discovery_modules   : {module_name: run_fn}
                          e.g., {"candle": run_candle_discovery}
    stage_configs_dir   : directory with 01_*.yaml, 02_*.yaml configs
    output_dir          : where to save results and generated configs
    num_stages          : how many stages to run
    min_pf_threshold    : PF threshold for parameter expansion

    Returns
    -------
    {stage_1: results, stage_2: results, ...}

    Example:
        results = interactive_discovery_flow(
            bars_1m=bars_1m,
            discovery_modules={"candle": run_candle_discovery},
            stage_configs_dir=Path("./discovery"),
            output_dir=Path("./discovery_results"),
            num_stages=3,
        )
    """
    coordinator = DiscoveryStageCoordinator(output_dir, expansion_factor=1.5)
    all_results: Dict[int, Dict[str, Any]] = {}

    for stage in range(1, num_stages + 1):
        print(f"\n{'='*70}")
        print(f"STAGE {stage}")
        print(f"{'='*70}\n")

        # Load config for this stage
        config_path = stage_configs_dir / f"{stage:02d}_quick_scan.yaml"
        if not config_path.exists() and stage > 1:
            # Use auto-generated config from previous stage
            config_path = output_dir / f"{stage:02d}_scan_expanded.yaml"

        if not config_path.exists():
            print(f"[interactive_discovery] Config not found: {config_path}")
            break

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        print(f"[interactive_discovery] Loaded config from {config_path}")

        # Run discovery
        for module_name, run_fn in discovery_modules.items():
            try:
                print(f"[interactive_discovery] Running {module_name}...")
                results = run_fn(bars_1m, config)
                all_results[stage] = results
                print(f"[interactive_discovery] {module_name} complete: "
                      f"{results.get('n_results', 0)} results from "
                      f"{results.get('n_combinations_run', 0)} combos")

                # Save analysis
                coordinator.save_analysis(stage, results.get("sweep_results", []))

                # Auto-expand for next stage (if not the last)
                if stage < num_stages:
                    next_config = coordinator.expand_for_next_stage(
                        current_stage=stage,
                        current_config=config,
                        current_results=results,
                        min_pf_threshold=min_pf_threshold,
                    )
                    coordinator.save_stage_config(next_config, stage=stage + 1)

            except Exception as e:
                print(f"[interactive_discovery] ERROR in {module_name}: {e}")
                import traceback
                traceback.print_exc()

    return all_results
