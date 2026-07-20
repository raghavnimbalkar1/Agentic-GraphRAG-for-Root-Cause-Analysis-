"""
graph/graph_client.py

Neo4j driver wrapper. Every database call in the project goes through here.
Implements all 5 agent runtime queries as typed Python methods.

Usage:
    from graph.graph_client import GraphClient
    client = GraphClient()
    result = client.get_root_cause("frontend", "DEGRADED")
"""

from __future__ import annotations

import atexit
import threading
from typing import Optional

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, AuthError

from core.config import settings
from core.logging_config import get_logger
from core.schemas import DependencyChainResult, SkillNode, ServiceStatus
from core.exceptions import (
    GraphError,
    RootCauseNotFoundError,
    SkillNotFoundError,
)

log = get_logger(__name__)


class GraphClient:
    """
    Singleton-style Neo4j client.
    One instance per process — created once and reused.

    Thread-safe: the neo4j Driver manages a connection pool internally.
    """

    _instance: Optional["GraphClient"] = None
    _driver: Optional[Driver] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "GraphClient":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Double-checked lock: __init__ runs on EVERY GraphClient() call, and the
        # collector's daemon alert threads can construct concurrently with the
        # main loop — without the lock, two first-callers both see _driver=None
        # and each open (and leak) a driver.
        if self._driver is not None:
            return
        with GraphClient._init_lock:
            if self._driver is None:
                self._connect()

    # ── Connection ─────────────────────────────────────────────────────────

    def _connect(self) -> None:
        """Establish connection to Neo4j. Fails fast with clear error."""
        try:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=settings.neo4j_auth,
                max_connection_pool_size=10,
                connection_timeout=10,
            )
            self._driver.verify_connectivity()
            log.info("neo4j_connected", uri=settings.neo4j_uri)
            atexit.register(self.close)
        except AuthError as e:
            raise GraphError(
                f"Neo4j authentication failed. "
                f"Check NEO4J_USER and NEO4J_PASSWORD in .env. "
                f"Detail: {e}"
            ) from e
        except ServiceUnavailable as e:
            raise GraphError(
                f"Neo4j unreachable at {settings.neo4j_uri}. "
                f"Is the container running? (docker compose up neo4j -d). "
                f"Detail: {e}"
            ) from e

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            log.info("neo4j_disconnected")

    def health_check(self) -> bool:
        """Returns True if Neo4j is reachable and responsive."""
        try:
            with self._driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False

    # ── Internal query runner ──────────────────────────────────────────────

    def _run(self, cypher: str, **params) -> list[dict]:
        """
        Execute a Cypher query and return results as a list of dicts.
        All query methods use this — never call the driver directly.
        """
        with self._driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    # ── Q1: Root cause traversal ───────────────────────────────────────────

    # Labels the traversal may run against. Labels cannot be Cypher parameters,
    # so node_label is interpolated — this allowlist keeps that interpolation safe.
    #   Service   = the live Online Boutique topology (the deployed system)
    #   TTService = the isolated TrainTicket topology (localisation study only)
    _VALID_LABELS = {"Service", "TTService"}

    def get_root_cause(
        self,
        alert_service: str,
        error_type: str,
        node_label: str = "Service",
        max_hops: int = 8,
    ) -> DependencyChainResult:
        """
        Walk the DEPENDS_ON graph from the alerting service to its dependencies
        to find the deepest unhealthy upstream node.

        If no unhealthy upstream found, the alerting service itself
        is the root cause (single-hop scenario).

        The traversal is topology-agnostic: `node_label` selects which graph to
        walk (default "Service" = live deployment; "TTService" = the isolated
        TrainTicket graph). Same logic, different topology — this is what lets the
        exact same localisation run on a deeper published benchmark without change.

        Returns: DependencyChainResult with root_cause_node + full chain.
        """
        if node_label not in self._VALID_LABELS:
            raise ValueError(f"Unknown node_label {node_label!r}; "
                             f"expected one of {sorted(self._VALID_LABELS)}")
        max_hops = max(1, int(max_hops))
        cypher = f"""
        MATCH path = (alert:{node_label} {{name: $alert_service}})
                     -[:DEPENDS_ON*1..{max_hops}]->(root:{node_label})
        WHERE root.status <> 'HEALTHY'
        WITH root,
             reverse([n IN nodes(path) | n.name]) AS chain,
             length(path) AS depth
        RETURN root.name  AS root_cause_node,
               chain      AS dependency_chain,
               depth       AS depth
        ORDER BY depth DESC
        LIMIT 1
        """
        rows = self._run(cypher, alert_service=alert_service)

        if rows:
            row = rows[0]
            log.info(
                "root_cause_found",
                root=row["root_cause_node"],
                depth=row["depth"],
                chain=row["dependency_chain"],
            )
            return DependencyChainResult(
                root_cause_node=row["root_cause_node"],
                dependency_chain=row["dependency_chain"],
                depth=row["depth"],
            )

        # No unhealthy upstream — the alerting service is the root
        log.info("root_cause_self", service=alert_service)
        return DependencyChainResult(
            root_cause_node=alert_service,
            dependency_chain=[alert_service],
            depth=0,
        )

    # ── Q2: Retrieve SOP skill ─────────────────────────────────────────────

    def get_skill(
        self,
        root_node: str,
        error_type: str,
        visited: list[str] | None = None,
    ) -> SkillNode:
        """
        Find the best matching SOP skill for a root cause node + error type.
        Excludes already-visited skills.

        Raises: SkillNotFoundError if no matching skill exists.
        """
        visited = visited or []
        cypher = """
        MATCH (svc:Service {name: $root_node})<-[:APPLIES_TO]-(skill:Skill)
        WHERE skill.trigger_condition = $error_type
          AND NOT skill.name IN $visited
        RETURN skill.name             AS name,
               skill.script_path     AS script_path,
               skill.script_type     AS script_type,
               skill.description     AS description,
               skill.params          AS params,
               skill.timeout_seconds AS timeout_seconds,
               skill.risk_level      AS risk_level,
               skill.trigger_condition AS trigger_condition
        LIMIT 1
        """
        rows = self._run(
            cypher,
            root_node=root_node,
            error_type=error_type,
            visited=visited,
        )

        if not rows:
            raise SkillNotFoundError(node=root_node, error_type=error_type)

        row = rows[0]
        log.info("skill_retrieved", skill=row["name"], node=root_node)
        return SkillNode(
            name=row["name"],
            script_path=row["script_path"],
            script_type=row["script_type"],
            description=row["description"],
            params=row["params"] or [],
            timeout_seconds=row["timeout_seconds"] or 30,
            risk_level=row["risk_level"] or "LOW",
            trigger_condition=row["trigger_condition"] or "",
        )

    def get_skills(
        self,
        root_node: str,
        error_type: str,
        visited: list[str] | None = None,
    ) -> list[SkillNode]:
        """
        Return ALL SOP skills that apply to a root cause node + error type
        (excluding already-visited ones), ordered by ascending risk level.

        This is the candidate set the LLM is allowed to choose from in the
        reasoner — the security boundary. The LLM can only pick a name from this
        graph-derived list; it can never introduce a SOP not present here.

        Returns [] if none match (caller decides to escalate).
        """
        visited = visited or []
        cypher = """
        MATCH (svc:Service {name: $root_node})<-[:APPLIES_TO]-(skill:Skill)
        WHERE skill.trigger_condition = $error_type
          AND NOT skill.name IN $visited
        RETURN skill.name             AS name,
               skill.script_path     AS script_path,
               skill.script_type     AS script_type,
               skill.description     AS description,
               skill.params          AS params,
               skill.timeout_seconds AS timeout_seconds,
               skill.risk_level      AS risk_level,
               skill.trigger_condition AS trigger_condition
        """
        rows = self._run(cypher, root_node=root_node,
                         error_type=error_type, visited=visited)

        risk_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        skills = [
            SkillNode(
                name=r["name"], script_path=r["script_path"],
                script_type=r["script_type"], description=r["description"],
                params=r["params"] or [], timeout_seconds=r["timeout_seconds"] or 30,
                risk_level=r["risk_level"] or "LOW",
                trigger_condition=r["trigger_condition"] or "",
            )
            for r in rows
        ]
        skills.sort(key=lambda s: risk_order.get(s.risk_level, 1))
        log.info("skills_retrieved", node=root_node, error_type=error_type,
                 candidates=[s.name for s in skills])
        return skills

    # ── Q3: Get next SOP in failure chain ─────────────────────────────────

    def get_next_skill(self, current_skill: str) -> SkillNode | None:
        """
        Follow NEXT_IF_FAIL edge to get the next SOP to try.
        Returns None if current skill has no fallback.
        """
        cypher = """
        MATCH (:Skill {name: $current_skill})-[:NEXT_IF_FAIL]->(next:Skill)
        RETURN next.name             AS name,
               next.script_path     AS script_path,
               next.script_type     AS script_type,
               next.description     AS description,
               next.params          AS params,
               next.timeout_seconds AS timeout_seconds,
               next.risk_level      AS risk_level,
               next.trigger_condition AS trigger_condition
        """
        rows = self._run(cypher, current_skill=current_skill)

        if not rows:
            log.info("no_next_skill", current=current_skill)
            return None

        row = rows[0]
        log.info("next_skill_found", next=row["name"], from_=current_skill)
        return SkillNode(
            name=row["name"],
            script_path=row["script_path"],
            script_type=row["script_type"],
            description=row["description"],
            params=row["params"] or [],
            timeout_seconds=row["timeout_seconds"] or 30,
            risk_level=row["risk_level"] or "LOW",
            trigger_condition=row["trigger_condition"] or "",
        )

    # ── Q4: Update service health status ──────────────────────────────────

    def update_service_status(
        self,
        service_name: str,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """
        Update the health status of a service node.
        Called by: telemetry collector, sandbox executor post-verification.
        """
        cypher = """
        MATCH (s:Service {name: $service_name})
        SET s.status       = $status,
            s.error_code   = $error_code,
            s.last_updated = datetime()
        """
        self._run(
            cypher,
            service_name=service_name,
            status=status,
            error_code=error_code,
        )
        log.info(
            "service_status_updated",
            service=service_name,
            status=status,
            error_code=error_code,
        )

    # ── Q5: Count unhealthy services ──────────────────────────────────────

    def count_unhealthy(self, service_names: list[str]) -> int:
        """
        Returns the count of services in service_names that are NOT HEALTHY.
        Used by the evaluator to decide whether to terminate the ReAct loop.
        """
        cypher = """
        MATCH (s:Service)
        WHERE s.name IN $services
          AND s.status <> 'HEALTHY'
        RETURN count(s) AS still_unhealthy
        """
        rows = self._run(cypher, services=service_names)
        count = rows[0]["still_unhealthy"] if rows else 0
        log.debug("unhealthy_count", count=count, services=service_names)
        return count

    # ── Multi-root: independent root causes ────────────────────────────────

    def get_independent_roots(self, max_hops: int = 8) -> list[dict]:
        """
        Return every unhealthy service that is an INDEPENDENT root cause — i.e.
        it has no unhealthy service among its own transitive dependencies. These
        are the genuine source faults; an unhealthy node that *does* depend on
        another unhealthy node is a downstream symptom, not a root.

        This is the detection primitive for multi-fault handling: two faults in
        different subtrees (e.g. redis-cart OOM + adservice HIGH_CPU) surface as
        two independent roots, each remediable by the single-root agent loop.

        Returns [{"name", "status"}], deepest-first is not meaningful here (roots
        are independent), so ordered by name for determinism.
        """
        cypher = f"""
        MATCH (r:Service)
        WHERE r.status <> 'HEALTHY'
          AND NOT EXISTS {{
              MATCH (r)-[:DEPENDS_ON*1..{max(1, int(max_hops))}]->(d:Service)
              WHERE d.status <> 'HEALTHY'
          }}
        RETURN r.name AS name, r.status AS status
        ORDER BY r.name
        """
        rows = self._run(cypher)
        log.info("independent_roots_found",
                 roots=[r["name"] for r in rows], count=len(rows))
        return [{"name": r["name"], "status": r["status"]} for r in rows]

    def count_all_unhealthy(self) -> int:
        """Total unhealthy Service nodes across the whole graph (multi-root
        termination check — distinct from count_unhealthy(chain), which is scoped
        to one incident's dependency chain)."""
        rows = self._run("MATCH (s:Service) WHERE s.status <> 'HEALTHY' "
                         "RETURN count(s) AS n")
        return rows[0]["n"] if rows else 0

    # ── Q6: Reverse traversal — find dependents ───────────────────────────

    def get_dependents(self, service_name: str) -> list[str]:
        """
        Returns names of services that directly DEPEND_ON service_name.
        i.e. services immediately affected if service_name fails.

        Used by fault_injector to determine which upstream service fires
        the alert — mirrors how real monitoring surfaces symptoms, not roots.
        """
        cypher = """
        MATCH (dependent:Service)-[:DEPENDS_ON]->(target:Service {name: $service_name})
        RETURN dependent.name AS name
        """
        rows = self._run(cypher, service_name=service_name)
        dependents = [row["name"] for row in rows]
        log.debug("dependents_found", service=service_name, dependents=dependents)
        return dependents

    # ── Utility queries ────────────────────────────────────────────────────

    def get_all_service_statuses(self) -> dict[str, str]:
        """Returns {service_name: status} for all Service nodes."""
        cypher = "MATCH (s:Service) RETURN s.name AS name, s.status AS status"
        rows = self._run(cypher)
        return {row["name"]: row["status"] for row in rows}

    def reset_all_to_healthy(self) -> None:
        """
        Reset all Service nodes to HEALTHY status.
        Used between evaluation scenarios to restore a clean state.
        """
        cypher = """
        MATCH (s:Service)
        SET s.status = 'HEALTHY', s.error_code = null, s.last_updated = datetime()
        """
        self._run(cypher)
        log.info("all_services_reset_to_healthy")

    def node_counts(self) -> dict[str, int]:
        """Returns count of each node type. Used for validation."""
        cypher = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS count
        ORDER BY label
        """
        rows = self._run(cypher)
        return {row["label"]: row["count"] for row in rows}