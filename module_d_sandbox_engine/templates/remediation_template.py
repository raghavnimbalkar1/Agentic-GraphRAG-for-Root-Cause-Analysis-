"""
Container templates for common remediation scenarios.

Phase 4: Pre-built images with Python, database tools, HTTP clients, etc.

Each template:
- Based on minimal Alpine/Debian slim image
- Pre-installed tools for specific remediation categories
- Exec entrypoint: python /opt/app/remediation.py (or equivalent)
"""

# Dockerfile templates for pre-built sandbox images

PYTHON_TEMPLATE = """
FROM python:3.10-slim

# Install base tools
RUN apt-get update && apt-get install -y \\
    curl \\
    jq \\
    telnet \\
    && rm -rf /var/lib/apt/lists/*

# Create app directory
RUN mkdir -p /opt/app && chmod 555 /opt/app

# Install common Python packages
RUN pip install --no-cache-dir \\
    requests \\
    psycopg2-binary \\
    pymongo \\
    redis \\
    pandas

# Non-root user
RUN useradd -m -u 1000 remediator
USER remediator

ENTRYPOINT ["python"]
CMD ["/opt/app/remediation.py"]
"""

POSTGRES_CLIENT_TEMPLATE = """
FROM postgres:15-alpine

# Install additional tools
RUN apk add --no-cache curl jq bash

# Non-root user
RUN adduser -D -u 1000 remediator
USER remediator

ENTRYPOINT ["psql"]
"""

REDIS_TOOLS_TEMPLATE = """
FROM redis:7-alpine

# Install additional tools
RUN apk add --no-cache curl jq bash

# Non-root user
RUN adduser -D -u 1000 remediator
USER remediator

ENTRYPOINT ["redis-cli"]
"""

CURL_JQ_TEMPLATE = """
FROM alpine:latest

RUN apk add --no-cache \\
    curl \\
    jq \\
    bash \\
    python3 \\
    py3-pip

# Non-root user
RUN adduser -D -u 1000 remediator
USER remediator

WORKDIR /opt/app
"""

# Registry mapping
TEMPLATE_REGISTRY = {
    "python": PYTHON_TEMPLATE,
    "postgres": POSTGRES_CLIENT_TEMPLATE,
    "redis": REDIS_TOOLS_TEMPLATE,
    "curl": CURL_JQ_TEMPLATE,
}
