#!/bin/bash
# sops/redis/cache_flush.sh
#
# Fallback SOP — used when a plain restart (Redis_Restart_SOP) does not
# resolve the OOM condition, e.g. because the underlying maxmemory cap
# is still set low from a fault injection or misconfiguration.
#
# Flushes all keys and restores maxmemory to a healthy default.
#
# Requires: redis-cli inside the sandbox image, network access to target
# Env vars: REDIS_HOST (default: redis-cart), REDIS_PORT (default: 6379)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

HOST="${REDIS_HOST:-redis-cart}"
PORT="${REDIS_PORT:-6379}"
RESTORE_MAXMEMORY="${RESTORE_MAXMEMORY:-256mb}"

echo "Connecting to redis at ${HOST}:${PORT}" >&2

if ! redis-cli -h "${HOST}" -p "${PORT}" PING 2>/dev/null | grep -q PONG; then
    echo "{\"success\": false, \"error\": \"cannot reach redis at ${HOST}:${PORT}\"}"
    exit 1
fi

KEYS_BEFORE=$(redis-cli -h "${HOST}" -p "${PORT}" DBSIZE)
echo "Keys before flush: ${KEYS_BEFORE}" >&2

redis-cli -h "${HOST}" -p "${PORT}" FLUSHALL ASYNC >&2
redis-cli -h "${HOST}" -p "${PORT}" CONFIG SET maxmemory "${RESTORE_MAXMEMORY}" >&2
redis-cli -h "${HOST}" -p "${PORT}" CONFIG SET maxmemory-policy allkeys-lru >&2

sleep 1
KEYS_AFTER=$(redis-cli -h "${HOST}" -p "${PORT}" DBSIZE)
CURRENT_MAXMEM=$(redis-cli -h "${HOST}" -p "${PORT}" CONFIG GET maxmemory | tail -1)

echo "Keys after flush: ${KEYS_AFTER}, maxmemory restored to: ${CURRENT_MAXMEM}" >&2

# Success criterion: maxmemory was lifted back above the OOM cap (10MB). This is
# the real remediation — it clears the OOM condition and re-enables the cache.
# We deliberately do NOT require "0 keys remaining": under live loadgenerator
# traffic, cartservice repopulates keys within milliseconds of the flush, so a
# zero-key check would spuriously fail on a healthy system. The flush still runs
# (clearing stale entries); restoring capacity is what makes redis healthy again.
OOM_CEILING=10485760
if [ "${CURRENT_MAXMEM}" -gt "${OOM_CEILING}" ] || [ "${CURRENT_MAXMEM}" -eq 0 ]; then
    echo "{\"success\": true, \"action\": \"cache_flushed\", \"keys_flushed\": ${KEYS_BEFORE}, \"maxmemory_restored\": \"${CURRENT_MAXMEM}\"}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"maxmemory still capped after flush\", \"maxmemory\": \"${CURRENT_MAXMEM}\"}"
    exit 1
fi