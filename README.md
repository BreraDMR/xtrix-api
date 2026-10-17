# Xtrix API

Backend for Xtrix, a business management platform: clients, visits, services, staff, salaries,
inventory, expenses, loyalty and analytics.

The web client lives in a separate repo: [BreraDMR/xtrix-web](https://github.com/BreraDMR/xtrix-web).

## Stack

FastAPI, SQLAlchemy, PostgreSQL, Alembic, Redis (optional, for rate limiting).

## Local setup

1. Copy `.env.example` to `.env` and fill in your values.
2. `pip install -r requirements.txt`
3. `alembic upgrade head`
4. `uvicorn app.main:app --reload`

Or just `docker compose up` for the API with Postgres and Redis.

In production set at least `DATABASE_URL`, `JWT_SECRET` and `CORS_ORIGINS` (comma-separated web client
origins) - nothing about the hosting is hardcoded.

## API contract

`openapi.json` in the repo root is the contract the web client builds its types from
(`npx openapi-typescript openapi.json -o src/api/schema.ts` on the web side). After changing
endpoints or schemas run `python scripts/export_openapi.py` and commit the file; `check.sh`
fails if it's stale. Breaking changes get a `breaking:` prefix in the commit message.

## Checks

`./scripts/check.sh` - lint for real errors and the whole test suite against a throwaway Postgres
in Docker (or against `DATABASE_URL` if you set one; its name must contain `test`). CI runs the same
script.

## License

Proprietary. All rights reserved.
