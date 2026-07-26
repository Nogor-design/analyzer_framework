from __future__ import annotations

from ta_foundation.analysis.large_candle_excursion.reversal_decision_engine import compute_reversal_decision_engine


def _event(i: int, *, fav2: float, adv2: float, adv: float, fav: float, reclaim: bool, rebreak: bool, session: str = "ny_open") -> dict:
    return {
        "dt": f"2026-01-01T07:{i:02d}:00-07:00",
        "window_minutes": 30,
        "size_ticks": 20.0,
        "adv_ticks": adv,
        "fav_ticks": fav,
        "early_fav_1bar_ticks": fav2 * 0.4,
        "early_fav_2bar_ticks": fav2,
        "early_fav_3bar_ticks": fav2 * 1.2,
        "early_adv_1bar_ticks": adv2 * 0.5,
        "early_adv_2bar_ticks": adv2,
        "early_adv_3bar_ticks": adv2 * 1.4,
        "did_price_reclaim_signal_midpoint": reclaim,
        "did_price_break_signal_extreme_again": rebreak,
        "session_bucket": session,
        "trend_alignment_label": "countertrend_exhaustion",
        "vwap_ext_bucket": "extended",
        "directional_context_label": "trend_exhaustion",
        "level_interaction_label": "approaching",
        "candle_bucket": "25-50",
        "tf_minutes": 1,
        "direction": -1,
    }


def test_reversal_decision_engine_outputs_rules_and_tables() -> None:
    events = []
    for i in range(45):
        events.append(_event(i, fav2=12.0, adv2=8.0, adv=24.0, fav=4.0, reclaim=True, rebreak=False, session="ny_open"))
    for i in range(45, 90):
        events.append(_event(i, fav2=2.0, adv2=12.0, adv=1.0, fav=10.0, reclaim=False, rebreak=True, session="mid_ny"))

    out = compute_reversal_decision_engine(events, cfg={"min_n": {"overall": 40, "class": 10, "interaction": 10, "rule": 10}})

    assert out.get("enabled") is True
    assert out.get("n_events") == 90
    assert out.get("baseline", {}).get("n") == 90
    assert out.get("tables", {}).get("outcome_by_early_path_class")
    assert out.get("decision_rules")
    assert out.get("strong_runner_definition")


def test_reversal_decision_engine_window_and_excursion_fallbacks() -> None:
    events = [
        {
            "dt": "2026-01-01T07:00:00-07:00",
            "forward_window_minutes": "15",
            "size_ticks": 20.0,
            "trade_fav_ticks": 22.0,
            "trade_adv_ticks": 8.0,
            "early_fav_2bar_ticks": 10.0,
            "early_adv_2bar_ticks": 4.0,
            "did_price_reclaim_signal_midpoint": "true",
            "did_price_break_signal_extreme_again": "false",
            "session_bucket": "ny_open",
            "trend_alignment_label": "countertrend_exhaustion",
            "vwap_ext_bucket": "extended",
            "directional_context_label": "trend_exhaustion",
            "level_interaction_label": "approaching",
            "candle_bucket": "25-50",
            "tf_minutes": 1,
            "direction": -1,
        }
        for _ in range(45)
    ]

    out = compute_reversal_decision_engine(
        events,
        cfg={"forward_window_minutes": 30, "min_n": {"overall": 20, "class": 10, "interaction": 10, "rule": 10}},
    )

    assert out.get("enabled") is True
    assert out.get("baseline", {}).get("n") == 45
    assert out.get("diagnostics", {}).get("window_fallback_used") is True
