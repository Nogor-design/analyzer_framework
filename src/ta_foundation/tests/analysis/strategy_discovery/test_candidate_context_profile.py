from __future__ import annotations

from ta_foundation.analysis.strategy_discovery.candidate_context_profile import (
    _classify_cell,
    build_candidate_context_profile,
    profile_candidates,
)


def _cell(n: int, pf, avg):
    """One context-breakdown cell."""
    return {
        "n_trades": n,
        "profit_factor": pf,
        "avg_trade": avg,
        "net_profit": (avg * n) if avg is not None else None,
        "win_rate": 0.4,
    }


def _result(**overrides):
    base = {
        "pattern_id": "orb",
        "direction_mode": "both",
        "entry_timing": "body_midpoint",
        "outcome_mode": "ticks_150_20",
        "params_key": "min_sweep=4",
        "n_trades": 120,
        "is_oos_degradation": 0.10,
        "metrics": {
            "profit_factor": 2.0,
            "by_session": {
                "NyOpen": _cell(60, 2.5, 30.0),
                "Lunch": _cell(60, 0.6, -10.0),
            },
            "by_direction": {
                "Long": _cell(60, 2.0, 20.0),
                "Short": _cell(60, 1.1, 5.0),
            },
        },
        "regime_breakdown": {
            "by_regime": {
                "high_vol_expansion": _cell(80, 3.0, 40.0),
                "ranging_tight": _cell(10, 0.5, -20.0),
            },
            "by_vol_regime": {"high_vol": _cell(120, 2.0, 25.0)},
            "by_trend_direction": {"up": _cell(120, 2.0, 25.0)},
        },
    }
    base.update(overrides)
    return base


def test_classify_cell_buckets() -> None:
    assert _classify_cell(_cell(50, 2.0, 10.0), min_trades=20, strong_pf=1.3) == "strong"
    assert _classify_cell(_cell(50, 1.1, 5.0), min_trades=20, strong_pf=1.3) == "marginal"
    assert _classify_cell(_cell(50, 0.7, -8.0), min_trades=20, strong_pf=1.3) == "weak"
    assert _classify_cell(_cell(5, 9.0, 99.0), min_trades=20, strong_pf=1.3) == "unknown"
    # No losing trades (pf None) but net-positive is unambiguously strong.
    no_losers = {"n_trades": 50, "profit_factor": None, "avg_trade": 10.0, "net_profit": 500.0}
    assert _classify_cell(no_losers, min_trades=20, strong_pf=1.3) == "strong"


def test_profile_classifies_every_context_dimension() -> None:
    prof = build_candidate_context_profile(_result())

    assert "regime=high_vol_expansion" in prof["strong_contexts"]
    assert "vol_regime=high_vol" in prof["strong_contexts"]
    assert "trend_direction=up" in prof["strong_contexts"]
    assert "session=NyOpen" in prof["strong_contexts"]
    assert "direction=Long" in prof["strong_contexts"]

    assert "direction=Short" in prof["marginal_contexts"]   # positive, PF 1.1 < 1.3
    assert "session=Lunch" in prof["weak_contexts"]         # net-negative
    assert "regime=ranging_tight" in prof["unknown_contexts"]  # 10 trades < 20


def test_profile_collects_warnings() -> None:
    prof = build_candidate_context_profile(
        _result(
            n_trades=30,
            is_oos_degradation=0.55,
            hardening={"regime_scoping": {"track": "regime-limited"}},
        )
    )
    assert prof["regime_track"] == "regime-limited"
    assert any("thin overall sample" in w for w in prof["warnings"])
    assert any("out-of-sample degradation" in w for w in prof["warnings"])
    assert any("regime-limited" in w for w in prof["warnings"])


def test_profile_flags_failed_hardening_gates() -> None:
    prof = build_candidate_context_profile(
        _result(
            hardening={
                "passed": False,
                "honest_execution": {"passed": False},
                "regime_scoping": {"track": "none"},
            }
        )
    )
    assert any("hardening gate stack" in w for w in prof["warnings"])
    assert any("honest-execution" in w for w in prof["warnings"])
    assert any("no regime cleared" in w for w in prof["warnings"])


def test_profile_candidates_builds_a_context_matrix() -> None:
    # Three candidates: high_vol_expansion strong in all three, Lunch weak in two.
    weak_lunch = _result()
    weak_lunch["params_key"] = "a"
    c2 = _result(params_key="b")
    c3 = _result(params_key="c")
    # c3 has too few Lunch trades -> unknown there instead of weak.
    c3["metrics"]["by_session"]["Lunch"] = _cell(5, 0.6, -10.0)

    out = profile_candidates([weak_lunch, c2, c3])
    assert out["n_candidates"] == 3

    regime_matrix = out["context_matrix"]["regime"]
    assert regime_matrix["high_vol_expansion"]["strong"] == 3

    session_matrix = out["context_matrix"]["session"]
    assert session_matrix["Lunch"]["weak"] == 2
    assert session_matrix["Lunch"]["unknown"] == 1


def test_best_contexts_ranks_what_the_market_rewards() -> None:
    out = profile_candidates([_result(params_key="a"), _result(params_key="b")])
    best = out["summary"]["best_contexts"]
    assert best, "expected at least one rewarding context"
    contexts = {b["context"] for b in best}
    # high_vol_expansion is strong in both candidates, weak in none.
    assert "regime=high_vol_expansion" in contexts
    # Lunch loses in both candidates, so it must not be a "best" context.
    assert "session=Lunch" not in contexts


def test_disabled_and_empty_are_graceful() -> None:
    disabled = profile_candidates([_result()], options={"enabled": False})
    assert disabled["n_candidates"] == 0
    assert any("disabled" in m for m in disabled["issues"])

    empty = profile_candidates([])
    assert empty["n_candidates"] == 0
    assert any("no sweep results" in m for m in empty["issues"])
