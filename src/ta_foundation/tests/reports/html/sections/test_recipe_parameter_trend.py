from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ta_foundation.optimization.model import OptimizationBatch, OptimizationStore
from ta_foundation.reports.html.sections.recipe_parameter_trend import (
    render_recipe_parameter_trend,
)


def _batch(bid: str, rows: list[dict]) -> OptimizationBatch:
    df = pd.DataFrame(rows)
    return OptimizationBatch(
        batch_id=bid,
        source_path=Path(f"{bid}_Optimization.csv"),
        strategy_name=bid,
        instrument="NQ 06-26",
        imported_at=datetime.now(timezone.utc),
        results=df,
        parameter_names=["MaxStop", "averageSlow"],
        metric_columns=["total_net_profit", "total_trades"],
        warnings=[],
        row_count=len(df),
        successfully_parsed_rows=len(df),
    )


def _store_with_upper_boundary() -> OptimizationStore:
    # MaxStop trends UP monotonically -> best at the top of the swept range.
    # averageSlow is pinned (constant) -> must be skipped, not charted.
    rows = []
    for stop, net in [(50, 1000), (100, 2000), (150, 3000), (200, 5000)]:
        rows.append({
            "param_MaxStop": stop,
            "param_averageSlow": 100,
            "total_net_profit": net,
            "total_trades": 40,
        })
    store = OptimizationStore()
    store.add(_batch("stage_1__a", rows))
    store.add(_batch("stage_1__b", [dict(r, total_net_profit=r["total_net_profit"] + 200) for r in rows]))
    return store


def test_renders_pooled_chart_and_flags_upper_boundary():
    store = _store_with_upper_boundary()
    html = render_recipe_parameter_trend({"optimization_store": store, "options": {}})

    # MaxStop varies -> charted; averageSlow pinned -> not charted.
    assert "MaxStop" in html
    assert "averageSlow" not in html
    # Best value sits at the top of the range -> upper-boundary next-run advice.
    assert "Suggested Next Runs" in html
    assert "run a stage above 200" in html
    # Pooled across both batches (8 rows total).
    assert "data:image/png" in html


def test_no_boundary_when_best_is_interior():
    rows = [
        {"param_MaxStop": 50, "param_averageSlow": 100, "total_net_profit": 1000, "total_trades": 40},
        {"param_MaxStop": 100, "param_averageSlow": 100, "total_net_profit": 9000, "total_trades": 40},
        {"param_MaxStop": 150, "param_averageSlow": 100, "total_net_profit": 1200, "total_trades": 40},
    ]
    store = OptimizationStore()
    store.add(_batch("stage_1__a", rows))
    html = render_recipe_parameter_trend({"optimization_store": store, "options": {}})
    assert "No boundary winners" in html


def test_min_trades_guard_and_empty_store():
    assert "No optimization data" in render_recipe_parameter_trend({})

    # Every row filtered out by min_trades -> graceful message, no crash.
    rows = [{"param_MaxStop": 50, "param_averageSlow": 100, "total_net_profit": 1000, "total_trades": 3}]
    store = OptimizationStore()
    store.add(_batch("stage_1__a", rows))
    html = render_recipe_parameter_trend(
        {"optimization_store": store, "options": {"min_trades": 100}}
    )
    assert "nothing varies" in html or "No boundary winners" in html or "No pooled rows" in html
