from __future__ import annotations

"""
Discovery → recipe routing.
===========================
Turn an :class:`EdgeSpec` (a discovered edge) into the two artifacts the recipe
optimizer needs to *confirm* it in NinjaTrader:

  * a StrategyDiscoveryFilter seed template whose entry is pinned to the
    discovered structure / timeframe / pattern params (built by the existing
    nt_template_generator), and
  * an :class:`OptimizerRecipeDocument` that keeps the entry fixed and runs a
    tight sweep around the discovered stop/target so we learn whether the edge
    sits on a plateau or a fragile spike.

This module only *builds* the artifacts — it never launches NinjaTrader. The
operator (or a web route) persists them to a session and starts the run via the
existing RecipeRunOrchestrator. Comparing the NT result back to the discovery is
``edge_spec.compare_to_discovery``.
"""

import re
from pathlib import Path
from typing import Any, Optional

from ta_foundation.analysis.strategy_discovery.edge_spec import EdgeSpec
from ta_foundation.analysis.strategy_discovery.nt_template_generator import (
    _extract_entry_pattern_params,
    generate_nt_template,
)
from ta_foundation.web.optimizer_recipe import OptimizerRecipeDocument


CONFIRMATION_STRATEGY_ID = "StrategyDiscoveryFilter"

# StopTicks/TargetTicks are swept by the confirm stage, so they are NOT pinned
# in base_matrix.
_SWEPT_PARAMS = {"StopTicks", "TargetTicks"}


def _allow_flags(direction: int) -> tuple[bool, bool]:
    if direction == 1:
        return True, False
    if direction == -1:
        return False, True
    return True, True


def build_confirmation_seed_xml(edge: EdgeSpec, run_id: str = "edge_confirm") -> str:
    """Generate the StrategyDiscoveryFilter template XML with the discovered
    entry baked in. Used as the recipe seed baseline."""
    result = generate_nt_template(
        {},  # no discovery dict needed — options carry the entry explicitly
        run_id=run_id,
        options=edge.template_options(),
    )
    return result.xml_str


