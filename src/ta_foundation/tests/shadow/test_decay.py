"""Phase D.3 — CUSUM decay state machine unit tests.

These cover the math in isolation. The runner integration is covered by
``test_runner.py::test_runner_auto_disables_decayed_candidate``.
"""

from __future__ import annotations

import json
import random

import pytest

from ta_foundation.research_ledger import Candidate
from ta_foundation.shadow.decay import (
    DEFAULT_H_SIGMA,
    DEFAULT_K_SIGMA,
    decode_state,
    encode_state,
    init_decay_state,
    update_decay_state,
)


def _make_candidate(
    *,
    pf_dev: float = 2.0,
    tp_ticks: float = 100.0,
    sl_ticks: float = 20.0,
    expectancy_dev: float = 30.0,
) -> Candidate:
    """Build a minimal Candidate dataclass for direct decay-math tests.

    We bypass the repository here — the decay module only reads two fields
    (``pf_dev`` and the tp/sl ticks via params_json/notes_json), so a
    handcrafted dataclass is fine.
    """
    params = {"target_ticks": tp_ticks, "stop_ticks": sl_ticks}
    return Candidate(
        candidate_id="c_test_decay",
        run_id="r_test",
        hypothesis_id="h_test",
        rank_in_run=1,
        params_json=json.dumps(params, sort_keys=True),
        n_trades_dev=100,
        pf_dev=pf_dev,
        expectancy_dev=expectancy_dev,
        n_trades_oos=None,
        pf_oos=None,
        expectancy_oos=None,
        n_trades_holdout=None,
        pf_holdout=None,
        expectancy_holdout=None,
        gate_verdict="survivor",
        gate_reasons_json=None,
        slippage_stress_pass=1,
        folds_distribution=None,
        triage_state="shadow",
        triage_reason="enrolled for forward observation",
        triaged_at=None,
        triaged_by=None,
        holdout_attempted=0,
        notes_json=None,
    )


def test_init_state_derives_mu0_sigma_from_pf_and_tp_sl() -> None:
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)

    # win_rate = 2.0 * 20 / (2.0 * 20 + 100) = 40 / 140 ≈ 0.2857
    # mu0 = 0.2857 * 100 - 0.7143 * 20 ≈ 14.29
    assert state["init_basis"]["from"] == "pf_dev_tp_sl"
    assert state["mu0_ticks"] == pytest.approx(14.2857, abs=1e-3)
    assert state["sigma_ticks"] > 0
    assert state["S"] == 0.0
    assert state["triggered"] is False
    assert state["last_signal_id"] is None
    assert state["k_sigma"] == DEFAULT_K_SIGMA
    assert state["h_sigma"] == DEFAULT_H_SIGMA


def test_init_fallback_when_pf_dev_missing() -> None:
    c = _make_candidate(pf_dev=0.0, tp_ticks=10.0, sl_ticks=10.0)
    state = init_decay_state(c)
    assert state["init_basis"]["from"] == "fallback"
    assert state["mu0_ticks"] == 0.0
    assert state["sigma_ticks"] >= 1.0


def test_baseline_does_not_trigger_over_500_trades() -> None:
    """At-baseline trades (~ μ₀) should not trip the alarm within 500 trades.

    The ARL₀ for k=0.5σ, H=5σ is ~465; this test uses a deterministic
    seed and a Gaussian-around-μ₀ stream so the result is stable. With
    σ=20 the noise is tame enough that the upper bound holds.
    """
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)
    mu0 = state["mu0_ticks"]
    sigma = state["sigma_ticks"]

    rng = random.Random(20260511)
    for i in range(1, 501):
        # Symmetric noise around μ₀: same scale as σ but capped — keeps
        # the CUSUM tame so the test is deterministic across platforms.
        x = mu0 + rng.uniform(-0.5, 0.5) * sigma
        res = update_decay_state(state, profit_ticks=x, signal_id=i)
        state = res.state
        assert not state["triggered"], (
            f"Spurious trigger at trade {i}: S={state['S']:.2f}, "
            f"H={sigma * DEFAULT_H_SIGMA:.2f}"
        )


