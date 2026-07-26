"""Phase D.3 — Sequential edge-decay test (Page CUSUM).

`Real Edge In Day Trading.md` §7 (and `agentic_phase_d_forward_observation.md`
D.3) mandates a sequential test that flags when realized expectancy has
statistically diverged from backtest, and *auto-disables* the candidate
above a threshold. Auto-disable, never auto-retune.

This module is the pure state machine. The shadow runner calls
``update_decay_state`` after each newly-resolved signal; the repository
persists the returned state to ``candidates.decay_state_json``.

CUSUM (Page 1954, lower one-sided, tabular form)
------------------------------------------------

Let ``X_t`` be the realized profit (in **ticks**) of the t-th resolved
shadow trade. Let ``μ_0`` be the backtest reference expectancy (ticks per
trade). The one-sided lower CUSUM tracks negative drift away from μ_0:

    S_0 = 0
    S_t = max(0, S_{t-1} - (X_t - μ_0) + k)
        = max(0, S_{t-1} + (μ_0 - X_t) + k)

Trigger when ``S_t > H``. ``k`` is the reference slack (half the smallest
drift size the design wants to detect quickly); ``H`` is the decision
threshold.

We choose ``k`` and ``H`` in units of the one-trade standard deviation σ
so the ARL₀ (average run length under H₀: no decay) is interpretable.
For ``k = 0.5 σ`` the standard Hawkins ARL curves give roughly:

    H / σ  →  ARL₀
    4.00   →  ~168
    4.77   →  ~250  (Phase D plan minimum)
    5.00   →  ~465  (default — slightly conservative)

The defaults ``k_sigma = 0.5`` and ``h_sigma = 5.0`` therefore give an
expected ~465-trade ARL₀, which keeps spurious trips comfortably above
the 250-trade floor in the design.

Reference expectancy and σ — unit consistency
---------------------------------------------

The runner feeds ``profit_ticks`` from ``realized_outcome_json``. For the
math to make sense, μ₀ and σ must also be in **ticks per trade**. The
candidate's ``expectancy_dev`` field can be populated in different units
by different discovery paths (sometimes dollars, sometimes ticks), so we
do not rely on it directly. Instead we *derive* μ₀ and σ from quantities
that are guaranteed to be in ticks:

    win_rate ← solve from pf_dev and tp/sl:
        pf = win_rate · tp / ((1 − win_rate) · sl)
        ⇒ win_rate = pf · sl / (pf · sl + tp)

    μ₀_ticks = win_rate · tp − (1 − win_rate) · sl
    σ_ticks  = sqrt(win_rate · (tp − μ₀)² + (1 − win_rate) · (sl + μ₀)²)

This is a binary tp/sl approximation: it assumes every trade exits at
exactly +tp or −sl. The shadow simulator can also exit at timeout, but
the approximation is good enough for setting CUSUM scale — what matters
is that μ₀ and σ are stable, reproducible per-candidate quantities.

If ``pf_dev`` is missing or ≤ 0, we fall back to ``μ₀ = 0`` and σ
estimated from ``(tp + sl) / 2`` so the test still functions; the runner
journals this fallback.

State shape
-----------

The state is a small JSON dict. ``init_decay_state`` builds it from a
``Candidate`` (and optional override params). ``update_decay_state``
takes one trade and returns the new state plus a ``triggered`` flag.

::

    {
        "schema": 1,
        "mu0_ticks":     float,    # reference expectancy
        "sigma_ticks":   float,    # reference σ
        "k_sigma":       float,    # slack (in σ units), default 0.5
        "h_sigma":       float,    # threshold (in σ units), default 5.0
        "S":             float,    # current CUSUM statistic
        "n_trades_seen": int,      # number of resolved trades consumed
        "last_signal_id": int|None,# highest signal_id already consumed
        "triggered":     bool,     # True once S > H
        "triggered_at_n": int|None,# n_trades_seen when triggered
        "init_basis": {            # how μ0/σ were derived (for the journal)
            "from": "pf_dev_tp_sl" | "expectancy_dev" | "fallback",
            "pf_dev": float | None,
            "tp_ticks": float | None,
            "sl_ticks": float | None,
            "win_rate_implied": float | None,
        },
        "version": 1,
    }
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Optional

from ta_foundation.research_ledger import Candidate, Hypothesis

SCHEMA_VERSION = 1

DEFAULT_K_SIGMA = 0.5
DEFAULT_H_SIGMA = 5.0


@dataclass(frozen=True)
class DecayUpdateResult:
    state: dict[str, Any]
    triggered_now: bool  # True only on the transition where S first exceeds H
    delta_S: float       # Change in S applied by this trade (signed)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def init_decay_state(
    candidate: Candidate,
    *,
    hypothesis: Optional[Hypothesis] = None,
    k_sigma: float = DEFAULT_K_SIGMA,
    h_sigma: float = DEFAULT_H_SIGMA,
) -> dict[str, Any]:
    """Build a fresh decay state for ``candidate``.

    ``hypothesis`` is optional and currently unused but kept in the signature
    so the family-aware fallbacks added later can route through one entry
    point.
    """
    tp_ticks, sl_ticks = _tp_sl_ticks_from_candidate(candidate)
    pf = candidate.pf_dev
    mu0, sigma, basis = _derive_mu0_sigma(pf, tp_ticks, sl_ticks)

    return {
        "schema": SCHEMA_VERSION,
        "mu0_ticks": float(mu0),
        "sigma_ticks": float(sigma),
        "k_sigma": float(k_sigma),
        "h_sigma": float(h_sigma),
        "S": 0.0,
        "n_trades_seen": 0,
        "last_signal_id": None,
        "triggered": False,
        "triggered_at_n": None,
        "init_basis": basis,
        "version": SCHEMA_VERSION,
    }


def _derive_mu0_sigma(
    pf_dev: Optional[float],
    tp_ticks: Optional[float],
    sl_ticks: Optional[float],
) -> tuple[float, float, dict[str, Any]]:
    """Solve μ₀, σ from PF and tp/sl assuming a binary tp/sl trade.

    Returns ``(mu0_ticks, sigma_ticks, basis_dict)``. ``sigma_ticks`` is
    guaranteed positive; we floor at 1.0 tick to keep the CUSUM scale sane
    if inputs are tiny or degenerate.
    """
    basis: dict[str, Any] = {
        "from": "pf_dev_tp_sl",
        "pf_dev": pf_dev,
        "tp_ticks": tp_ticks,
        "sl_ticks": sl_ticks,
        "win_rate_implied": None,
    }
    if (
        pf_dev is None
        or tp_ticks is None
        or sl_ticks is None
        or pf_dev <= 0
        or tp_ticks <= 0
        or sl_ticks <= 0
    ):
        sigma = (tp_ticks or 0.0) + (sl_ticks or 0.0)
        sigma = max(sigma / 2.0, 1.0)
        basis["from"] = "fallback"
        return 0.0, sigma, basis

    win_rate = (pf_dev * sl_ticks) / (pf_dev * sl_ticks + tp_ticks)
    win_rate = max(0.0, min(1.0, win_rate))
    mu0 = win_rate * tp_ticks - (1.0 - win_rate) * sl_ticks
    variance = (
        win_rate * (tp_ticks - mu0) ** 2
        + (1.0 - win_rate) * (sl_ticks + mu0) ** 2
    )
    sigma = math.sqrt(max(variance, 0.0))
    sigma = max(sigma, 1.0)
    basis["win_rate_implied"] = win_rate
    return mu0, sigma, basis


def _tp_sl_ticks_from_candidate(
    candidate: Candidate,
) -> tuple[Optional[float], Optional[float]]:
    """Best-effort extraction of target/stop ticks from candidate params/notes.

    Order of preference:
      1. params_json: ``target_ticks`` / ``stop_ticks`` (canonical).
      2. notes_json.outcome: ``take_profit`` / ``stop`` (may be a list, in
         which case the first entry wins — matches the shadow simulator's
         ``_first_or`` resolution).
      3. notes_json.discovery_params: ``target_ticks`` / ``stop_ticks``.
    """
    try:
        params = json.loads(candidate.params_json) if candidate.params_json else {}
    except json.JSONDecodeError:
        params = {}
    try:
        notes = json.loads(candidate.notes_json) if candidate.notes_json else {}
    except json.JSONDecodeError:
        notes = {}

    tp = _coerce_first_number(params.get("target_ticks"))
    sl = _coerce_first_number(params.get("stop_ticks"))

    if tp is None or sl is None:
        outcome = notes.get("outcome") if isinstance(notes, dict) else None
        if isinstance(outcome, dict):
            if tp is None:
                tp = _coerce_first_number(outcome.get("take_profit"))
            if sl is None:
                sl = _coerce_first_number(outcome.get("stop"))

    if tp is None or sl is None:
        disc = notes.get("discovery_params") if isinstance(notes, dict) else None
        if isinstance(disc, dict):
            if tp is None:
                tp = _coerce_first_number(disc.get("target_ticks"))
            if sl is None:
                sl = _coerce_first_number(disc.get("stop_ticks"))

    # Discovery sidecars often record the outcome as ``{"mode": "ticks_<TP>_<SL>"}``
    # without structured take_profit/stop fields. Parse that as a last resort so
    # entry-strategy candidates get a proper σ scale instead of falling back to
    # the 1-tick floor (which trips CUSUM on the first realistic trade).
    if tp is None or sl is None:
        outcome = notes.get("outcome") if isinstance(notes, dict) else None
        mode = outcome.get("mode") if isinstance(outcome, dict) else None
        if isinstance(mode, str) and mode.startswith("ticks_"):
            parts = mode.split("_")
            if len(parts) == 3:
                if tp is None:
                    tp = _coerce_first_number(parts[1])
                if sl is None:
                    sl = _coerce_first_number(parts[2])

    return tp, sl


def _coerce_first_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        for item in v:
            f = _coerce_first_number(item)
            if f is not None:
                return f
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# ---------------------------------------------------------------------------
# Update step
# ---------------------------------------------------------------------------


def update_decay_state(
    state: dict[str, Any],
    *,
    profit_ticks: float,
    signal_id: int,
) -> DecayUpdateResult:
    """Apply one resolved trade to the CUSUM state.

    Idempotent: if ``signal_id`` is not strictly greater than the state's
    ``last_signal_id``, the state is returned unchanged with
    ``triggered_now=False`` and ``delta_S=0.0``. This mirrors the repo-
    level idempotency on inserts — a runner pass that re-presents an
    already-consumed trade must not double-count.

    Once the state's ``triggered`` flag is True, further trades do not
    advance the statistic. The candidate is decayed; the runner's job is
    to journal the auto-disable, not to keep accumulating.
    """
    s = dict(state)  # shallow copy, callers persist the returned dict
    last_id = s.get("last_signal_id")
    if last_id is not None and signal_id <= last_id:
        return DecayUpdateResult(state=s, triggered_now=False, delta_S=0.0)

    if s.get("triggered"):
        s["last_signal_id"] = max(int(last_id or 0), int(signal_id))
        return DecayUpdateResult(state=s, triggered_now=False, delta_S=0.0)

    mu0 = float(s["mu0_ticks"])
    sigma = float(s["sigma_ticks"])
    k = float(s["k_sigma"]) * sigma
    h = float(s["h_sigma"]) * sigma
    prev_S = float(s["S"])

    # Lower CUSUM: penalise negative drift below μ₀.
    increment = (mu0 - float(profit_ticks)) - k
    new_S = max(0.0, prev_S + increment)
    delta = new_S - prev_S

    s["S"] = new_S
    s["n_trades_seen"] = int(s.get("n_trades_seen", 0)) + 1
    s["last_signal_id"] = int(signal_id)

    triggered_now = (not bool(s.get("triggered"))) and (new_S > h)
    if triggered_now:
        s["triggered"] = True
        s["triggered_at_n"] = s["n_trades_seen"]

    return DecayUpdateResult(state=s, triggered_now=triggered_now, delta_S=delta)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def encode_state(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def decode_state(text: Optional[str]) -> Optional[dict[str, Any]]:
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def summarise_state_for_journal(state: dict[str, Any]) -> str:
    return (
        f"S={float(state.get('S', 0.0)):.3f}"
        f" mu0={float(state.get('mu0_ticks', 0.0)):.3f}"
        f" sigma={float(state.get('sigma_ticks', 0.0)):.3f}"
        f" k_sigma={float(state.get('k_sigma', 0.0))}"
        f" h_sigma={float(state.get('h_sigma', 0.0))}"
        f" n={int(state.get('n_trades_seen', 0))}"
        f" triggered={bool(state.get('triggered', False))}"
    )
