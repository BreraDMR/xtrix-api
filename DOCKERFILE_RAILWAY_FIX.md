# 🔧 Исправление Dockerfile для Railway

## Проблема
Railway использует Dockerfile, который может использовать фиксированный порт 8000 вместо переменной $PORT.

## Решение
Нужно изменить Dockerfile, чтобы использовать переменную окружения PORT.

---

## ✅ Исправление Dockerfile

Измените последнюю строку в `Dockerfile`:

**Было:**
```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Должно быть:**
```dockerfile
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Или, если нужно использовать форму с массивом:
```dockerfile
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
```

---

## 📝 Полный исправленный Dockerfile

```dockerfile
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

# Expose port (Railway использует переменную PORT)
EXPOSE ${PORT:-8000}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/metrics/health/live || exit 1

# Run application (используем $PORT для Railway)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

---

## 🔄 Альтернативный вариант (рекомендуемый для Railway)

Если Railway автоматически устанавливает переменную PORT, можно использовать более простой вариант:

```dockerfile
# В конце файла:
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Railway автоматически подставит значение переменной PORT.

---

## 📤 После исправления

1. Сохраните изменения в Dockerfile
2. Сделайте коммит и push в GitHub
3. Railway автоматически пересоберет и перезапустит сервис
4. Проверьте логи - должно работать правильно

---

## 🆘 Если не помогает

Проверьте настройки в Railway:

1. **Settings** → **Deploy**
2. Убедитесь, что нет переопределения Start Command (оно должно быть пустым, если используется Dockerfile)
3. Проверьте логи после перезапуска

