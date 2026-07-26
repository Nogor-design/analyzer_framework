from __future__ import annotations

from datetime import date

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.strategy_session_momentum_board import (
    render_strategy_session_momentum_board,
)
from ta_foundation.reports.session_momentum_board import (
    active_window_for_pkg,
    build_strategy_session_momentum_board,
    classify_strategy_session,
    build_stackability_summary,
)
from ta_foundation.reports.text.export_strategy_session_momentum_text import (
    export_strategy_session_momentum_text,
)


def _pkg(run_id: str, day_pnls: dict[str, float], *, bot_name: str | None = None, start_hour: int | None = None, start_minute: int | None = None) -> AnalysisPackage:
    settings_rows = []
    if bot_name is not None:
        settings_rows.append({"item": "Bot_Name", "value": bot_name})
    if start_hour is not None:
        settings_rows.append({"item": "Start_Time_(HH)", "value": start_hour})
    if start_minute is not None:
        settings_rows.append({"item": "Start_Time_(mm)", "value": start_minute})

    return AnalysisPackage(
        run_id=run_id,
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(sorted(day_pnls.keys())),
                "net_profit": [day_pnls[d] for d in sorted(day_pnls.keys())],
                "trade_count": [1 if day_pnls[d] != 0 else 0 for d in sorted(day_pnls.keys())],
            }
        ),
        settings=pd.DataFrame(settings_rows) if settings_rows else None,
    )


def test_classify_strategy_session_prefers_name_token_and_falls_back_to_time() -> None:
    rise_pkg = _pkg("RisePoseidon-NQ", {"2026-04-14": 100.0}, bot_name="Rise Poseidon", start_hour=8, start_minute=30)
    ny_pkg = _pkg("AtlasCustom-NQ", {"2026-04-14": 100.0}, bot_name="Atlas Custom", start_hour=8, start_minute=30)

    rise = classify_strategy_session(rise_pkg.run_id, rise_pkg)
    ny = classify_strategy_session(ny_pkg.run_id, ny_pkg)

    assert rise["label"] == "London Early"
    assert rise["source"] == "name_token"
    assert ny["label"] == "NY Open"
    assert ny["source"] == "settings_start_time"


def test_active_window_for_pkg_reads_start_and_duration() -> None:
    pkg = _pkg(
        "RisePoseidon-NQ",
        {"2026-04-14": 100.0},
        start_hour=2,
        start_minute=0,
    )
    pkg.settings = pd.DataFrame(
        [
            {"item": "Start_Time_(HH)", "value": 2},
            {"item": "Start_Time_(mm)", "value": 0},
            {"item": "Duration_Time_(HH)", "value": 3},
            {"item": "Duration_Time_(mm)", "value": 0},
        ]
    )

    window = active_window_for_pkg(pkg)

    assert window["label"] == "02:00-05:00 MT"
    assert window["duration_label"] == "3h"


def test_active_window_for_pkg_accepts_variant_settings_labels() -> None:
    pkg = _pkg("RisePoseidon-NQ", {"2026-04-14": 100.0})
    pkg.settings = pd.DataFrame(
        [
            {"item": "Start Time (HH)", "value": 1},
            {"item": "Start Time (mm)", "value": 0},
            {"item": "Duration Time (HH)", "value": 4},
            {"item": "Duration Time (mm)", "value": 0},
        ]
    )

    window = active_window_for_pkg(pkg)

    assert window["label"] == "01:00-05:00 MT"
    assert window["duration_label"] == "4h"


def test_strategy_session_momentum_groups_rows_by_session() -> None:
    packages = {
        "DawnAtlas-NQ": _pkg("DawnAtlas-NQ", {"2026-04-08": 50.0, "2026-04-09": 60.0, "2026-04-10": 70.0, "2026-04-13": 80.0, "2026-04-14": 90.0}),
        "RisePoseidon-NQ": _pkg("RisePoseidon-NQ", {"2026-04-08": 20.0, "2026-04-09": 30.0, "2026-04-10": 40.0, "2026-04-13": 50.0, "2026-04-14": 60.0}),
        "PrimingAres-NQ": _pkg("PrimingAres-NQ", {"2026-04-08": 10.0, "2026-04-09": 15.0, "2026-04-10": 20.0, "2026-04-13": 25.0, "2026-04-14": 30.0}),
        "AtlasCustom-NQ": _pkg("AtlasCustom-NQ", {"2026-04-08": 5.0, "2026-04-09": 10.0, "2026-04-10": 15.0, "2026-04-13": 20.0, "2026-04-14": 25.0}, start_hour=8, start_minute=30),
    }

    board = build_strategy_session_momentum_board(
        packages,
        as_of=date.fromisoformat("2026-04-14"),
        overall_top_n=5,
        top_n_per_session=5,
    )

    labels = {group["label"]: [row["run_id"] for row in group["rows"]] for group in board["groups"]}
    assert "DawnAtlas-NQ" in labels["Asia"]
    assert "RisePoseidon-NQ" in labels["London Early"]
    assert "PrimingAres-NQ" in labels["London Late"]
    assert "AtlasCustom-NQ" in labels["NY Open"]


def test_stackability_marks_overlap_and_safe_windows() -> None:
    packages = {
        "RiseAlpha-NQ": _pkg("RiseAlpha-NQ", {"2026-04-08": 100.0, "2026-04-09": 100.0, "2026-04-10": 100.0, "2026-04-13": 100.0, "2026-04-14": 100.0}, start_hour=1, start_minute=0),
        "RiseBeta-NQ": _pkg("RiseBeta-NQ", {"2026-04-08": 90.0, "2026-04-09": 90.0, "2026-04-10": 90.0, "2026-04-13": 90.0, "2026-04-14": 90.0}, start_hour=2, start_minute=0),
        "CloseGamma-NQ": _pkg("CloseGamma-NQ", {"2026-04-08": 80.0, "2026-04-09": 80.0, "2026-04-10": 80.0, "2026-04-13": 80.0, "2026-04-14": 80.0}, start_hour=13, start_minute=0),
    }
    for pkg, duration in zip(packages.values(), [180, 120, 120]):
        pkg.settings = pd.DataFrame(
            [
                {"item": "Start_Time_(HH)", "value": int(pkg.settings.iloc[0]["value"])},
                {"item": "Start_Time_(mm)", "value": int(pkg.settings.iloc[1]["value"])},
                {"item": "Duration_Time_(HH)", "value": duration // 60},
                {"item": "Duration_Time_(mm)", "value": 0},
            ]
        )

    board = build_strategy_session_momentum_board(
        packages,
        as_of=date.fromisoformat("2026-04-14"),
        overall_top_n=3,
        top_n_per_session=3,
        overlap_compare_top_n=3,
    )

    alpha = next(r for r in board["rows"] if r["run_id"] == "RiseAlpha-NQ")
    beta = next(r for r in board["rows"] if r["run_id"] == "RiseBeta-NQ")
    gamma = next(r for r in board["rows"] if r["run_id"] == "CloseGamma-NQ")

    assert beta["stackability"]["status"] == "conflict"
    assert "Overlaps" in beta["stackability"]["label"]
    assert gamma["stackability"]["status"] == "safe"
    assert "Safe" in gamma["stackability"]["label"]
    assert alpha["stackability"]["status"] in {"conflict", "safe"}


def test_strategy_session_momentum_renderer_contains_group_titles() -> None:
    packages = {
        "RisePoseidon-NQ": _pkg("RisePoseidon-NQ", {"2026-04-08": 20.0, "2026-04-09": 30.0, "2026-04-10": 40.0, "2026-04-13": 50.0, "2026-04-14": 60.0}, start_hour=2, start_minute=0),
        "CoilingAtlas-NQ": _pkg("CoilingAtlas-NQ", {"2026-04-08": 5.0, "2026-04-09": 10.0, "2026-04-10": 15.0, "2026-04-13": 20.0, "2026-04-14": 25.0}),
    }
    packages["RisePoseidon-NQ"].settings = pd.DataFrame(
        [
            {"item": "Start_Time_(HH)", "value": 2},
            {"item": "Start_Time_(mm)", "value": 0},
            {"item": "Duration_Time_(HH)", "value": 3},
            {"item": "Duration_Time_(mm)", "value": 0},
        ]
    )

    html = render_strategy_session_momentum_board(
        {
            "packages": packages,
            "options": {"as_of_date": "2026-04-14", "overall_top_n": 5, "top_n_per_session": 5},
        }
    )

    assert "Strategy Session Momentum Board" in html
    assert "Best All Around" in html
    assert "London Early" in html
    assert "Pre-Market" in html
    assert "Window" in html
    assert "02:00-05:00 MT" in html
    assert 'class="tf-sess-grid"' in html
    assert "Stackability" in html


def test_strategy_session_momentum_text_export_writes_groups(tmp_path) -> None:
    pkg = _pkg("RisePoseidon-NQ", {"2026-04-08": 20.0, "2026-04-09": 30.0, "2026-04-10": 40.0, "2026-04-13": 50.0, "2026-04-14": 60.0})
    pkg.settings = pd.DataFrame(
        [
            {"item": "Start_Time_(HH)", "value": 2},
            {"item": "Start_Time_(mm)", "value": 0},
            {"item": "Duration_Time_(HH)", "value": 3},
            {"item": "Duration_Time_(mm)", "value": 0},
        ]
    )
    packages = {"RisePoseidon-NQ": pkg}

    out_path = tmp_path / "session-momentum.txt"
    export_strategy_session_momentum_text(
        packages,
        out_path,
        options={"as_of_date": "2026-04-14"},
    )

    text = out_path.read_text(encoding="utf-8")
    assert "BEST ALL AROUND" in text
    assert "LONDON EARLY" in text
    assert "RisePoseidon-NQ" in text
    assert "02:00-05:00 MT" in text
    assert "Stackability" in text
