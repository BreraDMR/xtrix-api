#!/usr/bin/env bash
# One command for "is everything green": lint for real errors + the whole test suite
# against a throwaway Postgres. Whatever it starts, it stops.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
PG_PORT=${CHECK_PG_PORT:-55433}
PG_NAME=${CHECK_PG_NAME:-xtrix-check-pg}
# nothing listens on port 1, so the app falls back to in-memory cache and rate limits -
# parallel runs then can't see each other's analytics cache through the compose Redis.
# A REDIS_URL from the shell is ignored on purpose: tests wipe cache keys and must never hit a real one
export REDIS_URL=redis://localhost:1/0

# Reject remote and non-test targets before importing the app or starting tools.
if [ -n "${DATABASE_URL:-}" ]; then
    "$PY" - <<'GUARD'
import os
import re
from sqlalchemy.engine import make_url
url = make_url(os.environ["DATABASE_URL"])
routing_env = any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"))
if (url.host not in {"localhost", "127.0.0.1", "::1"}
        or not re.search(r"(^test_|_test$)", url.database or "") or url.query or routing_env):
    raise SystemExit("Refusing to run checks: use a local database named test_* or *_test.")
GUARD
fi

echo "==> ruff (errors only)"
"$PY" -m ruff check --isolated --select E9,F63,F7,F82,F811,F823 app tests alembic

echo "==> openapi.json up to date"
"$PY" scripts/export_openapi.py --check

pg_id=""
cleanup() {
    if [ -n "$pg_id" ]; then docker rm -f -v "$pg_id" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

if [ -z "${DATABASE_URL:-}" ]; then
    echo "==> starting Postgres on :$PG_PORT"
    pg_id=$(docker create --name "$PG_NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=xtrix_test \
        -p "127.0.0.1:$PG_PORT:5432" postgres:15)
    docker start "$pg_id" >/dev/null
    pg_ready=false
    for _ in $(seq 1 30); do
        if docker exec "$pg_id" pg_isready -h 127.0.0.1 -U postgres -d xtrix_test >/dev/null 2>&1; then
            pg_ready=true
            break
        fi
        sleep 1
    done
    if [ "$pg_ready" != true ]; then
        echo "Postgres did not become ready over TCP; refusing to run pytest." >&2
        exit 1
    fi
    export DATABASE_URL="postgresql://postgres:postgres@localhost:$PG_PORT/xtrix_test"
fi

echo "==> pytest"
"$PY" -m pytest -q --cov=app --cov-report=term:skip-covered --cov-fail-under=65 "$@"
