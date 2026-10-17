#!/bin/sh
set -e
echo "Running database migrations..."
# a failed migration must stop the deploy - serving on a half-migrated schema is worse than not serving
alembic upgrade head
echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
