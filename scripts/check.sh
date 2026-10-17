#!/usr/bin/env bash
# One command for "is everything green": lint for real errors + the whole test suite
# against a throwaway Postgres. Whatever it starts, it stops.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
PG_PORT=${CHECK_PG_PORT:-55433}
PG_NAME=xtrix-check-pg

echo "==> ruff (errors only)"
"$PY" -m ruff check --isolated --select E9,F63,F7,F82,F811,F823 app tests alembic

cleanup() { docker rm -f "$PG_NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ -z "${DATABASE_URL:-}" ]; then
    echo "==> starting Postgres on :$PG_PORT"
    cleanup
    docker run -d --name "$PG_NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=xtrix_test \
        -p "$PG_PORT:5432" postgres:15 >/dev/null
    for _ in $(seq 1 30); do
        docker exec "$PG_NAME" pg_isready -U postgres -d xtrix_test >/dev/null 2>&1 && break
        sleep 1
    done
    export DATABASE_URL="postgresql://postgres:postgres@localhost:$PG_PORT/xtrix_test"
fi

echo "==> pytest"
"$PY" -m pytest -q --cov=app --cov-report=term:skip-covered "$@"
