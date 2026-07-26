from pathlib import Path

from ta_foundation.optimization.workflow import create_pass1_handoff
from ta_foundation.tests.optimization.test_template_generator import _seed_template


def test_create_pass1_handoff_generates_templates_and_run_plan(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)

    handoff_dir = create_pass1_handoff(
        seed,
        tmp_path / "package",
        start_hours=(0,),
        modes=("breakout",),
    )

    assert (tmp_path / "package" / "generated_templates" / "breakout" / "Pass1_Breakout_Start00.xml").exists()
    assert (handoff_dir / "run_plan.csv").exists()
    assert (
        handoff_dir
        / "templates"
        / "breakout"
        / "pass_1_broad_discovery"
        / "Pass1_Breakout_Start00.xml"
    ).exists()
