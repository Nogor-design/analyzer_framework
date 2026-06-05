from __future__ import annotations

from pathlib import Path

import pandas as pd

from ta_foundation.analysis.exits.simulate import ExitSimConfig
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_template_quality_features import (
    exit_robustness_for_template,
    template_quality_features,
)


def _template_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate><Strategy><PantheonMasterBotV01TesterV2>
<MaxTrades>3</MaxTrades><ProfitStop>9000</ProfitStop>
<MaxStop>100</MaxStop><LossStop>500</LossStop><MaxTPRatio>1.0</MaxTPRatio>
</PantheonMasterBotV01TesterV2></Strategy></StrategyTemplate>"""


def _trades() -> pd.DataFrame:
    tz = "America/Denver"
    return pd.DataFrame(
        [
            {
                "market_pos": "Long",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "entry_time": pd.Timestamp("2026-01-05 06:10:00", tz=tz),
                "exit_time": pd.Timestamp("2026-01-05 06:20:00", tz=tz),
                "profit": 40.0,
                "mae": 10.0,
                "mfe": 80.0,
            },
            {
                "market_pos": "Short",
                "entry_price": 104.0,
                "exit_price": 103.0,
                "entry_time": pd.Timestamp("2026-01-05 06:22:00", tz=tz),
                "exit_time": pd.Timestamp("2026-01-05 06:28:00", tz=tz),
                "profit": 20.0,
                "mae": 15.0,
                "mfe": 45.0,
            },
        ]
    )


def _market() -> MarketDataStore:
    tz = "America/Denver"
    dt = pd.date_range("2026-01-05 05:55:00", periods=45, freq="1min", tz=tz)
    prices = [
        99.0,
        99.5,
        100.0,
        100.5,
        101.0,
        101.5,
        102.0,
        102.5,
        103.0,
        103.5,
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        104.0,
        103.0,
        102.0,
        101.0,
        102.0,
        103.0,
        104.0,
        103.5,
        103.0,
        102.5,
        102.0,
        102.5,
        103.0,
        103.5,
        104.0,
        103.0,
        102.0,
        101.0,
        100.0,
        100.5,
        101.0,
        101.5,
        102.0,
        102.5,
        103.0,
        103.5,
        104.0,
        104.5,
        105.0,
    ]
    ticks = pd.DataFrame(
        {
            "dt": dt,
            "last": prices,
            "bid": [p - 0.25 for p in prices],
            "ask": [p + 0.25 for p in prices],
            "volume": [1] * len(prices),
        }
    )
    store = MarketDataStore()
    store.put_ticks("NQ", "06-26", ticks)
    store.finalize()
    return store


def test_exit_robustness_for_template_populates_policy_margin() -> None:
    features = exit_robustness_for_template(
        _trades(),
        _market(),
        instrument="NQ",
        contract="06-26",
        run_id="F_001",
        current_net=60.0,
        cfg=ExitSimConfig(atr_tf="1m", atr_period=2),
    )
    assert features["best_policy"]
    assert isinstance(features["exit_robustness_margin"], float)
    assert isinstance(features["exit_rank_spread"], float)


def _write_trades_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "Trade number,Instrument,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Profit,MAE,MFE",
                "1,NQ 06-26,Long,1,100,101,2026-01-05 06:00:00,2026-01-05 06:10:00,20,10,40",
                "2,NQ 06-26,Short,1,102,103,2026-01-06 06:00:00,2026-01-06 06:10:00,-20,30,35",
            ]
        ),
        encoding="utf-8",
    )


def test_template_quality_features_degrades_when_ticks_absent(tmp_path: Path) -> None:
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    seed = tmp_path / "seed.xml"
    seed.write_text("<StrategyTemplate />", encoding="utf-8")
    session = opt_session.create_session(
        label="quality",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path=str(seed),
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    template = tmp_path / "F1.xml"
    template.write_text(_template_xml(), encoding="utf-8")
    _write_trades_csv(
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "nt8_backtest_results"
        / "F_001"
        / "Trades.csv"
    )

    features = template_quality_features(
        session,
        {
            "run_id": "F_001",
            "template_path": str(template),
            "total_net_profit": 0.0,
        },
        market=None,
    )

    assert features["prop_max_daily_loss"] == 999.0
    assert features["effective_trades"] == 1
    assert features["best_policy"] is None
    assert features["exit_robustness_margin"] is None
    assert features["daily_green_pct"] == 0.5
    opt_session.set_storage_root(None)
