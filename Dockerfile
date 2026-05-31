# Main application container for Agentic GraphRAG
# Runs the LangGraph orchestration brain (Module C)

FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    docker.io \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY core/ ./core/
COPY module_a_target_env/ ./module_a_target_env/
COPY module_b_graph_database/ ./module_b_graph_database/
COPY module_c_agentic_brain/ ./module_c_agentic_brain/
COPY module_d_sandbox_engine/ ./module_d_sandbox_engine/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create non-root user
RUN useradd -m -u 1000 agentic && chown -R agentic:agentic /app
USER agentic

# Default: Run the agentic brain (Phase 3+)
# For Phase 1, this is a stub. Override with specific entry point.
CMD ["python", "-m", "module_c_agentic_brain.state_machine"]
