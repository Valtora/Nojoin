#!/bin/bash
set -e

# Run migrations
echo "Running database migrations..."
python -m backend.startup_migrations

# Avoid running the same Alembic upgrade a second time during FastAPI startup.
export NOJOIN_SKIP_APP_STARTUP_MIGRATIONS=1

# Execute the command passed to the docker container
exec "$@"
