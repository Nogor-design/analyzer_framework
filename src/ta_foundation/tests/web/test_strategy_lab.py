from __future__ import annotations

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")

from ta_foundation.web import app as web_app
from ta_foundation.web.strategy_lab import summarize_strategy_spec


def test_summarize_strategy_spec_returns_strategy_identity(tmp_path: Path):
    spec_path = tmp_path / "strategy_spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "strategy_name": "MyCrossBot",
                "family": "sma_cross",
                "intent": "9/21 SMA cross on NQ.",
                "parameters": {"FastPeriod": 9, "SlowPeriod": 21},
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_strategy_spec(str(spec_path))

    assert summary["ok"] is True
    assert summary["strategy_name"] == "MyCrossBot"
    assert summary["family"] == "sma_cross"
    assert summary["parameter_keys"] == ["FastPeriod", "SlowPeriod"]


def test_summarize_strategy_spec_reports_missing_file(tmp_path: Path):
    summary = summarize_strategy_spec(str(tmp_path / "missing.json"))

    assert summary["ok"] is False
    assert "was not found" in summary["errors"][0]


def test_strategy_lab_spec_info_route(tmp_path: Path):
    spec_path = tmp_path / "strategy_spec.json"
    spec_path.write_text(
        json.dumps({"strategy_name": "RouteBot", "family": "sma_cross_smoke"}),
        encoding="utf-8",
    )
    app = web_app.create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        res = client.post(
            "/api/strategy-lab/spec-info",
            data=json.dumps({"spec_path": str(spec_path)}),
            content_type="application/json",
        )

    assert res.status_code == 200
    assert res.get_json()["spec"]["strategy_name"] == "RouteBot"
