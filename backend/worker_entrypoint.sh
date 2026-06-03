#!/bin/bash
set -e

echo "Preloading ML models..."
python -m backend.preload_models

exec "$@"
