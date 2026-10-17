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

## License

Proprietary. All rights reserved.
