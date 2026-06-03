from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from ta_foundation.reports.html.sections.final_template_bundle_basket import (
    render_final_template_bundle_basket,
)


def _pkg(start_hour: int, pnl: list[float]) -> SimpleNamespace:
    dates = pd.date_range("2026-01-01", periods=len(pnl), freq="D")
    daily = pd.DataFrame({
        "Period": dates,
        "Net profit": pnl,
        "Trades": [1 for _ in pnl],
    })
    settings = pd.DataFrame([
        {"item": "StartTimeH", "value": str(start_hour)},
    ])
    return SimpleNamespace(
        daily=daily,
        settings=settings,
        metadata={"derived": {}},
        assets={},
        warnings=[],
    )


def test_final_template_bundle_basket_renders_one_per_time_bucket():
    html = render_final_template_bundle_basket({
        "packages": {
            "F_001": _pkg(0, [100, -10, 80]),
            "F_002": _pkg(0, [-50, 20, 30]),
            "F_003": _pkg(4, [70, 40, -5]),
            "F_004": _pkg(4, [-30, -40, 100]),
            "F_005": _pkg(8, [20, 30, 40]),
            "F_006": _pkg(8, [10, -90, 10]),
        },
        "options": {"show_chart": False, "top_n": 5},
    })

    assert "Runnable Bundle Basket" in html
    assert "StartTimeH_00" in html
    assert "StartTimeH_04" in html
    assert "StartTimeH_08" in html
    assert "6 candidates" in html
    assert "8 bundles scored" in html