def build_confirmation_recipe(
    edge: EdgeSpec,
    *,
    recipe_id: Optional[str] = None,
    recipe_name: Optional[str] = None,
    stop_radius_ticks: int = 8,
    target_radius_ticks: int = 8,
    step_ticks: int = 4,
    keep_top: int = 5,
    max_total_combinations: int = 100_000,
) -> OptimizerRecipeDocument:
    """
    Build a confirmation recipe for ``edge``:

      * base_matrix fixes the trade direction (and regime, if the spec pins one);
        the entry signal + pattern thresholds come pinned from the seed.
      * one optimizer stage sweeps StopTicks / TargetTicks in a tight band
        around the discovered values.
      * a final fixed backtest on the selected rows.

    ``stop_radius_ticks`` / ``step_ticks`` control the sweep width; the defaults
    give a small plateau probe (e.g. 5×5 = 25 combinations).
    """
    stop = int(edge.stop_ticks or 60)
    target = int(edge.target_ticks or 90)
    step = max(1, int(step_ticks))

    allow_long, allow_short = _allow_flags(edge.direction)

    # Pin the full entry: enum (EntrySignal/TimingMode), bools (AllowLong/Short),
    # and every pattern threshold. _patch_fixed sets the strategy-section tag for
    # enums and pins the <Parameter> block for numerics, so the discovered entry
    # is reproduced exactly regardless of what the regenerated seed defaulted to.
    entry_params, _tf = _extract_entry_pattern_params(edge.template_options())
    entry_params["EntrySignal"] = edge.entry_signal

    base_matrix: list[dict[str, Any]] = [
        {"param": "AllowLong", "role": "fixed", "value": allow_long},
        {"param": "AllowShort", "role": "fixed", "value": allow_short},
    ]
    if edge.regime_mode:
        base_matrix.append({"param": "RegimeMode", "role": "fixed", "value": edge.regime_mode})

    for name, value in entry_params.items():
        if name in _SWEPT_PARAMS:
            continue
        base_matrix.append({"param": name, "role": "fixed", "value": value})

    optimize_inside = {
        "StopTicks": {
            "min": max(4, stop - int(stop_radius_ticks)),
            "max": stop + int(stop_radius_ticks),
            "step": step,
        },
        "TargetTicks": {
            "min": max(4, target - int(target_radius_ticks)),
            "max": target + int(target_radius_ticks),
            "step": step,
        },
    }

    rid = recipe_id or f"confirm_{edge.entry_signal.lower()}"
    rname = recipe_name or f"Confirm {edge.entry_signal} ({edge.timeframe_minutes}m)"

    payload: dict[str, Any] = {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": rid,
        "recipe_name": rname,
        "strategy_id": CONFIRMATION_STRATEGY_ID,
        "target_final_candidates": keep_top,
        "safety_caps": {
            "max_total_combinations": max_total_combinations,
            "max_templates_per_stage": 250,
        },
        "base_matrix": base_matrix,
        "active_targets": ["MaxProfitFactor"],
        "stages": [
            {
                "stage_id": "confirm",
                "stage_type": "optimizer",
                "description": (
                    f"Confirm discovered {edge.entry_signal} edge "
                    f"(rule: {edge.rule_str or 'n/a'}); sweep stop/target around "
                    f"{stop}/{target} ticks."
                ),
                "optimize_inside_template": optimize_inside,
                "selection": {
                    "group_by": [],
                    "keep_per_group": int(keep_top),
                    "rank_by": "portfolio_score",
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "confirm.selected_rows",
            },
        ],
    }

    return OptimizerRecipeDocument.from_dict(payload)


def _patch_seed_timeframe(text: str, timeframe_minutes: int) -> str:
    """Set the primary bar period inside <BarsPeriodSerializable> so the
    confirmation run uses the discovered timeframe. The data-series period is
    not a strategy parameter, so it can't be pinned via the recipe."""
    tf = max(1, int(timeframe_minutes or 1))

    def repl(m: re.Match[str]) -> str:
        block = m.group(0)
        block = re.sub(r"<BaseBarsPeriodValue>\d+</BaseBarsPeriodValue>",
                       f"<BaseBarsPeriodValue>{tf}</BaseBarsPeriodValue>", block, count=1)
        block = re.sub(r"<Value>\d+</Value>", f"<Value>{tf}</Value>", block, count=1)
        return block

    return re.sub(r"<BarsPeriodSerializable>.*?</BarsPeriodSerializable>",
                  repl, text, count=1, flags=re.DOTALL)


def prepare_confirmation_session(
    edge: EdgeSpec,
    *,
    instrument: str = "NQ 06-26",
    market_suffix: str = "NQ",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    label: Optional[str] = None,
    start: bool = False,
    recipe_kwargs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Assemble a confirmation run for ``edge``: regenerate the StrategyDiscoveryFilter
    seed, patch in the discovered timeframe, create a session, save the
    confirmation recipe, and build the plan.

    SAFE BY DEFAULT: with ``start=False`` (the default) NOTHING is dispatched to
    NinjaTrader — the function only persists artifacts and returns the plan for
    review. Pass ``start=True`` to actually launch via RecipeRunOrchestrator
    (which writes the AddOn command file). Do not start while another NT
    optimization is running.
    """
    from ta_foundation.web.optimizer_session import create_session
    from ta_foundation.web.optimizer_recipe import save_recipe
    from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan
    from ta_foundation.web.optimizer_strategy_catalog import regenerate_recipe_seed

    summary = regenerate_recipe_seed(
        CONFIRMATION_STRATEGY_ID,
        instrument=instrument,
        from_date=from_date,
        to_date=to_date,
    )
    seed_path = Path(summary.path)

    if edge.timeframe_minutes and int(edge.timeframe_minutes) > 1:
        text = seed_path.read_text(encoding="utf-8")
        seed_path.write_text(_patch_seed_timeframe(text, edge.timeframe_minutes), encoding="utf-8")

    label = label or f"Confirm {edge.entry_signal} {edge.timeframe_minutes}m"
    session = create_session(
        label=label,
        strategy_id=CONFIRMATION_STRATEGY_ID,
        seed_template_path=str(seed_path),
        instrument=instrument,
        market_suffix=market_suffix,
    )
    if from_date or to_date:
        session.update(oos_from_date=from_date or "", oos_to_date=to_date or "")

    recipe = build_confirmation_recipe(edge, **(recipe_kwargs or {}))
    save_recipe(session, recipe)
    plan = build_and_save_recipe_plan(session)

    status = None
    if start:
        from ta_foundation.web.optimizer_recipe_orchestrator import RecipeRunOrchestrator
        status = RecipeRunOrchestrator(session).start()

    return {
        "session": session,
        "recipe": recipe,
        "plan": plan,
        "status": status,
        "seed_path": str(seed_path),
        "started": bool(start),
    }
