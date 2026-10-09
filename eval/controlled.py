"""Controlled localization against run-scoped Neo4j fixtures, never live health."""

from contextlib import AbstractContextManager
from pathlib import Path
from uuid import uuid4
import json

FIXTURE = Path(__file__).parent / "fixtures" / "localisation.json"


def load_fixture(path: Path = FIXTURE) -> dict:
    fixture = json.loads(path.read_text())
    names = {name for edge in fixture["edges"] for name in edge}
    ids = [scenario["id"] for scenario in fixture["scenarios"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Fixture IDs must be unique")
    for scenario in fixture["scenarios"]:
        referenced = {scenario["alert"]["service"], scenario["truth_root"]}
        referenced.update(scenario["observations"])
        referenced.update(scenario["truth_potential_blast"])
        if not referenced <= names:
            raise ValueError("Fixture references an unknown service")
    return fixture


class ControlledGraph(AbstractContextManager):
    """Each context owns its EvalService nodes; live Service nodes are read-only."""
    def __init__(self, client, edges: list[list[str]], observations: dict):
        self.client = client
        self.scope = uuid4().hex
        self.edges = edges
        self.observations = observations

    def __enter__(self):
        names = sorted({name for edge in self.edges for name in edge})
        try:
            self.client._run(
                "UNWIND $nodes AS node CREATE (s:EvalService) "
                "SET s.name=node.name, s.scope_id=$scope, s.status=node.status",
                scope=self.scope,
                nodes=[{"name": name, "status": self.observations.get(name, "HEALTHY")} for name in names],
            )
            self.client._run(
                "UNWIND $edges AS edge "
                "MATCH (a:EvalService {scope_id:$scope, name:edge[0]}), "
                "(b:EvalService {scope_id:$scope, name:edge[1]}) "
                "CREATE (a)-[:DEPENDS_ON]->(b)", scope=self.scope, edges=self.edges,
            )
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def localise(self, alert: dict):
        return self.client.get_root_cause(alert["service"], alert["error_type"],
                                          node_label="EvalService", scope_id=self.scope)

    def blast(self, predicted_root: str) -> list[str]:
        rows = self.client._run(
            "MATCH path=(d:EvalService)-[:DEPENDS_ON*1..12]->(r:EvalService {name:$root}) "
            "WHERE all(n IN nodes(path) WHERE n.scope_id=$scope) "
            "RETURN collect(DISTINCT d.name) AS blast", root=predicted_root, scope=self.scope,
        )
        return sorted(rows[0]["blast"]) if rows else []

    def __exit__(self, *args):
        self.client._run("MATCH (s:EvalService {scope_id:$scope}) DETACH DELETE s", scope=self.scope)


def localise_demo(client, scenario: dict) -> dict:
    fixture = load_fixture()
    with ControlledGraph(client, fixture["edges"], {scenario["root"]: scenario["condition"]}) as graph:
        result = graph.localise({"service": scenario["alert_service"], "error_type": scenario["error_type"]})
        return result.model_dump()
