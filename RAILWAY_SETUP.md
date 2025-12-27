# ⚙️ Настройка Railway для деплоя

## 🔧 Необходимые переменные окружения в Railway

После деплоя на Railway нужно настроить следующие переменные окружения:

### 1. CORS_ORIGINS (ОБЯЗАТЕЛЬНО)

Добавьте домен вашего frontend (Vercel) в CORS origins:

```
CORS_ORIGINS=https://xtrix.vercel.app,https://xtrix.vercel.app
```

**Как добавить:**
1. Откройте проект в Railway Dashboard
2. Перейдите в Settings → Variables
3. Добавьте переменную:
   - **Key:** `CORS_ORIGINS`
   - **Value:** `https://xtrix.vercel.app` (или ваш домен Vercel)

### 2. ENVIRONMENT

```
ENVIRONMENT=production
```

### 3. DATABASE_URL

Должна быть настроена автоматически при подключении PostgreSQL в Railway.

### 4. JWT_SECRET

Должен быть настроен (убедитесь, что он установлен).

### 5. Другие переменные (если используются)

- `REDIS_URL` (если используется Redis)
- `SMTP_*` (для email)
- `TWILIO_*` (для SMS)
- И другие по необходимости

## 🗄️ Применение миграций

После деплоя **обязательно** примените миграции базы данных:

### Вариант 1: Через Railway Dashboard

1. Откройте проект в Railway Dashboard
2. Перейдите в раздел "Deployments"
3. Найдите последний успешный деплой
4. Откройте вкладку "Shell" или "Logs"
5. В консоли выполните:
   ```bash
   alembic upgrade head
   ```

### Вариант 2: Через Railway CLI

```bash
railway shell
alembic upgrade head
exit
```

### Вариант 3: Добавить в Dockerfile (автоматический запуск миграций)

Если хотите, чтобы миграции применялись автоматически при каждом деплое, можно изменить Dockerfile:

```dockerfile
# После COPY . . добавьте:
RUN chmod +x entrypoint.sh

# И замените CMD на:
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Но это менее безопасно, так как миграции могут упасть и помешать запуску приложения.

## 🔍 Проверка после деплоя

### 1. Проверить, что backend запущен

Откройте в браузере:
```
https://xtrix-production.up.railway.app/
```

Должен вернуться JSON:
```json
{
  "name": "Xtrix API",
  "version": "1.0.0",
  "status": "running",
  "docs": "/docs",
  "health": "/metrics/health"
}
```

### 2. Проверить health endpoint

```
https://xtrix-production.up.railway.app/metrics/health
```

### 3. Проверить CORS

Проверьте в браузере (Console) при запросе с frontend:
- Не должно быть ошибок CORS
- Заголовок `Access-Control-Allow-Origin` должен содержать ваш домен Vercel

### 4. Проверить документацию API

```
https://xtrix-production.up.railway.app/docs
```

## ⚠️ Устранение проблем

### 502 Bad Gateway

**Проблема:** Backend не отвечает

**Решение:**
1. Проверьте логи в Railway Dashboard → Deployments → Logs
2. Убедитесь, что все переменные окружения настроены
3. Проверьте, что порт 8000 экспонируется
4. Проверьте, что база данных подключена

### CORS ошибки

**Проблема:** `Access-Control-Allow-Origin header is missing`

**Решение:**
1. Убедитесь, что `CORS_ORIGINS` содержит ваш домен Vercel
2. Убедитесь, что `ENVIRONMENT=production`
3. Перезапустите deployment в Railway

### Миграции не применены

**Проблема:** Новые таблицы/колонки не существуют

**Решение:**
1. Примените миграции вручную (см. выше)
2. Проверьте, что `DATABASE_URL` правильный

## 📝 Пример полной конфигурации переменных окружения

```
ENVIRONMENT=production
CORS_ORIGINS=https://xtrix.vercel.app
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET=your-secret-key-minimum-32-characters
REDIS_URL=redis://localhost:6379/0
```





