#!/bin/bash
# sops/email/disk_cleanup.sh
#
# Remediation for DISK_PRESSURE: reclaim space by removing the bloat/temp file
# from the target container's writable layer. Non-restart remediation — the
# service keeps running.
#
# Requires: docker CLI inside the sandbox image, Docker socket mounted (MEDIUM).
# Env vars: TARGET_CONTAINER (required)
#
# Exit 0 + JSON on success, exit 1 + JSON on failure.

set -euo pipefail

TARGET="${TARGET_CONTAINER:-}"
FILL_PATH="${DISKFILL_PATH:-/tmp/diskfill.bin}"

if [ -z "${TARGET}" ]; then
    echo '{"success": false, "error": "TARGET_CONTAINER env var not set"}'
    exit 1
fi

if ! docker inspect "${TARGET}" >/dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"container '${TARGET}' not found\"}"
    exit 1
fi

SIZE_BEFORE=$(docker inspect -s -f '{{.SizeRw}}' "${TARGET}" 2>/dev/null || echo 0)
echo "Writable layer before cleanup: ${SIZE_BEFORE} bytes" >&2

# Remove the bloat file (and any stray large temp files we created).
docker exec "${TARGET}" rm -f "${FILL_PATH}" >&2 || true

sleep 1
SIZE_AFTER=$(docker inspect -s -f '{{.SizeRw}}' "${TARGET}" 2>/dev/null || echo 0)
echo "Writable layer after cleanup: ${SIZE_AFTER} bytes" >&2

# Success if the writable layer shrank back under the pressure ceiling (100MB).
CEILING=$((100 * 1024 * 1024))
if [ "${SIZE_AFTER}" -lt "${CEILING}" ]; then
    echo "{\"success\": true, \"action\": \"disk_cleaned\", \"target\": \"${TARGET}\", \"size_rw_before\": ${SIZE_BEFORE}, \"size_rw_after\": ${SIZE_AFTER}}"
    exit 0
else
    echo "{\"success\": false, \"error\": \"writable layer still over ceiling\", \"size_rw_after\": ${SIZE_AFTER}}"
    exit 1
fi
