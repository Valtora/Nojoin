#!/bin/bash
set -e

# The api/worker images intentionally default to root ONLY to repair ownership of
# the ./data bind mount (Docker creates a missing bind source as root, which an
# unprivileged process cannot then write into), after which they immediately drop
# to the non-root appuser via gosu. This replaces the former one-shot init-perms
# container. The long-running process runs as uid 1000; the release smoke test
# asserts that dropped runtime uid.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data/recordings
    # Only walk the tree when ownership is actually wrong. The recursive chown
    # does real work only on the first boot after a fresh ./data; on later
    # restarts (potentially a large recordings library) this short-circuits.
    [ "$(stat -c %u /app/data)" = "1000" ] || chown -R appuser:appuser /app/data
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