def test_negative_drift_triggers_within_expected_window() -> None:
    """A persistent shift to ~ -1σ below μ₀ should trigger within a few
    dozen trades. Exact ARL₁ depends on the shift size; here we use a
    1.5σ negative shift which has ARL₁ ≈ 7–10 trades for these defaults.
    """
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)
    mu0 = state["mu0_ticks"]
    sigma = state["sigma_ticks"]
    shift_target = mu0 - 1.5 * sigma

    triggered_at = None
    for i in range(1, 100):
        res = update_decay_state(state, profit_ticks=shift_target, signal_id=i)
        state = res.state
        if res.triggered_now:
            triggered_at = i
            break

    assert triggered_at is not None, (
        f"Expected trigger within 100 trades at 1.5σ shift, got S={state['S']}"
    )
    assert triggered_at <= 30, (
        f"Trigger took {triggered_at} trades; expected ≤ 30 for a 1.5σ shift "
        f"with k=0.5σ, H=5σ"
    )
    assert state["triggered_at_n"] == triggered_at


def test_idempotent_on_replayed_signal_id() -> None:
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)
    mu0 = state["mu0_ticks"]
    sigma = state["sigma_ticks"]

    res1 = update_decay_state(state, profit_ticks=mu0 - 2 * sigma, signal_id=1)
    state = res1.state
    s_after_first = state["S"]
    n_after_first = state["n_trades_seen"]

    # Replay signal_id=1 → no-op.
    res_replay = update_decay_state(
        state, profit_ticks=mu0 - 2 * sigma, signal_id=1,
    )
    assert res_replay.state["S"] == pytest.approx(s_after_first)
    assert res_replay.state["n_trades_seen"] == n_after_first
    assert res_replay.delta_S == 0.0


def test_post_trigger_updates_are_noops_on_math() -> None:
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)
    mu0 = state["mu0_ticks"]
    sigma = state["sigma_ticks"]

    # Crank S above H with a huge loss.
    state = update_decay_state(
        state, profit_ticks=mu0 - 20 * sigma, signal_id=1,
    ).state
    assert state["triggered"]
    s_at_trigger = state["S"]

    # A follow-up trade — even a winning one — must not move S.
    res = update_decay_state(
        state, profit_ticks=mu0 + 5 * sigma, signal_id=2,
    )
    assert not res.triggered_now
    assert res.state["S"] == pytest.approx(s_at_trigger)
    assert res.state["last_signal_id"] == 2  # watermark still advances


def test_state_json_round_trip() -> None:
    c = _make_candidate(pf_dev=2.0, tp_ticks=100.0, sl_ticks=20.0)
    state = init_decay_state(c)
    state = update_decay_state(
        state, profit_ticks=-5.0, signal_id=1,
    ).state
    text = encode_state(state)
    again = decode_state(text)
    assert again == state


def test_decode_state_handles_garbage() -> None:
    assert decode_state(None) is None
    assert decode_state("") is None
    assert decode_state("not-json") is None
    assert decode_state("[1, 2, 3]") is None  # not a dict


def test_init_reads_tp_sl_from_notes_outcome_when_params_missing() -> None:
    notes = {
        "outcome": {"take_profit": [60], "stop": [12]},
    }
    c = Candidate(
        candidate_id="c_x",
        run_id="r_x",
        hypothesis_id="h_x",
        rank_in_run=1,
        params_json="{}",
        n_trades_dev=50,
        pf_dev=1.8,
        expectancy_dev=10.0,
        n_trades_oos=None, pf_oos=None, expectancy_oos=None,
        n_trades_holdout=None, pf_holdout=None, expectancy_holdout=None,
        gate_verdict="survivor",
        gate_reasons_json=None,
        slippage_stress_pass=1,
        folds_distribution=None,
        triage_state="shadow",
        triage_reason="x",
        triaged_at=None, triaged_by=None,
        holdout_attempted=0,
        notes_json=json.dumps(notes),
    )
    state = init_decay_state(c)
    assert state["init_basis"]["from"] == "pf_dev_tp_sl"
    assert state["init_basis"]["tp_ticks"] == 60
    assert state["init_basis"]["sl_ticks"] == 12
