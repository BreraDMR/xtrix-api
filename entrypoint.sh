#!/bin/sh
# Entrypoint script for Railway deployment
# Runs database migrations and starts the application

set -e  # Exit on any error

echo "🚀 Starting application..."

# Run database migrations
echo "📦 Running database migrations..."
if ! alembic upgrade head; then
    echo "❌ Migration failed! This is critical - application may not work correctly."
    echo "Checking current migration status..."
    alembic current || true
    echo "Attempting to continue, but errors may occur..."
fi

# Start the application
echo "✅ Starting uvicorn server on port ${PORT:-8000}..."
# Используем --proxy-headers чтобы uvicorn правильно обрабатывал заголовки от Railway прокси
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers

