from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_session import (
    OptimizerSessionNotFoundError,
    clone_session,
    create_session,
    delete_session,
    get_session,
    list_sessions,
    require_session,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path)
    yield
    opt_session.set_storage_root(None)


def test_create_session_persists_initial_document():
    session = create_session(
        label="Pantheon NQ broad",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="C:/seed.xml",
        instrument="NQ",
        market_suffix="NQ",
    )
    assert session.id.startswith("opt_")
    doc = session.load_document()
    assert doc.label == "Pantheon NQ broad"
    assert doc.strategy_id == "PantheonMasterBotV01TesterV2"
    assert doc.chunking.max_combinations_per_chunk == 5000


def test_update_round_trips_parameters_and_guardrails():
    session = create_session(strategy_id="FakeStrategy")
    session.update(
        parameters=[
            {
                "name": "averageSlow",
                "type_name": "int",
                "mode": "optimize",
                "minimum": 50,
                "maximum": 200,
                "increment": 50,
            },
            {
                "name": "MaxStop",
                "type_name": "int",
                "mode": "fixed",
                "fixed_value": 100,
            },
        ],
        guardrails={"max_drawdown_dollars": 2500, "min_trades": 10},
        chunking={"max_combinations_per_chunk": 200},
    )

    doc = session.load_document()
    assert len(doc.parameters) == 2
    assert doc.parameters[0].mode == "optimize"
    assert doc.parameters[1].fixed_value == 100
    assert doc.guardrails.max_drawdown_dollars == 2500.0
    assert doc.guardrails.min_trades == 10
    assert doc.chunking.max_combinations_per_chunk == 200


def test_list_sessions_orders_by_updated_at(tmp_path):
    first = create_session(label="alpha")
    second = create_session(label="beta")
    # bump the second one
    second.update(label="beta updated")
    rows = list_sessions()
    labels = [r["label"] for r in rows]
    assert labels[0] == "beta updated"
    assert "alpha" in labels


def test_delete_session_removes_directory():
    session = create_session(strategy_id="X")
    sid = session.id
    assert get_session(sid) is not None
    assert delete_session(sid) is True
    assert get_session(sid) is None


def test_require_session_raises_for_unknown():
    with pytest.raises(OptimizerSessionNotFoundError):
        require_session("opt_does_not_exist")


def test_save_and_load_plan_round_trip():
    session = create_session(strategy_id="X")
    payload = {"plan_hash": "abc", "chunks": [{"chunk_id": "chunk_001"}]}
    session.save_plan(payload)
    assert session.load_plan() == payload


def test_clone_session_copies_config_but_not_outputs():
    source = create_session(
        label="proven session",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="C:/seed.xml",
        instrument="NQ 06-26",
    )
    source.update(
        parameters=[
            {"name": "averageSlow", "type_name": "int", "mode": "optimize",
             "minimum": 50, "maximum": 200, "increment": 50},
        ],
        guardrails={"max_drawdown_dollars": 2500, "min_trades": 10},
        oos_from_date="2026-04-14",
        oos_to_date="2026-05-14",
    )
    # Drop a fake nt_output file into source's directory so we can prove it
    # is not copied to the clone.
    (source.directory / "nt_output").mkdir(parents=True, exist_ok=True)
    (source.directory / "nt_output" / "marker.txt").write_text("source", encoding="utf-8")

    clone = clone_session(source)
    cd = clone.load_document()
    sd = source.load_document()

    assert clone.id != source.id
    assert cd.strategy_id == sd.strategy_id
    assert cd.seed_template_path == sd.seed_template_path
    assert cd.instrument == sd.instrument
    assert cd.oos_from_date == "2026-04-14"
    assert len(cd.parameters) == 1
    assert cd.parameters[0].name == "averageSlow"
    assert cd.guardrails.max_drawdown_dollars == 2500
    # Plan hash must match — same config means same plan hash.
    assert cd.plan_hash() == sd.plan_hash()
    # Outputs must NOT be copied.
    assert not (clone.directory / "nt_output").exists()
    # Label gets a "(refined)" suffix by default.
    assert "refined" in cd.label.lower()


def test_plan_hash_is_stable_and_distinguishes_configs():
    a = create_session(strategy_id="PantheonMasterBotV01TesterV2", seed_template_path="C:/seed.xml", instrument="NQ 06-26")
    a.update(parameters=[
        {"name": "averageSlow", "type_name": "int", "mode": "optimize", "minimum": 50, "maximum": 200, "increment": 50},
        {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
    ])
    h_a = a.load_document().plan_hash()

    # Same config, different session -> same hash.
    b = create_session(strategy_id="PantheonMasterBotV01TesterV2", seed_template_path="C:/seed.xml", instrument="NQ 06-26")
    b.update(parameters=[
        {"name": "averageSlow", "type_name": "int", "mode": "optimize", "minimum": 50, "maximum": 200, "increment": 50},
        {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
    ])
    assert b.load_document().plan_hash() == h_a

    # Different parameter range -> different hash.
    c = create_session(strategy_id="PantheonMasterBotV01TesterV2", seed_template_path="C:/seed.xml", instrument="NQ 06-26")
    c.update(parameters=[
        {"name": "averageSlow", "type_name": "int", "mode": "optimize", "minimum": 50, "maximum": 300, "increment": 50},
        {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
    ])
    assert c.load_document().plan_hash() != h_a

    # Parameter ordering must not affect the hash.
    d = create_session(strategy_id="PantheonMasterBotV01TesterV2", seed_template_path="C:/seed.xml", instrument="NQ 06-26")
    d.update(parameters=[
        {"name": "MaxStop", "type_name": "int", "mode": "fixed", "fixed_value": 100},
        {"name": "averageSlow", "type_name": "int", "mode": "optimize", "minimum": 50, "maximum": 200, "increment": 50},
    ])
    assert d.load_document().plan_hash() == h_a
