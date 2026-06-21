#!/bin/bash
# sops/redis/restart.sh
#
# Restarts the redis-cart container via the Docker daemon.
# Used when redis-cart is OOM_KILLED or unresponsive and a clean
# restart is the first remediation attempt.
#
# Requires: docker CLI inside the sandbox image, Docker socket mounted
# Env vars: TARGET_CONTAINER (default: redis-cart)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

TARGET="${TARGET_CONTAINER:-redis-cart}"

echo "Attempting restart of container: ${TARGET}" >&2

if ! docker inspect "${TARGET}" >/dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"container '${TARGET}' not found\"}"
    exit 1
fi

docker restart "${TARGET}" >&2

# Wait for the container to report running state (max 15s)
for i in $(seq 1 15); do
    STATUS=$(docker inspect -f '{{.State.Status}}' "${TARGET}" 2>/dev/null || echo "unknown")
    if [ "${STATUS}" = "running" ]; then
        echo "Container running after ${i}s" >&2
        break
    fi
    sleep 1
done

if [ "${STATUS}" != "running" ]; then
    echo "{\"success\": false, \"error\": \"container did not return to running state\", \"final_status\": \"${STATUS}\"}"
    exit 1
fi

# Confirm Redis itself is responding post-restart, not just the container process
sleep 1
if docker exec "${TARGET}" redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "{\"success\": true, \"action\": \"container_restarted\", \"target\": \"${TARGET}\", \"redis_responsive\": true}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"container running but redis-cli ping failed\", \"target\": \"${TARGET}\"}"
    exit 1
fi