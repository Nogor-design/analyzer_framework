from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.handoff import write_handoff_package
from ta_foundation.optimization.template_generator import generate_broad_discovery_templates


def create_pass1_handoff(
    seed_template: str | Path,
    output_dir: str | Path,
    *,
    start_hours: Sequence[int] = (0, 4, 8, 12, 16, 20),
    modes: Sequence[str] = ("breakout", "regression"),
) -> Path:
    destination = Path(output_dir)
    generated_dir = destination / "generated_templates"
    handoff_dir = destination / "team_handoff"

    if generated_dir.exists():
        shutil.rmtree(generated_dir)
    generate_broad_discovery_templates(
        seed_template,
        generated_dir,
        start_hours=start_hours,
        modes=modes,
    )
    write_handoff_package(generated_dir, handoff_dir, recursive=True)
    return handoff_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a pass-1 Pantheon optimizer team handoff from a seed XML.")
    parser.add_argument("--seed-template", required=True, help="Seed Strategy Analyzer XML template.")
    parser.add_argument("--output-dir", required=True, help="Folder where generated templates and handoff should be written.")
    parser.add_argument("--start-hours", default="0,4,8,12,16,20", help="Comma-separated start hours.")
    parser.add_argument("--modes", default="breakout,regression", help="Comma-separated modes.")
    args = parser.parse_args(argv)

    start_hours = tuple(int(part.strip()) for part in args.start_hours.split(",") if part.strip())
    modes = tuple(part.strip().lower() for part in args.modes.split(",") if part.strip())
    handoff_dir = create_pass1_handoff(
        args.seed_template,
        args.output_dir,
        start_hours=start_hours,
        modes=modes,
    )
    print(f"Created pass-1 handoff at {handoff_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
