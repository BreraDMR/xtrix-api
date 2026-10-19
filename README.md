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

## Deploying for free (test environment)

- **Database:** [Neon](https://neon.com) free plan (limits and sleep policy: see the Neon console). Copy the
  connection string, keep `?sslmode=require`.
- **API:** [Render](https://render.com) - "New Blueprint" on this repo picks up `render.yaml` (free Docker
  web service; sleeps after 15 min idle, ~1 min to wake). Fill in `DATABASE_URL`, `CORS_ORIGINS` and,
  if you want `/docs`, `DOCS_USERNAME`/`DOCS_PASSWORD`. Migrations run on every start; a failed
  migration stops the deploy.
- **Scheduled jobs:** `.github/workflows/jobs.yml` runs them every 15 minutes once the repo secrets
  `JOBS_DATABASE_URL` and `JOBS_JWT_SECRET` (plus `SMTP_*` for e-mail) are set.

Render's own free Postgres is deleted after 30 days, so it isn't used.

The demo frontend runs on Vercel. Set `VITE_API_URL` to the Render HTTPS origin, then set both
`FRONTEND_URL` and `CORS_ORIGINS` on the API to the Vercel HTTPS origin. Use the same European region
for Render and Neon. The free demo uses one API worker and in-memory fallback for Redis; unconfirmed
assistant plans are temporary and do not survive a restart. Add shared Redis before scaling workers.

Use Stripe test keys for the demo. Keep all credentials in hosting environment variables; `.env*`
files are excluded from Docker builds.

## Scheduled jobs

Reminders, follow-ups, birthday greetings, subscription expiry, low-stock e-mails and the monthly
finance archive don't run by themselves. Either start the worker (`docker compose up worker`, or
`python scripts/run_jobs.py --loop`), or call them from cron, e.g.

    */15 * * * *  python scripts/run_jobs.py reminders followups
    0 8 * * *     python scripts/run_jobs.py birthdays subscriptions low_stock finance_archive

Running a job twice is harmless: sent messages are recorded in `notification_log`.
Messages to clients use the client's own `language`, e-mails to staff use their UI language
from preferences.

## Stripe checkout

Checkout reuses the user's open session. Choosing another plan expires the old session before creating
its replacement. `stripe_checkout_state` keeps the customer binding and the exact idempotent request
across network failures and e-mail changes; run migrations before deploying this code.

An unresolved attempt older than 23 hours returns 409 until it is reconciled with Stripe. It is never
blindly submitted again after Stripe's idempotency retention window. Subscription changes use cancel
and undo-cancellation; a paid tariff change needs a separate billing flow.

## API reference

`/docs` serves a [Scalar](https://scalar.com) page built from the same OpenAPI schema (Swagger UI and
ReDoc are off). In production it's behind HTTP Basic: set `DOCS_USERNAME` and `DOCS_PASSWORD`, or
leave them empty and there are no docs at all.

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

## Release branches

Work in `feature/*` branches, integrate into `develop`, then promote checked commits to `staging`.
The public demo (Render API, Neon database and Vercel web client) follows `staging` and uses Stripe sandbox.
`main` holds reviewed releases; no production service is connected to it yet. Promote the API first,
then the web client, so each web branch can validate against the API contract on the same branch.
Never share the staging database or Stripe sandbox with a future production environment.
