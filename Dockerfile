# Optional agent API image; the supported local workflow runs Python on the host.

FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml README.md constraints-tested.txt ./
COPY core/ ./core/
COPY agent/ ./agent/
COPY graph/ ./graph/
COPY sops/ ./sops/

# Install Python dependencies
RUN pip install --no-cache-dir -c constraints-tested.txt .

# Create non-root user
RUN useradd -m -u 1000 agentic && mkdir -p /app/audit && chown -R agentic:agentic /app
USER agentic

EXPOSE 8888
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8888", "--workers", "1"]
