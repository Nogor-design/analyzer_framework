from __future__ import annotations
from typing import Annotated, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """
    The state of the agentic market analysis system.
    """
    # Conversation history
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Planning
    todos: List[str]
    current_task: Optional[str]
    
    # Context & Artifacts
    instrument: Optional[str]
    timeframe: Optional[str]
    run_id: Optional[str]
    artifacts: List[str]
    
    # Status
    status: str
