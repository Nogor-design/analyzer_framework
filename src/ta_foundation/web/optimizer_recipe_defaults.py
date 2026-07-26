from __future__ import annotations

"""Default recipe builders for upgrading a standard optimizer session."""

from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


def build_recipe_from_session(session: OptimizerSession) -> dict[str, Any]:
    doc = session.load_document()
    fixed: list[dict[str, Any]] = []
    optimize: dict[str, dict[str, Any]] = {}

    for param in doc.parameters:
        if param.mode == "fixed":
            fixed.append({
                "param": param.name,
                "role": "fixed",
                "value": param.fixed_value,
            })
        elif param.mode == "optimize":
            optimize[param.name] = {
                "min": param.minimum,
                "max": param.maximum,
                "step": param.increment,
            }

    target = max(1, min(8, int(doc.chunking.keep_best_results or 4)))
    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": f"rec_{doc.session_id.removeprefix('opt_')}",
        "recipe_name": doc.label or f"{doc.strategy_id} recipe",
        "strategy_id": doc.strategy_id,
        "target_final_candidates": target,
        "safety_caps": {
            "max_total_combinations": 250000,
            "max_templates_per_stage": 250,
        },
        "base_matrix": fixed,
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Recipe stage generated from the standard optimizer session.",
                "optimize_inside_template": optimize,
                "selection": {
                    "group_by": [],
                    "keep_per_group": target,
                    "target_total_candidates": target,
                    "rank_by": "portfolio_score",
                    "tie_breakers": [
                        "lower_drawdown",
                        "higher_trade_count",
                        "higher_net_profit",
                    ],
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_1.selected_rows",
                "description": "Final fixed Backtest validation.",
            },
        ],
    }
