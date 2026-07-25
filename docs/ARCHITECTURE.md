## Architecture Overview

Agentic GraphRAG implements a **graph-based autonomous reasoning system** for root cause analysis.

### Core Innovation

Traditional monitoring + LLM RAG systems fail on microservices because:
1. **Noise**: Hundreds of alerts cascade from single root cause
2. **Hallucination**: LLMs invent fixes without checking actual topology
3. **Danger**: State-of-the-art runs AI code directly on production hosts

**This system solves all three** by combining:
- **Graph database** = map of service topology + remediation scripts
- **Multi-agent reasoning** = LangGraph ReAct loop over structured state
- **Sandbox execution** = all AI-generated code runs in isolated containers

### Information Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ A: Target Environment (Docker Compose + Chaos Mesh)              │
│                                                                   │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐           │
│  │  Frontend   │    │   Checkout  │    │  CartSvc    │           │
│  └────┬────────┘    └────┬────────┘    └────┬────────┘           │
│       │ (error spike)    │ (timeout)        │ (500 error)         │
│       └────────┬─────────┴─────────┬────────┘                     │
│                ↓                   ↓                              │
│         Log stream: "Database connection pool exhausted"          │
└────────────────┬──────────────────────────────────────────────────┘
                 │
                 ↓ TelemetryEvent (structured)
┌──────────────────────────────────────────────────────────────────┐
│ B: Neo4j Semantic Skill Graph                                    │
│                                                                   │
│  Service Nodes:        Relationship:      Remediation SOPs:      │
│  • Frontend            • DEPENDS_ON       • ResetConnPool        │
│  • Checkout          • HOSTED_ON        • RestartService        │
│  • CartService       • USES               • IncreaseTimeout      │
│  • Database          • REMEDIATED_BY     • ScaleReplicas        │
│                                                                   │
│  Query: "Why is Checkout failing?"                               │
│  → Traverse DEPENDS_ON edges                                     │
│  → Find Database (cartservice connection pool)                   │
│  → Find applicable SOPs for "db_connection_pool_exhaustion"      │
└────────────────┬──────────────────────────────────────────────────┘
                 │
                 ↓ (Blast radius + SOP candidates)
┌──────────────────────────────────────────────────────────────────┐
│ C: LangGraph Agentic Brain (ReAct Loop)                          │
│                                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Ingestion   │→ │ Analysis     │→ │ Planning     │            │
│  │             │  │ (query graph)│  │ (select SOP) │            │
│  └─────────────┘  └──────────────┘  └──────┬───────┘            │
│                                              ↓                    │
│                                      ┌──────────────┐            │
│                                      │ Confidence   │            │
│                                      │  > 0.75 ?    │            │
│                                      └──┬────────┬──┘            │
│                                    Yes ↓        ↓ No             │
│                                  ┌─────────┐  ┌──────────────┐   │
│                                  │Execute  │  │Escalation    │   │
│                                  └────┬────┘  └──────────────┘   │
│                                       ↓                          │
│                               ┌──────────────┐                   │
│                               │ Verification │                   │
│                               └────┬─────────┘                   │
│                                    ↓                             │
│                            Result: SUCCESS yes                     │
└────────────────────┬──────────────────────────────────────────────┘
                     │
                     ↓ SandboxExecutionRequest
