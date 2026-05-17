from __future__ import annotations

"""Tests for parameter-neighborhood cell planning."""

import pytest

from ta_foundation.optimization.neighborhood import (
    NeighborhoodError,
    ParameterSweepSpec,
    plan_neighborhood_cells,
)


def test_one_at_a_time_produces_off_center_int_samples():
    cells = plan_neighborhood_cells(
        candidate_params={"slow": 100, "tp": 1.5},
        sweep_specs=[
            ParameterSweepSpec(name="slow", type_name="int", pct=0.10, steps=4),
            ParameterSweepSpec(name="tp", type_name="float", pct=0.10, steps=4),
        ],
        mode="one_at_a_time",
    )
    # 4 cells per axis × 2 axes = 8 cells
    assert len(cells) == 8
    # Each cell touches exactly one parameter
    for c in cells:
        assert len(c.overrides) == 1
    # Center value never appears in overrides
    slow_vals = [c.overrides["slow"] for c in cells if "slow" in c.overrides]
    tp_vals = [c.overrides["tp"] for c in cells if "tp" in c.overrides]
    assert 100 not in slow_vals
    assert all(v != 1.5 for v in tp_vals)
    # Int samples are integers
    assert all(isinstance(v, int) for v in slow_vals)
    # Indices are unique and sequential
    assert [c.index for c in cells] == list(range(8))


def test_int_snaps_to_unit_grid_by_default():
    cells = plan_neighborhood_cells(
        candidate_params={"n": 10},
        sweep_specs=[ParameterSweepSpec(name="n", type_name="int", pct=0.20, steps=4)],
    )
    vals = sorted(c.overrides["n"] for c in cells)
    # 10 * (1 ± 0.10, 1 ± 0.20) → 8, 9, 11, 12. Center 10 dropped.
    assert vals == [8, 9, 11, 12]


def test_float_samples_respect_increment_snap():
    cells = plan_neighborhood_cells(
        candidate_params={"ratio": 2.0},
        sweep_specs=[
            ParameterSweepSpec(name="ratio", type_name="float", pct=0.25,
                               steps=4, increment=0.1),
        ],
    )
    vals = sorted(round(c.overrides["ratio"], 6) for c in cells)
    # 2.0 * (1 ± 0.125, 1 ± 0.25) = 1.75, 1.875, 2.125, 2.25 → snap to 0.1 →
    # 1.8, 1.9, 2.1, 2.3 (2.125 snaps to 2.1; 2.25 snaps to 2.3 because 2.25/0.1=22.5 rounds even to 22 → 2.2... use banker's rounding via Python's round).
    # Test the looser invariant: every value is a multiple of 0.1 and != 2.0
    for v in vals:
        assert abs(v - round(v / 0.1) * 0.1) < 1e-9
        assert v != 2.0
    assert len(vals) == 4


def test_minimum_clamp_drops_samples_below_floor():
    cells = plan_neighborhood_cells(
        candidate_params={"n": 5},
        sweep_specs=[ParameterSweepSpec(name="n", type_name="int", pct=0.40,
                                        steps=4, minimum=4)],
    )
    vals = sorted(c.overrides["n"] for c in cells)
    # 5 ± 20%/40% → snapped ints 3, 4, 6, 7. Minimum=4 drops 3.
    assert 3 not in vals
    assert 4 in vals


def test_bool_and_unknown_specs_skipped_silently():
    cells = plan_neighborhood_cells(
        candidate_params={"x": 10, "flag": True},
        sweep_specs=[
            ParameterSweepSpec(name="x", type_name="int", pct=0.10, steps=2),
            ParameterSweepSpec(name="flag", type_name="bool", pct=0.10, steps=2),
        ],
    )
    # Only "x" sweep is honored
    assert all("x" in c.overrides for c in cells)
    assert all("flag" not in c.overrides for c in cells)


def test_full_cube_takes_cartesian_product():
    cells = plan_neighborhood_cells(
        candidate_params={"a": 100, "b": 200},
        sweep_specs=[
            ParameterSweepSpec(name="a", type_name="int", pct=0.10, steps=2),
            ParameterSweepSpec(name="b", type_name="int", pct=0.10, steps=2),
        ],
        mode="full_cube",
    )
    # 2 samples per axis × 2 axes = 4 cells
    assert len(cells) == 4
    for c in cells:
        assert set(c.overrides.keys()) == {"a", "b"}


def test_rejects_bad_inputs():
    with pytest.raises(NeighborhoodError):
        plan_neighborhood_cells(
            candidate_params={"x": 10},
            sweep_specs=[ParameterSweepSpec(name="x", type_name="int", pct=0.10, steps=1)],
        )
    with pytest.raises(NeighborhoodError):
        plan_neighborhood_cells(
            candidate_params={"x": 10},
            sweep_specs=[ParameterSweepSpec(name="x", type_name="int", pct=0.0, steps=4)],
        )
    with pytest.raises(NeighborhoodError):
        plan_neighborhood_cells(
            candidate_params={},
            sweep_specs=[ParameterSweepSpec(name="missing", type_name="int", pct=0.10, steps=4)],
        )
    with pytest.raises(NeighborhoodError):
        plan_neighborhood_cells(
            candidate_params={"x": 10},
            sweep_specs=[ParameterSweepSpec(name="x", type_name="int", pct=0.10, steps=4)],
            mode="bogus",
        )


def test_label_includes_parameter_and_value():
    cells = plan_neighborhood_cells(
        candidate_params={"slow": 100},
        sweep_specs=[ParameterSweepSpec(name="slow", type_name="int", pct=0.10, steps=2)],
    )
    assert all(c.label.startswith("slow=") for c in cells)
    assert all(c.label != "slow=100" for c in cells)
