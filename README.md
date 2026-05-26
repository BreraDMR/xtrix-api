# Xtrix

Business management platform: clients, visits, services, staff, finance, and analytics.

## Stack

- **Backend:** FastAPI, PostgreSQL, Alembic
- **Web:** React, TypeScript, Vite, Tailwind CSS
- **Mobile:** Flutter

## Local setup

1. Copy `.env.example` to `.env` and fill in your values.
2. Backend: `pip install -r requirements.txt` → `alembic upgrade head` → `uvicorn app.main:app --reload`
3. Frontend: `cd frontend` → `npm install` → `npm run dev`

## License

Proprietary. All rights reserved.
