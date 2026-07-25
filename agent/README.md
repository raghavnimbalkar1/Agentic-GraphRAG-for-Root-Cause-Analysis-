# Module C: LangGraph Agentic Brain

## Overview

The **reasoning engine** that orchestrates autonomous root cause analysis and remediation.

A **stateful directed graph** (LangGraph) where:
- **Nodes** = reasoning tasks (log analysis, graph querying, LLM inference, action execution)
- **Edges** = conditional routing based on analysis results
- **State** = `AgentSystemState` TypedDict flowing through the graph
- **Messages** = Agent reasoning steps recorded in state for audit

This module implements a **ReAct loop** (Reasoning + Acting):
1. **Reason**: Parse telemetry, query graph for context, ask LLM "what's wrong?"
2. **Act**: Execute recommended remediation SOP in sandbox (Module D)
3. **Observe**: Collect post-remediation metrics
4. **Update State**: Record success/failure, update confidence
5. **Decide**: Escalate if needed or continue reasoning

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    LangGraph State Machine                    │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  [Ingestion] ──→ [Analysis] ──→ [Planning] ──┐               │
│                      ↓              ↓          │               │
│                  [Graph Query]   [SOP Select] │               │
│                      ↓              ↓          ↓               │
│                  [LLM Reasoning] ──→ [Execution]              │
│                                      ↓                         │
│                                  [Sandbox Run]                │
│                                      ↓                         │
│                              [Verification]                    │
│                                      ↓                         │
│                      ┌──────────────┴──────────────┐           │
│                      ↓ Success                   ↓ Fail        │
│                   [END]                    [Escalation]        │
│                                              ↓                 │
│                                          [Alert Human]         │
│                                                                │
└──────────────────────────────────────────────────────────────┘

State Flow:
  TelemetryEvent → AgentSystemState → (phase transitions)
  
Tool Calls:
  - Neo4j Queries (blast radius, SOP lookup)
  - LLM Inference (reasoning steps)
  - Sandbox Execution (remediation code)
```

## Phase 3 Implementation Goals

yes Define `AgentSystemState` (in core/schemas.py — already done)  
yes Implement state machine graph with 5 main nodes  
yes Configure LLM endpoint (Ollama/vLLM)  
yes Define OpenClaw tool schemas for validation  
yes Implement conditional router for escalation  
yes Build post-remediation verification loop  
yes Add ReAct reasoning artifacts to state  

## Usage (Phase 3+)

```python
from module_c_agentic_brain.state_machine import create_agentic_brain

# Create the agent
agent = create_agentic_brain(config)

# Trigger analysis on telemetry event
result = agent.invoke({
    "phase": "ingestion",
    "current_telemetry": [telemetry_event_1, telemetry_event_2],
    "messages": [],
    # ... other state fields
})

# Check result
if result["should_escalate"]:
    print(f"Escalation reason: {result['escalation_reason']}")
else:
    print(f"Remediation executed. Confidence: {result['confidence_score']}")
```

## Node Specifications

### Node: Log Ingestion
- **Input**: Raw telemetry events
- **Output**: Normalized TelemetryEvent list, identified service + severity
- **Next**: analysis

### Node: Graph Analysis
- **Input**: Root cause service name
- **Output**: Blast radius (affected services), critical dependencies
- **Queries**: Multi-hop DEPENDS_ON traversal
- **Next**: planning

### Node: LLM Reasoning
- **Input**: Graph context + telemetry summary
- **Output**: Hypothesis about root cause, confidence score
- **Prompt Engineering**: Few-shot examples of failure modes
- **Next**: planning

### Node: SOP Selection
- **Input**: Root cause hypothesis, applicable services
- **Output**: Ranked list of candidate remediation SOPs
- **Next**: conditional router

### Node: Conditional Router
- **Logic**: IF confidence_score > threshold AND risk_level < "high" THEN execution ELSE escalation
- **Next**: execution or escalation

### Node: Sandbox Execution
- **Input**: Selected SOP script + parameters
- **Output**: Execution result (stdout, stderr, exit code)
- **Safety**: All code runs in isolated Docker container (Module D)
- **Next**: verification

### Node: Verification
- **Input**: Pre-fix metrics + post-fix metrics
- **Output**: Boolean success flag, updated confidence score
- **Queries**: Validation queries from SOP definition
- **Next**: end or escalation

### Node: Escalation
- **Input**: Failure reason, reasoning steps
- **Output**: Alert message for human operator
- **Next**: end

## Tool Definitions (OpenClaw)

All tool inputs validated by Pydantic before execution.

### Tool: graph_query
```python
class GraphQueryInput(ToolInput):
    cypher_query: str  # Parameterized Cypher
    parameters: Dict[str, Any]
```

### Tool: llm_inference
```python
class LLMInferenceInput(ToolInput):
    prompt: str
    temperature: float = 0.7
    max_tokens: int = 1024
```

### Tool: sandbox_execute
```python
class SandboxExecutionInput(ToolInput):
    request: SandboxExecutionRequest  # See core/schemas.py
```

## Key Conventions

1. **No direct LLM calls** — all go through `llm_inference` node
2. **No raw dicts** — all state is `AgentSystemState`
3. **Parameterized queries** — all Neo4j queries use $param syntax
4. **Audit trail** — all reasoning steps recorded in `state.messages`
5. **Deterministic routing** — conditional edges based on explicit thresholds

## Next Steps (Phase 4+)

- **Performance Tuning**: Profile agent latency, optimize graph queries
- **User Feedback**: Record human decisions, retrain confidence thresholds
- **Multi-Agent**: Coordinate multiple brain instances for complex scenarios
- **Cascade Management**: Handle second-order failures during remediation

---

Dependencies: LangGraph, LangChain, Neo4j driver  
Author: AIOps Research Team  
