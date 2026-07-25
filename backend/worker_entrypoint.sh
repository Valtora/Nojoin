#!/bin/bash
set -e

# Enter as root only to repair ./data bind-mount ownership, then drop to the
# non-root appuser via gosu (replaces the former init-perms container). See
# backend/entrypoint.sh for the full rationale.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data/recordings
    [ "$(stat -c %u /app/data)" = "1000" ] || chown -R appuser:appuser /app/data
    exec gosu appuser "$0" "$@"
fi

exec "$@"
