


> **STATUS: ASPIRATIONAL — NOT IMPLEMENTED — NOT PART OF EDGE DISCOVERY PROGRAM (2026-05-13)**
>
> This document describes a generic LLM-output reward-engine pattern across an
> external `NinjaTraderDocScrapper` project and `ta_foundation`. **None of this
> is built in `ta_foundation`**, and it is not on the active roadmap. The actual
> persistence layer that drives candidate evaluation is
> `src/ta_foundation/research_ledger/` plus `src/ta_foundation/shadow/`
> (migrations 0004/0005) — not the "RewardVector / Reward Store" pattern below.
>
> Keep for historical reference only. For the actual research pipeline and
> hardening contracts, see:
> - `docs/designs/real_edge_discovery_program.md` (doc of record)
> - `docs/designs/discovery_hardening_plan.md`
> - `docs/designs/agentic_research_program.md`
>
> If you want to revive this design, first reconcile the RewardVector schema
> against the existing `candidates` / `shadow_signals` tables; do not assume
> the schema below is canonical.

---

# Unified AI System Architecture

## Purpose

This document describes the architecture that unifies:

- NinjaTraderDocScrapper (RAG + compile + human loop)
- ta_foundation (prediction + multi-agent system)

Into a single:

👉 Self-improving AI system with a shared evaluation and learning layer

---

## High-Level Architecture


            +------------------------+
            |   Task / Input Layer   |
            +----------+-------------+
                       |
                       v

      +--------------------------------------+
      |   Candidate Generation Layer         |
      |--------------------------------------|
      | - RAG Code Generation               |
      | - Multi-Agent Prediction            |
      | - Tool-Using Agents                 |
      +-----------------+-------------------+
                        |
                        v

      +--------------------------------------+
      |      Evaluation / Reward Layer       |
      |--------------------------------------|
      | - Compile Evaluation                 |
      | - Human Feedback                     |
      | - Outcome Scoring                    |
      | - Consistency Checks                 |
      +-----------------+-------------------+
                        |
                        v

      +--------------------------------------+
      |       Unified Reward Engine          |
      |--------------------------------------|
      | - Reward Vector Construction         |
      | - Scalar Reward Computation          |
      | - Cross-System Normalization         |
      +-----------------+-------------------+
                        |
                        v

      +--------------------------------------+
      |          Reward Store               |
      |--------------------------------------|
      | - Tasks                             |
      | - Candidates                        |
      | - Evaluations                       |
      | - Reward Vectors                    |
      +-----------------+-------------------+
                        |
                        v

      +--------------------------------------+
      |   Optimization / Selection Layer     |
      |--------------------------------------|
      | - Rank candidates                    |
      | - Select best outputs               |
      | - Identify best models/agents       |
      +-----------------+-------------------+
                        |
                        v

      +--------------------------------------+
      |       Feedback / Learning Loop       |
      |--------------------------------------|
      | - Prompt optimization                |
      | - Strategy refinement               |
      | - Cross-system learning             |
      +--------------------------------------+


---

## Core Subsystems

### NinjaTraderDocScrapper

Capabilities:
- RAG over NinjaTrader docs
- Code generation
- Compile-feedback repair loop
- Human labeling (good/bad)

Acts as:
👉 Code reasoning + repair system

---

### ta_foundation

Capabilities:
- Multi-agent prediction
- Market data scoring
- Horizon/outcome tracking

Acts as:
👉 Decision / prediction system

---

## New Components

---

### 1. Unified Reward Engine

Purpose:
- Convert all evaluation signals into a common reward vector

Responsibilities:
- Normalize signals across subsystems
- Combine multiple signals
- Produce structured reward data

---

### 2. Reward Store (SQLite)

Tables:
- tasks
- candidate_outputs
- evaluation_events
- reward_vectors

Why SQLite:
- Local
- Fast
- Already consistent with RAG index

---

### 3. Adapter Layer

Each subsystem must implement:


subsystem_output → evaluation_events → reward_vector

Adapters:
- ninjatrader_adapter.py
- prediction_adapter.py

---

### 4. Optimization Layer

Capabilities:
- Generate multiple candidates
- Score all candidates
- Select best result

Key Concept:
👉 "Search + evaluation" instead of "single generation"

---

### 5. Learning Loop

Enables:
- Continuous improvement
- Strategy optimization
- Cross-domain knowledge transfer

---

## Data Flow

1. User or system creates task
2. Multiple candidate outputs generated
3. Each candidate evaluated
4. Reward vectors computed
5. Best candidate selected
6. Results stored for future learning

---

## Key Properties

### Cross-System Learning

- Code improvements can influence prediction reasoning
- Prediction failures can inform code constraints

---

### Self-Improvement

System improves without:
- retraining models
- manual intervention

---

### Evaluation-Driven Behavior

System behavior is determined by:
👉 Reward definitions, not prompts alone

---

## Short-Term Implementation Plan

1. Build SQLite reward store
2. Implement schema
3. Add adapters to both systems
4. Log evaluation events
5. Compute reward vectors
6. Rank and compare outputs

---

## Long-Term Extensions

- Learned reward models (LLM-based judges)
- Reinforcement learning optimization
- Strategy evolution / mutation systems
- Autonomous agent coordination

---

## Core Insight

This architecture transforms:

FROM:
- Multiple disconnected AI tools

TO:
- A unified, self-improving AI system

---
