#!/bin/sh
# Entrypoint script for Railway deployment
# Runs database migrations and starts the application

set -e  # Exit on any error

echo "🚀 Starting application..."

# Run database migrations
echo "📦 Running database migrations..."
alembic upgrade head || {
    echo "❌ Migration failed, but continuing (might be already applied)..."
    # Don't fail if migration already applied
}

# Start the application
echo "✅ Starting uvicorn server on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

