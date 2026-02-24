# src/ta_foundation/analysis/pattern_engine/templates/builtins.py
from __future__ import annotations

from ta_foundation.analysis.pattern_engine.model import PatternTemplate
from ta_foundation.analysis.pattern_engine.engine import TemplateRegistry
from ta_foundation.analysis.pattern_engine.templates.orb import detect_orb_break_retest


def register_builtin_templates(registry: TemplateRegistry) -> None:
    """
    Register built-in pattern templates here.

    The key format in TemplateRegistry is:
      f"{family}::{structure}"

    So sweep patterns must refer to (family, structure) that match these.
    """
    registry.register(
        PatternTemplate(
            family="ORB",
            structure="orb_break_retest",
            direction_mode="both",
            requires_ticks=False,
            param_schema={
                "orb_minutes": "int",
                "retest_bars": "int",
                "session_start": "HH:MM",
                "globex_start": "HH:MM",
                "buffer_ticks": "int",
            },
            feature_keys_emitted=[],
            detect_fn=detect_orb_break_retest,
        )
    )