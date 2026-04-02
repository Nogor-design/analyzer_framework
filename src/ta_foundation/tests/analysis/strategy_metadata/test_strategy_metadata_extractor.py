from __future__ import annotations

from ta_foundation.analysis.strategy_metadata import build_strategy_profile


def test_build_profile_for_pantheon_master_has_defaults_and_templates():
    profile = build_strategy_profile("PantheonMasterBotV01TesterV2")

    assert profile["strategy_id"] == "PantheonMasterBotV01TesterV2"
    assert profile["defaults"].get("UseTrend") is True
    assert profile["defaults"].get("UseTimeFilter") is True
    assert profile["defaults"].get("MaxStop") == 200

    preset_names = {t["name"] for t in profile["template_presets"]}
    assert "sampleTemplate" in preset_names
    assert "BronzeApolloGod" in preset_names


def test_build_profile_for_pantheon_bot_has_regime_filter_params():
    profile = build_strategy_profile("PantheonBotV2")

    params = profile["parameters"]
    assert "RequiredTrendRegimeFilter" in params
    assert "RequiredVwapRegimeFilter" in params
    assert "RequiredVolatilityRegimeFilter" in params
    assert "BlockedVolatilityRegimeFilter" in params

    defaults = profile["defaults"]
    assert defaults.get("TrendHigherTimeFrameMinutes") == 15
    assert defaults.get("VwapNearThresholdAtr") == 0.20
    assert defaults.get("UseDynamicStop") is True


def test_missing_strategy_returns_warnings_not_exception():
    profile = build_strategy_profile("DOES_NOT_EXIST")
    assert profile["strategy_id"] == "DOES_NOT_EXIST"
    assert profile["warnings"]
