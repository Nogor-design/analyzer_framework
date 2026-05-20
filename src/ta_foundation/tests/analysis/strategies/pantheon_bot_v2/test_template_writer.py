from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.analysis.strategies.pantheon_bot_v2.param_map import (
    PANTHEON_BOT_V2_PARAMS,
    ParamMapping,
    params_by_role,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.template_writer import (
    settings_from_discovery,
    write_pantheon_template,
)

SEED = (
    Path(__file__).parents[4]
    / "strategies"
    / "PantheonBotV2"
    / "templates"
    / "sampleTemplate.xml"
)


def _sample_value(param: ParamMapping):
    if param.nt_type == "bool":
        return True
    if param.nt_type == "int":
        return 7
    if param.nt_type == "float":
        return 1.25
    return param.enum_values[0].nt_enum_value


def _expected(param: ParamMapping) -> str:
    if param.nt_type == "bool":
        return "true"
    if param.nt_type == "int":
        return "7"
    if param.nt_type == "float":
        return "1.25"
    return param.enum_values[0].nt_enum_value


def test_seed_exists() -> None:
    assert SEED.is_file(), f"canonical seed template missing: {SEED}"


def test_noop_patch_is_byte_identical_to_seed(tmp_path: Path) -> None:
    out = tmp_path / "noop.xml"
    manifest = write_pantheon_template(seed_template=SEED, output_path=out, settings={})
    assert manifest["applied"] == []
    assert out.read_bytes() == SEED.read_bytes()


def test_round_trip_required_filters(tmp_path: Path) -> None:
    out = tmp_path / "filters.xml"
    write_pantheon_template(
        seed_template=SEED,
        output_path=out,
        settings={
            "RequiredTrendRegimeFilter": "down",
            "RequiredVwapRegimeFilter": "above",
            "RequiredVolatilityRegimeFilter": "low",
            "BlockedVolatilityRegimeFilter": "high",
        },
    )
    text = out.read_text(encoding="utf-8-sig")
    assert "<RequiredTrendRegimeFilter>Down</RequiredTrendRegimeFilter>" in text
    assert "<RequiredVwapRegimeFilter>Above</RequiredVwapRegimeFilter>" in text
    assert "<RequiredVolatilityRegimeFilter>Low</RequiredVolatilityRegimeFilter>" in text
    assert "<BlockedVolatilityRegimeFilter>High</BlockedVolatilityRegimeFilter>" in text
    # The OptimizationParameters value list moved in lockstep.
    assert "<ValueSerializable>Down</ValueSerializable>" in text


def test_every_filter_exit_time_param_is_writable(tmp_path: Path) -> None:
    params = (
        params_by_role("filter")
        + params_by_role("exit")
        + params_by_role("time")
    )
    assert params, "expected filter/exit/time params in the registry"
    for param in params:
        out = tmp_path / f"{param.nt_property}.xml"
        manifest = write_pantheon_template(
            seed_template=SEED,
            output_path=out,
            settings={param.nt_property: _sample_value(param)},
        )
        assert param.nt_property in manifest["applied"], (
            f"{param.nt_property} ({param.role}) was not written into the seed"
        )
        text = out.read_text(encoding="utf-8-sig")
        expected = _expected(param)
        assert f"<{param.nt_property}>{expected}</{param.nt_property}>" in text


def test_end_to_end_discovery_payload(tmp_path: Path) -> None:
    payload = {
        "market_regime": {
            "trend_regime": "up",
            "vwap_regime": "near",
            "htf_minutes": 30,
            "trend_ema_period": 21,
            "session_window_optimizer": {"start_hour": 7, "start_minute": 30},
        },
        "ma_discovery": {"signals": {"ma_cross": {"trend_period": 250}}},
    }
    out = tmp_path / "discovery.xml"
    manifest = write_pantheon_template(
        seed_template=SEED,
        output_path=out,
        discovery_payload=payload,
    )
    text = out.read_text(encoding="utf-8-sig")
    assert "<RequiredTrendRegimeFilter>Up</RequiredTrendRegimeFilter>" in text
    assert "<RequiredVwapRegimeFilter>Near</RequiredVwapRegimeFilter>" in text
    assert "<TrendHigherTimeFrameMinutes>30</TrendHigherTimeFrameMinutes>" in text
    assert "<TrendEmaPeriod>21</TrendEmaPeriod>" in text
    assert "<StartTimeH>7</StartTimeH>" in text
    assert "<StartTimeM>30</StartTimeM>" in text
    assert "<averageTrend>250</averageTrend>" in text
    assert manifest["source"] == "discovery_payload"


def test_ambiguous_shared_keys_are_skipped_not_guessed() -> None:
    payload = {
        "market_regime": {"vol_regime": "high"},
        "ma_discovery": {"signals": {"ma_cross": {"period": 50}}},
    }
    settings, skipped = settings_from_discovery(payload)
    assert "RequiredVolatilityRegimeFilter" not in settings
    assert "BlockedVolatilityRegimeFilter" not in settings
    assert "averageFast" not in settings
    assert "averageSlow" not in settings
    assert skipped == [
        "BlockedVolatilityRegimeFilter",
        "RequiredVolatilityRegimeFilter",
        "averageFast",
        "averageSlow",
    ]


def test_baseline_overrides_resolve_ambiguous_keys(tmp_path: Path) -> None:
    out = tmp_path / "overrides.xml"
    manifest = write_pantheon_template(
        seed_template=SEED,
        output_path=out,
        discovery_payload={"market_regime": {"trend_regime": "up"}},
        baseline_overrides={"averageFast": 30, "averageSlow": 120},
    )
    text = out.read_text(encoding="utf-8-sig")
    assert "<averageFast>30</averageFast>" in text
    assert "<averageSlow>120</averageSlow>" in text
    assert manifest["settings"]["averageFast"] == "30"


def test_rejects_both_or_neither_source(tmp_path: Path) -> None:
    out = tmp_path / "bad.xml"
    with pytest.raises(ValueError, match="exactly one"):
        write_pantheon_template(seed_template=SEED, output_path=out)
    with pytest.raises(ValueError, match="exactly one"):
        write_pantheon_template(
            seed_template=SEED, output_path=out, settings={}, discovery_payload={}
        )


def test_invalid_enum_value_is_rejected(tmp_path: Path) -> None:
    out = tmp_path / "bad_enum.xml"
    with pytest.raises(ValueError, match="not a valid NT enum"):
        write_pantheon_template(
            seed_template=SEED,
            output_path=out,
            settings={"RequiredTrendRegimeFilter": "sideways"},
        )


def test_missing_seed_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="seed template not found"):
        write_pantheon_template(
            seed_template=tmp_path / "nope.xml",
            output_path=tmp_path / "out.xml",
            settings={},
        )


def test_registered_properties_all_present_in_seed(tmp_path: Path) -> None:
    # Every registry property must be writable into the canonical seed, so the
    # writer never silently drops a discovery recommendation.
    settings = {p.nt_property: _sample_value(p) for p in PANTHEON_BOT_V2_PARAMS}
    out = tmp_path / "all.xml"
    manifest = write_pantheon_template(
        seed_template=SEED, output_path=out, settings=settings, strict=True
    )
    assert not manifest["unapplied"]
