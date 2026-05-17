"""DEPRECATED — see docs/designs/agentic_research_program.md §3 and Phase B.

This module wires the original "Lead Strategist" god-orchestrator pattern
that the master plan rejects. It still imports the deprecated
`analysis_tools.py` god-tools rather than the journaled registry in
`ta_foundation.agent.tools`.

Phase B replaces the topology entirely: a fixed Python `scheduler.py`
invokes role-scoped subgraphs (Triage, Scribe, Hypothesis Author, Sweep
Operator) over the new tool registry. Until that lands, this module remains
importable for backwards compatibility but should not be used in new code.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Any, Literal

from langchain_ollama import ChatOllama

warnings.warn(
    "ta_foundation.agent.graph is deprecated. The Lead-Strategist topology is "
    "being replaced by role-scoped subgraphs in Phase B; until then use the "
    "journaled tool registry in ta_foundation.agent.tools directly. "
    "See docs/designs/agentic_research_program.md.",
    DeprecationWarning,
    stacklevel=2,
)
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from ta_foundation.agent.state import AgentState
from ta_foundation.agent.tools.analysis_tools import ingest_data, run_discovery_sweep, run_strategy_optimization, generate_final_report

# Initialize Tools
tools = [ingest_data, run_discovery_sweep, run_strategy_optimization, generate_final_report]
tool_node = ToolNode(tools)

def get_model(model_name: str = "llama3.1"):
    return ChatOllama(model=model_name, temperature=0).bind_tools(tools)

def planner_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Analyzes the request and updates the todo list.
    """
    model_name = config.get("configurable", {}).get("model_name", "llama3.1")
    llm = get_model(model_name)
    
    system_prompt = (
        "You are the Lead Strategist for ta_foundation. "
        "Analyze the conversation and tool outputs. "
        "Update the 'todos' list and decide the next best action. "
        "CRITICAL: Do not hallucinate file paths. If you do not know the path to market data or backtest results, ASK the user to provide it. "
        "If the user has provided a path, use it exactly. "
        "If the analysis is complete, summarize your findings. "
        "Current context: instrument={instrument}, timeframe={timeframe}. "
    ).format(instrument=state.get("instrument"), timeframe=state.get("timeframe"))
    
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = llm.invoke(messages)
    
    return {"messages": [response], "status": "planning"}

def synthesizer_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    Final node to aggregate all findings into a structured summary.
    """
    model_name = config.get("configurable", {}).get("model_name", "llama3.1")
    llm = ChatOllama(model=model_name, temperature=0)
    
    system_prompt = "You are a Senior Market Analyst. Summarize the final results of the analysis clearly."
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "status": "complete"}

def router(state: AgentState) -> Literal["call_tools", "synthesize", "planner"]:
    """
    Decides the next node.
    """
    messages = state["messages"]
    last_message = messages[-1]
    
    if last_message.tool_calls:
        return "call_tools"
    
    # If the agent seems to be finished or explicitly says it's done
    if "complete" in last_message.content.lower() or "final report" in last_message.content.lower():
        return "synthesize"
    
    return "planner"

def build_graph(checkpointer=None):
    workflow = StateGraph(AgentState)
    
    workflow.add_node("planner", planner_node)
    workflow.add_node("call_tools", tool_node)
    workflow.add_node("synthesize", synthesizer_node)
    
    workflow.set_entry_point("planner")
    
    workflow.add_conditional_edges(
        "planner",
        router,
        {
            "call_tools": "call_tools",
            "synthesize": "synthesize",
            "planner": "planner"
        }
    )
    
    workflow.add_edge("call_tools", "planner")
    workflow.add_edge("synthesize", END)
    
    return workflow.compile(checkpointer=checkpointer)
