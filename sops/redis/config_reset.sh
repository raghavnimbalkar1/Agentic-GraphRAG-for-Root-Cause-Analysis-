#!/bin/bash
# sops/redis/config_reset.sh
#
# Remediation for CONFIG_DRIFT: reset the drifted runtime config back to the
# known-good baseline. Non-restart remediation (live CONFIG SET).
#
# Requires: redis-cli inside the sandbox image, network access (LOW risk).
# Env vars: REDIS_HOST (default redis-cart), REDIS_PORT (default 6379),
#           BASELINE_MAXMEMORY_POLICY (default allkeys-lru)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

HOST="${REDIS_HOST:-redis-cart}"
PORT="${REDIS_PORT:-6379}"
BASELINE="${BASELINE_MAXMEMORY_POLICY:-allkeys-lru}"

BEFORE=$(redis-cli -h "${HOST}" -p "${PORT}" CONFIG GET maxmemory-policy | tail -1 | tr -d '\r')
echo "maxmemory-policy before: ${BEFORE}" >&2

redis-cli -h "${HOST}" -p "${PORT}" CONFIG SET maxmemory-policy "${BASELINE}" >&2

AFTER=$(redis-cli -h "${HOST}" -p "${PORT}" CONFIG GET maxmemory-policy | tail -1 | tr -d '\r')
echo "maxmemory-policy after: ${AFTER}" >&2

if [ "${AFTER}" = "${BASELINE}" ]; then
    echo "{\"success\": true, \"action\": \"config_reset\", \"param\": \"maxmemory-policy\", \"from\": \"${BEFORE}\", \"to\": \"${AFTER}\"}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"config not restored to baseline\", \"current\": \"${AFTER}\"}"
    exit 1
fi
