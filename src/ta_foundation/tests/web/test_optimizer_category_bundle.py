from __future__ import annotations

import csv
from pathlib import Path

from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_category_bundle import (
    CategoryBundleError,
    build_pruned_bundle,
    compute_category_bundle,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _member(run_id, *, stop, net, score, long_en="True", short_en="True", template, path):
    return {
        "run_id": run_id, "template_name": template, "bucket": "00-04", "side": "God",
        "reverse": "False", "slowMA": 100, "direction_shape": _dir(long_en, short_en),
        "status": "pass", "score": score, "net_profit": net, "profit_factor": 2.0,
        "max_drawdown": -500, "trades": 10, "percent_days_traded": 30, "average_fast": 5,
        "max_stop": stop, "max_tp_ratio": 1.5, "profit_stop": 10000, "loss_stop": 10000,
        "max_trades": 500, "long_enabled": long_en, "short_enabled": short_en,
        "template_path": str(path),
    }


def _dir(long_en, short_en):
    if long_en == "True" and short_en == "True":
        return "both"
    return "long_only" if long_en == "True" else "short_only"


def _seed(session_dir: Path) -> dict[str, Path]:
    data = session_dir / "deployment_package" / "weekly_coverage_package" / "data"
    tdir = session_dir / "deployment_package" / "weekly_coverage_package" / "operationally_diverse_validated_named_templates"
    tdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for rid in ("A", "B", "C"):
        p = tdir / f"{rid}.xml"
        p.write_text("<x/>", encoding="utf-8")
        paths[rid] = p
    rows = [
        # A and B: same lane/dir, stop 200 vs 210 -> same band -> one cluster (B dropped by score)
        _member("A", stop=200, net=1000, score=80, template="A.xml", path=paths["A"]),
        _member("B", stop=210, net=1100, score=70, template="B.xml", path=paths["B"]),
        # C: short-only -> different category, kept
        _member("C", stop=200, net=900, score=60, long_en="False", short_en="True", template="C.xml", path=paths["C"]),
    ]
    _write_csv(data / "operationally_diverse_validated_selection.csv", rows)
    return paths


def test_close_stops_cluster_and_recommend_best_score(tmp_path: Path):
    session_dir = tmp_path / "opt_cat"
    session_dir.mkdir()
    _seed(session_dir)
    session = OptimizerSession(session_dir)

    view = compute_category_bundle(session)
    assert view.total_templates == 3
    # A+B collapse into one category; C is its own -> 2 categories, 1 duplicate.
    assert view.category_count == 2
    assert view.duplicate_count == 1

    cats = {c.member_count: c for c in view.categories}
    pair = cats[2]
    kept = [m for m in pair.members if m.recommended_keep]
    assert len(kept) == 1
    assert kept[0].run_id == "A"  # higher score wins despite B's higher net


def test_build_pruned_bundle_copies_only_kept(tmp_path: Path):
    session_dir = tmp_path / "opt_cat2"
    session_dir.mkdir()
    _seed(session_dir)
    session = OptimizerSession(session_dir)

    result = build_pruned_bundle(session, ["A", "C"])
    assert result["kept"] == 2
    bundle_templates = session_dir / "deployment_package" / "weekly_coverage_package" / "pruned_final_bundle" / "templates"
    names = sorted(p.name for p in bundle_templates.iterdir())
    assert names == ["A.xml", "C.xml"]  # B (the near-duplicate) excluded
    assert (session_dir / "deployment_package" / "weekly_coverage_package" / "pruned_final_bundle.zip").exists()


def test_empty_selection_raises(tmp_path: Path):
    session_dir = tmp_path / "opt_cat3"
    session_dir.mkdir()
    _seed(session_dir)
    session = OptimizerSession(session_dir)
    try:
        build_pruned_bundle(session, [])
    except CategoryBundleError:
        pass
    else:
        raise AssertionError("expected CategoryBundleError for empty keep list")


def test_no_package_returns_status(tmp_path: Path):
    session_dir = tmp_path / "opt_cat4"
    session_dir.mkdir()
    (session_dir / OptimizerSession.SESSION_FILENAME).write_text("{}", encoding="utf-8")
    session = OptimizerSession(session_dir)
    view = compute_category_bundle(session)
    assert view.status == "no_weekly_package"
    assert view.total_templates == 0
