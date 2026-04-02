from __future__ import annotations

import numpy as np
import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.regime_recommender.recommender import recommend_parameters
from ta_foundation.analysis.regime_recommender.orchestrator import compute_and_attach_regime_recommendation


RNG = np.random.default_rng(11)


def _make_minute_bars(n: int = 7 * 24 * 60) -> pd.DataFrame:
    dt = pd.date_range("2026-03-15 00:00", periods=n, freq="1min", tz="America/Denver")
    base = 20000 + np.linspace(0, 80, n)
    noise = RNG.normal(0, 2.0, n).cumsum()
    close = base + noise
    return pd.DataFrame(
        {
            "dt": dt,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": RNG.integers(50, 500, n),
        }
    )


def test_recommend_parameters_returns_no_trade_when_confidence_low():
    strategy_profile = {"defaults": {"MaxStop": 200, "averageFast": 50, "averageSlow": 200}, "parameters": {}}
    regime = {"confidence": 0.1, "primary": "range", "secondary": []}
    features = {"feature_values": {}}

    out = recommend_parameters(strategy_profile, regime, features, cfg={"min_confidence": 0.5})
    assert out["decision"] == "NO_TRADE"


def test_orchestrator_attaches_payload_and_exports_templates(tmp_path):
    market = MarketDataStore()
    market.put_minute_bars("NQ", "03-26", _make_minute_bars())

    pkg = AnalysisPackage(run_id="PantheonMasterBotV01TesterV2", metadata={"derived": {}})

    compute_and_attach_regime_recommendation(
        pkg=pkg,
        market=market,
        options={
            "strategy_id": "PantheonMasterBotV01TesterV2",
            "instrument": "NQ",
            "contract": "03-26",
            "source_template": "sampleTemplate.xml",
            "template_output_dir": str(tmp_path),
            "generate_templates": True,
            "recommender": {"min_confidence": 0.0},
            "persist_store_path": str(tmp_path / "rr_store.jsonl"),
        },
    )

    rr = pkg.metadata.get("derived", {}).get("regime_recommender", {})
    assert rr.get("version") == "rr_v1"
    assert "snapshot" in rr
    assert "regime" in rr
    assert "recommendation" in rr
    assert "outcomes" in rr
    assert "storage" in rr

    bundle = rr.get("template_bundle", {})
    templates = bundle.get("templates", [])
    assert len(templates) == 5
    for item in templates:
        assert item.get("path")