┌──────────────────────────────────────────────────────────────────┐
│ D: Docker Sandbox Engine (Secure Execution)                      │
│                                                                   │
│  ┌─────────────────────────────────────────┐                    │
│  │ Ephemeral Container (destroyed after)   │                    │
│  │ ┌──────────────────────────────────────┐│                    │
│  │ │ Remediation Script:                  ││                    │
│  │ │ SELECT * FROM stale_connections     ││                    │
│  │ │ ALTER SYSTEM SET max_connections=100 ││                    │
│  │ └──────────────────────────────────────┘│                    │
│  │ Constraints:                             │                    │
│  │ • read_only_rootfs = true               │                    │
│  │ • memory_limit = 512MB                  │                    │
│  │ • cpu_limit = 0.5 cores                 │                    │
│  │ • cap_drop = [SYS_ADMIN, NET_ADMIN]    │                    │
│  │ • timeout = 30 seconds                  │                    │
│  └─────────────────────────────────────────┘                    │
│                  ↓                                               │
│           Result: SUCCESS (exit_code=0)                         │
└──────────────────────────────────────────────────────────────────┘
```

### State Machine Phases

The agent flows through structured states:

```
AgentSystemState {
  phase: "ingestion" | "analysis" | "planning" | "execution" | "verification" | "escalation"
  current_telemetry: [TelemetryEvent, ...]
  blast_radius: GraphBlastRadiusResult
  llm_reasoning_steps: ["Step 1: Ingested 5 error events", "Step 2: Found root cause: db_pool"]
  identified_root_causes: ["database_connection_pool_exhaustion"]
  candidate_sops: [RemediationSOP, ...]
  executed_actions: [{action: "ResetConnPool", timestamp: ...}, ...]
  sandbox_results: [SandboxExecutionResult, ...]
  should_escalate: bool
  confidence_score: 0.85  # [0.0, 1.0]
  messages: [{"role": "agent", "content": "reasoning..."}, ...]
}
```

### ReAct Loop Example

**Scenario**: Frontend suddenly returns 500 errors.

**Reasoning Steps**:

1. **Ingestion**: 
   - Parse 5 error logs from frontend, checkout, cartservice
   - Identify primary service: `checkout`

2. **Analysis (Graph Query)**:
   ```cypher
   MATCH (checkout:Service)-[:DEPENDS_ON]->(dep:Service)
   RETURN dep.name  -- Find: cartservice, database
   ```

3. **LLM Reasoning**:
   - Input: "Frontend→Checkout→CartService + Database error pattern"
   - LLM: "Connection pool likely exhausted on database"
   - Confidence: 0.85 (high, clear error pattern)

4. **SOP Selection**:
   ```cypher
   MATCH (sop:RemediationSOP {category: "database"})
   WHERE sop.applicable_services CONTAINS "cartservice"
   RETURN sop ORDER BY risk_level  -- Find: ResetConnPool (LOW risk)
   ```

5. **Confidence Check**:
   - 0.85 > threshold (0.75)? YES
   - risk_level = LOW? YES
   - → Execute

6. **Execution (Sandbox)**:
   ```python
   # Inside isolated container (512MB RAM, read-only /)
   import psycopg2
   conn = psycopg2.connect("dbname=inventory")
   cursor = conn.cursor()
   cursor.execute("ALTER SYSTEM SET max_connections=100")
   print("yes Connection pool reset")
   ```

7. **Verification**:
   - Run validation query: `SELECT active_connections FROM ...`
   - Check: active_connections < max_connections?
   - YES → Success! Confidence = 0.95

8. **End State**:
   ```json
   {
     "should_escalate": false,
     "confidence_score": 0.95,
     "root_cause": "database_connection_pool_exhaustion",
     "remediation_applied": "ResetConnPool (SOP-DB-001)",
     "execution_time_seconds": 12,
     "outcome": "SUCCESS"
   }
   ```

### Security Model

All remediation code executes in **hardened ephemeral containers**:

| Layer | Control | Purpose |
|-------|---------|---------|
| **FS** | read_only_rootfs=true | Prevent system modification |
| **Network** | isolated bridge | No outbound internet, reach services only |
| **Memory** | cgroups limit 512MB | Prevent OOM exhaustion |
| **CPU** | cgroups limit 0.5 core | Prevent resource denial |
| **Capabilities** | cap_drop[SYS_ADMIN, ...] | Prevent privilege escalation |
| **Timeout** | 30 seconds enforced | Kill runaway processes |

**Result**: Code injection = contained to ephemeral container, no host compromise.

### Comparison to State-of-the-Art

| Dimension | Traditional Monitoring | Text-Only RAG | Flow-of-Action | **Agentic GraphRAG** |
|-----------|---|---|---|---|
| Root cause identification | Manual | Hallucinations | Good | yes Graph-based (accurate) |
| Alert noise | High | High | Medium | yes Structured inference |
| Code execution | N/A | N/A | Direct on host Note: | yes Sandboxed (safe) |
| Reasoning transparency | None | Black box | Limited | yes Full audit trail |
| Multi-hop dependencies | None | None | None | yes Transitive closure |

---

**Next**: See [PHASE_1_CHECKLIST.md](PHASE_1_CHECKLIST.md) for implementation status.
