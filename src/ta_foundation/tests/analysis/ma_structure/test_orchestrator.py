from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ta_foundation.analysis.ma_structure.orchestrator import (
    _coerce_bars_candidate,
    attach_anchor_interaction_failure,
    run_anchor_interaction_analysis,
)

def _sample_bars() -> pd.DataFrame:
    idx = pd.date_range("2025-02-03 08:00", periods=240, freq="min", tz="America/Denver")
    close = 100 + np.cumsum(np.random.default_rng(3).normal(0, 0.2, len(idx)))
    return pd.DataFrame(
        {
            "dt": idx,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100,
        }
    )


def test_coerce_bars_candidate_extracts_dataframe_from_nested_tuple() -> None:
    bars = _sample_bars()
    wrapped = ({"meta": "x"}, ("ignored", {"bars": bars}))

    out = _coerce_bars_candidate(wrapped)

    assert isinstance(out, pd.DataFrame)
    assert out.equals(bars)


@dataclass
class _TupleMarket:
    bars: pd.DataFrame

    def get_bars(self, instrument_root=None, contract=None, timeframe="1m"):
        return ({"instrument": instrument_root, "contract": contract}, (self.bars, {"source": timeframe}))


@dataclass
class _Pkg:
    run_id: str = "BronzeApolloGod"
    assets: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


def test_run_anchor_interaction_analysis_accepts_tuple_wrapped_bars() -> None:
    pkg = _Pkg()
    market = _TupleMarket(_sample_bars())
    options = {
        "enabled": True,
        "instrument": "NQ",
        "contract": "H25",
        "anchors": [{"family": "EMA", "length": 21, "source": "close"}],
        "tp_sl": {
            "enabled": True,
            "tp_grid": [0.8],
            "sl_grid": [0.8],
            "folds": {"mode": "blocked_kfold", "min_train_segments": 10, "min_test_segments": 5},
        },
    }

    out = run_anchor_interaction_analysis(pkg=pkg, market=market, options=options)

    assert out["ok"] is True
    assert "anchor_interaction" in pkg.assets
    assert isinstance(pkg.assets["anchor_interaction"]["tp_sl_candidates"], pd.DataFrame)
    assert isinstance(pkg.assets["anchor_interaction"]["validation_folds"], pd.DataFrame)
    assert isinstance(pkg.assets["anchor_interaction"]["tp_sl_candidates"], pd.DataFrame)
    assert isinstance(pkg.assets["anchor_interaction"]["validation_folds"], pd.DataFrame)
    assert "validation_folds" in pkg.metadata["derived"]["anchor_interaction"]["artifacts"]
    assert pkg.metadata["derived"]["anchor_interaction"]["diagnostics"]["validation_fold_count"] >= 0


def test_attach_anchor_interaction_failure_populates_metadata_envelope() -> None:
    pkg = _Pkg()

    meta = attach_anchor_interaction_failure(
        pkg=pkg,
        reason="anchor_interaction_exception: ValueError: sample failure",
        options={
            "enabled": True,
            "instrument": "NQ",
            "contract": "H25",
            "anchors": [{"family": "EMA", "length": 21, "source": "close"}],
            "tp_sl": {"folds": {"mode": "anchored_walk_forward", "min_train_segments": 10, "min_test_segments": 5}},
        },
    )

    assert meta["disabled"] is True
    assert meta["engine"]["timezone"] == "America/Denver"
    assert meta["artifacts"]["validation_folds"]["path"] is None
    assert meta["diagnostics"]["warnings"] == ["anchor_interaction_exception: ValueError: sample failure"]


def test_attach_anchor_interaction_failure_populates_metadata_envelope() -> None:
    pkg = _Pkg()

    meta = attach_anchor_interaction_failure(
        pkg=pkg,
        reason="anchor_interaction_exception: ValueError: sample failure",
        options={
            "enabled": True,
            "instrument": "NQ",
            "contract": "H25",
            "anchors": [{"family": "EMA", "length": 21, "source": "close"}],
            "tp_sl": {"folds": {"mode": "anchored_walk_forward", "min_train_segments": 10, "min_test_segments": 5}},
        },
    )

    assert meta["disabled"] is True
    assert meta["engine"]["timezone"] == "America/Denver"
    assert meta["artifacts"]["validation_folds"]["path"] is None
    assert meta["diagnostics"]["warnings"] == ["anchor_interaction_exception: ValueError: sample failure"]
