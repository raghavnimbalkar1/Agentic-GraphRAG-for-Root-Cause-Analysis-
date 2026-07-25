"""
graph/schema_definitions.py

Single source of truth for all Neo4j node labels, relationship types,
and property names used in the project.

Also contains the complete Online Boutique service registry — every
service, its type, port, and known failure modes.

Why this file exists:
    Prevents typos in Cypher strings scattered across the codebase.
    Import these constants instead of writing raw strings.

    Wrong:   session.run("MATCH (s:Servic) ...")   # silent typo
    Correct: session.run(f"MATCH (s:{NodeLabel.SERVICE}) ...")
"""

from dataclasses import dataclass, field
from core.schemas import ServiceStatus


# ── Node Labels ────────────────────────────────────────────────────────────

class NodeLabel:
    # Layer 1 — built now
    SERVICE  = "Service"
    SKILL    = "Skill"

    # Layer 2 — added in Phase 3 (richer graph)
    CONTAINER    = "Container"
    METRIC       = "Metric"
    HEALTH_CHECK = "HealthCheck"

    # Layer 3 — added pre-evaluation
    FAULT         = "Fault"
    FAULT_HISTORY = "FaultHistory"


# ── Relationship Types ─────────────────────────────────────────────────────

class RelType:
    # Layer 1
    DEPENDS_ON   = "DEPENDS_ON"     # Service → Service
    APPLIES_TO   = "APPLIES_TO"     # Skill → Service
    NEXT_IF_FAIL = "NEXT_IF_FAIL"   # Skill → Skill

    # Layer 2
    HOSTED_ON  = "HOSTED_ON"    # Service → Container
    HAS_METRIC = "HAS_METRIC"   # Service → Metric
    CHECKS     = "CHECKS"       # HealthCheck → Service

    # Layer 3
    CAUSED_BY     = "CAUSED_BY"     # FaultHistory → Fault
    RESOLVED_BY   = "RESOLVED_BY"   # FaultHistory → Skill


# ── Property Names ─────────────────────────────────────────────────────────

class Prop:
    # Service
    NAME          = "name"
    SERVICE_TYPE  = "service_type"
    STATUS        = "status"
    PORT          = "port"
    LANGUAGE      = "language"
    ERROR_CODE    = "error_code"
    LAST_UPDATED  = "last_updated"

    # Skill
    SCRIPT_PATH      = "script_path"
    SCRIPT_TYPE      = "script_type"
    DESCRIPTION      = "description"
    TRIGGER_CONDITION= "trigger_condition"
    TIMEOUT_SECONDS  = "timeout_seconds"
    RISK_LEVEL       = "risk_level"
    PARAMS           = "params"

    # Relationship
    CRITICALITY  = "criticality"
    TIMEOUT_MS   = "timeout_ms"


# ── Service Types ──────────────────────────────────────────────────────────

class ServiceType:
    FRONTEND      = "frontend"
    API           = "api"
    CACHE         = "cache"
    DATABASE      = "database"
    GATEWAY       = "gateway"
    LOAD_GEN      = "load_generator"
    MESSAGE_QUEUE = "message_queue"


# ── Criticality Levels ─────────────────────────────────────────────────────

class Criticality:
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


# ── Online Boutique Service Registry ──────────────────────────────────────
#
# Source: https://github.com/GoogleCloudPlatform/microservices-demo
# Container names match exactly — used for fault injection targeting.

@dataclass
class ServiceDefinition:
    name: str                           # Exact Docker container name
    service_type: str
    port: int
    language: str
    description: str
    known_failure_modes: list[str] = field(default_factory=list)


