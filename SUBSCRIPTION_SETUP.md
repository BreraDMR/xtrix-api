# Настройка системы подписок

## 1. Применение миграции

Миграция `0023_add_transactions_and_subscription_fields.py` должна применяться автоматически при деплое через Railway. 

Если нужно применить вручную:

```bash
# Локально
alembic upgrade head

# На Railway (через CLI)
railway run alembic upgrade head
```

## 2. Переменные окружения

Добавьте следующие переменные окружения в Railway (Settings → Variables):

### Обязательные (для Stripe)
```bash
STRIPE_API_KEY=sk_test_... # или sk_live_... для production
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Обязательные (для редиректов)
```bash
FRONTEND_URL=https://your-frontend-domain.vercel.app
# или для локальной разработки:
# FRONTEND_URL=http://localhost:3000
```

### Опциональные (для других платежных систем)
```bash
# Apple Pay
APPLE_SHARED_SECRET=your_apple_shared_secret
APPLE_WEBHOOK_TOKEN=your_apple_webhook_token

# Google Pay
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_WEBHOOK_TOKEN=your_google_webhook_token

# ERL Pay
ERL_WEBHOOK_TOKEN=your_erl_webhook_token
```

### Для уведомлений (опционально)
```bash
# Email (уже должны быть настроены)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=noreply@yourdomain.com

# SMS (если используется Twilio)
TWILIO_SID=your_twilio_sid
TWILIO_TOKEN=your_twilio_token
TWILIO_FROM=+1234567890
```

## 3. Настройка периодических задач

### Вариант 1: Railway Cron Jobs (рекомендуется)

1. В Railway Dashboard → ваш проект → Settings → Cron Jobs
2. Добавьте новую задачу:
   - **Schedule**: `0 * * * *` (каждый час) или `0 0 * * *` (раз в день в полночь)
   - **Command**: `python scripts/subscription_tasks.py`

### Вариант 2: Cron в контейнере

Если Railway не поддерживает cron jobs, можно добавить cron в Dockerfile или запускать через отдельный сервис.

Добавьте в Dockerfile:
```dockerfile
# Установка cron
RUN apt-get update && apt-get install -y cron

# Копирование скрипта и настройка cron
COPY scripts/subscription_tasks.py /app/scripts/
COPY crontab /etc/cron.d/subscription-tasks
RUN chmod 0644 /etc/cron.d/subscription-tasks
RUN crontab /etc/cron.d/subscription-tasks

# Запуск cron
CMD cron && uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Создайте файл `crontab`:
```
0 * * * * cd /app && python scripts/subscription_tasks.py >> /var/log/subscription_tasks.log 2>&1
```

### Вариант 3: Отдельный worker контейнер

Создайте отдельный сервис в Railway для запуска задач:

1. Создайте новый сервис в Railway
2. Используйте тот же Dockerfile, но с другим entrypoint:
   ```dockerfile
   CMD while true; do python scripts/subscription_tasks.py; sleep 3600; done
   ```

### Вариант 4: Celery (для более сложных сценариев)

Если нужны более сложные задачи, можно использовать Celery + Redis:

```bash
# Установить зависимости
pip install celery redis

# Запустить worker
celery -A app.tasks.celery_app worker --loglevel=info
```

## 4. Проверка работы

### Проверка деактивации подписок

1. Создайте тестовую подписку с истекшим `current_period_end`
2. Запустите вручную:
   ```bash
   python scripts/deactivate_expired_subscriptions.py
   ```
3. Проверьте, что статус подписки изменился на `expired`

### Проверка уведомлений

1. Создайте тестовую подписку с `current_period_end` через 3 дня
2. Запустите:
   ```bash
   python -c "from app.db.session import SessionLocal; from app.api.core.subscription_notifications import send_subscription_expiry_notifications; db = SessionLocal(); send_subscription_expiry_notifications(db, days_before=3); db.close()"
   ```
3. Проверьте email/SMS получателя

## 5. Мониторинг

Рекомендуется настроить мониторинг выполнения задач:

- Логи Railway автоматически показывают выполнение
- Можно добавить отправку уведомлений об ошибках в Slack/Email
- Настроить алерты при критических ошибках

## 6. Ручной запуск задач (для тестирования)

```bash
# Деактивация истекших подписок
python scripts/deactivate_expired_subscriptions.py

# Все задачи (деактивация + уведомления)
python scripts/subscription_tasks.py
```

## 7. API эндпоинт для ручного запуска (только для админа)

Админ может запустить деактивацию вручную через API:
```
POST /subscriptions/admin/deactivate-expired
```

Требует аутентификацию и роль `admin`.

