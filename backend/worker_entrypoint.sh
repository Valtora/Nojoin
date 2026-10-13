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

# Hide any GPU this image's CUDA build cannot drive (driver older than 580, or a
# card older than Turing), so torch and ONNX Runtime run on the CPU instead of
# failing every recording. Exit 3 means "use the CUDA_VISIBLE_DEVICES printed";
# any other failure leaves the environment as it was.
gpu_visible="$(python -m backend.processing.gpu_preflight)" && gpu_status=0 || gpu_status=$?
if [ "$gpu_status" -eq 3 ]; then
    export CUDA_VISIBLE_DEVICES="$gpu_visible"
fi

exec "$@"
