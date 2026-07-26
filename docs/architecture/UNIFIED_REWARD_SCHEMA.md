> **STATUS: ASPIRATIONAL — NOT IMPLEMENTED — NOT PART OF EDGE DISCOVERY PROGRAM (2026-05-13)**
>
> This schema is **not the canonical reward model** for `ta_foundation`.
> Edge discovery uses concrete hardening gates (t-test with Bonferroni
> correction, walk-forward fold sign-consistency, slippage stress, locked
> holdout, Page-CUSUM decay) — not a generic `RewardVector` of normalized
> floats. See `docs/designs/real_edge_discovery_program.md` for the actual
> verdict contracts and `src/ta_foundation/research_ledger/` for the
> shipped persistence schema.
>
> Keep for historical reference only.

---

# Unified Reward Schema

## Purpose

This document defines the canonical reward model for all AI subsystems:
- NinjaTraderDocScrapper (RAG + compile loop + human labeling)
- ta_foundation (prediction + multi-agent systems)

The goal is to unify all evaluation signals into a single structured reward representation
that enables:
- Cross-system learning
- Ranking of candidate outputs
- Self-improving AI behavior

---

## Core Concept

All outputs from any system are evaluated using a **RewardVector**:

Each dimension represents a measurable aspect of quality.

---

## Reward Vector Definition

```python
RewardVector = {
    "correctness": float,         # Compile success, logical validity
    "task_success": float,        # Did the output satisfy the objective?
    "performance": float,         # Prediction accuracy / PnL / horizon score
    "robustness": float,          # Stability across variations
    "consistency": float,         # Internal logical consistency
    "efficiency": float,          # Cost / latency / steps
    "human_preference": float,    # Human good/bad labeling
    "risk_penalty": float,        # Safety violations / invalid API use
    "complexity_penalty": float   # Over-complex or overfitted solutions
}

Scalar Reward (Optional)
A scalar reward can be computed using a weighted sum:
R = Σ (w_i * metric_i)

Example weights:
weights = {
    "correctness": 0.35,
    "performance": 0.35,
    "human_preference": 0.15,
    "robustness": 0.10,
    "complexity_penalty": -0.05
}

Core Data Model
Task
Represents a problem or input prompt.

{
  "task_id": "uuid",
  "task_type": "ninjascript_generation | prediction_run",
  "prompt": "...",
  "context_refs": {},
  "constraints": {},
  "created_at": "timestamp"
}
Candidate Output
Represents one generated response from a model or agent.
{
  "candidate_id": "uuid",
  "task_id": "uuid",
  "producer": {
    "subsystem": "NinjaTrader | ta_foundation",
    "model": "qwen3-coder",
    "agent": "agent_name"
  },
  "output_artifact": {
    "type": "code | prediction",
    "path": "/path/to/output"
  }
}

Evaluation Event
Represents raw signals.
{
  "eval_id": "uuid",
  "candidate_id": "uuid",
  "eval_type": "compile | human_label | outcome",
  "raw_signal": {
    "label": "good",
    "score": 0.75,
    "errors": []
  }
}

Reward Record
Final unified output.
{
  "candidate_id": "uuid",
  "reward_vector": {...},
  "scalar_reward": 0.82,
  "explanations": [
    "compile_success=1",
    "human_label=good"
  ]
}
Adapter Mapping
NinjaTraderDocScrapper
Inputs:

Compile success / errors
Human good/bad labels
Code output artifacts

Mapping:

correctness = compile_success
human_preference = label_score
risk_penalty = invalid_api_penalty
complexity_penalty = code_size_penalty

ta_foundation (Prediction)
Inputs:

Prediction outcomes
Horizon scoring
Multi-agent results

Mapping:
performance = pnl_or_accuracy_score
robustness = variance_penalty
consistency = signal_stability
``

Key Design Principles

Reward is multi-dimensional (not a single score)
All systems must output evaluation events
Evaluation is decoupled from generation
Schema must support:

Code systems
Prediction systems
Future agents




Future Extensions

Process-level reward modeling (step-by-step scoring)
Learned reward models (LLM-as-judge)
Automatic prompt optimization
Cross-domain learning

