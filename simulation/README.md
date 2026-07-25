# Module A: Target Environment

## Overview

This module simulates a real microservice cluster environment and provides:
- **Cluster Deployment**: Docker Compose or Minikube-based microservice topology
- **Telemetry Streaming**: Real-time container logs, metrics, and traces
- **Fault Injection**: Controlled chaos engineering (latency, CPU, memory, connection pool exhaustion)
- **Health Monitoring**: Validation and readiness checks

## Phase 1 Goals

yes Deploy Google Online Boutique (or TrainTicket) using Docker Compose  
yes Stream container stdout/stderr to local files or syslog  
yes Implement basic fault injection (CPU spike, network latency, pod restart)  
yes Validate telemetry collection pipeline  

## Setup Steps

### Prerequisites
```bash
docker --version  # Must be >= 20.10
docker-compose --version  # Or: minikube + kubectl
```

### Deploy Cluster
```bash
# 1. Copy .env.example to .env and set CLUSTER_TYPE=docker-compose
cp .env.example .env
export CLUSTER_TYPE=docker-compose

# 2. Start the cluster
python -m module_a_target_env.cluster_setup --action deploy

# 3. Verify readiness (wait ~60s for all services)
docker-compose -f module_a_target_env/docker/docker-compose.yml ps
```

### Collect Telemetry
```bash
# Stream all logs to console
python -m module_a_target_env.telemetry_collector --service all

# Stream from specific service
python -m module_a_target_env.telemetry_collector --service frontend

# Save to file (Phase 2: fed into graph populator)
python -m module_a_target_env.telemetry_collector --service all --output telemetry_data.jsonl
```

### Inject Faults
```bash
# Spike CPU on frontend service for 60 seconds
python -m module_a_target_env.fault_injector --fault cpu_spike --service frontend --duration 60

# Add 500ms network latency to checkout service
python -m module_a_target_env.fault_injector --fault network_latency --service checkout --latency 500

# Exhaust database connection pool
python -m module_a_target_env.fault_injector --fault db_connection_pool --duration 120

# Kill a pod (simulates crash)
python -m module_a_target_env.fault_injector --fault pod_kill --service cartservice
```

## Project Structure

```
module_a_target_env/
├── __init__.py
├── README.md (this file)
├── cluster_setup.py         # Deploy/teardown orchestration
├── telemetry_collector.py   # Log streaming and metric collection
├── fault_injector.py        # Chaos engineering interface
├── docker/
│   ├── docker-compose.yml   # Online Boutique stack
│   ├── docker-compose-trainticket.yml  # Alternative
│   └── Dockerfile.*         # Custom service images (if needed)
└── manifests/
    ├── online-boutique-k8s.yaml  # Kubernetes YAML for Minikube
    └── kustomization.yaml        # Kustomize patches for chaos injection
```

## Telemetry Output

Telemetry collected in Phase 1 flows to:
1. **Console**: Real-time debugging during development
2. **Local files**: `telemetry_data/` directory (JSONL format)
3. **Neo4j**: Phase 2 populates graph from these events

Schema: See `core/schemas.py::TelemetryEvent`

## Fault Injection Catalog

| Fault Type | Target | Impact |
|---|---|---|
| `cpu_spike` | Service pod | 80-100% CPU utilization |
| `memory_pressure` | Service pod | Force OOM conditions |
| `network_latency` | Service-to-service gRPC | Add N ms delay |
| `network_loss` | Network link | Drop X% of packets |
| `db_connection_pool` | Database service | Exhaust connection slots |
| `pod_kill` | Container | Forceful restart |
| `disk_full` | Service data dir | Fill temporary storage |

## Minikube vs Docker Compose

**Docker Compose** (Phase 1 default):
- Simpler setup, local-only
- All services on single host
- Faster iteration

**Minikube** (Phase 1 optional):
- More realistic K8s topology
- Proper pod scheduling
- Requires ~4GB RAM + virtualization

## Next Steps (Phase 2)

- Graph Populator ingests telemetry → Neo4j nodes/edges
- Service dependency inference from communication patterns
- SOP library linking remediations to detected failure modes

## Troubleshooting

**Q: Services not starting**  
A: Check Docker daemon, increase resource limits, review `docker-compose logs`

**Q: Telemetry not streaming**  
A: Verify container names match configuration, check Docker socket permissions

**Q: Faults not applied**  
A: Ensure Docker containers are running, check fault injector logs for errors

---

Dependencies: See `pyproject.toml` (core, docker client)  
Author: AIOps Research Team  
