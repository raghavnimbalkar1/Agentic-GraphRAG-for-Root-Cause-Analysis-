# Simulation

Online Boutique provides the live microservice testbed. Fault injection changes
the environment; a separate collector detects conditions and delivers incidents.

## Components

| File | Responsibility |
| --- | --- |
| `docker-compose.yml` | Supported 12-container stack and ownership labels |
| `fault_injector.py` | Physical faults, target validation and matching resets |
| `telemetry_collector.py` | Independent observations, graph synchronization and debounce |
| `incident_delivery.py` | Durable episode identity and acknowledged retry |
| `incident_tracking.py` | Collector readiness and exact incident correlation |
| `application_canary.py` | Private-session cart and mock-checkout verification |
| `chaos_daemon.py` | Seeded collector-driven fault campaign with raw outcomes |

The stack has ten application services, `redis-cart` and `loadgenerator`.
It uses pinned Boutique v0.10.5 image tags. An optional upstream checkout under
`online-boutique/` is ignored and is not required to run the prebuilt images.

## Startup

```bash
docker compose -f simulation/docker-compose.yml pull
docker compose -f simulation/docker-compose.yml up -d --wait --wait-timeout 300
docker compose -f simulation/docker-compose.yml ps
python -m simulation.telemetry_collector --observe-only
```

Use exactly one collector. Observe-only mode records and synchronizes observations
but does not submit alerts. Normal mode can invoke the configured model and
trigger repairs; enable it only after the [operations checks](../docs/OPERATIONS.md).

## Faults And Recovery

```bash
python -m simulation.fault_injector list
python -m simulation.fault_injector inject config_drift
python -m simulation.fault_injector reset config_drift
```

These commands modify owned simulation containers. The injector does not write
Neo4j health or submit alerts. Follow the newly created collector incident ID,
then reset the physical fault and independently confirm recovery before another
trial. Do not inject into a production environment.

Fault names retain compatibility labels. A low Redis memory cap is not a kernel
OOM kill; expiring-key/client-count thresholds are controlled anomalies, not
proof of stale business data or production pool exhaustion. Writable-layer
pressure is not a full filesystem. The `high_latency` scenario requires tools
absent from the supplied images and is unsupported; `dependency_timeout` uses
the supported mechanism.

The application canary tests cart content and a mock order-completion marker in
its own session. It makes no real payment. Fixed-SOP acceptance tests are separate
from full LLM-driven agent trials.

See the [Evaluation Protocol](../docs/EVALUATION_PROTOCOL.md) for experiment
boundaries and [Project Status](../docs/STATUS.md) for validated coverage.
