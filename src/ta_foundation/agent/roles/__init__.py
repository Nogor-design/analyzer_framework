"""Agent roles — Triage, Scribe, Hypothesis Author, Sweep Operator.

Each role module contains:
    - the deterministic decision logic (classification, scheduling, …)
    - the LLM-generated narrative call (injected for testability)
    - the validation pipeline that gates whatever the LLM produces

Master plan §3 caps the role roster at four. Adding a fifth requires
written justification.
"""