ONLINE_BOUTIQUE_SERVICES: list[ServiceDefinition] = [
    ServiceDefinition(
        name="frontend",
        service_type=ServiceType.FRONTEND,
        port=8080,
        language="Go",
        description="Serves the web UI; fan-out to nearly all backend services",
        known_failure_modes=[
            ServiceStatus.DEGRADED,
            ServiceStatus.CONNECTION_REFUSED,
        ],
    ),
    ServiceDefinition(
        name="cartservice",
        service_type=ServiceType.API,
        port=7070,
        language="C#",
        description="Manages user shopping carts; backed by Redis",
        known_failure_modes=[
            ServiceStatus.CONNECTION_REFUSED,
            ServiceStatus.CRASH_LOOPING,
        ],
    ),
    ServiceDefinition(
        name="productcatalogservice",
        service_type=ServiceType.API,
        port=3550,
        language="Go",
        description="Serves product listings and details",
        known_failure_modes=[
            ServiceStatus.CRASH_LOOPING,
            ServiceStatus.DEGRADED,
        ],
    ),
    ServiceDefinition(
        name="currencyservice",
        service_type=ServiceType.API,
        port=7000,
        language="Node.js",
        description="Converts prices between currencies",
        known_failure_modes=[
            ServiceStatus.CRASH_LOOPING,
            ServiceStatus.DOWN,
        ],
    ),
    ServiceDefinition(
        name="paymentservice",
        service_type=ServiceType.API,
        port=50051,
        language="Node.js",
        description="Processes payment transactions",
        known_failure_modes=[
            ServiceStatus.CONNECTION_REFUSED,
            ServiceStatus.DOWN,
        ],
    ),
    ServiceDefinition(
        name="shippingservice",
        service_type=ServiceType.API,
        port=50051,
        language="Go",
        description="Calculates and executes shipping",
        known_failure_modes=[
            ServiceStatus.DEGRADED,
            ServiceStatus.DOWN,
        ],
    ),
    ServiceDefinition(
        name="emailservice",
        service_type=ServiceType.API,
        port=5000,
        language="Python",
        description="Sends order confirmation emails",
        known_failure_modes=[
            ServiceStatus.DOWN,
            ServiceStatus.CRASH_LOOPING,
        ],
    ),
    ServiceDefinition(
        name="checkoutservice",
        service_type=ServiceType.API,
        port=5050,
        language="Go",
        description="Orchestrates the full checkout flow",
        known_failure_modes=[
            ServiceStatus.DEGRADED,
            ServiceStatus.CONNECTION_REFUSED,
        ],
    ),
    ServiceDefinition(
        name="recommendationservice",
        service_type=ServiceType.API,
        port=8080,
        language="Python",
        description="Returns product recommendations",
        known_failure_modes=[
            ServiceStatus.DEGRADED,
            ServiceStatus.CRASH_LOOPING,
        ],
    ),
    ServiceDefinition(
        name="adservice",
        service_type=ServiceType.API,
        port=9555,
        language="Java",
        description="Serves contextual advertisements",
        known_failure_modes=[
            ServiceStatus.HIGH_CPU,
            ServiceStatus.CRASH_LOOPING,
        ],
    ),
    ServiceDefinition(
        name="redis-cart",
        service_type=ServiceType.CACHE,
        port=6379,
        language="Redis",
        description="In-memory store for cart data",
        known_failure_modes=[
            ServiceStatus.OOM_KILLED,
            ServiceStatus.STALE_DATA,
            ServiceStatus.CONNECTION_REFUSED,
        ],
    ),
    ServiceDefinition(
        name="loadgenerator",
        service_type=ServiceType.LOAD_GEN,
        port=0,
        language="Python",
        description="Locust-based synthetic load generator",
        known_failure_modes=[],
    ),
]

# Quick lookup dict: service_name → ServiceDefinition
SERVICE_REGISTRY: dict[str, ServiceDefinition] = {
    s.name: s for s in ONLINE_BOUTIQUE_SERVICES
}


# ── SOP Registry ───────────────────────────────────────────────────────────
#
# Defines all SOP skill nodes to be loaded into the Skill Graph.
# script_path must match an actual file under sops/.

@dataclass
class SOPDefinition:
    name: str
    script_path: str
    script_type: str                    # python | bash
    description: str
    trigger_condition: str              # maps to ServiceStatus value
    applies_to: list[str]               # service names this SOP targets
    next_if_fail: list[str] = field(default_factory=list)  # SOP names to try next
    timeout_seconds: int = 30
    risk_level: str = Criticality.LOW
    params: list[str] = field(default_factory=list)


SOP_REGISTRY: list[SOPDefinition] = [
    SOPDefinition(
        name="Redis_Flush_SOP",
        script_path="/sops/redis/cache_flush.sh",
        script_type="bash",
        description="Flushes all keys from Redis to clear stale cart data",
        trigger_condition=ServiceStatus.STALE_DATA,
        applies_to=["redis-cart"],
        next_if_fail=["Redis_Restart_SOP"],
        params=["REDIS_HOST", "REDIS_PORT"],
    ),
    SOPDefinition(
        name="Redis_Restart_SOP",
        script_path="/sops/redis/restart.sh",
        script_type="bash",
        description="Restarts the Redis container after OOM or connection failure",
        trigger_condition=ServiceStatus.OOM_KILLED,
        applies_to=["redis-cart"],
        next_if_fail=["Cart_Restart_SOP"],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="Cart_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts the cartservice container",
        trigger_condition=ServiceStatus.CONNECTION_REFUSED,
        applies_to=["cartservice"],
        next_if_fail=["Redis_Flush_SOP"],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="Payment_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts the paymentservice container",
        trigger_condition=ServiceStatus.CONNECTION_REFUSED,
        applies_to=["paymentservice"],
        next_if_fail=[],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="ProductCatalog_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts productcatalogservice after crash loop",
        trigger_condition=ServiceStatus.CRASH_LOOPING,
        applies_to=["productcatalogservice"],
        next_if_fail=[],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="Checkout_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts checkoutservice after degradation",
        trigger_condition=ServiceStatus.DEGRADED,
        applies_to=["checkoutservice"],
        next_if_fail=["Cart_Restart_SOP"],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="Frontend_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts frontend service",
        trigger_condition=ServiceStatus.DEGRADED,
        applies_to=["frontend"],
        next_if_fail=[],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="AdService_CPU_Throttle_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Restarts adservice to relieve CPU spike",
        trigger_condition=ServiceStatus.HIGH_CPU,
        applies_to=["adservice"],
        next_if_fail=[],
        params=["CONTAINER_NAME"],
    ),
    SOPDefinition(
        name="Generic_Restart_SOP",
        script_path="/sops/container/restart.sh",
        script_type="bash",
        description="Generic container restart for crash-looping services",
        trigger_condition=ServiceStatus.CRASH_LOOPING,
        applies_to=[
            "currencyservice", "emailservice",
            "shippingservice", "recommendationservice",
        ],
        next_if_fail=[],
        params=["CONTAINER_NAME"],
    ),
]

SOP_REGISTRY_BY_NAME: dict[str, SOPDefinition] = {
    s.name: s for s in SOP_REGISTRY
}