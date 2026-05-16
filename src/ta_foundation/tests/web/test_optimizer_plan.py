from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_plan import build_plan_preview, parameter_step_count
from ta_foundation.web.optimizer_session import ParameterConfig, create_session


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path)
    yield
    opt_session.set_storage_root(None)


def _make_session(parameters: list[dict], *, cap: int = 5000):
    session = create_session(
        strategy_id="FakeStrategy",
        seed_template_path="C:/seed.xml",
        instrument="NQ",
    )
    session.update(parameters=parameters, chunking={"max_combinations_per_chunk": cap})
    return session


def test_parameter_step_count_for_fixed_and_swept():
    fixed = ParameterConfig(name="x", type_name="int", mode="fixed", fixed_value=5)
    assert parameter_step_count(fixed) == 1

    sweep = ParameterConfig(
        name="averageSlow", type_name="int", mode="optimize",
        minimum=50, maximum=200, increment=50,
    )
    assert parameter_step_count(sweep) == 4  # 50, 100, 150, 200


def test_parameter_step_count_bool_sweep():
    sweep = ParameterConfig(name="Long", type_name="bool", mode="optimize")
    assert parameter_step_count(sweep) == 2


def test_plan_preview_single_chunk_when_below_cap():
    session = _make_session(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
            {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
        ],
        cap=100,
    )
    plan = build_plan_preview(session.load_document())
    assert plan.combination_estimate == 4
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.combination_count == 4
    assert chunk.optimized[0].name == "averageSlow"
    # Fixed parameters still recorded at the chunk level for downstream template patching
    assert any(f.name == "MaxStop" and f.value == 100 for f in chunk.fixed)


def test_plan_preview_slices_offending_parameter_when_over_cap():
    # averageFast 2..10 step 1 = 9 steps; averageSlow 50..500 step 50 = 10 steps.
    # 9 * 10 = 90 total. Cap at 20 should force averageSlow to slice.
    session = _make_session(
        parameters=[
            {"name": "averageFast", "type_name": "int", "mode": "optimize",
             "minimum": 2, "maximum": 10, "increment": 1},
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 500, "increment": 50},
        ],
        cap=20,
    )
    plan = build_plan_preview(session.load_document())
    assert plan.combination_estimate == 90
    assert len(plan.chunks) >= 2
    for chunk in plan.chunks:
        assert chunk.combination_count <= 20
    # Each chunk should carry the full averageFast sweep, and a slice of averageSlow
    optimized_names_per_chunk = [
        {sweep.name for sweep in c.optimized} for c in plan.chunks
    ]
    for names in optimized_names_per_chunk:
        assert {"averageFast", "averageSlow"} <= names


def test_plan_hash_is_stable_across_rebuilds():
    session = _make_session(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
        ],
        cap=100,
    )
    doc = session.load_document()
    h1 = build_plan_preview(doc).plan_hash
    h2 = build_plan_preview(doc).plan_hash
    assert h1 == h2


def test_plan_hash_changes_when_range_changes():
    session = _make_session(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
        ],
        cap=100,
    )
    h1 = build_plan_preview(session.load_document()).plan_hash
    session.update(parameters=[
        {"name": "averageSlow", "type_name": "int", "mode": "optimize",
         "minimum": 50, "maximum": 250, "increment": 50},
    ])
    h2 = build_plan_preview(session.load_document()).plan_hash
    assert h1 != h2


def test_plan_warns_when_no_parameters():
    session = _make_session(parameters=[])
    plan = build_plan_preview(session.load_document())
    assert "no_parameters_configured" in plan.warnings
