#!/bin/sh

set -e

echo "Starting application..."

echo "Running database migrations..."
if ! alembic upgrade head; then
    echo "Migration failed!"
    alembic current || true
fi

echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers
