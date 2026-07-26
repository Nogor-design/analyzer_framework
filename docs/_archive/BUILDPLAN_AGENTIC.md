# Build Plan: Agentic Market Analysis System

This plan outlines the implementation of a multi-agent system for `ta_foundation`, enabling automated market analysis, strategy discovery, and reporting using local LLMs (Ollama) and LangGraph.

## Goals
- **Autonomy**: High-level goal-directed analysis (e.g., "Find an edge in NQ pre-market").
- **Reliability**: Deterministic tool execution with LLM-based planning and synthesis.
- **Persistence**: Long-running workflows with state recovery.
- **Local-First**: Minimal token cost using local models.

## Architecture: Hybrid Orchestration (Option C)
We use a hybrid approach where a high-level Agentic Loop (LangGraph) manages planning and delegation, while deterministic computation is handled by the existing `ta_foundation` analysis modules wrapped as "Tools".

### 1. Agents
- **Lead Strategist**: Orchestrates the project, manages the TODO list, and synthesizes final reports.
- **Discovery Agent**: Specializes in running "Discovery Sweeps" (Candle, ORB, LCR, etc.) to find raw statistical edges.
- **Strategy Engineer**: Optimizes discovered edges using the Pattern Engine and Strategy Discovery module (regime labeling, validation).
- **Risk Analyst**: Evaluates portfolio fit, drawdown, and generates final NinjaTrader artifacts.

### 2. State Model (`AgentState`)
- `messages`: Conversation history.
- `todos`: List of planned tasks.
- `context`: Current run ID, instrument, timeframe, and paths to artifacts.
- `results`: Key metrics and findings from tool calls.

---

## Phase 0: Toolification (The "Atoms")
**Goal**: Wrap existing CLI functionality into discrete, callable Python functions.

- [x] Create `src/ta_foundation/agent/tools/` directory.
- [x] **Ingestion Tools**:
    - `ingest_data` tool implemented.
- [x] **Discovery Tools**:
    - `run_discovery_sweep` tool implemented.
- [x] **Analysis Tools**:
    - `run_strategy_optimization` tool implemented.
- [x] **Export Tools**:
    - `generate_final_report` tool implemented.

---

## Phase 1: Agent Runtime (The "Brain")
**Goal**: Set up the LangGraph orchestration.

- [x] Define `AgentState` schema in `state.py`.
- [x] Implement Agent Nodes in `graph.py`:
    - `planner_node`: Logic for task management.
    - `tool_node`: Standard ToolNode for execution.
    - `synthesizer_node`: For final result aggregation.
- [x] Configure `Ollama` connectivity via `langchain-ollama`.

---

## Phase 2: Orchestration & Logic (The "Nerves")
**Goal**: Define the workflow graph.

- [x] Implement the `StateGraph` with dedicated `planner` and `synthesizer` nodes.
- [x] Define conditional edges for tool routing and final summarization.
- [x] Add persistence using `SqliteSaver` checkpointer.

---

## Phase 3: UX & Packaging (The "Interface")
**Goal**: Make it easy to run.

- [x] Create a CLI entrypoint: `python -m ta_foundation.agent.cli --request "Analyze NQ..."`.
- [x] Implement automated logging of "agent thoughts" and tool calls to the console.
- [x] Create a "Session Report" Markdown file that tracks the agent's research process.

---

## Phase 4: Validation
- [ ] Test with a specific scenario: "Find a 5m ORB edge for NQ and optimize a 150-tick profit target".
- [ ] Verify NinjaTrader XML output is valid and functional.
- [ ] Evaluate LLM tool-calling reliability and add guardrails/retries where needed.
