#!/bin/bash
# sops/frontend/restore_cpu.sh
#
# Remediation for DEPENDENCY_TIMEOUT caused by CPU starvation: restore a healthy
# CPU allocation so the service responds within its latency budget again.
# Non-restart remediation (raises the cgroup CPU cap live).
#
# Requires: docker CLI inside the sandbox image, Docker socket mounted (MEDIUM).
# Env vars: TARGET_CONTAINER (required), RESTORE_CPUS (default 2.0)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

TARGET="${TARGET_CONTAINER:-}"
RESTORE_CPUS="${RESTORE_CPUS:-2.0}"

if [ -z "${TARGET}" ]; then
    echo '{"success": false, "error": "TARGET_CONTAINER env var not set"}'
    exit 1
fi

if ! docker inspect "${TARGET}" >/dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"container '${TARGET}' not found\"}"
    exit 1
fi

echo "Clearing CPU quota on ${TARGET} (restore unlimited)" >&2
# The fault sets a tiny cpu_quota; clearing it (-1 = unlimited) is the reliable
# inverse on the same cgroup knob. (--cpus uses NanoCpus, a different field that
# does not cleanly clear, so we stay on cpu_quota throughout.)
docker update --cpu-quota=-1 "${TARGET}" >&2

QUOTA=$(docker inspect -f '{{.HostConfig.CpuQuota}}' "${TARGET}" 2>/dev/null || echo 0)
# Healthy = quota cleared (-1). The agent's evaluator independently re-probes
# real HTTP latency to confirm the service actually got fast again.
if [ "${QUOTA}" = "-1" ] || [ "${QUOTA}" = "0" ]; then
    echo "{\"success\": true, \"action\": \"cpu_restored\", \"target\": \"${TARGET}\", \"cpu_quota\": ${QUOTA}}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"CPU quota not cleared\", \"cpu_quota\": ${QUOTA}}"
    exit 1
fi
