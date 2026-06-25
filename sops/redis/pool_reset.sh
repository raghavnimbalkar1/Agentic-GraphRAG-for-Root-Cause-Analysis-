#!/bin/bash
# sops/redis/pool_reset.sh
#
# Remediation for POOL_EXHAUSTION: drop saturating client connections without
# restarting redis. `CLIENT KILL TYPE normal` defaults to SKIPME yes, so it
# closes the blocking/idle clients (and any app connections, which auto-reconnect)
# but not the SOP's own connection. Non-restart remediation.
#
# Requires: redis-cli inside the sandbox image, network access (LOW risk).
# Env vars: REDIS_HOST (default redis-cart), REDIS_PORT (default 6379)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

HOST="${REDIS_HOST:-redis-cart}"
PORT="${REDIS_PORT:-6379}"

BEFORE=$(redis-cli -h "${HOST}" -p "${PORT}" INFO clients | tr -d '\r' | awk -F: '/^connected_clients:/{print $2}')
echo "connected_clients before: ${BEFORE}" >&2

redis-cli -h "${HOST}" -p "${PORT}" CLIENT KILL TYPE normal >&2

sleep 1
AFTER=$(redis-cli -h "${HOST}" -p "${PORT}" INFO clients | tr -d '\r' | awk -F: '/^connected_clients:/{print $2}')
echo "connected_clients after: ${AFTER}" >&2

if [ "${AFTER:-999}" -le 50 ]; then
    echo "{\"success\": true, \"action\": \"pool_reset\", \"clients_before\": ${BEFORE:-0}, \"clients_after\": ${AFTER:-0}}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"connection pool still saturated\", \"clients_after\": ${AFTER:-0}}"
    exit 1
fi
