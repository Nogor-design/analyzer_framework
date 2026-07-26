"""Configure a FULL deployment-matrix session for PantheonMaster with the
RELAXED regime pin (EnableDiscoveryFilters=False -> full coverage, MA-cross-parity
entry; AtrTrail exit kept as the single variable under test).

Mirrors the prior A/B (opt_3d12659e4be8) exactly except for the relaxed overrides:
  - strategy PantheonMaster, NQ 06-26, OOS 2026-05-01..2026-06-03
  - floor=10 on stage_1 + refine selection
Prints SESSION_ID on the last line. Does NOT dispatch — pair with
drive_recipe_to_complete.py to run it on NT.
"""
from __future__ import annotations

from ta_foundation.web.optimizer_session import create_session
from ta_foundation.web.optimizer_recipe import save_recipe
from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan
from ta_foundation.web.optimizer_deployment_matrix import (
    build_deployment_matrix_recipe,
    pantheonmaster_recipe_overrides,
    load_naming_rules,
)

STRATEGY = "PantheonMaster"
INSTRUMENT = "NQ 06-26"
SEED = r"C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy\PantheonMaster\PantheonMaster_recipe_seed.xml"
OOS_FROM = "2026-05-01"
OOS_TO = "2026-06-03"


def main() -> int:
    rules = load_naming_rules()
    recipe = build_deployment_matrix_recipe(
        strategy_id=STRATEGY,
        recipe_name="Deployment Matrix PantheonMaster relaxed regime",
        rules=rules,
        refine_selection_min_trades=10,
        **pantheonmaster_recipe_overrides(),
    )

    session = create_session(
        label="PantheonMaster relaxed regime (full coverage)",
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

    base = {e["param"]: e for e in recipe["base_matrix"]}
    print(f"EnableDiscoveryFilters pin: {base['EnableDiscoveryFilters']['value']}")
    print(f"RegimeMode pin: {base['RegimeMode']['value']}  exit: {base['DiscoveryExitPolicy']['value']}")
    print(f"stage_1 root jobs (template_count): {plan.template_count}")
    print(f"stages: {[s.stage_id for s in plan.stages]}")
    print(f"SESSION_ID={session.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
