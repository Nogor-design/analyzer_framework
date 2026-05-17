"""DEPRECATED — superseded by `ta_foundation.agent.tools.{read,write}` (A.2).

These four god-tools violate the principles laid out in
docs/designs/agentic_research_program.md §4 (no schema validation, no
preconditions, no journaling, and `run_strategy_optimization` is the
agent-driven optimization that the master plan explicitly forbids).

This module remains importable for one release cycle so any in-flight
notebooks or scripts continue to load. New code must use the journaled
tool registry in `ta_foundation.agent.tools`.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from ta_foundation.agent.facade import AnalysisFacade

warnings.warn(
    "ta_foundation.agent.tools.analysis_tools is deprecated. "
    "Use ta_foundation.agent.tools.{read,write} via TOOLS_BY_NAME instead. "
    "See docs/designs/agentic_phase_a_foundation.md A.2.",
    DeprecationWarning,
    stacklevel=2,
)

# Shared facade instance for the agent session
# In a production multi-user environment, this would be managed per session
_facade = AnalysisFacade()

@tool
def ingest_data(input_path: str, market_data_path: Optional[str] = None) -> str:
    """
    Ingests market data and backtest results from the specified paths.
    This must be called before running any discovery or analysis.
    """
    try:
        res = _facade.ingest(input_path, market_data_path=market_data_path)
        return f"Ingested {res['n_packages']} packages. Run IDs: {res['run_ids']}. Market Instruments: {res['market_instruments']}"
    except Exception as e:
        return f"Error ingesting data: {str(e)}"

@tool
def run_discovery_sweep(discovery_type: str, instrument: str, timeframe: str = "5m", config_overrides: Optional[Dict[str, Any]] = None) -> str:
    """
    Runs a statistical discovery sweep to find edges.
    discovery_type can be: 'candle', 'ma', 'orb', 'bb', 'breakout', 'pullback', 'level', 'lcr', 'premarket'.
    instrument is the ticker symbol (e.g., 'NQ').
    """
    config = {
        "enabled": True,
        "instrument": instrument,
        "timeframe": timeframe,
    }
    if config_overrides:
        config.update(config_overrides)
    
    res = _facade.run_discovery(discovery_type, config)
    if "error" in res:
        return f"Discovery failed: {res['error']}"
    return f"Discovery {discovery_type} complete: {res['n_results']} results found from {res['n_combinations']} combinations."

@tool
def run_strategy_optimization(instrument: str, contract: str, timeframe: str = "5m", strategy_config: Optional[Dict[str, Any]] = None) -> str:
    """
    Runs the full strategy discovery and optimization pipeline, including regime labeling and walk-forward validation.
    Expects data to be already ingested.
    """
    config = {
        "enabled": True,
        "instrument": instrument,
        "contract": contract,
        "timeframe": timeframe,
        "regime": {"enabled": True},
        "walk_forward": {"enabled": True},
        "cost_model": {"commission_per_side": 2.09, "slippage_ticks": 1, "tick_value": 5.0}
    }
    if strategy_config:
        config.update(strategy_config)
    
    res = _facade.run_strategy_discovery(config)
    if "error" in res:
        return f"Optimization failed: {res['error']}"
    return f"Strategy optimization complete for {instrument} {contract}."

@tool
def generate_final_report(output_dir: str, report_name: str = "Agent_Analysis_Report") -> str:
    """
    Generates the final HTML and text reports summarizing the analysis and optimized strategies.
    """
    # Create a minimal report config if one doesn't exist
    res = _facade.generate_reports(output_dir)
    if "error" in res:
        return f"Report generation failed: {res['error']}"
    return f"Reports generated: {', '.join(res['written_files'])}"
