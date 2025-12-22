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

# Copy and make entrypoint script executable
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose port (Railway uses dynamic ports, but we expose 8000 as default)
# Railway will override with PORT env variable at runtime
EXPOSE 8000

# Health check (use PORT env variable at runtime, default to 8000)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD sh -c 'curl -f http://localhost:${PORT:-8000}/metrics/health/live || exit 1'

# Use entrypoint script (runs migrations then starts app)
ENTRYPOINT ["/app/entrypoint.sh"]
