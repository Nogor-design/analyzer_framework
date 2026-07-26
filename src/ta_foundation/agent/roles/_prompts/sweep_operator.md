# Sweep Operator — operator-facing diagnostics template

The Sweep Operator has no LLM in its hot path. This file exists purely for
the human-readable summary that the operator role can emit after a pass.

## Per-hypothesis report shape

```
hypothesis_id: <id>
status: <one of>
  - completed_fast_only        # ran fast, no dev survivors, stopped
  - completed_hardened         # ran fast → hardened, recorded survivors
  - no_trades                  # fast produced zero candidates; retired hypothesis
  - fast_run_failed            # run_probe failed at the fast stage
  - hardened_run_failed        # fast survived, hardened subprocess failed
  - skipped_already_run        # hypothesis already had a completed run
  - skipped_not_open           # hypothesis.status was retired/superseded
  - skipped_unknown_hypothesis # hypothesis_id not in the ledger
fast_run_id: <id or null>
n_candidates_fast: <int>
n_survivors_fast: <int>
hardened_run_id: <id or null>
n_candidates_hardened: <int>
n_survivors_hardened: <int>
error: <string or null>
```

## Hard guardrails (enforced in Python, not in this template)

1. The Operator never modifies a hypothesis after seeing its result. No
   tweak-and-retry. If a result is interesting but underpowered, the only
   legal action is to author a *new* hypothesis (which contributes to the
   multiple-testing denominator).
2. The Operator never invokes `run_probe(mode='locked_holdout')`. The
   one-shot holdout attempt is a human decision.
3. Hypotheses with zero candidates from the fast probe are retired
   (`status='retired'`), NOT graveyarded. Graveyard implies tested-and-failed;
   no_trades means the signal didn't trigger and the hypothesis is untested.
