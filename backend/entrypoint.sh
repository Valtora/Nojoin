#!/bin/bash
set -e

# Run migrations
echo "Running database migrations..."
alembic upgrade head

# Execute the command passed to the docker container
exec "$@"
