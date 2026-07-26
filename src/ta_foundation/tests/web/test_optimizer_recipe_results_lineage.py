from __future__ import annotations

"""Regression tests for child-stage parent lineage recovery.

NinjaTrader truncates per-run output-FOLDER names past the point where the
manifest ``bucket_id`` token survives, so a parsed row's ``batch_id`` cannot be
matched to its manifest entry by substring. Without the BatchRunSummary
folder->full-name bridge, ``parent_candidate_id`` is dropped and refine_risk
selection collapses to ~4 finalists instead of one-per-parent (deployment matrix
lineage bug, 2026-06-05).
"""

import pandas as pd

from ta_foundation.web.optimizer_recipe_results import (
    _enrich_results,
    _load_batch_name_map,
)


# A realistic truncated folder name vs the full template name NT records in
# BatchRunSummary. The full name carries the parent identity; the folder does not.
_TRUNCATED = "refine_risk__parent_stage_1_stage_1_0001_75d07bd3"
_FULL = (
    "refine_risk__parent_stage_1_stage_1_starttimeh_00_starttimem_00"
    "_0002_d05c2e1_row00001__opt_maxprofitfactor"
)
_PARENT = "stage_1__stage_1__starttimeh_00__starttimem_00_0002_d05c2e1__row00001"
_BUCKET = "parent_stage_1_stage_1_starttimeh_00_starttimem_00_0002_d05c2e1_row00001"


def _manifest() -> dict[str, dict]:
    return {
        _FULL: {
            "template_id": _FULL,
            "bucket_id": _BUCKET,
            "parent_candidate_id": _PARENT,
            "optimization_target": "MaxProfitFactor",
            "matrix_values": {},
            "fixed_values": {"averageSlow": 100},
        }
    }


def test_load_batch_name_map_bridges_truncated_folder(tmp_path) -> None:
    summary = pd.DataFrame(
        {
            "Template": [_FULL],
            "Output folder": [rf"D:\sessions\opt_x\nt_output\refine_risk\{_TRUNCATED}"],
        }
    )
    summary.to_csv(tmp_path / "BatchRunSummary.csv", index=False)

    name_map = _load_batch_name_map(tmp_path)
    assert name_map[_TRUNCATED] == _FULL


def test_enrich_recovers_parent_via_name_map() -> None:
    # The parsed row only knows the truncated folder name as its batch_id.
    df = pd.DataFrame({"batch_id": [_TRUNCATED], "profit_factor": [1.8], "total_trades": [42]})

    # Without the bridge the lineage is lost (locks in the bug).
    no_bridge = _enrich_results(df, recipe_id="r", stage_id="refine_risk", manifest=_manifest())
    assert no_bridge["parent_candidate_id"].isna().all()

    # With the bridge the parent resolves exactly.
    fixed = _enrich_results(
        df,
        recipe_id="r",
        stage_id="refine_risk",
        manifest=_manifest(),
        name_map={_TRUNCATED: _FULL},
    )
    assert fixed["parent_candidate_id"].iloc[0] == _PARENT
    assert fixed["optimizer_target"].iloc[0] == "MaxProfitFactor"
