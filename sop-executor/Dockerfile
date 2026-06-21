# sop-executor/Dockerfile
#
# Minimal base image for executing SOP remediation scripts inside the
# Docker sandbox. Built once, reused for every SOP execution.
#
# Includes:
#   - bash            (for .sh scripts)
#   - python3          (for future .py scripts)
#   - redis-cli         (for redis/*.sh scripts)
#   - docker CLI        (for scripts that restart sibling containers,
#                        e.g. redis/restart.sh)
#
# Security note: docker CLI inside this image is a deliberate, narrow
# exception. It only works if the Docker socket is explicitly mounted
# at container run time (agent/tools/sandbox_tools.py controls this
# per-SOP, not globally). Scripts that don't need container control
# (e.g. cache_flush.sh) never get the socket mounted and cannot use it
# even though the binary is present.

FROM python:3.11-slim

# Install bash (slim image defaults to dash), redis-cli, and Docker CLI
# (not the daemon -- just the client binary to talk to a mounted socket)
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        redis-tools \
        curl \
        ca-certificates \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-25.0.3.tgz \
        -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker.tgz /tmp/docker \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Scripts run as this user inside the sandbox.
# Docker socket access (when mounted) is controlled by socket group
# permissions at container run time, not by root inside this image.
RUN useradd -m -u 1000 sopuser
USER sopuser

WORKDIR /script

# No CMD/ENTRYPOINT — the sandbox_tools.py caller specifies the exact
# script to run at `docker run` time. This image is a runtime, not an app.