#!/usr/bin/env bash
# Production start script for Render.
# Render provides the PORT env var; we bind uvicorn to it.
# No --reload in production (that's a dev-only feature that watches files).
# We run migrations first so the schema is up to date on every deploy.

set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
