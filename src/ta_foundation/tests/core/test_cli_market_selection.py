from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from ta_foundation.cli.main import _select_bars_1m_from_market


def test_select_bars_prefers_configured_instrument_merged_contract():
    es = pd.DataFrame({"close": [1]})
    nq = pd.DataFrame({"close": [2]})
    market = SimpleNamespace(
        minute_bars={
            ("ES", ""): es,
            ("NQ", "03-26"): pd.DataFrame({"close": [3]}),
            ("NQ", ""): nq,
        }
    )

    assert _select_bars_1m_from_market(market, "NQ") is nq


def test_select_bars_falls_back_to_specific_contract_for_configured_instrument():
    es = pd.DataFrame({"close": [1]})
    nq = pd.DataFrame({"close": [2]})
    market = SimpleNamespace(minute_bars={("ES", ""): es, ("NQ", "03-26"): nq})

    assert _select_bars_1m_from_market(market, "NQ") is nq


def test_select_bars_keeps_legacy_fallback_without_preferred_instrument():
    es = pd.DataFrame({"close": [1]})
    nq = pd.DataFrame({"close": [2]})
    market = SimpleNamespace(minute_bars={("ES", ""): es, ("NQ", ""): nq})

    assert _select_bars_1m_from_market(market, "") is es
