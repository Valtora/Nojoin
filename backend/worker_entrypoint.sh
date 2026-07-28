#!/bin/bash
set -e

# Enter as root only to repair ./data bind-mount ownership, then drop to the
# non-root appuser via gosu (replaces the former init-perms container). See
# backend/entrypoint.sh for the full rationale.
if [ "$(id -u)" = "0" ]; then
    . /app/backend/entrypoint_common.sh
    # Never fatal: both entrypoints run under `set -e`, and a container that
    # refuses to boot would hide the diagnostic it is trying to surface.
    nojoin_repair_data_ownership || true
    exec gosu appuser "$0" "$@"
fi

exec "$@"
