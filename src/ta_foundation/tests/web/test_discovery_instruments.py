from __future__ import annotations

import pytest

from ta_foundation.web.discovery_instruments import (
    clear_custom_instruments,
    default_instrument,
    get_instrument,
    list_instruments,
    register_custom_instrument,
)


@pytest.fixture(autouse=True)
def _reset_custom_instruments():
    clear_custom_instruments()
    yield
    clear_custom_instruments()


def test_nq_tick_value_is_five_dollars_not_twelve_fifty():
    """The legacy discovery YAMLs encode tick_value=12.50 for NQ, which is
    actually the ES value. The UI's source of truth must use the correct
    NQ value of $5.00 per tick (1 point = $20)."""
    nq = get_instrument("NQ")
    assert nq is not None
    assert nq.tick_size == 0.25
    assert nq.tick_value == 5.00
    assert nq.point_value == 20.00


def test_es_tick_value_is_twelve_fifty():
    es = get_instrument("ES")
    assert es is not None
    assert es.tick_value == 12.50
    assert es.point_value == 50.00


def test_micro_contracts_are_one_tenth_size():
    nq = get_instrument("NQ")
    mnq = get_instrument("MNQ")
    es = get_instrument("ES")
    mes = get_instrument("MES")
    assert mnq.tick_value == nq.tick_value / 10
    assert mes.tick_value == es.tick_value / 10


def test_lookup_is_case_insensitive():
    assert get_instrument("nq") is not None
    assert get_instrument("Nq") is not None
    assert get_instrument(" NQ ") is not None


def test_lookup_unknown_returns_none():
    assert get_instrument("BOGUS") is None
    assert get_instrument("") is None
    assert get_instrument(None) is None  # type: ignore[arg-type]


def test_list_instruments_includes_canonical_set():
    payload = list_instruments()
    symbols = {item["symbol"] for item in payload}
    expected = {"NQ", "MNQ", "ES", "MES", "YM", "RTY", "M2K", "CL", "GC", "MGC", "NG", "6E"}
    assert expected.issubset(symbols)


def test_list_instruments_entries_are_json_safe():
    for item in list_instruments():
        assert isinstance(item, dict)
        assert isinstance(item["symbol"], str)
        assert isinstance(item["tick_size"], float)
        assert isinstance(item["tick_value"], float)
        assert isinstance(item["rth"], dict)
        for key in ("hour_from", "minute_from", "hour_to", "label"):
            assert key in item["rth"]


def test_default_instrument_is_nq():
    assert default_instrument().symbol == "NQ"


def test_register_custom_instrument_round_trips():
    inst = register_custom_instrument(
        symbol="ZB",
        name="30-Year US T-Bond",
        tick_size=1.0 / 32.0,
        tick_value=31.25,
    )
    assert inst.is_custom is True
    assert inst.symbol == "ZB"
    assert get_instrument("zb") is inst
    payload = list_instruments()
    assert any(item["symbol"] == "ZB" for item in payload)


def test_register_custom_cannot_overwrite_canonical():
    with pytest.raises(ValueError):
        register_custom_instrument(
            symbol="NQ",
            name="not allowed",
            tick_size=0.25,
            tick_value=99.0,
        )


def test_register_custom_validates_inputs():
    with pytest.raises(ValueError):
        register_custom_instrument(symbol="", name="x", tick_size=0.25, tick_value=5.0)
    with pytest.raises(ValueError):
        register_custom_instrument(symbol="XX", name="x", tick_size=0.0, tick_value=5.0)
    with pytest.raises(ValueError):
        register_custom_instrument(symbol="XX", name="x", tick_size=0.25, tick_value=0.0)


def test_register_custom_derives_point_value_when_omitted():
    inst = register_custom_instrument(
        symbol="ZX",
        name="Test contract",
        tick_size=0.10,
        tick_value=2.0,
    )
    # 1.0 point / 0.10 tick = 10 ticks per point. 10 * $2 = $20 per point.
    assert inst.point_value == pytest.approx(20.0)


def test_rth_sessions_use_denver_local_hours():
    """Every RTH window must look like a discovery session_filter so the
    web layer can splice it directly into generated YAML."""
    for item in list_instruments():
        rth = item["rth"]
        assert 0 <= rth["hour_from"] <= 23
        assert 0 <= rth["minute_from"] <= 59
        assert 0 < rth["hour_to"] <= 24
        assert rth["hour_from"] < rth["hour_to"]
