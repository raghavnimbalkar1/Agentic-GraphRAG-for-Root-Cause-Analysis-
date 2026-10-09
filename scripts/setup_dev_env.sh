#!/usr/bin/env bash
# Install the local development environment; --start additionally starts Neo4j.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" != "" && "${1:-}" != "--start" ]]; then
    echo "Usage: bash scripts/setup_dev_env.sh [--start]"
    exit 2
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -c constraints-tested.txt -e ".[dev,dashboard]"
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created .env template. Configure the password/provider before live startup."
fi
mkdir -p audit logs
if [[ "${1:-}" == "--start" ]]; then
    .venv/bin/python -c 'from core.config import settings; assert settings.neo4j_password not in {"", "change_me"}, "Configure NEO4J_PASSWORD in .env first"'
    docker info >/dev/null
    docker compose up -d --wait --wait-timeout 180 neo4j
    .venv/bin/python -m graph.scripts.init_graph
fi
echo "Local environment ready. Activate with: source .venv/bin/activate"
echo "Next: follow README.md for the simulation, executor image, agent and collector."
