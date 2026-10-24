# Graph

The Neo4j dual graph provides infrastructure context and an executable SOP
catalog for the agent.

## Model

| Element | Meaning |
| --- | --- |
| `Service` | Supported microservice identity and observed health |
| `Skill` | Curated SOP metadata, trigger, risk and script path |
| `A -DEPENDS_ON-> B` | Service A depends on service B |
| `Skill -APPLIES_TO-> Service` | Approved remediation target |
| `Skill -NEXT_IF_FAIL-> Skill` | Explicit same-target fallback |

The catalog contains 12 services, 15 skills, 16 dependency edges, 19 applicability
edges and one fallback edge. Service and skill names are unique. Multiple skills
can reference the same curated script; the catalog is not 15 distinct executables.

Q1 walks from an alert toward unhealthy dependency leaves. Multiple candidates
are exposed for review. Reverse reachability yields potential blast radius, not
observed outage extent. Q2 matches SOPs to the root's observed condition, rather
than the upstream alert symptom.

## Components

| File | Responsibility |
| --- | --- |
| `cypher/service_topology.cypher` | Authoritative topology and SOP seed catalog |
| `cypher/remediation_queries.cypher` | Reference runtime queries |
| `schema_definitions.py` | Labels, relationship constants and service definitions |
| `graph_client.py` | Shared Neo4j driver and typed query methods |
| `scripts/init_graph.py` | Catalog migration, count validation and read-only smoke test |
| `scripts/load_sops.py` | Compatibility entry point for the unified initializer |
| `procedure_catalog.py` | Capture graph skills without dropping fields; compile versioned local procedure contracts |
| `scripts/audit_procedures.py` | Read-only contract/code audit with private, non-overwriting output |
| `graph_populator.py` | Retired discovery interface; rejects unimplemented automation |

## Initialization And Checks

Run from the repository root with Neo4j credentials configured:

```bash
docker compose up -d --wait --wait-timeout 180 neo4j
python -m graph.scripts.init_graph
python -m graph.scripts.init_graph --verify-only
RCA_LIVE_GRAPH_TESTS=1 python -m pytest tests/test_live_graph.py -q
```

Normal initialization preserves existing observed health. `--verify-only` makes
no writes and now also validates procedure contracts. Do not use `--clean` or
delete Neo4j volumes for routine upgrades. The new
[contract/preflight path](../docs/PROCEDURE_CONTRACTS.md) does not grant execution.

Controlled evaluation owns UUID-scoped `EvalService` nodes and cleans up only
its own scope. TrainTicket uses separate `TTService` fixtures; it is not a
deployed remediation environment.

See [Architecture](../docs/ARCHITECTURE.md) and the
[Evaluation Protocol](../docs/EVALUATION_PROTOCOL.md).
