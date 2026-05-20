from __future__ import annotations

"""
Trial-Budget Accounting (selection-bias / multiple-testing)
============================================================
A profit factor found after sweeping 5,000 parameter combinations is not the
same evidence as one found after 5. With enough trials, noise *will* produce a
combination that looks like a strong edge. The validation stack already has the
two corrections that account for this — Bonferroni on the t-test
(``corrected_alpha = 0.05 / n``) and the Deflated Sharpe Ratio gate — but they
default to ``n = 1``, which makes them inert.

This module computes the **effective number of trials** a candidate should be
penalised for, so those corrections receive a real number.

Two components:

  within_run_trials      Parameter combinations evaluated in the candidate's own
                         sweep run.
  prior_program_trials   Combinations evaluated across the wider research
                         program before this candidate. Older trials are
                         decayed: a sweep from a year ago is a smaller live
                         multiple-testing burden than one from last week, since
                         both the market and the search focus have moved on.

  effective_trials = within_run_trials + round(prior_program_trials * prior_decay)

The decay term matters specifically for transient-edge harvesting (see
docs/designs/discovery_realism_and_adaptation_plan.md): continuous re-scanning
runs a very large cumulative trial count, and an undecayed sum would make the
correction so punitive that nothing could ever pass. Decay keeps the penalty
proportional to the *recent* search intensity.

The counts themselves are supplied by the caller (the sweep knows its grid
size; the CLI can read the program total from the research ledger) — this
module only does the arithmetic, so it is pure and trivially testable.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


DEFAULT_TRIAL_BUDGET_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "within_run_trials": 1,
    "prior_program_trials": 0,
    "prior_decay": 0.5,           # weight applied to prior-program trials, 0..1
    "max_effective_trials": 100_000,  # guard against absurd corrections
}


@dataclass(frozen=True)
class TrialBudget:
    within_run_trials: int
    prior_program_trials: int
    prior_decay: float
    effective_trials: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "within_run_trials": self.within_run_trials,
            "prior_program_trials": self.prior_program_trials,
            "prior_decay": self.prior_decay,
            "effective_trials": self.effective_trials,
        }


def compute_trial_budget(options: Optional[Dict[str, Any]] = None) -> TrialBudget:
    """Compute the effective trial count from supplied counts.

    ``effective_trials`` is at least 1 (so it is always a valid divisor for the
    Bonferroni correction) and is capped at ``max_effective_trials``.
    """
    cfg = {**DEFAULT_TRIAL_BUDGET_CONFIG, **(options or {})}

    within_run = max(1, int(cfg.get("within_run_trials", 1) or 1))
    prior = max(0, int(cfg.get("prior_program_trials", 0) or 0))

    decay = float(cfg.get("prior_decay", 0.5))
    decay = min(1.0, max(0.0, decay))

    max_effective = max(1, int(cfg.get("max_effective_trials", 100_000)))

    effective = within_run + int(round(prior * decay))
    effective = max(1, min(effective, max_effective))

    return TrialBudget(
        within_run_trials=within_run,
        prior_program_trials=prior,
        prior_decay=decay,
        effective_trials=effective,
    )
