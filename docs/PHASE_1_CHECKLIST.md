## Phase 1 Implementation Checklist

**Status**: In Progress  
**Target**: May 2026  

### Goals for Phase 1

Establish the **simulation environment** and **telemetry pipeline**.

---

### Module A: Target Environment

- [ ] **Cluster Deployment**
  - [ ] Docker Compose setup for Google Online Boutique
  - [ ] Validate all services start healthy
  - [ ] Expose metrics endpoints (port 8000+)
  - [ ] Alternative: Minikube deployment (optional)

- [ ] **Telemetry Collection**
  - [ ] Stream container stdout/stderr to local files (JSONL)
  - [ ] Parse logs for error keywords (ERROR, CRITICAL, timeout)
  - [ ] Export as TelemetryEvent objects
  - [ ] Implement CLI: `telemetry_collector --service all --output telemetry_data/`

- [ ] **Fault Injection**
  - [ ] Implement CPU spike injection (stress-ng inside container)
  - [ ] Implement network latency (tc traffic control)
  - [ ] Implement pod kill (docker-compose kill)
  - [ ] Implement connection pool exhaustion (open connections loop)
  - [ ] Test each fault manually, verify telemetry capture

- [ ] **Documentation**
  - [ ] Write module_a_target_env/README.md with setup + usage
  - [ ] Document fault injection commands
  - [ ] Troubleshooting guide

### Module B: Neo4j Graph Database

- [ ] **Schema Initialization**
  - [ ] Define ServiceEntity, PodEntity, Database, RemediationSOP node types
  - [ ] Create unique constraints on IDs
  - [ ] Create indexes on frequently-queried fields (name, status, category)
  - [ ] Write init script: `scripts/init_graph.py`

- [ ] **Graph Client**
  - [ ] Implement Neo4jClient class with connection pooling
  - [ ] Implement read_query and write_query methods
  - [ ] Add basic blast_radius method (multi-hop DEPENDS_ON)
  - [ ] Add test connectivity method

- [ ] **SOP Library**
  - [ ] Define 15 production-ready remediation SOPs
  - [ ] Write as YAML file with:
     - name, category, description
     - applicable_services list
     - python script code
     - validation_queries list
     - risk_level, estimated_duration_sec
  - [ ] Implement load_sops.py script

- [ ] **Pre-written Queries**
  - [ ] Write Cypher queries for common patterns (see cypher/ directory)
  - [ ] Test queries manually in Neo4j browser

- [ ] **Documentation**
  - [ ] Write module_b_graph_database/README.md with setup + usage
  - [ ] Document schema with ER diagram
  - [ ] Create SOP catalog documentation

### Core: Shared Utilities

- [x] **Configuration Loading** (config.py completed)
  - [x] Load .env file
  - [x] Environment variable overrides
  - [x] Config singleton

- [x] **Schema Definitions** (schemas.py completed)
  - [x] Pydantic models for all entities
  - [x] TelemetryEvent model
  - [x] RemediationSOP model
  - [x] AgentSystemState model

- [x] **Logging** (logging_config.py completed)
  - [x] Structured logging with loguru
  - [x] JSON output for production
  - [x] Separate error log

- [x] **Exception Hierarchy** (exceptions.py completed)
  - [x] Base AgenticGraphRAGError
  - [x] Module-specific exceptions

### Module C: LangGraph Agentic Brain

- [ ] **Project Structure** (stubbed, for Phase 3)
  - [ ] Create state_machine.py with function stubs
  - [ ] Create agents/ and tools/ directories
  - [ ] Add docstrings explaining Phase 3 implementation

### Module D: Docker Sandbox Engine

- [ ] **Project Structure** (stubbed, for Phase 4)
  - [ ] Create engine/ and templates/ directories
  - [ ] Add security policy documentation
  - [ ] Write template Dockerfiles

### Testing Infrastructure

- [ ] **Test Suite Setup**
  - [ ] Create pytest fixtures for:
     - Neo4j test instance
     - Docker Compose cluster
     - Configuration
  - [ ] Implement test_*.py files for each module
  - [ ] Target: 70%+ code coverage

- [ ] **CI/CD Pipeline**
  - [ ] GitHub Actions workflow
  - [ ] Lint + test on PR
  - [ ] Coverage reporting

### Documentation

- [x] **README.md** (main project overview)
- [x] **ARCHITECTURE.md** (system design)
- [x] **Module READMEs** (module_a, module_b)
- [ ] **PHASE_1_CHECKLIST.md** (this file)
- [ ] **NEO4J_SCHEMA.md** (detailed schema reference)
- [ ] **SOP_LIBRARY.md** (remediation catalog)
- [ ] **DOCKER_SANDBOX_SPEC.md** (Phase 4, security hardening)

### Deliverables

By end of Phase 1, the system should:

yes Deploy a microservice cluster (Docker Compose)  
yes Stream live telemetry (logs, metrics)  
yes Inject realistic faults (CPU, network, connections)  
yes Store schema in Neo4j  
yes Load 15+ production SOP scripts  
yes Execute basic graph queries (blast radius, SOP lookup)  
yes Have comprehensive documentation  
yes Pass test suite  

### Hand-off to Phase 2

Phase 2 will:
1. Implement graph populator (telemetry → Neo4j nodes/edges)
2. Implement dependency inference from traces
3. Build the agentic brain node stubs

---

**Last Updated**: May 31, 2026  
**Owner**: AIOps Research Team  
