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

    # Docker's group_add grants the container root process access to ROCm device
    # nodes, but gosu drops supplementary groups unless they are also recorded
    # for appuser in /etc/group. Preserve that access after the privilege drop.
    if [ -n "${ROCM_RENDER_GID:-}" ]; then
        case "$ROCM_RENDER_GID" in
            *[!0-9]*)
                echo "ROCM_RENDER_GID must be a numeric group ID" >&2
                exit 1
                ;;
        esac

        render_group="$(getent group "$ROCM_RENDER_GID" | cut -d: -f1 || true)"
        if [ -z "$render_group" ]; then
            render_group=rocmrender
            if getent group "$render_group" >/dev/null 2>&1; then
                render_group="rocmrender${ROCM_RENDER_GID}"
            fi
            groupadd --gid "$ROCM_RENDER_GID" "$render_group"
        fi
        usermod --append --groups "$render_group" appuser
        echo "Added appuser to ROCm device group $render_group (GID $ROCM_RENDER_GID)"
    fi

    exec gosu appuser "$0" "$@"
fi

exec "$@"
