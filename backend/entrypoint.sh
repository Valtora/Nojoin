#!/bin/bash
set -e

# The api/worker images intentionally default to root ONLY to repair ownership of
# the ./data bind mount (Docker creates a missing bind source as root, which an
# unprivileged process cannot then write into), after which they immediately drop
# to the non-root appuser via gosu. This replaces the former one-shot init-perms
# container. The long-running process runs as uid 1000; the release smoke test
# asserts that dropped runtime uid.
if [ "$(id -u)" = "0" ]; then
    # Repair logic is shared with backend/worker_entrypoint.sh; it checks every
    # write-critical directory independently rather than inferring the whole
    # tree's state from the data root (issue #153).
    . /app/backend/entrypoint_common.sh
    # Never fatal: both entrypoints run under `set -e`, and a container that
    # refuses to boot would hide the diagnostic it is trying to surface.
    nojoin_repair_data_ownership || true
    exec gosu appuser "$0" "$@"
fi

# Run migrations
echo "Running database migrations..."
python -m backend.startup_migrations

echo "Running startup canonical cutover..."
python -m backend.startup_canonical_cutover

# Avoid running the same Alembic upgrade a second time during FastAPI startup.
export NOJOIN_SKIP_APP_STARTUP_MIGRATIONS=1

# Execute the command passed to the docker container
exec "$@"
