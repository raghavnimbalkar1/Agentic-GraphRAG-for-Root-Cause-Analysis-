"""
graph/scripts/init_graph.py

Initialises the Neo4j dual-graph from the Cypher files.

Steps:
  1. Connect to Neo4j (fails fast if unreachable)
  2. Optionally wipe existing data (--clean flag)
  3. Run service_topology.cypher  (creates all nodes + edges)
  4. Verify expected node counts
  5. Run a sample Q1 traversal to confirm graph is queryable

Usage:
    # From project root (venv active):
    python graph/scripts/init_graph.py

    # Wipe and reload (fresh start):
    python graph/scripts/init_graph.py --clean

    # Verify only (no writes):
    python graph/scripts/init_graph.py --verify-only
"""

import sys
import argparse
from pathlib import Path

# ── Path setup: allow running from project root ───────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core import settings, setup_logging, get_logger
from core.exceptions import GraphError
from graph.graph_client import GraphClient
from graph.schema_definitions import get_sop_registry

setup_logging()
log = get_logger(__name__)

TOPOLOGY_CYPHER = PROJECT_ROOT / "graph" / "cypher" / "service_topology.cypher"

# Expected counts after a clean init — used for validation
EXPECTED_NODES = {
    "Service": 12,
    "Skill":   15,
}
EXPECTED_RELS = {
    "DEPENDS_ON":   16,
    "APPLIES_TO":   19,
    "NEXT_IF_FAIL":  1,
}


def wipe_graph(client: GraphClient) -> None:
    log.info("wiping_graph")
    client._run("MATCH (n) WHERE n:Service OR n:Skill DETACH DELETE n")
    log.info("graph_wiped")


def run_cypher_file(client: GraphClient, path: Path) -> None:
    """
    Execute a .cypher file against Neo4j.
    Splits on ';' to handle multi-statement files.
    """
    log.info("running_cypher_file", file=path.name)
    raw = path.read_text(encoding="utf-8")

    # Strip whole-line comments before splitting into individual statements.
    # The splitter must ignore semicolons inside quoted string literals.
    cleaned_raw = "\n".join(
        line for line in raw.splitlines()
        if not line.strip().startswith("//")
    )

    statements = []
    current = []
    quote_char = None
    escape_next = False

    for char in cleaned_raw:
        if escape_next:
            current.append(char)
            escape_next = False
            continue

        if char == "\\" and quote_char is not None:
            current.append(char)
            escape_next = True
            continue

        if char in ("'", '"'):
            current.append(char)
            if quote_char is None:
                quote_char = char
            elif quote_char == char:
                quote_char = None
            continue

        if char == ";" and quote_char is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    log.info("statements_found", count=len(statements))
    for i, stmt in enumerate(statements, 1):
        try:
            client._run(stmt)
        except Exception as e:
            log.error("cypher_statement_failed", stmt_num=i,
                      error=str(e), stmt=stmt[:120])
            raise GraphError(f"Cypher statement {i} failed: {e}") from e

    log.info("cypher_file_complete", file=path.name, statements=len(statements))


