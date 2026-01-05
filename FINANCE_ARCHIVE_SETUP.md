# Настройка автоматической архивации финансовых данных

Система автоматически архивирует финансовые данные за прошлый месяц в первый день каждого месяца.

## Как это работает

1. **Автоматическая архивация**: В первый день каждого месяца система создает сводный отчет за прошлый месяц:
   - Доходы (завершенные визиты)
   - Расходы
   - Прибыль
   - Расходы по категориям
   - Статистика по дням

2. **Dashboard**: После архивации dashboard показывает только данные текущего месяца (пустой в начале месяца, пока не будет данных)

3. **Отчетность**: Все исторические данные доступны в разделе "Финансы" через фильтры по датам

## Настройка автоматической архивации

### Вариант 1: Railway Cron Jobs (рекомендуется)

1. В Railway Dashboard → ваш проект → Settings → Cron Jobs
2. Добавьте новую задачу:
   - **Schedule**: `0 0 1 * *` (первый день каждого месяца в полночь)
   - **Command**: `python scripts/finance_tasks.py`

### Вариант 2: Cron в контейнере

Добавьте в Dockerfile:
```dockerfile
# Установка cron
RUN apt-get update && apt-get install -y cron

# Копирование скрипта и настройка cron
COPY scripts/finance_tasks.py /app/scripts/
COPY crontab_finance /etc/cron.d/finance-tasks
RUN chmod 0644 /etc/cron.d/finance-tasks
RUN crontab /etc/cron.d/finance-tasks

# Запуск cron
CMD cron && uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Создайте файл `crontab_finance`:
```
0 0 1 * * cd /app && python scripts/finance_tasks.py >> /var/log/finance_tasks.log 2>&1
```

### Вариант 3: Ежедневная проверка (более надежно)

Если хотите, чтобы система сама проверяла, нужно ли архивировать (рекомендуется):

1. В Railway Cron Jobs:
   - **Schedule**: `0 0 * * *` (каждый день в полночь)
   - **Command**: `python scripts/finance_tasks.py`

Система автоматически проверит, первый ли это день месяца, и запустит архивацию только при необходимости.

### Вариант 4: Отдельный worker контейнер

Создайте отдельный сервис в Railway:

1. Создайте новый сервис в Railway
2. Используйте тот же Dockerfile, но с другим entrypoint:
   ```dockerfile
   CMD while true; do python scripts/finance_tasks.py; sleep 86400; done
   ```

## Ручной запуск архивации

Для тестирования или ручного запуска:

```bash
# Проверка и архивация (если нужно)
python scripts/finance_tasks.py

# Принудительная архивация за конкретный месяц
python -c "
from app.db.session import SessionLocal
from app.api.core.finance_archive import archive_monthly_financial_data
db = SessionLocal()
archive = archive_monthly_financial_data(db, year=2025, month=1, business_id=None)
print(archive)
db.close()
"
```

## Что архивируется

Для каждого бизнеса создается сводный отчет:

```json
{
  "period": {
    "year": 2025,
    "month": 1,
    "from": "2025-01-01T00:00:00",
    "to": "2025-01-31T23:59:59"
  },
  "business_id": 1,
  "summary": {
    "revenue": 15000.00,
    "expenses": 5000.00,
    "profit": 10000.00,
    "profit_margin": 66.67,
    "total_visits": 120,
    "completed_visits": 100
  },
  "expenses_by_category": [
    {
      "category_id": 1,
      "category_name": "Аренда",
      "color": "#EF4444",
      "total": 2000.00
    }
  ],
  "daily": [
    {
      "date": "2025-01-01",
      "revenue": 500.00,
      "expenses": 100.00,
      "profit": 400.00
    }
  ],
  "archived_at": "2025-02-01T00:00:00"
}
```

## Мониторинг

- Логи Railway автоматически показывают выполнение архивации
- В таблице `audit_logs` создается запись с `action='monthly_finance_archive'`
- Можно настроить алерты при ошибках архивации

## Важные замечания

1. **Dashboard очищается автоматически**: После архивации dashboard показывает только текущий месяц
2. **Исторические данные доступны**: Все данные остаются в базе, просто dashboard фильтрует по текущему месяцу
3. **Архивация не удаляет данные**: Система только создает сводные отчеты, исходные данные остаются
4. **Множественные бизнесы**: Архивация выполняется для всех активных бизнесов автоматически

## Проверка работы

1. Дождитесь первого числа месяца или запустите вручную
2. Проверьте логи: `python scripts/finance_tasks.py`
3. Проверьте audit_logs: должна быть запись с `action='monthly_finance_archive'`
4. Проверьте dashboard: должен показывать только текущий месяц

