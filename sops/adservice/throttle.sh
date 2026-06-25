#!/bin/bash
# sops/adservice/throttle.sh
#
# Non-restart remediation for a HIGH_CPU fault. Instead of killing/restarting
# the offending container (which would drop in-flight requests), this caps its
# CPU allocation at the cgroup level via `docker update --cpus`. The container
# keeps serving — it just can no longer monopolise host CPU. This is the first
# SOP in the system that remediates WITHOUT a restart.
#
# Requires: docker CLI inside the sandbox image, Docker socket mounted
#           (Skill risk_level MEDIUM grants the socket — see sandbox_tools).
# Env vars: TARGET_CONTAINER (default: adservice)
#           THROTTLE_CPUS    (default: 0.1)
#           CPU_THRESHOLD    (default: 80  — success if CPU% falls below this)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

TARGET="${TARGET_CONTAINER:-adservice}"
THROTTLE_CPUS="${THROTTLE_CPUS:-0.1}"
CPU_THRESHOLD="${CPU_THRESHOLD:-80}"

echo "Throttling ${TARGET} to ${THROTTLE_CPUS} CPUs" >&2

if ! docker inspect "${TARGET}" >/dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"container '${TARGET}' not found\"}"
    exit 1
fi

# Apply the CPU cap (cgroup-level — takes effect immediately, no restart).
docker update --cpus="${THROTTLE_CPUS}" "${TARGET}" >&2

# Let the new limit take effect, then sample real CPU usage.
sleep 3
CPU_RAW=$(docker stats --no-stream --format '{{.CPUPerc}}' "${TARGET}" | tr -d '% ')
echo "CPU after throttle: ${CPU_RAW}%" >&2

# Confirm the cap is actually recorded on the container (deterministic check)
NANO=$(docker inspect -f '{{.HostConfig.NanoCpus}}' "${TARGET}" 2>/dev/null || echo 0)

if awk "BEGIN{exit !(${CPU_RAW:-100} < ${CPU_THRESHOLD})}"; then
    echo "{\"success\": true, \"action\": \"cpu_throttled\", \"target\": \"${TARGET}\", \"cpus\": \"${THROTTLE_CPUS}\", \"nano_cpus\": ${NANO}, \"cpu_pct\": \"${CPU_RAW}\"}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"CPU still above ${CPU_THRESHOLD}% after throttle\", \"cpu_pct\": \"${CPU_RAW}\", \"nano_cpus\": ${NANO}}"
    exit 1
fi
