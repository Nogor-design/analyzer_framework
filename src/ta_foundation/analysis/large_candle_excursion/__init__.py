from ta_foundation.analysis.large_candle_excursion.adaptive_context import (
    attach_context_to_events,
    build_intraday_context,
    classify_trend_state,
    structurally_aligned_mode,
)
from ta_foundation.analysis.large_candle_excursion.adaptive_context_gate import (
    run_adaptive_context_gate,
)
from ta_foundation.analysis.large_candle_excursion.adaptive_window import (
    build_adaptive_event_streams,
    run_adaptive_large_candle_windows,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_outcome_cube import (
    build_dynamic_family_a_outcome_cube,
    deduplicate_execution_candidates,
    write_dynamic_outcome_cube,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_causal_learnability import (
    build_causal_learnability_panel,
    run_causal_learnability_audit,
    write_causal_learnability_audit,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_representation_audit import (
    run_representation_audit,
    write_representation_audit,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_representation_confirmation import (
    run_representation_confirmation,
    write_representation_confirmation,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_factorial_shift_audit import (
    run_factorial_shift_audit,
    write_factorial_shift_audit,
)
from ta_foundation.analysis.large_candle_excursion.opening_inventory_audit import (
    run_opening_inventory_audit,
    write_opening_inventory_audit,
)
from ta_foundation.analysis.large_candle_excursion.gc_opening_inventory_robustness import (
    run_gc_opening_inventory_robustness,
    write_gc_opening_inventory_robustness,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_opportunity_oracle import (
    run_bounded_switching_oracle,
    solve_bounded_cell_path,
    write_dynamic_opportunity_oracle,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_fixed_share_selector import (
    run_dynamic_carried_fixed_share_matrix,
    run_dynamic_carried_fixed_share_selector,
    run_dynamic_fixed_share_matrix,
    run_dynamic_fixed_share_selector,
    write_dynamic_fixed_share_selector,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_multisequence_replay import (
    build_multisequence_summary,
    summarize_dynamic_sequence,
    write_dynamic_multisequence_replay,
)
from ta_foundation.analysis.large_candle_excursion.dynamic_selector_diagnostic import (
    analyze_sequence,
    build_panel_summary as build_dynamic_selector_diagnostic_summary,
    write_dynamic_selector_diagnostic,
)
from ta_foundation.analysis.large_candle_excursion.sweep import run_large_candle_excursion

__all__ = [
    "attach_context_to_events",
    "analyze_sequence",
    "build_adaptive_event_streams",
    "build_dynamic_selector_diagnostic_summary",
    "build_causal_learnability_panel",
    "build_dynamic_family_a_outcome_cube",
    "build_intraday_context",
    "classify_trend_state",
    "deduplicate_execution_candidates",
    "run_bounded_switching_oracle",
    "run_dynamic_carried_fixed_share_matrix",
    "run_dynamic_carried_fixed_share_selector",
    "run_causal_learnability_audit",
    "run_representation_audit",
    "run_representation_confirmation",
    "run_factorial_shift_audit",
    "run_opening_inventory_audit",
    "run_gc_opening_inventory_robustness",
    "run_dynamic_fixed_share_matrix",
    "run_dynamic_fixed_share_selector",
    "build_multisequence_summary",
    "summarize_dynamic_sequence",
    "run_adaptive_context_gate",
    "run_adaptive_large_candle_windows",
    "run_large_candle_excursion",
    "solve_bounded_cell_path",
    "structurally_aligned_mode",
    "write_dynamic_outcome_cube",
    "write_dynamic_opportunity_oracle",
    "write_dynamic_fixed_share_selector",
    "write_dynamic_multisequence_replay",
    "write_dynamic_selector_diagnostic",
    "write_causal_learnability_audit",
    "write_representation_audit",
    "write_representation_confirmation",
    "write_factorial_shift_audit",
    "write_opening_inventory_audit",
    "write_gc_opening_inventory_robustness",
]
