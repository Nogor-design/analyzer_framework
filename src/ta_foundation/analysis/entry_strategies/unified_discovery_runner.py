"""
Unified Discovery Runner — End-to-end discovery with cross-family optimization.

Orchestrates the full discovery workflow:
1. Run all 8 families at stage 1
2. Build unified leaderboard
3. Identify focus families
4. Auto-generate stage 2 configs focusing on winners
5. Run deeper discovery on focus families only

This eliminates manual decision-making about which families to pursue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import yaml

from .cross_family_optimizer import (
    build_unified_leaderboard,
    suggest_next_focus,
    print_leaderboard,
    print_family_summary,
)
from .dynamic_params import (
    generate_expanded_config,
    print_recommendation_summary,
)


class UnifiedDiscoveryRunner:
    """Runs multi-family discovery with automatic focus on winners."""

    def __init__(
        self,
        output_dir: str | Path = "./discovery_results",
        num_stages: int = 2,
        expansion_factor: float = 1.5,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.num_stages = num_stages
        self.expansion_factor = expansion_factor
        self.stage_results: Dict[int, Dict[str, Any]] = {}
        self.leaderboards: Dict[int, List] = {}

    def process_stage_results(
        self,
        stage: int,
        family_results: Dict[str, Dict[str, Any]],
        min_pf_threshold: float = 1.2,
    ) -> Dict[str, Any]:
        """
        Process results from a stage of discovery.

        Parameters
        ----------
        stage             : stage number (1, 2, 3, ...)
        family_results    : {family_name: result_dict}
                           e.g., {"candle": {...}, "ma": {...}, ...}
        min_pf_threshold  : PF threshold for inclusion

        Returns
        -------
        Analysis dict with leaderboard and suggestions
        """
        # Extract sweep_results from each family
        all_sweep_results = {}
        for family, result in family_results.items():
            sweep_results = result.get("sweep_results", [])
            if sweep_results:
                all_sweep_results[family] = sweep_results

        # Build unified leaderboard
        leaderboard = build_unified_leaderboard(
            all_sweep_results,
            min_trades=20,
            top_n=100,
        )

        self.leaderboards[stage] = leaderboard

        # Get focus suggestions
        suggestion = suggest_next_focus(leaderboard, top_n_families=3)

        # Save outputs
        self._save_leaderboard(stage, leaderboard)
        self._save_analysis(stage, suggestion)

        # Print summaries
        print(print_leaderboard(leaderboard, top_n=30))
        print(print_family_summary(suggestion))

        analysis = {
            "stage": stage,
            "leaderboard_size": len(leaderboard),
            "focus_families": suggestion["focus_families"],
            "skip_families": suggestion["skip_families"],
            "reasoning": suggestion["reasoning"],
        }

        self.stage_results[stage] = analysis
        return analysis

    def generate_focused_configs(
        self,
        stage: int,
        stage1_config: Dict[str, Any],
        family_results: Dict[str, Dict[str, Any]],
        focus_families: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Generate stage N+1 configs, focusing on winner families only.

        Parameters
        ----------
        stage               : current stage
        stage1_config       : original config from stage 1
        family_results      : results from this stage
        focus_families      : list of families to deep-dive (from suggestion)

        Returns
        -------
        {family: next_config}
        """
        configs = {}

        for family in focus_families:
            result = family_results.get(family)
            if not result:
                continue

            sweep_results = result.get("sweep_results", [])
            if not sweep_results:
                continue

            # Auto-expand this family's config
            next_config = generate_expanded_config(
                stage1_config,
                sweep_results,
                expansion_factor=self.expansion_factor,
            )

            # Disable other families
            for other_fam in [
                "candle_discovery",
                "ma_discovery",
                "orb_discovery",
                "bb_discovery",
                "lcr_discovery",
                "breakout_discovery",
                "pullback_discovery",
                "level_discovery",
            ]:
                if other_fam in next_config:
                    # Keep family's section but set enabled: false if not in focus
                    if family not in other_fam:
                        next_config[other_fam]["enabled"] = False

            configs[family] = next_config

        return configs

    def _save_leaderboard(self, stage: int, leaderboard: List) -> Path:
        """Save leaderboard as JSON."""
        data = [cand.to_dict() for cand in leaderboard]
        filepath = self.output_dir / f"stage_{stage:02d}_leaderboard.json"
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[unified_runner] Saved leaderboard to {filepath}")
        return filepath

    def _save_analysis(self, stage: int, suggestion: Dict[str, Any]) -> Path:
        """Save analysis as JSON."""
        # Make summary JSON-safe
        summary = {
            "stage": stage,
            "focus_families": suggestion["focus_families"],
            "skip_families": suggestion["skip_families"],
            "reasoning": suggestion["reasoning"],
            "family_stats": {
                k: {
                    "best_rank": v["best_rank"],
                    "count": v["count"],
                    "best_pf": v["best_pf"],
                    "avg_pf": v["avg_pf"],
                    "avg_score": v.get("avg_score", 0),
                }
                for k, v in suggestion.get("summary", {}).items()
            },
        }

        filepath = self.output_dir / f"stage_{stage:02d}_analysis.json"
        with open(filepath, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[unified_runner] Saved analysis to {filepath}")
        return filepath

    def save_focused_configs(
        self,
        stage: int,
        focused_configs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Path]:
        """Save focused configs for next stage."""
        paths = {}

        for family, config in focused_configs.items():
            filename = f"{stage + 1:02d}_quick_scan_{family}_focused.yaml"
            filepath = self.output_dir / filename
            with open(filepath, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            print(f"[unified_runner] Saved {family} focused config to {filepath}")
            paths[family] = filepath

        return paths

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all stages processed."""
        return {
            "stages_processed": len(self.stage_results),
            "stages": self.stage_results,
        }


def run_unified_discovery_workflow(
    bars_1m,
    discovery_modules: Dict[str, Any],
    initial_config: Dict[str, Any],
    output_dir: Path = Path("./discovery_results"),
    num_stages: int = 2,
    min_pf_threshold: float = 1.2,
) -> Dict[int, Dict[str, Any]]:
    """
    Run unified discovery workflow: all families → leaderboard → focus → repeat.

    Parameters
    ----------
    bars_1m             : 1-minute OHLCV DataFrame
    discovery_modules   : {module_name: run_fn}
    initial_config      : config dict (e.g., from 01_quick_scan.yaml)
    output_dir          : where to save results
    num_stages          : how many stages to run
    min_pf_threshold    : PF threshold for focus recommendation

    Returns
    -------
    {stage: {results, analysis, configs}}

    Example:
        results = run_unified_discovery_workflow(
            bars_1m=bars_1m,
            discovery_modules={
                "candle": run_candle_discovery,
                "ma": run_ma_discovery,
                ...
            },
            initial_config=quick_scan_cfg,
            output_dir=Path("./discovery_results"),
            num_stages=2,
        )
    """
    runner = UnifiedDiscoveryRunner(
        output_dir=output_dir,
        num_stages=num_stages,
        expansion_factor=1.5,
    )

    all_results: Dict[int, Dict[str, Any]] = {}

    for stage in range(1, num_stages + 1):
        print(f"\n{'='*80}")
        print(f"STAGE {stage} — Running all discovery families")
        print(f"{'='*80}\n")

        # Prepare config for this stage
        if stage == 1:
            stage_config = initial_config
        else:
            # Load auto-generated config from previous stage
            config_path = output_dir / f"{stage:02d}_quick_scan_expanded.yaml"
            if config_path.exists():
                with open(config_path, "r") as f:
                    stage_config = yaml.safe_load(f)
            else:
                print(f"[unified_runner] No config found for stage {stage}, using previous")
                stage_config = initial_config

        # Run all discovery families
        family_results = {}
        for family, run_fn in discovery_modules.items():
            try:
                print(f"[unified_runner] Running {family}...")
                result = run_fn(bars_1m, stage_config)
                family_results[family] = result
                print(
                    f"[unified_runner]   {family}: "
                    f"{result.get('n_results', 0)} results from "
                    f"{result.get('n_combinations_run', 0)} combos"
                )
            except Exception as e:
                print(f"[unified_runner] ERROR {family}: {e}")

        # Analyze stage results
        analysis = runner.process_stage_results(
            stage,
            family_results,
            min_pf_threshold=min_pf_threshold,
        )

        # Generate configs for next stage if not final
        if stage < num_stages:
            focus_families = analysis["focus_families"]
            print(f"\n[unified_runner] Focus families for next stage: {focus_families}")

            focused_configs = runner.generate_focused_configs(
                stage,
                stage_config,
                family_results,
                focus_families,
            )

            config_paths = runner.save_focused_configs(stage, focused_configs)

            # Save a master config combining all focused families
            master_config = stage_config.copy()
            for family, config in focused_configs.items():
                # Merge focused config into master
                for key, val in config.items():
                    if key not in ["report", "discovery"]:
                        master_config[key] = val

            master_path = output_dir / f"{stage + 1:02d}_quick_scan_master.yaml"
            with open(master_path, "w") as f:
                yaml.dump(master_config, f, default_flow_style=False, sort_keys=False)
            print(f"[unified_runner] Saved master config to {master_path}")

        all_results[stage] = {
            "family_results": family_results,
            "analysis": analysis,
        }

    print(f"\n{'='*80}")
    print("UNIFIED DISCOVERY WORKFLOW COMPLETE")
    print(f"{'='*80}")
    print(f"\nSummary: {runner.get_summary()}")

    return all_results
