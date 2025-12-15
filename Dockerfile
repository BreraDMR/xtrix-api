FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create logs directory
RUN mkdir -p logs

# Expose port (Railway автоматически управляет портом)
# Используем переменную окружения PORT в runtime, не в build time
EXPOSE 8080

# Health check (отключен, Railway сам управляет health checks)
# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
#     CMD curl -f http://localhost:$PORT/metrics/health/live || exit 1

# Run application (используем $PORT для Railway)
# Railway всегда устанавливает переменную PORT, поэтому fallback не нужен
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
