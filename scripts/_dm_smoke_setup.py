"""Configure a TINY deployment-matrix smoke session (1 session x Reverse[F,T] x 1 tier,
tiny inner sweeps) so it finishes on NT quickly. Prints the session_id on the last line.

This exercises the genuinely new pieces end-to-end against live NT:
  - matrix_bundle_axis (Session -> StartTimeH/M + DurationTimeH/M) pinned in NT XML
  - the 3-stage auto-chain (stage_1 -> refine_risk -> final_backtest)
  - derived single_multi selection in refine_risk

Not committed long-term; lives under scripts/ as a one-shot harness.
"""
from __future__ import annotations

from ta_foundation.web.optimizer_session import create_session
from ta_foundation.web.optimizer_recipe import save_recipe
from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan
from ta_foundation.web.optimizer_deployment_matrix import (
    build_deployment_matrix_recipe,
    load_naming_rules,
)

STRATEGY = "PantheonMasterBotV01TesterV2"
INSTRUMENT = "NQ 06-26"
SEED = r".ta_artifacts\web_optimizer\recipe_seeds\PantheonMasterBotV01TesterV2\PantheonMasterBotV01TesterV2_recipe_seed.xml"
OOS_FROM = "2026-05-01"
OOS_TO = "2026-06-03"


def main() -> int:
    rules = load_naming_rules()  # real D:\templateNaming\naming_rules.json
    recipe = build_deployment_matrix_recipe(
        strategy_id=STRATEGY,
        recipe_name="DeploymentMatrix SMOKE (tiny)",
        rules=rules,
        average_fast=5,
        max_stop=(50, 100, 50),        # 2 values
        max_tp_ratio=(1.0, 1.0, 1.0),  # 1 value
        profit_stop=(500, 500, 500),   # 1 value (multi side)
        loss_stop=(500, 500, 500),     # 1 value
        max_trades=(1, 3, 2),          # [1, 3] -> exercises single + multi
    )

    # Shrink the base matrix to the smallest slice that still exercises the
    # bundle axis + god/monster + single/multi: 1 session x 2 reverse x 1 tier.
    for entry in recipe["base_matrix"]:
        if entry["param"] == "Session":
            entry["values"] = entry["values"][:1]
        elif entry["param"] == "averageSlow":
            entry["values"] = entry["values"][:1]

    session = create_session(
        label="DeploymentMatrix smoke",
        strategy_id=STRATEGY,
        seed_template_path=SEED,
        instrument=INSTRUMENT,
        market_suffix="NQ",
    )
    doc = session.load_document()
    doc.oos_from_date = OOS_FROM
    doc.oos_to_date = OOS_TO
    session.save_document(doc)

    save_recipe(session, recipe)
    plan = build_and_save_recipe_plan(session)

    print(f"base_matrix Session values: 1 | Reverse: 2 | averageSlow: 1")
    print(f"stage_1 root jobs (template_count): {plan.template_count}")
    print(f"stages: {[s.stage_id for s in plan.stages]}")
    print(f"SESSION_ID={session.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
