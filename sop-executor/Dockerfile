# Read-only SOP runtime and gRPC readiness probes; no Docker client or socket.
FROM python:3.11-slim

RUN pip install --no-cache-dir grpcio-health-checking==1.68.1
RUN apt-get update && apt-get install -y --no-install-recommends bash redis-tools ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 sopuser
USER sopuser
WORKDIR /script