def verify_counts(client: GraphClient) -> bool:
    """Verify node and relationship counts match expectations."""
    counts = client.node_counts()

    all_ok = True
    print("\n── Node counts ─────────────────────────────────")
    for label, expected in EXPECTED_NODES.items():
        actual = counts.get(label, 0)
        status = "OK  " if actual == expected else "FAIL"
        print(f"  {status}  {label:<25} expected={expected}  actual={actual}")
        if actual != expected:
            all_ok = False

    # Relationship counts
    rel_cypher = """
    MATCH (a)-[r]->(b)
    WHERE (a:Service OR a:Skill) AND (b:Service OR b:Skill)
    RETURN type(r) AS rel_type, count(r) AS count
    ORDER BY rel_type
    """
    rel_rows = client._run(rel_cypher)
    rel_counts = {row["rel_type"]: row["count"] for row in rel_rows}

    print("\n── Relationship counts ──────────────────────────")
    for rel_type, expected in EXPECTED_RELS.items():
        actual = rel_counts.get(rel_type, 0)
        status = "OK  " if actual == expected else "FAIL"
        print(f"  {status}  {rel_type:<25} expected={expected}  actual={actual}")
        if actual != expected:
            all_ok = False

    for skill in get_sop_registry(client):
        from dataclasses import asdict
        from core.schemas import SkillNode
        try:
            SkillNode.model_validate(asdict(skill))
        except ValueError:
            print(f"  FAIL  invalid skill metadata for {skill.name}")
            all_ok = False
        root = settings.sops_dir.resolve()
        path = (root / skill.script_path.removeprefix('/sops/')).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            print(f"  FAIL  invalid script for {skill.name}")
            all_ok = False
    invalid = client._run("""
        MATCH (a:Skill)-[:NEXT_IF_FAIL]->(b:Skill)
        WHERE NOT EXISTS {
            MATCH (a)-[:APPLIES_TO]->(s:Service)<-[:APPLIES_TO]-(b)
        }
        RETURN a.name AS source, b.name AS target
    """)
    if invalid:
        print("  FAIL  cross-service fallback edges are unsupported")
        all_ok = False
    for label, relationship in (("Service", "DEPENDS_ON"), ("Skill", "NEXT_IF_FAIL")):
        cycles = client._run(f"MATCH p=(n:{label})-[:{relationship}*1..12]->(n) RETURN n.name AS name LIMIT 1")
        if cycles:
            print(f"  FAIL  unsupported {relationship} cycle")
            all_ok = False
    return all_ok


def smoke_test(client: GraphClient) -> bool:
    """Run a sample Q1 traversal to confirm the graph is queryable."""
    print("\n── Smoke test: Q1 root cause traversal ─────────")

    rows = client._run("""
        MATCH p=(:Service {name:'frontend'})-[:DEPENDS_ON*1..8]->(:Service {name:'redis-cart'})
        RETURN max(length(p)) AS depth
    """)
    success = bool(rows) and rows[0]["depth"] == 3
    print("  Read-only frontend -> redis-cart topology check:", "OK" if success else "FAIL")
    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise Neo4j dual-graph")
    parser.add_argument("--clean",       action="store_true",
                        help="Wipe existing data before loading")
    parser.add_argument("--verify-only", action="store_true",
                        help="Only run verification, no writes")
    args = parser.parse_args()

    print("=" * 55)
    print("  Agentic GraphRAG — Neo4j Graph Initialisation")
    print("=" * 55)
    print(f"  Neo4j URI : {settings.neo4j_uri}")
    print(f"  Cypher    : {TOPOLOGY_CYPHER.name}")
    print()

    # ── Connect ──────────────────────────────────────────────
    try:
        client = GraphClient()
        print("  Connected to Neo4j")
    except GraphError as e:
        print(f"  Connection failed: {e}")
        sys.exit(1)

    if args.verify_only:
        ok = verify_counts(client)
        smoke_ok = smoke_test(client)
        sys.exit(0 if ok and smoke_ok else 1)

    # ── Wipe (optional) ───────────────────────────────────────
    if args.clean:
        confirm = input("\n  WARNING: this will DELETE all graph data. Confirm? [y/N] ")
        if confirm.lower() != "y":
            print("  Aborted.")
            sys.exit(0)
        wipe_graph(client)

    # ── Load topology ─────────────────────────────────────────
    if not TOPOLOGY_CYPHER.exists():
        print(f"  Cypher file not found: {TOPOLOGY_CYPHER}")
        sys.exit(1)

    run_cypher_file(client, TOPOLOGY_CYPHER)
    print("  Topology loaded")

    # ── Verify ────────────────────────────────────────────────
    counts_ok = verify_counts(client)
    smoke_ok  = smoke_test(client)

    print("\n" + "=" * 55)
    if counts_ok and smoke_ok:
        print("  Graph ready")
        print("\n  Open Neo4j Browser: http://localhost:7474")
        print("  Visualise topology: MATCH (a:Service)-[r:DEPENDS_ON]->(b:Service) RETURN a,r,b")
        print("  Visualise skills  : MATCH (k:Skill)-[r]->(n) RETURN k,r,n")
    else:
        print("  Validation failed - check output above")
        sys.exit(1)
    print("=" * 55)


if __name__ == "__main__":
    main()
