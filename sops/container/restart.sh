#!/bin/bash
# sops/container/restart.sh
#
# Generic container restart SOP. Restarts an arbitrary Online Boutique
# service container via the Docker daemon. Used for CRASH_LOOPING and
# CONNECTION_REFUSED faults on stateless gRPC services (productcatalog,
# payment, cart, recommendation, shipping, email, currency, frontend,
# checkout, adservice).
#
# A restart also re-attaches the container to the networks defined in its
# original config, so this doubles as the remediation for a
# network_partition fault (the service rejoins boutique-sim on restart).
#
# Unlike sops/redis/restart.sh this does NOT run a service-specific
# liveness probe (gRPC services have no shell-callable ping); reaching a
# "running" container state is the success signal.
#
# Requires: docker CLI inside the sandbox image, Docker socket mounted
#           (Skill risk_level MEDIUM grants the socket — see sandbox_tools).
# Env vars: TARGET_CONTAINER (required — the service to restart)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

TARGET="${TARGET_CONTAINER:-}"

if [ -z "${TARGET}" ]; then
    echo '{"success": false, "error": "TARGET_CONTAINER env var not set"}'
    exit 1
fi

echo "Attempting restart of container: ${TARGET}" >&2

if ! docker inspect "${TARGET}" >/dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"container '${TARGET}' not found\"}"
    exit 1
fi

# A network_partition fault detaches the container from the simulation
# network (docker network disconnect). A plain `docker restart` does NOT
# reattach it, so explicitly (idempotently) reconnect first. This is a
# harmless no-op for crash/OOM faults where the container is already
# attached. The connect persists across the restart below.
NETWORK="${SIM_NETWORK:-boutique-sim}"
if docker network connect "${NETWORK}" "${TARGET}" 2>/dev/null; then
    echo "Reattached ${TARGET} to ${NETWORK}" >&2
else
    echo "Network connect skipped (already attached or unavailable): ${NETWORK}" >&2
fi

# A docker-paused container can't receive signals, so `docker restart` would
# hang until its stop-timeout. Unpause first (idempotent no-op if not paused).
if docker unpause "${TARGET}" 2>/dev/null; then
    echo "Unpaused ${TARGET}" >&2
fi

docker restart "${TARGET}" >&2

# Wait for the container to report running state (max 15s)
STATUS="unknown"
for i in $(seq 1 15); do
    STATUS=$(docker inspect -f '{{.State.Status}}' "${TARGET}" 2>/dev/null || echo "unknown")
    if [ "${STATUS}" = "running" ]; then
        echo "Container running after ${i}s" >&2
        break
    fi
    sleep 1
done

if [ "${STATUS}" != "running" ]; then
    echo "{\"success\": false, \"error\": \"container did not return to running state\", \"final_status\": \"${STATUS}\", \"target\": \"${TARGET}\"}"
    exit 1
fi

# Confirm the container is actually attached to the simulation network —
# otherwise it is "running" but unreachable (the hollow-resolution case for
# a network_partition fault). A genuine fix requires real connectivity.
NETWORKS=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "${TARGET}" 2>/dev/null || echo "")
case " ${NETWORKS} " in
    *" ${NETWORK} "*)
        echo "{\"success\": true, \"action\": \"container_restarted\", \"target\": \"${TARGET}\", \"status\": \"running\", \"network\": \"${NETWORK}\"}"
        exit 0
        ;;
    *)
        echo "{\"success\": false, \"error\": \"container running but not attached to ${NETWORK}\", \"target\": \"${TARGET}\", \"networks\": \"${NETWORKS}\"}"
        exit 1
        ;;
esac
