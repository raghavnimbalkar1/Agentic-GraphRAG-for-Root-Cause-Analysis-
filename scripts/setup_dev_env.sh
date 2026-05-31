#!/bin/bash

# Setup development environment for Agentic GraphRAG

set -e

echo "🚀 Setting up Agentic GraphRAG development environment..."

# Check prerequisites
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker: https://docs.docker.com/install/"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.10+"
    exit 1
fi

# Create Python virtual environment
echo "📦 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "📥 Installing Python dependencies..."
pip install --upgrade pip
pip install -e .

# Copy environment configuration
if [ ! -f .env ]; then
    echo "⚙️  Creating .env from template..."
    cp .env.example .env
    echo "   ⚠️  Edit .env with your configuration"
fi

# Create data directories
mkdir -p telemetry_data logs neo4j/data neo4j/logs neo4j/import

# Start Neo4j
echo "🗄️  Starting Neo4j..."
docker-compose up -d neo4j

# Wait for Neo4j to be ready
echo "⏳ Waiting for Neo4j to be ready (max 30s)..."
for i in {1..30}; do
    if docker-compose exec -T neo4j cypher-shell -u neo4j -p your_secure_password "RETURN 1" &>/dev/null; then
        echo "✅ Neo4j is ready!"
        break
    fi
    echo -n "."
    sleep 1
done

# Initialize graph schema
echo "🔧 Initializing Neo4j schema..."
python -m module_b_graph_database.scripts.init_graph

echo ""
echo "✅ Development environment ready!"
echo ""
echo "Next steps:"
echo "1. Activate venv: source venv/bin/activate"
echo "2. Deploy cluster: python -m module_a_target_env.cluster_setup --action deploy"
echo "3. Stream telemetry: python -m module_a_target_env.telemetry_collector --service all"
echo "4. Run tests: pytest tests/ -v"
echo ""
echo "Neo4j Browser: http://localhost:7474 (user: neo4j)"
